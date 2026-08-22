from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from deebee.config import persistent_secret, settings
from deebee.main import app, workbench
from deebee.workbench import DatabaseWorkbenches


def test_generated_secret_is_persisted_with_private_permissions(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.delenv("DEEBEE_TOKEN_SECRET", raising=False)
    secret_file = tmp_path / "secret.key"
    monkeypatch.setenv("DEEBEE_SECRET_FILE", str(secret_file))

    first = persistent_secret(tmp_path / "connections.json")
    second = persistent_secret(tmp_path / "connections.json")

    assert first == second
    assert len(first) >= 48
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600


def test_connection_crud_is_encrypted_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    assert manager.list_profiles() == []

    mysql = manager.create_connection(
        {
            "driver": "mysql",
            "name": "Local MySQL",
            "host": "mysql.internal",
            "port": 3306,
            "user": "app",
            "password": "mysql-secret",
            "default_database": "app_data",
            "default_schema": "",
        }
    )
    postgres = manager.create_connection(
        {
            "driver": "postgresql",
            "name": "Local PostgreSQL",
            "host": "postgres.internal",
            "port": 5432,
            "user": "postgres",
            "password": "postgres-secret",
            "default_database": "app_data",
            "default_schema": "tenant_a",
        }
    )
    assert "password" not in mysql and "password" not in postgres

    manager.update_connection(
        mysql["id"],
        {
            "driver": "mysql",
            "name": "Renamed MySQL",
            "host": "mysql.internal",
            "port": 3307,
            "user": "app",
            "password": None,
            "default_database": "app_v2",
            "default_schema": "",
        },
    )

    stored_text = path.read_text(encoding="utf-8")
    assert "mysql-secret" not in stored_text
    assert "postgres-secret" not in stored_text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(stored_text)["version"] == 1

    restarted = DatabaseWorkbenches(store_path=path, defaults=[])
    profiles = restarted.list_profiles()
    assert [profile["name"] for profile in profiles] == [
        "Renamed MySQL",
        "Local PostgreSQL",
    ]
    assert profiles[0]["default_database"] == "app_v2"
    assert profiles[1]["default_schema"] == "tenant_a"
    assert restarted._engine(mysql["id"]).require_profile(mysql["id"]).password == "mysql-secret"

    restarted.delete_connection(mysql["id"])
    assert [profile["id"] for profile in restarted.list_profiles()] == [postgres["id"]]


def test_connection_management_api_contract(monkeypatch: Any) -> None:
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": settings.admin_user, "password": settings.admin_password},
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    payload = {
        "driver": "postgresql",
        "name": "API PostgreSQL",
        "host": "postgres.internal",
        "port": 5432,
        "user": "postgres",
        "password": "secret",
        "default_database": "app_data",
        "default_schema": "tenant_a",
    }
    public = {"id": "postgresql-api", **payload}
    public.pop("password")
    calls: dict[str, Any] = {}

    def fake_test(value: dict[str, Any], profile_id: str = "") -> dict[str, Any]:
        calls["test"] = (value, profile_id)
        return {"ok": True, "latency_ms": 1.2}

    def fake_create(value: dict[str, Any]) -> dict[str, Any]:
        calls["create"] = value
        return public

    def fake_update(profile_id: str, value: dict[str, Any]) -> dict[str, Any]:
        calls["update"] = (profile_id, value)
        return public

    def fake_delete(profile_id: str) -> None:
        calls["delete"] = profile_id

    monkeypatch.setattr(workbench, "test_connection", fake_test)
    monkeypatch.setattr(workbench, "create_connection", fake_create)
    monkeypatch.setattr(workbench, "update_connection", fake_update)
    monkeypatch.setattr(workbench, "delete_connection", fake_delete)

    tested = client.post(
        "/api/connections/test",
        headers=headers,
        json={**payload, "password": None, "profile_id": "postgresql-api"},
    )
    assert tested.status_code == 200
    assert calls["test"][1] == "postgresql-api"
    created = client.post("/api/connections", headers=headers, json=payload)
    assert created.status_code == 201
    assert created.json()["default_schema"] == "tenant_a"
    updated = client.patch(
        "/api/connections/postgresql-api",
        headers=headers,
        json={**payload, "name": "Renamed", "password": None},
    )
    assert updated.status_code == 200
    assert calls["update"][1]["password"] is None
    deleted = client.delete("/api/connections/postgresql-api", headers=headers)
    assert deleted.status_code == 204
    assert calls["delete"] == "postgresql-api"
