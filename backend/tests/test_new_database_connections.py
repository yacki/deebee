from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deebee import connection_drivers
from deebee.connection_drivers import (
    ClickHouseProfile,
    ClickHouseWorkbench,
    MongoDBProfile,
    MongoDBWorkbench,
    RedisProfile,
    RedisWorkbench,
)
from deebee.main import ConnectionTestBody
from deebee.mysql import DeeBeeError
from deebee.workbench import DatabaseWorkbenches


def test_new_connection_profiles_are_validated_encrypted_and_restored(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    profiles = [
        manager.create_connection(
            {
                "driver": "redis",
                "name": "Cache",
                "host": "redis.internal",
                "port": 6379,
                "user": "",
                "password": "redis-secret",
                "default_database": "2",
                "options": {"tls": True, "verify_tls": False},
            }
        ),
        manager.create_connection(
            {
                "driver": "clickhouse",
                "name": "Analytics",
                "host": "clickhouse.internal",
                "port": 8443,
                "user": "analyst",
                "password": "clickhouse-secret",
                "default_database": "warehouse",
                "options": {"tls": True, "verify_tls": True},
            }
        ),
        manager.create_connection(
            {
                "driver": "mongodb",
                "name": "Documents",
                "host": "mongodb.internal",
                "port": 27017,
                "user": "app",
                "password": "mongodb-secret",
                "default_database": "catalog",
                "options": {
                    "tls": False,
                    "verify_tls": True,
                    "auth_database": "admin",
                    "direct_connection": True,
                },
            }
        ),
    ]

    assert [profile["driver"] for profile in profiles] == [
        "redis",
        "clickhouse",
        "mongodb",
    ]
    assert all("password" not in profile for profile in profiles)
    stored = path.read_text(encoding="utf-8")
    assert "redis-secret" not in stored
    assert "clickhouse-secret" not in stored
    assert "mongodb-secret" not in stored

    restarted = DatabaseWorkbenches(store_path=path, defaults=[])
    restored = restarted.list_profiles()
    assert restored[0]["default_database"] == "2"
    assert restored[0]["options"] == {"tls": True, "verify_tls": False}
    assert restored[2]["options"]["auth_database"] == "admin"
    assert restarted._engine(profiles[2]["id"]).require_profile(
        profiles[2]["id"]
    ).password == "mongodb-secret"

    with pytest.raises(DeeBeeError, match="必须填写用户名"):
        manager.create_connection(
            {
                "driver": "mongodb",
                "name": "Invalid",
                "host": "localhost",
                "port": 27017,
                "user": "",
                "password": "secret",
                "default_database": "",
                "options": {},
            }
        )
    with pytest.raises(DeeBeeError, match="非负整数"):
        manager.create_connection(
            {
                "driver": "redis",
                "name": "Invalid",
                "host": "localhost",
                "port": 6379,
                "user": "",
                "password": "",
                "default_database": "cache",
                "options": {},
            }
        )


def test_connection_api_model_accepts_password_only_redis() -> None:
    body = ConnectionTestBody.model_validate(
        {
            "driver": "redis",
            "name": "Redis",
            "host": "localhost",
            "port": 6379,
            "user": "",
            "password": "secret",
            "default_database": "0",
            "options": {"tls": False, "verify_tls": True},
        }
    )
    assert body.driver == "redis"
    assert body.user == ""


def test_redis_connection_uses_acl_database_and_tls_options(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeRedis:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def ping(self) -> bool:
            return True

        def info(self, section: str) -> dict[str, str]:
            assert section == "server"
            return {"redis_version": "8.0.0", "redis_mode": "standalone"}

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(connection_drivers.redis, "Redis", FakeRedis)
    result = RedisWorkbench().test_connection(
        RedisProfile(
            id="redis-test",
            name="Redis",
            host="cache.internal",
            port=6380,
            user="reader",
            password="secret",
            default_database="3",
            options={"tls": True, "verify_tls": False},
        )
    )
    assert result["ok"] is True
    assert result["database"] == "db3"
    assert result["version"] == "8.0.0"
    assert captured["username"] == "reader"
    assert captured["db"] == 3
    assert captured["ssl"] is True
    assert captured["closed"] is True


def test_clickhouse_connection_uses_http_settings(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def query(self, sql: str) -> Any:
            assert sql == "SELECT version(), currentUser(), currentDatabase()"
            return SimpleNamespace(result_rows=[("26.8.1", "analyst", "warehouse")])

        def close(self) -> None:
            captured["closed"] = True

    def fake_get_client(**kwargs: Any) -> FakeClient:
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(connection_drivers.clickhouse_connect, "get_client", fake_get_client)
    result = ClickHouseWorkbench().test_connection(
        ClickHouseProfile(
            id="clickhouse-test",
            name="ClickHouse",
            host="analytics.internal",
            port=8443,
            user="analyst",
            password="secret",
            default_database="warehouse",
            options={"tls": True, "verify_tls": False},
        )
    )
    assert result["database"] == "warehouse"
    assert result["version"] == "26.8.1"
    assert captured["secure"] is True
    assert captured["verify"] is False
    assert captured["closed"] is True


def test_mongodb_connection_uses_auth_source_and_direct_mode(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAdmin:
        def command(self, command: str) -> dict[str, Any]:
            if command == "ping":
                return {"ok": 1}
            assert command == "buildInfo"
            return {"version": "8.0.0"}

    class FakeMongoClient:
        admin = FakeAdmin()
        topology_description = SimpleNamespace(topology_type_name="Single")

        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(connection_drivers, "MongoClient", FakeMongoClient)
    result = MongoDBWorkbench().test_connection(
        MongoDBProfile(
            id="mongodb-test",
            name="MongoDB",
            host="documents.internal",
            port=27017,
            user="app",
            password="secret",
            default_database="catalog",
            options={
                "tls": False,
                "verify_tls": True,
                "auth_database": "accounts",
                "direct_connection": True,
            },
        )
    )
    assert result["database"] == "catalog"
    assert result["version"] == "8.0.0"
    assert result["topology"] == "Single"
    assert captured["authSource"] == "accounts"
    assert captured["directConnection"] is True
    assert captured["closed"] is True


@pytest.mark.skipif(
    not all(
        os.getenv(name)
        for name in (
            "DEEBEE_TEST_REDIS_PORT",
            "DEEBEE_TEST_CLICKHOUSE_PORT",
            "DEEBEE_TEST_MONGODB_PORT",
        )
    ),
    reason="local database containers are not configured",
)
def test_real_new_database_connections() -> None:
    tests = [
        (
            RedisWorkbench(),
            RedisProfile(
                id="redis-real",
                name="Redis",
                host="127.0.0.1",
                port=int(os.environ["DEEBEE_TEST_REDIS_PORT"]),
                user="",
                password="deebee-secret",
                default_database="2",
                options={"tls": False, "verify_tls": True},
            ),
        ),
        (
            ClickHouseWorkbench(),
            ClickHouseProfile(
                id="clickhouse-real",
                name="ClickHouse",
                host="127.0.0.1",
                port=int(os.environ["DEEBEE_TEST_CLICKHOUSE_PORT"]),
                user="deebee",
                password="deebee-secret",
                default_database="default",
                options={"tls": False, "verify_tls": True},
            ),
        ),
        (
            MongoDBWorkbench(),
            MongoDBProfile(
                id="mongodb-real",
                name="MongoDB",
                host="127.0.0.1",
                port=int(os.environ["DEEBEE_TEST_MONGODB_PORT"]),
                user="deebee",
                password="deebee-secret",
                default_database="catalog",
                options={
                    "tls": False,
                    "verify_tls": True,
                    "auth_database": "admin",
                    "direct_connection": True,
                },
            ),
        ),
    ]
    for engine, profile in tests:
        result = engine.test_connection(profile)
        assert result["ok"] is True
        assert result["latency_ms"] >= 0
        assert result.get("version")
