from __future__ import annotations

import asyncio
import concurrent.futures
import json
import socket
import threading
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from deebee import transports
from deebee.transports import TransportManager, _ProxyForwarder, _SSHForwarder, _proxy_socket
from deebee.workbench import DatabaseWorkbenches


def _socket_pair(monkeypatch: Any) -> tuple[socket.socket, socket.socket]:
    client, server = socket.socketpair()
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: client)
    return client, server


def test_http_connect_proxy_authenticates_and_keeps_stream_open(monkeypatch: Any) -> None:
    _, server = _socket_pair(monkeypatch)
    captured: dict[str, bytes] = {}

    def proxy() -> None:
        request = bytearray()
        while b"\r\n\r\n" not in request:
            request.extend(server.recv(4096))
        captured["request"] = bytes(request)
        server.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        captured["payload"] = server.recv(4)
        server.sendall(b"pong")

    thread = threading.Thread(target=proxy)
    thread.start()
    connected = _proxy_socket(
        {
            "proxy_host": "proxy.internal",
            "proxy_port": 8080,
            "proxy_type": "http",
            "proxy_user": "alice",
            "proxy_password": "secret",
        },
        "db.internal",
        5432,
    )
    try:
        connected.sendall(b"ping")
        assert connected.recv(4) == b"pong"
    finally:
        connected.close()
        server.close()
        thread.join(timeout=2)
    assert captured["request"].startswith(b"CONNECT db.internal:5432 HTTP/1.1")
    assert b"Proxy-Authorization: Basic YWxpY2U6c2VjcmV0" in captured["request"]
    assert captured["payload"] == b"ping"


def test_socks5_proxy_uses_remote_dns_and_password_auth(monkeypatch: Any) -> None:
    _, server = _socket_pair(monkeypatch)
    captured: dict[str, bytes] = {}

    def proxy() -> None:
        assert server.recv(4) == b"\x05\x02\x00\x02"
        server.sendall(b"\x05\x02")
        auth = server.recv(1024)
        captured["auth"] = auth
        server.sendall(b"\x01\x00")
        request = server.recv(1024)
        captured["request"] = request
        server.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x12\x34")
        captured["payload"] = server.recv(4)
        server.sendall(b"pong")

    thread = threading.Thread(target=proxy)
    thread.start()
    connected = _proxy_socket(
        {
            "proxy_host": "proxy.internal",
            "proxy_port": 1080,
            "proxy_type": "socks5",
            "proxy_user": "bob",
            "proxy_password": "pw",
            "proxy_dns": True,
        },
        "mongo.internal",
        27017,
    )
    try:
        connected.sendall(b"ping")
        assert connected.recv(4) == b"pong"
    finally:
        connected.close()
        server.close()
        thread.join(timeout=2)
    assert captured["auth"] == b"\x01\x03bob\x02pw"
    assert captured["request"] == b"\x05\x01\x00\x03\x0emongo.internal\x69\x89"
    assert captured["payload"] == b"ping"


def test_proxy_forwarder_relays_bytes_through_local_endpoint(monkeypatch: Any) -> None:
    target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    target.bind(("127.0.0.1", 0))
    target.listen(1)
    target_port = int(target.getsockname()[1])

    def echo() -> None:
        connection, _ = target.accept()
        with connection:
            connection.sendall(connection.recv(1024))

    thread = threading.Thread(target=echo)
    thread.start()
    monkeypatch.setattr(
        transports,
        "_proxy_socket",
        lambda _config, host, port: socket.create_connection((host, port)),
    )
    forwarder = _ProxyForwarder({}, "127.0.0.1", target_port)
    try:
        with socket.create_connection(("127.0.0.1", forwarder.port)) as client:
            client.sendall(b"deebee")
            assert client.recv(6) == b"deebee"
    finally:
        forwarder.close()
        target.close()
        thread.join(timeout=2)


def test_ssh_route_takes_precedence_when_proxy_is_also_enabled(monkeypatch: Any) -> None:
    created: dict[str, Any] = {}

    class FakeSSHForwarder:
        def __init__(self, config: dict[str, Any], host: str, port: int) -> None:
            created.update(config=config, host=host, port=port)
            self.port = 45123
            self.alive = True
            self.fingerprint = "SHA256:test"
            self._config = config

        def close(self) -> None:
            self.alive = False

    monkeypatch.setattr(transports, "_SSHForwarder", FakeSSHForwarder)
    manager = TransportManager()
    endpoint = manager.endpoint(
        "db-test",
        "database.internal",
        3306,
        {
            "ssh_tunnel": True,
            "ssh_host": "bastion.internal",
            "proxy_enabled": True,
            "proxy_host": "proxy.internal",
        },
    )
    assert endpoint == ("127.0.0.1", 45123)
    assert created["host"] == "database.internal"
    assert created["config"]["proxy_host"] == "proxy.internal"
    assert manager.metadata("db-test") == {
        "host_key_fingerprint": "SHA256:test",
        "host_key_new": True,
    }
    manager.close_all()


@pytest.mark.parametrize("through_proxy", [False, True])
def test_ssh_forwarder_opens_a_real_local_direct_tcpip_channel(
    monkeypatch: Any, through_proxy: bool
) -> None:
    target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    target.bind(("127.0.0.1", 0))
    target.listen(1)
    target_port = int(target.getsockname()[1])

    def echo() -> None:
        connection, _ = target.accept()
        with connection:
            connection.sendall(connection.recv(1024))

    echo_thread = threading.Thread(target=echo)
    echo_thread.start()

    class TestSSHServer(asyncssh.SSHServer):
        def begin_auth(self, _username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, _username: str, password: str) -> bool:
            return password == "ssh-secret"

        def connection_requested(
            self, _dest_host: str, _dest_port: int, _orig_host: str, _orig_port: int
        ) -> bool:
            return True

    loop = asyncio.new_event_loop()
    ready: concurrent.futures.Future[tuple[Any, int]] = concurrent.futures.Future()

    async def start_server() -> None:
        listener = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_factory=TestSSHServer,
            server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        )
        ready.set_result((listener, int(listener.get_port())))

    def run_server() -> None:
        asyncio.set_event_loop(loop)
        loop.create_task(start_server())
        loop.run_forever()

    server_thread = threading.Thread(target=run_server)
    server_thread.start()
    listener, ssh_port = ready.result(timeout=5)
    if through_proxy:
        monkeypatch.setattr(
            transports,
            "_proxy_socket",
            lambda _config, host, port: socket.create_connection((host, port)),
        )
    forwarder = _SSHForwarder(
        {
            "ssh_host": "127.0.0.1",
            "ssh_port": ssh_port,
            "ssh_user": "operator",
            "ssh_auth_method": "password",
            "ssh_password": "ssh-secret",
            "ssh_host_key_mode": "accept_new",
            "ssh_keepalive_seconds": 1,
            "proxy_enabled": through_proxy,
            "proxy_host": "proxy.internal" if through_proxy else "",
        },
        "127.0.0.1",
        target_port,
    )
    try:
        with socket.create_connection(("127.0.0.1", forwarder.port)) as client:
            client.sendall(b"tunnel")
            assert client.recv(6) == b"tunnel"
        assert forwarder.fingerprint.startswith("SHA256:")
    finally:
        forwarder.close()
        target.close()
        echo_thread.join(timeout=2)

        async def stop_server() -> None:
            listener.close()
            await listener.wait_closed()

        asyncio.run_coroutine_threadsafe(stop_server(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        server_thread.join(timeout=2)
        loop.close()


def test_database_tunnel_secrets_are_encrypted_and_preserved_on_edit(tmp_path: Path) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    created = manager.create_connection(
        {
            "driver": "mysql",
            "name": "via bastion",
            "host": "database.internal",
            "port": 3306,
            "user": "app",
            "password": "db-secret",
            "ssh_password": "ssh-secret",
            "ssh_private_key": "private-secret",
            "proxy_password": "proxy-secret",
            "options": {
                "ssh_tunnel": True,
                "ssh_host": "bastion.internal",
                "ssh_port": 22,
                "ssh_user": "operator",
                "ssh_auth_method": "password",
                "ssh_host_key_mode": "accept_new",
                "ssh_host_key_fingerprint": "SHA256:known",
                "ssh_keepalive_seconds": 30,
                "proxy_enabled": True,
                "proxy_type": "socks5",
                "proxy_host": "proxy.internal",
                "proxy_port": 1080,
                "proxy_user": "proxy-user",
                "proxy_dns": True,
            },
        }
    )
    stored = path.read_text(encoding="utf-8")
    for secret in ("db-secret", "ssh-secret", "private-secret", "proxy-secret"):
        assert secret not in stored
    assert all(field in json.loads(stored)["connections"][0] for field in (
        "ssh_password", "ssh_private_key", "proxy_password"
    ))

    manager.update_connection(
        created["id"],
        {
            "driver": "mysql",
            "name": "renamed",
            "host": "database.internal",
            "port": 3306,
            "user": "app",
            "password": None,
            "ssh_password": None,
            "ssh_private_key": None,
            "proxy_password": None,
            "options": created["options"],
        },
    )
    restarted = DatabaseWorkbenches(store_path=path, defaults=[])
    record = restarted._records[created["id"]]
    assert record["password"] == "db-secret"
    assert record["ssh_password"] == "ssh-secret"
    assert record["ssh_private_key"] == "private-secret"
    assert record["proxy_password"] == "proxy-secret"
