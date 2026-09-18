from __future__ import annotations

import asyncio
import base64
import contextlib
import select
import socket
import threading
from dataclasses import dataclass
from typing import Any

import asyncssh

from .mysql import DeeBeeError


def _read_exact(sock: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise OSError("代理服务器提前关闭了连接")
        result.extend(chunk)
    return bytes(result)


def _proxy_socket(config: dict[str, Any], host: str, port: int) -> socket.socket:
    proxy_host = str(config.get("proxy_host", "")).strip()
    proxy_port = int(config.get("proxy_port", 1080))
    try:
        sock = socket.create_connection((proxy_host, proxy_port), timeout=10)
        sock.settimeout(10)
        if config.get("proxy_type", "socks5") == "http":
            authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            headers = [
                f"CONNECT {authority} HTTP/1.1",
                f"Host: {authority}",
                "Proxy-Connection: Keep-Alive",
            ]
            user = str(config.get("proxy_user", ""))
            password = str(config.get("proxy_password", ""))
            if user or password:
                credential = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
                headers.append(f"Proxy-Authorization: Basic {credential}")
            sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise OSError("HTTP 代理未返回完整响应")
                response.extend(chunk)
                if len(response) > 65536:
                    raise OSError("HTTP 代理响应过大")
            status = response.split(b"\r\n", 1)[0].split()
            if len(status) < 2 or not status[1].isdigit() or not 200 <= int(status[1]) < 300:
                detail = response.split(b"\r\n", 1)[0].decode("latin1", "replace")
                raise OSError(f"HTTP 代理拒绝连接：{detail}")
        else:
            user = str(config.get("proxy_user", "")).encode("utf-8")
            password = str(config.get("proxy_password", "")).encode("utf-8")
            if len(user) > 255 or len(password) > 255:
                raise OSError("SOCKS5 代理用户名或密码过长")
            methods = b"\x00\x02" if user or password else b"\x00"
            sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
            version, method = _read_exact(sock, 2)
            if version != 5 or method == 0xFF:
                raise OSError("SOCKS5 代理没有可用的认证方式")
            if method == 2:
                sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
                if _read_exact(sock, 2) != b"\x01\x00":
                    raise OSError("SOCKS5 代理认证失败")
            elif method != 0:
                raise OSError("不支持代理服务器要求的 SOCKS5 认证方式")

            if config.get("proxy_dns", True):
                encoded_host = host.encode("idna")
                if len(encoded_host) > 255:
                    raise OSError("目标主机名过长")
                address = b"\x03" + bytes([len(encoded_host)]) + encoded_host
            else:
                resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
                raw_address = socket.inet_pton(resolved[0], resolved[4][0])
                address = (b"\x01" if resolved[0] == socket.AF_INET else b"\x04") + raw_address
            sock.sendall(b"\x05\x01\x00" + address + port.to_bytes(2, "big"))
            version, status, _, address_type = _read_exact(sock, 4)
            if version != 5 or status != 0:
                raise OSError(f"SOCKS5 代理连接失败（状态 {status}）")
            address_size = {1: 4, 4: 16}.get(address_type)
            if address_size is None:
                if address_type != 3:
                    raise OSError("SOCKS5 代理返回了无效地址")
                address_size = _read_exact(sock, 1)[0]
            _read_exact(sock, address_size + 2)
        sock.settimeout(None)
        return sock
    except Exception:
        if "sock" in locals():
            with contextlib.suppress(OSError):
                sock.close()
        raise


class _ProxyForwarder:
    def __init__(self, config: dict[str, Any], target_host: str, target_port: int) -> None:
        self._config = config
        self._target = (target_host, target_port)
        self._closed = threading.Event()
        self._clients: set[socket.socket] = set()
        self._guard = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._listener.settimeout(0.5)
        self.port = int(self._listener.getsockname()[1])
        self._thread = threading.Thread(target=self._accept, name="deebee-proxy", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self._closed.is_set()

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._forward, args=(client,), daemon=True).start()

    def _forward(self, client: socket.socket) -> None:
        remote: socket.socket | None = None
        try:
            remote = _proxy_socket(self._config, *self._target)
            with self._guard:
                self._clients.update((client, remote))
            sockets = [client, remote]
            while not self._closed.is_set():
                readable, _, _ = select.select(sockets, [], [], 0.5)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is client else client).sendall(data)
        except OSError:
            return
        finally:
            with self._guard:
                self._clients.discard(client)
                if remote:
                    self._clients.discard(remote)
            for current in (client, remote):
                if current:
                    with contextlib.suppress(OSError):
                        current.close()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with contextlib.suppress(OSError):
            self._listener.close()
        with self._guard:
            clients = list(self._clients)
        for client in clients:
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
                client.close()
        self._thread.join(timeout=2)


class _SSHForwarder:
    def __init__(self, config: dict[str, Any], target_host: str, target_port: int) -> None:
        self._config = config
        self._target = (target_host, target_port)
        self._loop = asyncio.new_event_loop()
        self._connection: asyncssh.SSHClientConnection | None = None
        self._listener: asyncssh.SSHListener | None = None
        self.fingerprint = ""
        self.port = 0
        self._thread = threading.Thread(target=self._run_loop, name="deebee-ssh-tunnel", daemon=True)
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        try:
            future.result(timeout=25)
        except Exception as exc:
            self.close()
            original = exc.__cause__ or exc
            if isinstance(original, DeeBeeError):
                raise original
            raise DeeBeeError(f"SSH 隧道连接失败：{original}") from exc

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _client_keys(self) -> list[asyncssh.SSHKey] | None:
        if self._config.get("ssh_auth_method", "password") != "private_key":
            return None
        private_key = str(self._config.get("ssh_private_key", ""))
        if not private_key.strip():
            raise DeeBeeError("SSH 隧道私钥不能为空")
        try:
            return [
                asyncssh.import_private_key(
                    private_key,
                    passphrase=str(self._config.get("ssh_password", "")) or None,
                )
            ]
        except (asyncssh.KeyImportError, ValueError) as exc:
            raise DeeBeeError("SSH 隧道私钥格式或口令不正确") from exc

    async def _start(self) -> None:
        proxy_sock: socket.socket | None = None
        try:
            if self._config.get("proxy_enabled"):
                proxy_sock = await asyncio.to_thread(
                    _proxy_socket,
                    self._config,
                    str(self._config["ssh_host"]),
                    int(self._config.get("ssh_port", 22)),
                )
                proxy_sock.setblocking(False)
            kwargs: dict[str, Any] = {
                "username": str(self._config.get("ssh_user", "")),
                "password": (
                    str(self._config.get("ssh_password", ""))
                    if self._config.get("ssh_auth_method", "password") == "password"
                    else None
                ),
                "client_keys": self._client_keys(),
                "known_hosts": None,
                "config": None,
                "connect_timeout": 10,
                "login_timeout": 15,
                "keepalive_interval": int(self._config.get("ssh_keepalive_seconds", 30)),
                "keepalive_count_max": 3,
            }
            if proxy_sock:
                kwargs["sock"] = proxy_sock
                self._connection = await asyncssh.connect(**kwargs)
            else:
                self._connection = await asyncssh.connect(
                    str(self._config["ssh_host"]),
                    port=int(self._config.get("ssh_port", 22)),
                    **kwargs,
                )
            key = self._connection.get_server_host_key()
            if key is None:
                raise DeeBeeError("SSH 服务器未提供可验证的主机密钥")
            self.fingerprint = key.get_fingerprint("sha256")
            expected = str(self._config.get("ssh_host_key_fingerprint", "")).strip()
            mode = str(self._config.get("ssh_host_key_mode", "accept_new"))
            if mode != "insecure" and expected and expected != self.fingerprint:
                raise DeeBeeError(
                    f"SSH 主机密钥已变化（期望 {expected}，实际 {self.fingerprint}），已阻止连接"
                )
            if mode == "strict" and not expected:
                raise DeeBeeError("严格校验需要先填写 SSH 主机密钥指纹")
            self._listener = await self._connection.forward_local_port(
                "127.0.0.1", 0, *self._target
            )
            self.port = int(self._listener.get_port())
        except Exception:
            if self._connection:
                self._connection.close()
                with contextlib.suppress(Exception):
                    await self._connection.wait_closed()
            elif proxy_sock:
                proxy_sock.close()
            raise

    @property
    def alive(self) -> bool:
        return bool(
            self.port
            and self._thread.is_alive()
            and self._connection
            and not self._connection.is_closed()
        )

    async def _close_async(self) -> None:
        if self._listener:
            self._listener.close()
            with contextlib.suppress(Exception):
                await self._listener.wait_closed()
        if self._connection:
            self._connection.close()
            with contextlib.suppress(Exception):
                await self._connection.wait_closed()

    def close(self) -> None:
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._close_async(), self._loop)
            with contextlib.suppress(Exception):
                future.result(timeout=5)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        if not self._loop.is_closed():
            self._loop.close()


@dataclass
class _Route:
    signature: tuple[Any, ...]
    forwarder: _ProxyForwarder | _SSHForwarder


class TransportManager:
    """Owns reusable local endpoints for database proxy and SSH routes."""

    def __init__(self) -> None:
        self._routes: dict[str, _Route] = {}
        self._guard = threading.RLock()

    @staticmethod
    def _signature(host: str, port: int, config: dict[str, Any]) -> tuple[Any, ...]:
        return (host, port, *sorted((key, str(value)) for key, value in config.items()))

    def endpoint(
        self, profile_id: str, host: str, port: int, config: dict[str, Any] | None
    ) -> tuple[str, int]:
        value = config or {}
        if not value.get("ssh_tunnel") and not value.get("proxy_enabled"):
            return host, port
        signature = self._signature(host, port, value)
        with self._guard:
            current = self._routes.get(profile_id)
            if current and (current.signature != signature or not current.forwarder.alive):
                current.forwarder.close()
                self._routes.pop(profile_id, None)
                current = None
            if not current:
                try:
                    forwarder: _ProxyForwarder | _SSHForwarder
                    if value.get("ssh_tunnel"):
                        forwarder = _SSHForwarder(value, host, port)
                    else:
                        forwarder = _ProxyForwarder(value, host, port)
                except DeeBeeError:
                    raise
                except Exception as exc:
                    raise DeeBeeError(f"连接通道建立失败：{exc}") from exc
                current = _Route(signature, forwarder)
                self._routes[profile_id] = current
            return "127.0.0.1", current.forwarder.port

    def metadata(self, profile_id: str) -> dict[str, Any]:
        with self._guard:
            route = self._routes.get(profile_id)
            if not route or not isinstance(route.forwarder, _SSHForwarder):
                return {}
            expected = str(route.forwarder._config.get("ssh_host_key_fingerprint", "")).strip()
            return {
                "host_key_fingerprint": route.forwarder.fingerprint,
                "host_key_new": not expected,
            }

    def close(self, profile_id: str) -> None:
        with self._guard:
            route = self._routes.pop(profile_id, None)
        if route:
            route.forwarder.close()

    def close_all(self) -> None:
        with self._guard:
            profile_ids = list(self._routes)
        for profile_id in profile_ids:
            self.close(profile_id)


transport_manager = TransportManager()
