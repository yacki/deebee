from __future__ import annotations

import asyncio
import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deebee.access.api import install_access
from deebee.access.connection_pool import ConnectionPool
from deebee.access.legacy import LegacyBridge
from deebee.access.models import AccessError, Resource
from deebee.access.service import AccessService, public_entity
from deebee.access.store import AccessStore
from deebee.config import settings
from deebee.security import issue_token
from deebee.workbench import DatabaseWorkbenches


def connection(driver="ssh", **changes):
    return {"driver": driver, "name": "Server", "host": "192.0.2.25", "port": 22 if driver == "ssh" else 3306,
            "user": "operator", "password": "pool-secret-for-test", "default_database": "app" if driver in {"mysql", "postgresql"} else "",
            "options": {"host_key_fingerprint": "SHA256:test", "auth_method": "password"} if driver == "ssh" else {}, **changes}


@pytest.fixture
def pool(tmp_path):
    manager = DatabaseWorkbenches(store_path=tmp_path / "connections.json", defaults=[])
    store = AccessStore(tmp_path / "access")
    service = AccessService(store)
    service.legacy = LegacyBridge(store, manager)
    value = ConnectionPool(service.legacy)
    yield SimpleNamespace(manager=manager, store=store, service=service, sync=value.sync, pool=value)
    store.close()


def test_backfill_all_connection_types_without_publishing_or_copying_secrets(pool):
    for driver in ("ssh", "mysql", "postgresql", "mssql", "redis", "clickhouse", "mongodb", "rdp"):
        pool.manager.create_connection(connection(driver, name=driver))
    assert pool.sync("admin-one") == 8
    resources = pool.store.list("resources")
    assert len(resources) == 8
    assert all(not r["enabled"] for r in resources)
    assert len(pool.store.list("accounts")) == 3
    assert pool.store.list("grants") == []
    assert pool.store.list("keys") == []
    for resource in resources:
        assert resource["connection_sync"]["created_by"] == "admin-one"
        if resource["type"] not in {"ssh", "mysql", "postgresql"}:
            assert resource["connection_sync"]["state"] == "unsupported"
            with pytest.raises(ValueError):
                Resource.model_validate({k: v for k, v in resource.items() if k in Resource.model_fields} | {"enabled": True})
    dump = "\n".join(pool.store.db.iterdump())
    assert "pool-secret-for-test" not in dump
    assert not pool.store.db.execute("SELECT 1 FROM credentials").fetchone()
    assert pool.sync() == 0
    assert pool.store.list("resources") == resources
    assert "legacy_fingerprint" not in public_entity(resources[0])


def test_deleted_resource_is_not_resurrected_by_sync_or_legacy_refresh(pool):
    profile = pool.manager.create_connection(connection())
    pool.sync()
    resource = pool.store.list("resources")[0]
    pool.service.delete_resource(resource["id"], {"expected_version": resource["version"], "confirm_name": resource["name"]}, "admin")
    assert pool.sync() == 0
    pool.manager.update_connection(profile["id"], {"name": "Renamed source"})
    assert pool.sync() == 0
    assert len(pool.manager.list_profiles()) == 1
    assert pool.store.get("resources", resource["id"])["deleted_at"]
    bridge = pool.service.legacy
    with pytest.raises(AccessError, match="已.*删除"):
        bridge.refresh(profile["id"], bridge.preview()["items"][0]["fingerprint"], "admin")


def test_resource_delete_api_auth_version_and_hidden_records(tmp_path):
    app = FastAPI()
    install_access(app, tmp_path)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
        resource = client.post("/api/admin/v1/resources", headers=headers, json={"name": "Disposable", "host": "example.invalid", "port": 22, "type": "ssh"}).json()
        path = "/api/admin/v1/resources/" + resource["id"]
        body = {"expected_version": resource["version"], "confirm_name": resource["name"]}
        assert client.request("DELETE", path, json=body).status_code in (401, 403)
        assert client.request("DELETE", path, headers=headers, json=body | {"expected_version": 0}).status_code == 409
        assert client.request("DELETE", path, headers=headers, json=body).status_code == 200
        assert client.get("/api/admin/v1/resources", headers=headers).json()["items"] == []
        assert client.get("/api/admin/v1/access-config/export", headers=headers).json()["resources"] == []


def test_incomplete_and_tunneled_connections_are_visible_but_not_executable(pool):
    for values in (connection("mysql", default_database=""), connection("postgresql", options={"proxy_enabled": True, "proxy_host": "proxy.internal"}), connection(options={})):
        pool.manager.create_connection(values)
    pool.sync()
    for resource in pool.store.list("resources"):
        assert resource["connection_sync"]["state"] == "needs_configuration"
        assert resource["connection_sync"]["reason"]
        with pytest.raises((AccessError, ValueError)):
            asyncio.run(pool.service.save("resources", {"expected_version": resource["version"], "enabled": True}, "admin", resource["id"]))


def test_rename_preserves_verification_but_rotation_invalidates_it(pool):
    profile = pool.manager.create_connection(connection())
    pool.sync()
    resource = pool.store.list("resources")[0]
    resource = pool.store.put("resources", resource["id"], resource | {"enabled": True}, resource["version"])
    account = pool.store.list("accounts")[0]
    account = pool.store.put("accounts", account["id"], account | {"enabled": True, "permission_confirmed": True, "permission_note": "fixture",
        "test_result": {"connected": True, "normal_safe": True, "resource_version": resource["version"]}}, account["version"])
    pool.manager.update_connection(profile["id"], {"name": "Renamed"})
    pool.sync("admin-two")
    renamed = pool.store.get("resources", resource["id"])
    verified = pool.store.get("accounts", account["id"])
    assert renamed["name"] == "Renamed" and renamed["enabled"]
    assert verified["enabled"] and verified["test_result"]["resource_version"] == renamed["version"]
    assert pool.service.account_secret(verified)["password"] == "pool-secret-for-test"

    pool.manager.update_connection(profile["id"], {"password": "rotated-pool-secret"})
    # Even a failed/delayed sync must not leave the old reference callable.
    with pytest.raises(AccessError, match="原连接已变化"):
        pool.service.account_secret(verified)
    pool.sync("admin-two")
    rotated = pool.store.get("accounts", account["id"])
    assert not rotated["enabled"] and not rotated["permission_confirmed"] and rotated["test_result"] == {}
    assert not pool.store.get("resources", resource["id"])["enabled"]
    assert pool.service.account_secret(rotated)["password"] == "rotated-pool-secret"
    assert pool.sync() == 0


def test_delete_keeps_audit_and_cannot_be_reenabled(pool):
    profile = pool.manager.create_connection(connection())
    pool.sync()
    resource = pool.store.list("resources")[0]
    account = pool.store.list("accounts")[0]
    pool.manager.delete_connection(profile["id"])
    with pytest.raises(AccessError, match="原连接已删除"):
        pool.service.account_secret(account)
    pool.sync()
    removed = pool.store.get("resources", resource["id"])
    assert removed["connection_sync"]["state"] == "deleted" and not removed["enabled"]
    assert pool.store.list("accounts")[0]["id"] == account["id"]
    with pytest.raises(AccessError, match="原连接已删除"):
        asyncio.run(pool.service.save("resources", {"expected_version": removed["version"], "enabled": True}, "admin", removed["id"]))
    assert any(a["action"] == "connection_pool.removed" for a in pool.store.audits())
    assert pool.sync() == 0


def test_startup_reconciles_saved_connections_after_missed_sync(tmp_path):
    manager = DatabaseWorkbenches(store_path=tmp_path / "connections.json", defaults=[])
    profile = manager.create_connection(connection())
    app = FastAPI()
    install_access(app, tmp_path / "access", legacy=manager)
    headers = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
    with TestClient(app) as client:
        resources = client.get("/api/admin/v1/resources", headers=headers).json()["items"]
        assert len(resources) == 1 and resources[0]["legacy_profile_id"] == profile["id"]
        manager.update_connection(profile["id"], {"name": "Missed event"})
        assert client.get("/api/admin/v1/resources", headers=headers).json()["items"][0]["name"] == "Missed event"
    app.state.access.store.close()


def test_frontend_crud_sync_and_recovery_without_duplicate_creation(pool, monkeypatch):
    import deebee.main as main
    monkeypatch.setattr(main, "workbench", pool.manager)
    monkeypatch.setattr(main.api_app.state, "connection_pool", pool.pool)
    monkeypatch.setattr(main.api_app.state, "access_executions", SimpleNamespace(changed=SimpleNamespace(set=lambda: None)))
    # No lifespan: it belongs to the production module's original store, while
    # these API requests are intentionally isolated on the fixture's stores.
    with closing(TestClient(main.api_app)) as client:
        headers = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
        created = client.post("/api/connections", headers=headers, json=connection())
        assert created.status_code == 201
        assert created.json()["resource_sync"]["state"] == "synced"
        profile_id = created.json()["id"]
        assert len(pool.store.list("resources")) == 1
        update = client.patch(f"/api/connections/{profile_id}", headers=headers, json=connection(name="API name", password=None))
        assert update.status_code == 200
        assert pool.store.list("resources")[0]["name"] == "API name"
        with monkeypatch.context() as temporary:
            def fail(*_args):
                raise RuntimeError("database unavailable")
            temporary.setattr(pool.pool, "sync", fail)
            pending = client.post("/api/connections", headers=headers, json=connection(name="Retry safely"))
            assert pending.status_code == 201 and pending.json()["resource_sync"]["state"] == "pending"
        assert len(pool.manager.list_profiles()) == 2
        assert pool.sync() == 1
        assert len(pool.store.list("resources")) == 2
        assert client.delete(f"/api/connections/{profile_id}", headers=headers).status_code == 204
        assert sum(r["connection_sync"]["state"] == "deleted" for r in pool.store.list("resources")) == 1


def test_export_import_preserves_sync_provenance_and_manual_resources(pool):
    from deebee.access.configuration import import_configuration
    manual = asyncio.run(pool.service.save("resources", {"name": "Manual", "type": "ssh", "host": "manual.internal", "port": 22}, "admin"))
    pool.manager.create_connection(connection())
    pool.sync()
    config = {"version": 1, "resources": [public_entity(r) for r in pool.store.list("resources")]}
    assert asyncio.run(import_configuration(pool.service, config, "admin", preview=True))["updated"] == 0
    assert pool.store.get("resources", manual["id"])["version"] == manual["version"]
    assert pool.sync() == 0
    assert "pool-secret-for-test" not in json.dumps(config)


def test_existing_manual_reference_is_adopted_without_duplicate_or_lost_verification(pool):
    from deebee.access.store import encode
    profile = pool.manager.create_connection(connection())
    bridge = pool.service.legacy
    imported = bridge.refresh(profile["id"], bridge.preview()["items"][0]["fingerprint"], "admin")
    full_fingerprint = pool.store.digest(encode(bridge.record(profile["id"])))
    resource = pool.store.get("resources", imported["resource"]["id"])
    resource = pool.store.put("resources", resource["id"], resource | {"legacy_fingerprint": full_fingerprint, "enabled": True}, resource["version"])
    account = pool.store.get("accounts", imported["account"]["id"])
    pool.store.put("accounts", account["id"], account | {"legacy_fingerprint": full_fingerprint, "enabled": True,
        "test_result": {"connected": True, "resource_version": resource["version"]}}, account["version"])
    pool.sync()
    assert len(pool.store.list("resources")) == len(pool.store.list("accounts")) == 1
    assert pool.store.list("resources")[0]["enabled"]
    assert pool.store.list("accounts")[0]["enabled"]
    pool.manager.update_connection(profile["id"], {"name": "Rename after upgrade"})
    pool.sync()
    assert pool.service.account_secret(pool.store.list("accounts")[0])["password"] == "pool-secret-for-test"


def test_kubernetes_token_pool_preserves_source_and_requires_verification(pool):
    profile = pool.manager.create_connection(connection('k8s', name='Cluster', host='https://cluster.example:6443', port=6443,
        options={'k8s_auth_method': 'token', 'namespace': 'payments'}))
    assert pool.sync() == 1
    resource = pool.store.list('resources')[0]
    assert resource['host'] == 'cluster.example' and resource['port'] == 6443 and resource['tls']
    assert resource['namespaces'] == ['payments'] and resource['connection_sync']['state'] == 'synced'
    assert not resource['enabled'] and not pool.store.list('accounts')[0]['enabled']
    assert not pool.store.list('grants') and not pool.store.list('keys')
    assert pool.sync() == 0
    assert pool.service.legacy.record(profile['id'])['host'] == 'https://cluster.example:6443'
