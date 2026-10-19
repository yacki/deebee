from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from pathlib import Path
from typing import Any

import pytest

from deebee import remote_connections
from deebee.mysql import DeeBeeError
from deebee.remote_connections import RemoteProfile, encode_instruction, read_instruction
from deebee.workbench import DatabaseWorkbenches


def test_remote_profiles_validate_and_encrypt_private_key(tmp_path: Path) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    ssh = manager.create_connection(
        {
            "driver": "ssh",
            "name": "Ops SSH",
            "host": "server.internal",
            "port": 22,
            "user": "operator",
            "password": "key-passphrase",
            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----",
            "options": {
                "auth_method": "private_key",
                "strict_host_key": True,
                "host_key_fingerprint": "SHA256:known",
            },
        }
    )
    rdp = manager.create_connection(
        {
            "driver": "rdp",
            "name": "Windows",
            "host": "windows.internal",
            "port": 3389,
            "user": "alice",
            "password": "rdp-secret",
            "options": {
                "domain": "ACME",
                "security": "nla",
                "ignore_certificate": True,
                "color_depth": 24,
                "timezone": "Australia/Perth",
            },
        }
    )

    stored = path.read_text(encoding="utf-8")
    assert "key-passphrase" not in stored
    assert "BEGIN OPENSSH PRIVATE KEY" not in stored
    assert "rdp-secret" not in stored
    assert ssh["driver"] == "ssh" and "private_key" not in ssh and "password" not in ssh
    assert rdp["options"]["domain"] == "ACME"

    restarted = DatabaseWorkbenches(store_path=path, defaults=[])
    profile = restarted.remote_profile(ssh["id"], "ssh")
    assert profile.password == "key-passphrase"
    assert "BEGIN OPENSSH PRIVATE KEY" in profile.private_key


@pytest.mark.parametrize(
    ("driver", "options", "message"),
    [
        ("ssh", {"auth_method": "agent"}, "SSH 认证方式无效"),
        ("rdp", {"security": "invalid"}, "RDP 安全模式无效"),
        ("rdp", {"color_depth": 8}, "RDP 色深仅支持"),
    ],
)
def test_remote_profile_rejects_invalid_options(
    tmp_path: Path, driver: str, options: dict[str, Any], message: str
) -> None:
    manager = DatabaseWorkbenches(store_path=tmp_path / "connections.json", defaults=[])
    with pytest.raises(Exception, match=message):
        manager.create_connection(
            {
                "driver": driver,
                "name": "Remote",
                "host": "server.internal",
                "port": 22 if driver == "ssh" else 3389,
                "user": "operator",
                "password": "secret",
                "options": options,
            }
        )


def test_guacamole_instruction_codec_handles_utf8_and_multiple_values() -> None:
    raw = encode_instruction("error", "认证失败", "512")
    assert raw == "5.error,4.认证失败,3.512;".encode("utf-8")

    async def decode() -> tuple[str, list[str], bytes]:
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()
        return await read_instruction(reader)

    opcode, values, original = asyncio.run(decode())
    assert opcode == "error"
    assert values == ["认证失败", "512"]
    assert original == raw


def test_guacamole_reads_unicode_character_lengths_without_consuming_next_instruction() -> None:
    async def decode():
        reader = asyncio.StreamReader()
        raw = "4.name,5.桌面😀你好;4.sync,3.123;".encode("utf-8")
        reader.feed_data(raw)
        reader.feed_eof()
        first = await read_instruction(reader)
        second = await read_instruction(reader)
        assert first[:2] == ("name", ["桌面😀你好"])
        assert second[:2] == ("sync", ["123"])
    asyncio.run(decode())


@pytest.mark.parametrize(("message", "expected"), [
    ("SSL/TLS connection failed (untrusted/self-signed certificate?)", "证书校验失败"),
    ("Authentication failure (invalid credentials?)", "请检查用户名、密码和域"),
])
@pytest.mark.parametrize("initial_sync", [False, True])
def test_rdp_test_rejects_placeholder_desktop_before_authentication(monkeypatch, message, expected, initial_sync):
    async def verify():
        reader = asyncio.StreamReader()
        reader.feed_data(encode_instruction("size", "0", "800", "600"))
        if initial_sync:
            reader.feed_data(encode_instruction("sync", "100"))
        reader.feed_data(encode_instruction("error", message, "519"))
        reader.feed_eof()
        writer = type("Writer", (), {"write": lambda _, data: None, "drain": AsyncMock()})()
        tunnel = type("Tunnel", (), {"reader": reader, "writer": writer, "close": AsyncMock()})()
        monkeypatch.setattr(remote_connections.GuacdTunnel, "connect", AsyncMock(return_value=tunnel))
        with pytest.raises(DeeBeeError, match=expected):
            await remote_connections.test_rdp(RemoteProfile("rdp", "rdp", "RDP", "localhost", 3389, "user", "secret"))
        tunnel.close.assert_awaited_once()
    asyncio.run(verify())


def test_rdp_test_waits_for_authenticated_frame_and_acknowledges_sync(monkeypatch):
    async def verify():
        reader = asyncio.StreamReader()
        reader.feed_data(encode_instruction("size", "0", "800", "600")
                         + encode_instruction("sync", "100") + encode_instruction("sync", "100")
                         + encode_instruction("sync", "123"))
        reader.feed_eof()
        written = []
        writer = type("Writer", (), {"write": lambda _, data: written.append(data), "drain": AsyncMock()})()
        tunnel = type("Tunnel", (), {"reader": reader, "writer": writer, "close": AsyncMock()})()
        monkeypatch.setattr(remote_connections.GuacdTunnel, "connect", AsyncMock(return_value=tunnel))
        result = await remote_connections.test_rdp(RemoteProfile("rdp", "rdp", "RDP", "localhost", 3389, "user", "secret"))
        assert result["ok"] is True
        assert written == [encode_instruction("sync", "100"), encode_instruction("sync", "100"), encode_instruction("sync", "123")]
        tunnel.close.assert_awaited_once()
    asyncio.run(verify())


def test_rdp_test_reports_disconnect_before_first_frame(monkeypatch):
    async def verify():
        reader = asyncio.StreamReader()
        reader.feed_data(encode_instruction("size", "0", "800", "600"))
        reader.feed_eof()
        tunnel = type("Tunnel", (), {"reader": reader, "close": AsyncMock()})()
        monkeypatch.setattr(remote_connections.GuacdTunnel, "connect", AsyncMock(return_value=tunnel))
        with pytest.raises(DeeBeeError, match="登录完成前断开"):
            await remote_connections.test_rdp(RemoteProfile("rdp", "rdp", "RDP", "localhost", 3389, "user", "secret"))
        tunnel.close.assert_awaited_once()
    asyncio.run(verify())


def test_remote_profile_public_shape_never_exposes_secrets() -> None:
    profile = RemoteProfile(
        id="ssh-test",
        driver="ssh",
        name="SSH",
        host="127.0.0.1",
        port=22,
        user="root",
        password="password",
        private_key="private-key",
        options={"auth_method": "password"},
    )
    public = profile.public()
    assert "password" not in public
    assert "private_key" not in public
