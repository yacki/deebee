from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deebee.access.api import install_access
from deebee.access.auth import Authenticator
from deebee.access.models import AccessError, AuthContext, SCOPES
from deebee.access.service import AccessService, public_entity
from deebee.access.sql import prepare_sql
from deebee.access.store import AccessStore
from deebee.config import settings
from deebee.security import issue_token


@pytest.fixture
def service(tmp_path):
    store = AccessStore(tmp_path)
    yield AccessService(store)
    store.close()


def save(service, kind, body, entity_id=None):
    return asyncio.run(service.save(kind, body, "test-admin", entity_id))


def principal(service, name="Test"):
    return save(service, "principals", {"name": name, "kind": "service"})


def published(service, kind="mysql"):
    resource = save(service, "resources", {"name": "Test resource", "type": kind, "host": "localhost", "port": 3306 if kind == "mysql" else 22,
        "database": "access_lab" if kind == "mysql" else "", "host_key": "SHA256:test", "enabled": True})
    ids = []
    for tier in ("normal", "privileged"):
        account = save(service, "accounts", {"name": tier, "resource_id": resource["id"], "username": tier, "password": "secret", "tier": tier})
        # Unit fixture only: integration tests use an actual account verification.
        raw = service.store.get("accounts", account["id"])
        raw = service.store.put("accounts", raw["id"], raw | {"test_result": {"connected": True, "normal_safe": True, "resource_version": resource["version"]}}, raw["version"])
        account = save(service, "accounts", {"expected_version": raw["version"], "enabled": True, "permission_confirmed": True, "permission_note": "unit fixture"}, raw["id"])
        ids.append(account["id"])
    return resource, ids


def identity(service, principal_id, scopes=None):
    key = service.create_key({"name": "test", "principal_id": principal_id, "scopes": list(scopes or SCOPES)}, "admin")["key"]
    return asyncio.run(service.auth.authenticate({"x-deebee-api-key": key})), key


def test_resource_classification_preserves_verified_accounts(service):
    resource, ids = published(service)
    updated = save(service, "resources", {"expected_version": resource["version"], "environment": " prod ",
        "project_groups": ["平台组", "平台组", "数据组"], "tags": ["ARM64", "", " ARM64 "]}, resource["id"])
    assert updated["environment"] == "prod"
    assert updated["project_groups"] == ["平台组", "数据组"]
    assert updated["tags"] == ["ARM64"]
    for account_id in ids:
        account = service.store.get("accounts", account_id)
        assert account["enabled"] and account["test_result"]["resource_version"] == updated["version"]
    changed = save(service, "resources", {"expected_version": updated["version"], "host": "other.internal"}, resource["id"])
    assert service.store.get("accounts", ids[0])["test_result"]["resource_version"] != changed["version"]


def test_delete_resource_cascades_and_revokes_without_erasing_history(service):
    p = principal(service)
    resource, ids = published(service)
    grant = save(service, "grants", {"principal_id": p["id"], "resource_id": resource["id"], "normal_account_id": ids[0]})
    ctx, _ = identity(service, p["id"])
    assert service.resources(ctx)
    for body in ({"expected_version": 0, "confirm_name": resource["name"]}, {"expected_version": resource["version"], "confirm_name": "wrong"}):
        with pytest.raises(AccessError):
            service.delete_resource(resource["id"], body, "admin")
    result = service.delete_resource(resource["id"], {"expected_version": resource["version"], "confirm_name": resource["name"]}, "admin")
    assert result == {"deleted": True, "accounts": 2, "grants": 1}
    assert service.resources(ctx) == []
    assert service.store.get("grants", grant["id"])["deleted_at"]
    assert any(a["action"] == "resources.delete" for a in service.store.audits())
    for kind, body, id_ in [("resources", {"enabled": True}, resource["id"]), ("accounts", {"enabled": True}, ids[0])]:
        with pytest.raises(AccessError, match="已删除"):
            save(service, kind, body, id_)
    with pytest.raises(AccessError, match="已删除"):
        save(service, "accounts", {"name": "new", "username": "test", "resource_id": resource["id"]})


def test_key_is_hashed_and_password_is_encrypted(service):
    p = principal(service)
    _, key = identity(service, p["id"])
    account_resource, accounts = published(service)
    raw = service.store.get("accounts", accounts[0])
    assert service.store.secret(raw["credential_ref"])["password"] == "secret"
    dump = "\n".join(service.store.db.iterdump())
    assert key not in dump
    assert '"password":"secret"' not in dump
    assert "digest" not in public_entity(service.store.list("keys")[0])


def test_projection_and_privilege_intersection(service):
    p = principal(service)
    resource, accounts = published(service)
    grant = save(service, "grants", {"principal_id": p["id"], "resource_id": resource["id"], "normal_account_id": accounts[0], "privileged_account_id": accounts[1]})
    ctx, _ = identity(service, p["id"])
    assert [m["mode"] for m in service.resources(ctx)[0]["access_modes"]] == ["normal"]
    with pytest.raises(AccessError, match="特权"):
        service.authorize(ctx, resource["id"], "privileged", "db:write")
    save(service, "grants", {"expected_version": grant["version"], "allow_privileged": True}, grant["id"])
    assert len(service.resources(ctx)[0]["access_modes"]) == 2
    restricted, _ = identity(service, p["id"], {"resources:read", "db:query"})
    assert len(service.resources(restricted)[0]["access_modes"]) == 1
    assert "password" not in str(service.resources(ctx))


def test_identity_and_grant_uniqueness(service):
    p = principal(service)
    body = {"source_id": "local_keys", "subject": "alice", "principal_id": p["id"]}
    save(service, "bindings", body)
    with pytest.raises(AccessError) as error:
        save(service, "bindings", body)
    assert error.value.status == 409
    resource, accounts = published(service)
    grant = {"principal_id": p["id"], "resource_id": resource["id"], "normal_account_id": accounts[0]}
    save(service, "grants", grant)
    with pytest.raises(AccessError):
        save(service, "grants", grant)


def test_privileged_only_resource_account_keeps_explicit_permission_boundary(service):
    p = principal(service)
    resource, accounts = published(service)
    grant = save(service, "grants", {"principal_id": p["id"], "resource_id": resource["id"],
        "privileged_account_id": accounts[1], "allow_privileged": True})
    ctx, _ = identity(service, p["id"])
    result = service.resources(ctx)
    assert len(result) == 1
    assert result[0]["default_mode"] == "privileged"
    assert [m["mode"] for m in result[0]["access_modes"]] == ["privileged"]
    # Omitting mode must never silently elevate a call to the privileged account.
    with pytest.raises(AccessError):
        service.authorize(ctx, resource["id"], action="db:query")
    assert service.authorize(ctx, resource["id"], "privileged", "db:query")[1]["id"] == accounts[1]
    restricted, _ = identity(service, p["id"], {"resources:read", "db:query"})
    assert service.resources(restricted) == []
    save(service, "grants", {"expected_version": grant["version"], "allow_privileged": False}, grant["id"])
    assert service.resources(ctx) == []


def test_revoke_key_and_binding(service):
    p = principal(service)
    ctx, key = identity(service, p["id"])
    service.revoke_key(ctx.credential_id, "admin")
    with pytest.raises(AccessError):
        asyncio.run(service.auth.authenticate({"x-deebee-api-key": key}))


def test_managed_key_bearer_compatibility_is_opt_in(service, monkeypatch):
    p = principal(service)
    _, key = identity(service, p["id"])
    authorization = {"authorization": "Bearer " + key}
    with pytest.raises(AccessError):
        asyncio.run(service.auth.authenticate(authorization))
    monkeypatch.setenv("DEEBEE_ACCESS_ALLOW_MANAGED_KEY_BEARER", "1")
    context = asyncio.run(service.auth.authenticate(authorization))
    assert context.principal_id == p["id"]
    assert context.method == "api_key"
    with pytest.raises(AccessError) as error:
        asyncio.run(service.auth.authenticate(authorization | {"x-deebee-api-key": key}))
    assert error.value.code == "AMBIGUOUS_CREDENTIALS"
    ctx, key = identity(service, p["id"])
    binding = service.store.get("bindings", ctx.binding_id)
    save(service, "bindings", {"expected_version": binding["version"], "enabled": False}, binding["id"])
    with pytest.raises(AccessError):
        asyncio.run(service.auth.authenticate({"x-deebee-api-key": key}))


def test_no_automatic_admin_or_name_mapping(service):
    with pytest.raises(AccessError):
        save(service, "principals", {"name": "admin", "username": settings.admin_user, "password": "testing_password"})
    p = principal(service, "admin")
    ctx, _ = identity(service, p["id"])
    assert service.resources(ctx) == []


def test_optimistic_version_and_cross_resource_account(service):
    p = principal(service)
    resource, accounts = published(service)
    other, _ = published(service)
    with pytest.raises(AccessError):
        save(service, "grants", {"principal_id": p["id"], "resource_id": other["id"], "normal_account_id": accounts[0]})
    with pytest.raises(AccessError) as error:
        save(service, "resources", {"name": "Changed", "expected_version": 999}, resource["id"])
    assert error.value.code == "VERSION_CONFLICT"


@pytest.mark.parametrize("sql", ["SELECT 1; DROP TABLE items", "DELETE FROM items", "SELECT SLEEP(10)", "SELECT load_file('/etc/passwd')", "SELECT * FROM restricted_lab.secrets", "SELECT 1 INTO OUTFILE '/tmp/a'", "/*!50000 DELETE FROM items */ SELECT 1", "SELECT * FROM items FOR UPDATE", "SET ROLE admin"])
def test_unsafe_queries_are_rejected(sql):
    with pytest.raises(AccessError):
        prepare_sql(sql, {}, {"type": "mysql", "database": "access_lab", "schemas": ["public"]})


def test_query_parameter_binding_and_postgres_scope():
    compiled, parameters = prepare_sql("SELECT id FROM items WHERE id=:id", {"id": "1' OR 1=1"}, {"type": "postgresql", "database": "access_lab", "schemas": ["public"]})
    assert "%(id)s" in compiled and "public" in compiled
    assert "OR 1=1" not in compiled
    assert parameters["id"] == "1' OR 1=1"


def test_cursor_cannot_cross_identity(service):
    a, _ = identity(service, principal(service, "A")["id"])
    b, _ = identity(service, principal(service, "B")["id"])
    cursor = service.cursor(a, "resources", 100)
    assert service.offset(a, "resources", cursor) == 100
    with pytest.raises(AccessError):
        service.offset(b, "resources", cursor)


def test_rest_management_and_mcp_auth_boundaries(tmp_path):
    app = FastAPI()
    install_access(app, tmp_path)
    with TestClient(app) as client:
        admin = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
        result = client.post("/api/admin/v1/principals", json={"name": "Agent", "kind": "service"}, headers=admin)
        assert result.status_code == 201, result.text
        key = client.post("/api/admin/v1/api-keys", json={"name": "Agent key", "principal_id": result.json()["id"]}, headers=admin).json()["key"]
        headers = {"X-DeeBee-API-Key": key}
        assert client.get("/api/v1/me", headers=headers).status_code == 200
        assert client.get("/api/admin/v1/principals", headers=headers).status_code == 403
        assert client.get("/api/v1/me", headers=admin).status_code == 401
        assert client.get("/api/v1/me", headers=headers | admin).status_code == 400
        assert client.get("/api/v1/resources", headers=headers).json()["resources"] == []
        mcp_headers = headers | {"Accept": "application/json, text/event-stream"}
        init = client.post("/mcp/", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "independent-test", "version": "1.0"}}})
        assert init.status_code == 200, init.text
        result = client.post("/mcp/", headers=mcp_headers | {"MCP-Protocol-Version": "2025-11-25"}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "identity.me", "arguments": {}}})
        assert result.status_code == 200, result.text
        assert result.json()["result"]["structuredContent"]["principal"]["name"] == "Agent"
        audits = client.get("/api/admin/v1/audit-events", headers=admin).json()["items"]
        event = next(item for item in audits if item["operation"] == "mcp.tool.identity.me")
        assert event["actor_name"] == "Agent"
        assert event["actor_kind"] == "service"
        assert event["auth_method"] == "api_key"
        assert event["source_id"] == "local_keys"
        assert '"name":"identity.me"' in event["request_preview"]
        assert key not in str(event)
        assert client.get(f"/api/admin/v1/audit-events/{event['id']}", headers=admin).json()["request_id"]
        assert client.post("/mcp/", json={}).status_code == 401
        assert client.post("/api/v1/db/queries", headers=headers, content=b"x" * 262145).status_code == 413


def test_atomic_configuration_preview_create_rollback_and_roundtrip(service):
    from deebee.access.configuration import import_configuration, NAMES
    config = {"version": 1, "principals": [{"id": "agent_import", "name": "Imported", "kind": "service"}],
              "identity-bindings": [{"id": "binding_import", "source_id": "local_keys", "subject": "import_subject", "principal_id": "agent_import"}]}
    result = asyncio.run(import_configuration(service, config, "admin", preview=True))
    assert result["updated"] == 2
    assert service.store.get("principals", "agent_import", required=False) is None
    asyncio.run(import_configuration(service, config, "admin", preview=False))
    assert service.store.get("bindings", "binding_import")["principal_id"] == "agent_import"
    exported = {"version": 1, **{name: [public_entity(v) for v in service.store.list(kind)] for name, kind in NAMES.items()}}
    assert asyncio.run(import_configuration(service, exported, "admin", preview=False))["updated"] == 0
    invalid = {"version": 1, "principals": [{"id": "rollback_agent", "name": "Rollback", "kind": "service"}],
               "identity-bindings": [{"id": "rollback_binding", "source_id": "local_keys", "subject": "broken", "principal_id": "not_found"}]}
    with pytest.raises(AccessError):
        asyncio.run(import_configuration(service, invalid, "admin", preview=False))
    assert service.store.get("principals", "rollback_agent", required=False) is None


@pytest.mark.parametrize("claims", [{"aud": None}, {"scope": [{}]}, {"exp": float("nan")}, {"exp": float("inf")}])
def test_malformed_provider_claims_fail_closed(service, claims):
    source = service.store.get("sources", "local_keys")
    with pytest.raises(AccessError):
        service.auth.bind(source, {"sub": "a", "exp": time.time()+60, "scope": []} | claims, "k", "oidc", {})


def test_restart_recovery_is_explicit_and_retention_keeps_idempotency(service):
    from deebee.access.store import encode
    now = time.time()
    body = {"status": "running"}
    service.store.db.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?)", ("job", "p", "b", "idem", "hash", encode(body), service.store.encrypt({"sql": "secret query"}), None, now, now))
    other = AccessStore(service.store.directory)
    assert 'running' in other.db.execute("SELECT body FROM executions").fetchone()[0]
    other.close()
    service.store.recover_executions()
    assert 'unknown' in service.store.db.execute("SELECT body FROM executions").fetchone()[0]
    service.store.db.execute("UPDATE executions SET updated_at=?", (now-8*86400,))
    service.store.cleanup()
    assert service.store.db.execute("SELECT payload_cipher FROM executions").fetchone()[0] == ""
    assert service.store.db.execute("SELECT idem FROM executions").fetchone()[0] == "idem"


def test_local_password_reset_revokes_sessions_without_touching_target(service):
    p = save(service, "principals", {"name": "Local", "username": "local-test", "password": "old_password_for_test"})
    resource, accounts = published(service)
    token, _ = service.auth.login("local-test", "old_password_for_test", "127.0.0.1")
    save(service, "principals", {"expected_version": p["version"], "password": "new_password_for_test"}, p["id"])
    with pytest.raises(AccessError):
        service.auth.local_session(token)
    assert service.auth.login("local-test", "new_password_for_test", "127.0.0.1")
    assert service.store.secret(service.store.get("accounts", accounts[0])["credential_ref"])["password"] == "secret"


def test_audit_write_failure_blocks_dispatch(service):
    from deebee.access.executions import ExecutionManager
    import sqlite3
    p = principal(service)
    resource, accounts = published(service, "ssh")
    save(service, "grants", {"principal_id": p["id"], "resource_id": resource["id"], "normal_account_id": accounts[0]})
    ctx, _ = identity(service, p["id"])
    manager = ExecutionManager(service)
    service.store.db.execute("CREATE TRIGGER deny_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'test disk failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(manager.submit(ctx, "ssh.exec", {"resource_id": resource["id"], "command": "id", "idempotency_key": "disk-failure"}))
    assert manager.tasks == {}
    assert service.store.db.execute("SELECT count(*) FROM executions").fetchone()[0] == 0


def test_key_rotation_and_reopened_secret_store(service):
    p = principal(service)
    old_ctx, old_key = identity(service, p["id"])
    new_ctx, new_key = identity(service, p["id"])
    service.revoke_key(old_ctx.credential_id, "admin")
    _, accounts = published(service)
    restored = AccessStore(service.store.directory)
    try:
        auth = Authenticator(restored)
        assert asyncio.run(auth.authenticate({"x-deebee-api-key": new_key})).principal_id == p["id"]
        with pytest.raises(AccessError):
            asyncio.run(auth.authenticate({"x-deebee-api-key": old_key}))
        assert restored.secret(restored.get("accounts", accounts[0])["credential_ref"])["password"] == "secret"
    finally:
        restored.close()


def test_mounted_base_path_and_request_validation(tmp_path):
    inner = FastAPI()
    lifespan = install_access(inner, tmp_path)
    outer = FastAPI(lifespan=lifespan)
    outer.mount("/deebee", inner)
    with TestClient(outer) as client:
        h = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
        status = client.get("/deebee/api/admin/v1/status", headers=h).json()
        assert status["mcp_url"] == "http://testserver/deebee/mcp/"
        assert client.get("/deebee/.well-known/oauth-protected-resource").json()["resource"] == "http://testserver/deebee"


def test_legacy_reference_has_one_credential_source_and_rejects_drift(service):
    import copy
    import threading
    from types import SimpleNamespace
    from deebee.access.legacy import LegacyBridge
    original = {"id": "old-profile", "driver": "mysql", "name": "Original", "host": "db.example", "port": 3306, "user": "reader", "password": "original_secret", "default_database": "app", "options": {}}
    workbench = SimpleNamespace(_guard=threading.RLock(), _records={"old-profile": copy.deepcopy(original)})
    bridge = LegacyBridge(service.store, workbench)
    service.legacy = bridge
    preview = bridge.preview()["items"][0]
    result = bridge.refresh("old-profile", preview["fingerprint"], "admin")
    assert workbench._records["old-profile"] == original
    assert not result["resource"]["enabled"] and not result["account"]["enabled"]
    account = service.store.get("accounts", result["account"]["id"])
    assert not account.get("credential_ref")
    assert service.account_secret(account)["password"] == "original_secret"
    assert "original_secret" not in str(list(service.store.db.iterdump()))
    with pytest.raises(AccessError):
        save(service, "accounts", {"expected_version": account["version"], "password": "another_secret"}, account["id"])
    workbench._records["old-profile"]["password"] = "rotated_secret"
    with pytest.raises(AccessError, match="原连接已变化"):
        service.account_secret(account)
    refreshed = bridge.refresh("old-profile", bridge.preview()["items"][0]["fingerprint"], "admin")
    assert not refreshed["account"]["enabled"]
    assert service.account_secret(service.store.get("accounts", account["id"]))["password"] == "rotated_secret"


def test_local_user_password_api_and_administrator_reset(tmp_path, monkeypatch):
    from dataclasses import replace
    import deebee.access.api as access_api
    monkeypatch.setattr(access_api, "settings", replace(settings, base_path=""))
    app = FastAPI()
    install_access(app, tmp_path)
    admin = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
    with TestClient(app, base_url="https://testserver") as client:
        p = client.post("/api/admin/v1/principals", headers=admin, json={"name": "Local API", "username": "api-local", "password": "old-password-test"}).json()
        login = client.post("/api/v1/local/login", json={"username": "api-local", "password": "old-password-test"})
        assert login.status_code == 200
        h = {"X-CSRF-Token": login.json()["csrf"]}
        assert client.post("/api/v1/local/password", headers=h, json={"current_password": "old-password-test", "new_password": ""}).status_code == 400
        assert client.post("/api/v1/local/password", headers=h, json={"current_password": "old-password-test", "new_password": "new-password-test"}).status_code == 200
        assert client.get("/api/v1/me").status_code == 401
        assert client.post("/api/v1/local/login", json={"username": "api-local", "password": "new-password-test"}).status_code == 200
        current = next(r for r in client.get("/api/admin/v1/principals", headers=admin).json()["items"] if r["id"] == p["id"])
        assert client.post(f"/api/admin/v1/principals/{p['id']}/reset-password", headers=admin, json={"password": "reset-password-test", "expected_version": current["version"]}).status_code == 200
        assert client.get("/api/v1/me").status_code == 401
