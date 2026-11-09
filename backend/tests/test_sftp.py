from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncssh
import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from deebee import sftp
from deebee.mysql import DeeBeeError
from deebee.remote_connections import RemoteProfile


class TestSSHServer(asyncssh.SSHServer):
    __test__ = False

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "sftp-qa" and password == "local-test-only"


@asynccontextmanager
async def live_client(root):
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(TestSSHServer, "127.0.0.1", 0,
        server_host_keys=[key], sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(root)))
    profile = RemoteProfile("test", "ssh", "SFTP QA", "127.0.0.1", server.get_port(),
                            "sftp-qa", "local-test-only", options={"strict_host_key": True,
                            "host_key_fingerprint": key.get_fingerprint("sha256")})
    app = FastAPI()

    @app.exception_handler(DeeBeeError)
    async def error(_, exc):
        return JSONResponse({"error": {"message": str(exc)}}, status_code=400)

    def auth(authorization: str = Header(default="")):
        if authorization != "Bearer test-admin":
            raise DeeBeeError("登录已失效")

    def get_profile(profile_id, driver):
        if profile_id != "test" or driver != "ssh":
            raise DeeBeeError("SSH 连接不存在")
        return profile

    sftp.install_sftp(app, SimpleNamespace(remote_profile=get_profile), auth)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer test-admin"}) as client:
            yield client, profile
    finally:
        server.close()
        await server.wait_closed()


BASE = "/api/connections/test/files"


def test_real_sftp_roundtrip_and_protected_operations(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / ".hidden").write_text("hidden")
    (tmp_path / "你好.txt").write_text("中文 contents\n")
    (tmp_path / "dangling").symlink_to("missing")

    async def run():
        async with live_client(tmp_path) as (client, _):
            result = await client.get(BASE)
            assert result.status_code == 200, result.text
            body = result.json()
            assert body["path"] == "/" and body["home"] == "/"
            assert body["items"][0]["name"] == "folder"
            assert {item["name"] for item in body["items"]} == {"folder", ".hidden", "你好.txt", "dangling"}
            assert next(item for item in body["items"] if item["name"] == "dangling")["is_link"]
            assert (await client.get(BASE + "/download", params={"path": "/你好.txt"})).content == "中文 contents\n".encode()
            assert (await client.get(BASE + "/download", params={"path": "/folder"})).status_code == 400
            data = bytes(range(256)) * 2048
            upload = await client.post(BASE + "/upload", params={"path": "/folder"}, files={"file": ("binary.bin", data)})
            assert upload.status_code == 200, upload.text
            assert (tmp_path / "folder/binary.bin").read_bytes() == data
            assert (await client.get(BASE + "/download", params={"path": "/folder/binary.bin"})).content == data
            duplicate = await client.post(BASE + "/upload", params={"path": "/folder"}, files={"file": ("binary.bin", b"overwrite")})
            assert duplicate.status_code == 400 and "目标已存在" in duplicate.text
            assert (tmp_path / "folder/binary.bin").read_bytes() == data
            assert (await client.post(BASE + "/actions", json={"action": "mkdir", "path": "/目标 文件夹"})).status_code == 200
            assert (await client.post(BASE + "/actions", json={"action": "move", "path": "/folder/binary.bin", "target": "/目标 文件夹/移动.bin"})).status_code == 200
            assert not (tmp_path / "folder/binary.bin").exists()
            assert (tmp_path / "目标 文件夹/移动.bin").read_bytes() == data
            conflict = await client.post(BASE + "/actions", json={"action": "move", "path": "/你好.txt", "target": "/目标 文件夹/移动.bin"})
            assert conflict.status_code == 400 and (tmp_path / "你好.txt").exists()
            nested = await client.post(BASE + "/actions", json={"action": "move", "path": "/folder", "target": "/folder/child"})
            assert nested.status_code == 400 and "子目录" in nested.text
            for root in ["/", "/folder/..", "//"]:
                protected = await client.post(BASE + "/actions", json={"action": "delete", "path": root, "recursive": True})
                assert protected.status_code == 400 and "根目录" in protected.text
            nonempty = await client.post(BASE + "/actions", json={"action": "delete", "path": "/目标 文件夹"})
            assert nonempty.status_code == 400 and (tmp_path / "目标 文件夹/移动.bin").exists()
            assert (await client.post(BASE + "/actions", json={"action": "delete", "path": "/目标 文件夹", "recursive": True})).status_code == 200
            assert not (tmp_path / "目标 文件夹").exists()
            assert (await client.post(BASE + "/actions", json={"action": "delete", "path": "/dangling"})).status_code == 200
            assert not (tmp_path / "dangling").is_symlink()
            assert not list(tmp_path.rglob(".deebee-upload-*"))
    asyncio.run(run())


def test_symlink_delete_never_removes_target(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep/data").write_text("preserve")
    (tmp_path / "delete").mkdir()
    (tmp_path / "delete/link").symlink_to("../keep", target_is_directory=True)
    async def run():
        async with live_client(tmp_path) as (client, _):
            response = await client.post(BASE + "/actions", json={"action": "delete", "path": "/delete", "recursive": True})
            assert response.status_code == 200, response.text
            assert (tmp_path / "keep/data").read_text() == "preserve"
    asyncio.run(run())


def test_auth_host_key_paths_missing_files_and_upload_cleanup(tmp_path, monkeypatch):
    async def run():
        async with live_client(tmp_path) as (client, profile):
            assert (await client.get(BASE, headers={"Authorization": ""})).status_code == 400
            for path in ["relative", "/invalid\0name"]:
                assert (await client.get(BASE, params={"path": path})).status_code == 400
            assert (await client.get(BASE, params={"path": "/missing"})).status_code == 400
            for name in ["../escape.txt", "bad\\name.txt"]:
                assert (await client.post(BASE + "/upload", params={"path": "/"}, files={"file": (name, b"bad")})).status_code == 400
            monkeypatch.setattr(sftp, "MAX_FILE_BYTES", 10)
            result = await client.post(BASE + "/upload", params={"path": "/"}, files={"file": ("oversized", b"x"*11)})
            assert result.status_code == 400 and not (tmp_path / "oversized").exists()
            assert not list(tmp_path.glob(".deebee-upload-*"))
            assert (await client.post(BASE + "/upload", params={"path": "/"}, files={"file": ("empty", b"")})).status_code == 200
            assert (await client.get(BASE + "/download", params={"path": "/empty"})).content == b""
            profile.options["host_key_fingerprint"] = "SHA256:wrong"
            mismatch = await client.get(BASE)
            assert mismatch.status_code == 400 and "主机密钥已变化" in mismatch.text
    asyncio.run(run())


def test_permission_denied_is_actionable(tmp_path, monkeypatch):
    class Denied(asyncssh.SFTPServer):
        def scandir(self, path):
            raise asyncssh.SFTPPermissionDenied("denied")
    monkeypatch.setattr(asyncssh, "SFTPServer", Denied)
    async def run():
        async with live_client(tmp_path) as (client, _):
            response = await client.get(BASE)
            assert response.status_code == 400 and "操作权限" in response.text
    asyncio.run(run())
