from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import asyncssh

from .config import settings
from .mysql import DeeBeeError


@dataclass
class RemoteProfile:
    id: str
    driver: str
    name: str
    host: str
    port: int
    user: str
    password: str
    private_key: str = ""
    default_database: str = ""
    default_schema: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "driver": self.driver,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "default_database": "",
            "default_schema": "",
            "options": dict(self.options),
        }


def _ssh_client_keys(profile: RemoteProfile) -> list[asyncssh.SSHKey] | None:
    if profile.options.get("auth_method", "password") != "private_key":
        return None
    if not profile.private_key.strip():
        raise DeeBeeError("SSH 私钥不能为空")
    try:
        return [
            asyncssh.import_private_key(
                profile.private_key,
                passphrase=profile.password or None,
            )
        ]
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise DeeBeeError("SSH 私钥格式或口令不正确") from exc


async def open_ssh(profile: RemoteProfile) -> asyncssh.SSHClientConnection:
    try:
        connection = await asyncssh.connect(
            profile.host,
            port=profile.port,
            username=profile.user,
            password=(
                profile.password
                if profile.options.get("auth_method", "password") == "password"
                else None
            ),
            client_keys=_ssh_client_keys(profile),
            known_hosts=None,
            connect_timeout=10,
            login_timeout=15,
            keepalive_interval=30,
            keepalive_count_max=3,
        )
    except (asyncssh.Error, OSError) as exc:
        raise DeeBeeError(f"SSH 连接失败：{exc}") from exc

    fingerprint = connection.get_server_host_key().get_fingerprint("sha256")
    expected = str(profile.options.get("host_key_fingerprint", "")).strip()
    if expected and expected != fingerprint:
        connection.close()
        await connection.wait_closed()
        raise DeeBeeError(
            f"SSH 主机密钥已变化（期望 {expected}，实际 {fingerprint}），已阻止连接"
        )
    if profile.options.get("strict_host_key", True) and not expected:
        connection.close()
        await connection.wait_closed()
        raise DeeBeeError("首次连接必须先测试并确认 SSH 主机密钥")
    return connection


async def test_ssh(profile: RemoteProfile) -> dict[str, Any]:
    started = time.perf_counter()
    probe = RemoteProfile(**{**profile.__dict__, "options": {**profile.options, "strict_host_key": False}})
    connection = await open_ssh(probe)
    try:
        fingerprint = connection.get_server_host_key().get_fingerprint("sha256")
        expected = str(profile.options.get("host_key_fingerprint", "")).strip()
        if expected and expected != fingerprint:
            raise DeeBeeError(
                f"SSH 主机密钥已变化（期望 {expected}，实际 {fingerprint}），已阻止连接"
            )
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "version": str(connection.get_extra_info("server_version", "")),
            "host_key_fingerprint": fingerprint,
            "host_key_new": not expected,
        }
    finally:
        connection.close()
        await connection.wait_closed()


def encode_instruction(*elements: str) -> bytes:
    encoded: list[bytes] = []
    for index, element in enumerate(elements):
        raw = str(element).encode("utf-8")
        delimiter = b";" if index == len(elements) - 1 else b","
        encoded.append(str(len(raw)).encode("ascii") + b"." + raw + delimiter)
    return b"".join(encoded)


async def read_instruction(reader: asyncio.StreamReader) -> tuple[str, list[str], bytes]:
    elements: list[str] = []
    raw_instruction = bytearray()
    while True:
        length_bytes = bytearray()
        while True:
            char = await reader.readexactly(1)
            raw_instruction.extend(char)
            if char == b".":
                break
            if not char.isdigit() or len(length_bytes) >= 10:
                raise DeeBeeError("RDP 网关返回了无效的 Guacamole 指令")
            length_bytes.extend(char)
        if not length_bytes:
            raise DeeBeeError("RDP 网关返回了无效的 Guacamole 指令")
        value = await reader.readexactly(int(length_bytes))
        delimiter = await reader.readexactly(1)
        raw_instruction.extend(value)
        raw_instruction.extend(delimiter)
        try:
            elements.append(value.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise DeeBeeError("RDP 网关返回了无法解码的指令") from exc
        if delimiter == b";":
            break
        if delimiter != b",":
            raise DeeBeeError("RDP 网关返回了无效的 Guacamole 指令")
    return elements[0], elements[1:], bytes(raw_instruction)


def _rdp_parameter(profile: RemoteProfile, name: str, width: int, height: int, dpi: int) -> str:
    options = profile.options
    values: dict[str, Any] = {
        "hostname": profile.host,
        "port": str(profile.port),
        "username": profile.user,
        "password": profile.password,
        "domain": options.get("domain", ""),
        "security": options.get("security", "any"),
        "ignore-cert": "true" if options.get("ignore_certificate", False) else "false",
        "width": str(width),
        "height": str(height),
        "dpi": str(dpi),
        "color-depth": str(options.get("color_depth", 24)),
        "resize-method": "display-update",
        "enable-wallpaper": "true",
        "enable-font-smoothing": "true",
        "enable-desktop-composition": "true",
        "disable-audio": "false",
        "enable-audio-input": "false",
        "read-only": "false",
    }
    return str(values.get(name, ""))


class GuacdTunnel:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.connection_id = ""

    @classmethod
    async def connect(
        cls,
        profile: RemoteProfile,
        *,
        width: int = 1280,
        height: int = 720,
        dpi: int = 96,
    ) -> "GuacdTunnel":
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.guacd_host, settings.guacd_port),
                timeout=5,
            )
        except (OSError, TimeoutError) as exc:
            raise DeeBeeError(
                f"RDP 网关不可用，请启动 guacd（{settings.guacd_host}:{settings.guacd_port}）"
            ) from exc

        tunnel = cls(reader, writer)
        try:
            writer.write(encode_instruction("select", "rdp"))
            await writer.drain()
            opcode, arguments, _ = await asyncio.wait_for(read_instruction(reader), timeout=10)
            if opcode == "error":
                raise DeeBeeError(arguments[0] if arguments else "RDP 网关拒绝连接")
            if opcode != "args" or not arguments:
                raise DeeBeeError("RDP 网关握手失败")

            server_version = arguments[0] if arguments[0].startswith("VERSION_") else ""
            parameter_names = arguments[1:] if server_version else arguments
            negotiated_version = server_version or "VERSION_1_0_0"
            if negotiated_version > "VERSION_1_5_0":
                negotiated_version = "VERSION_1_5_0"

            writer.write(encode_instruction("size", str(width), str(height), str(dpi)))
            writer.write(encode_instruction("audio", "audio/L16", "audio/ogg"))
            writer.write(encode_instruction("video"))
            writer.write(encode_instruction("image", "image/png", "image/jpeg", "image/webp"))
            writer.write(encode_instruction("timezone", str(profile.options.get("timezone", "Etc/UTC"))))
            writer.write(encode_instruction("name", "DeeBee"))
            values = [_rdp_parameter(profile, name, width, height, dpi) for name in parameter_names]
            connect_values = [negotiated_version, *values] if server_version else values
            writer.write(encode_instruction("connect", *connect_values))
            await writer.drain()

            while True:
                opcode, arguments, raw = await asyncio.wait_for(read_instruction(reader), timeout=20)
                if opcode == "error":
                    raise DeeBeeError(arguments[0] if arguments else "RDP 认证或连接失败")
                if opcode == "ready":
                    tunnel.connection_id = arguments[0] if arguments else "deebee-rdp"
                    return tunnel
        except Exception:
            await tunnel.close()
            raise

    async def close(self) -> None:
        if not self.writer.is_closing():
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()


async def test_rdp(profile: RemoteProfile) -> dict[str, Any]:
    started = time.perf_counter()
    tunnel = await GuacdTunnel.connect(profile, width=800, height=600)
    try:
        while True:
            opcode, arguments, _ = await asyncio.wait_for(
                read_instruction(tunnel.reader), timeout=20
            )
            if opcode == "error":
                raise DeeBeeError(arguments[0] if arguments else "RDP 认证或连接失败")
            if opcode == "sync" and arguments:
                tunnel.writer.write(encode_instruction("sync", arguments[0]))
                await tunnel.writer.drain()
            if opcode == "size" and len(arguments) >= 3 and arguments[0] == "0":
                return {
                    "ok": True,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "version": "RDP via Apache Guacamole",
                }
    finally:
        await tunnel.close()


class RemoteConnectionWorkbench:
    def __init__(self) -> None:
        self.profiles: dict[str, RemoteProfile] = {}
        self.sessions: dict[str, Any] = {}

    def require_profile(self, profile_id: str) -> RemoteProfile:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise DeeBeeError("连接不存在") from exc

    def test_connection(self, profile: RemoteProfile) -> dict[str, Any]:
        if profile.driver == "ssh":
            return asyncio.run(test_ssh(profile))
        if profile.driver == "rdp":
            return asyncio.run(test_rdp(profile))
        raise DeeBeeError("不支持的远程连接类型")

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self.test_connection(self.require_profile(profile_id))

    def cleanup_workspace(self, _: str) -> int:
        return 0


remote_workbench = RemoteConnectionWorkbench()
