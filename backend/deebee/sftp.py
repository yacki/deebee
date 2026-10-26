"""Workbench file management over the saved, host-key-checked SSH connection."""
from __future__ import annotations

import asyncio
import contextlib
import posixpath
import stat
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

import asyncssh
from fastapi import Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .mysql import DeeBeeError
from .remote_connections import open_ssh

MAX_FILE_BYTES = 2 * 1024**3
CHUNK_BYTES = 256 * 1024


def remote_path(value: str) -> str:
    if not value.startswith("/") or "\0" in value or len(value) > 4096:
        raise DeeBeeError("请输入有效的远程绝对路径")
    return posixpath.normpath("/" + value.lstrip("/"))


def filename(value: str) -> str:
    if not value or value in {".", ".."} or any(c in value for c in ("/", "\\", "\0")):
        raise DeeBeeError("文件名不能包含路径分隔符，也不能为 . 或 ..")
    return value


class FileAction(BaseModel):
    action: str = Field(pattern="^(mkdir|move|delete)$")
    path: str = Field(max_length=4096)
    target: str = Field(default="", max_length=4096)
    recursive: bool = False


@asynccontextmanager
async def sftp_client(workbench, profile_id: str):
    try:
        async with await open_ssh(workbench.remote_profile(profile_id, "ssh")) as connection:
            async with await asyncio.wait_for(connection.start_sftp_client(), 30) as client:
                yield client
    except asyncssh.SFTPPermissionDenied as exc:
        raise DeeBeeError("远程账号没有此文件或目录的操作权限") from exc
    except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath) as exc:
        raise DeeBeeError("远程文件或目录不存在，请刷新后重试") from exc
    except asyncssh.SFTPFileAlreadyExists as exc:
        raise DeeBeeError("目标已存在，请使用其他名称；不会覆盖已有文件") from exc
    except TimeoutError as exc:
        raise DeeBeeError("SFTP 操作超时，请刷新目录核实远端状态后重试") from exc
    except (asyncssh.Error, OSError) as exc:
        raise DeeBeeError(f"SFTP 操作失败：{exc}") from exc


async def require_new(client, path: str):
    # lexists also detects dangling symlinks. rename() additionally refuses
    # overwrite on the server, closing the check/use race.
    if await client.lexists(path):
        raise DeeBeeError("目标已存在，请使用其他名称；不会覆盖已有文件")


def install_sftp(app, workbench, current_user):
    prefix = "/api/connections/{profile_id}/files"

    @app.get(prefix)
    async def listing(profile_id: str, path: str = Query(default="", max_length=4096),
                      _: str = Depends(current_user)):
        async with sftp_client(workbench, profile_id) as client:
            async with asyncio.timeout(60):
                home = await client.realpath(".")
                path = await client.realpath(remote_path(path) if path else home)
                items = []
                async for entry in client.scandir(path):
                    name = str(entry.filename)
                    if name in {".", ".."}:
                        continue
                    full_path = posixpath.join(path, name)
                    attrs = entry.attrs
                    is_link = stat.S_ISLNK(attrs.permissions or 0) or attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK
                    is_dir = stat.S_ISDIR(attrs.permissions or 0) or attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                    if is_link:
                        with contextlib.suppress(asyncssh.SFTPError):
                            target = await client.stat(full_path)
                            is_dir = stat.S_ISDIR(target.permissions or 0) or target.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                    items.append({"name": name, "path": full_path, "is_dir": is_dir,
                                  "is_link": is_link, "size": attrs.size or 0,
                                  "modified": attrs.mtime, "permissions": stat.filemode(attrs.permissions) if attrs.permissions is not None else "—"})
                    if len(items) > 20000:
                        raise DeeBeeError("此目录超过 20,000 项，请进入更具体的子目录")
                items.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
                return {"path": path, "home": home, "items": items, "max_file_bytes": MAX_FILE_BYTES}

    @app.post(prefix + "/actions")
    async def action(profile_id: str, body: FileAction, _: str = Depends(current_user)):
        path = remote_path(body.path)
        if path == "/":
            raise DeeBeeError("不能修改根目录")
        async with sftp_client(workbench, profile_id) as client:
            async with asyncio.timeout(120):
                if body.action == "mkdir":
                    await require_new(client, path)
                    await client.mkdir(path)
                else:
                    attrs = await client.lstat(path)
                    is_dir = stat.S_ISDIR(attrs.permissions or 0) or attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                    canonical = await client.realpath(path) if is_dir else path
                    if is_dir and canonical == "/":
                        raise DeeBeeError("不能修改根目录")
                    if body.action == "move":
                        target = remote_path(body.target)
                        await require_new(client, target)
                        parent = await client.realpath(posixpath.dirname(target))
                        if is_dir and (parent == canonical or parent.startswith(canonical.rstrip("/") + "/")):
                            raise DeeBeeError("不能将目录移动到自身或其子目录中")
                        await client.rename(path, target)
                    elif is_dir:
                        if body.recursive:
                            # AsyncSSH rmtree unlinks symlinks rather than following them.
                            await client.rmtree(path)
                        else:
                            await client.rmdir(path)
                    else:
                        await client.remove(path)
        return {"ok": True}

    @app.post(prefix + "/upload")
    async def upload(profile_id: str, path: str = Query(max_length=4096),
                     file: UploadFile = File(), _: str = Depends(current_user)):
        temporary = ""
        try:
            name = filename(file.filename or "")
            directory = remote_path(path)
            target = posixpath.join(directory, name)
            async with sftp_client(workbench, profile_id) as client:
                await require_new(client, target)
                temporary = posixpath.join(directory, ".deebee-upload-" + uuid.uuid4().hex)
                try:
                    size = 0
                    async with client.open(temporary, "xb", encoding=None) as output:
                        while chunk := await file.read(CHUNK_BYTES):
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                raise DeeBeeError("单个文件不能超过 2 GB")
                            await asyncio.wait_for(output.write(chunk), 60)
                    await client.rename(temporary, target)
                    temporary = ""
                    return {"ok": True, "path": target, "size": size}
                finally:
                    if temporary:
                        with contextlib.suppress(asyncssh.SFTPError):
                            await client.remove(temporary)
        finally:
            await file.close()

    @app.get(prefix + "/download")
    async def download(profile_id: str, path: str = Query(max_length=4096),
                       _: str = Depends(current_user)):
        path = remote_path(path)
        stack = contextlib.AsyncExitStack()
        try:
            client = await stack.enter_async_context(sftp_client(workbench, profile_id))
            attrs = await client.stat(path)
            if not (stat.S_ISREG(attrs.permissions or 0) or attrs.type == asyncssh.FILEXFER_TYPE_REGULAR):
                raise DeeBeeError("请选择普通文件下载；目录请进入后选择文件")
            source = await stack.enter_async_context(client.open(path, "rb", encoding=None))
            attrs = await source.stat()
            if not (stat.S_ISREG(attrs.permissions or 0) or attrs.type == asyncssh.FILEXFER_TYPE_REGULAR):
                raise DeeBeeError("请选择普通文件下载；目录请进入后选择文件")
            if (attrs.size or 0) > MAX_FILE_BYTES:
                raise DeeBeeError("单个文件不能超过 2 GB")
        except BaseException:
            await stack.aclose()
            raise

        async def chunks():
            try:
                remaining = attrs.size or 0
                while remaining:
                    chunk = await asyncio.wait_for(source.read(min(CHUNK_BYTES, remaining)), 60)
                    if not chunk:
                        raise DeeBeeError("下载期间远程文件发生变化，请重新下载")
                    remaining -= len(chunk)
                    yield chunk
            finally:
                await stack.aclose()

        return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
            "Content-Length": str(attrs.size or 0), "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(posixpath.basename(path), safe=""),
        })
