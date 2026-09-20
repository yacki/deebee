"""Opt-in real SSH/MySQL/Postgres + independent MCP client acceptance.

DEEBEE_ACCESS_LIVE_URL must point to the isolated lab server, never production.
Run the documented access_lab compose first. No real credentials are printed.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


BASE = os.getenv("DEEBEE_ACCESS_LIVE_URL", "")
pytestmark = pytest.mark.skipif(not BASE, reason="isolated access lab not requested")


@pytest.fixture(scope="module")
def lab():
    assert BASE.startswith("http://127.0.0.1:"), "This suite only uses a loopback test server"
    admin = httpx.Client(base_url=BASE, timeout=30)
    login = admin.post("/api/auth/login", json={"username": "admin", "password": "deebee"})
    login.raise_for_status()
    admin.headers["Authorization"] = "Bearer " + login.json()["token"]
    def create(kind, body):
        response = admin.post("/api/admin/v1/" + kind, json=body)
        assert response.is_success, response.text
        return response.json()
    name = "验收 " + uuid.uuid4().hex[:6]
    principal = create("principals", {"name": name, "kind": "human", "username": "lab_" + uuid.uuid4().hex[:6], "password": "lab_local_test_password"})
    keys = {}
    for mode, scopes in (("normal", ["resources:read", "ssh:exec", "db:query"]), ("privileged", ["resources:read", "ssh:exec", "db:query", "db:write", "privilege:use"])):
        keys[mode] = create("api-keys", {"name": name + " " + mode, "principal_id": principal["id"], "scopes": scopes})
    keyscan = subprocess.check_output(["docker", "compose", "-f", "tests/access_lab/compose.yml", "exec", "-T", "ssh", "ssh-keygen", "-lf", "/etc/ssh/ssh_host_ed25519_key.pub"], text=True)
    fingerprint = keyscan.split()[1]
    resources, grants = {}, {}
    for kind, port in (("ssh", 19222), ("mysql", 19306), ("postgresql", 19432)):
        resource = create("resources", {"name": name + " " + kind, "type": kind, "host": "127.0.0.1", "port": port,
            "database": "" if kind == "ssh" else "access_lab", "tls": False, "host_key": fingerprint if kind == "ssh" else "", "enabled": True})
        accounts = {}
        for tier, username in (("normal", "lab_reader"), ("privileged", "lab_operator")):
            account = create("accounts", {"name": kind + " " + tier, "resource_id": resource["id"], "username": username, "tier": tier, "password": username + "_test_password"})
            response = admin.post(f"/api/admin/v1/accounts/{account['id']}/test")
            assert response.is_success, response.text
            account = response.json()
            assert account["test_result"]["connected"], account["test_result"]
            if tier == "normal":
                assert account["test_result"]["normal_safe"], account["test_result"]
            response = admin.patch(f"/api/admin/v1/accounts/{account['id']}", json={"expected_version": account["version"], "permission_confirmed": True, "permission_note": "隔离实验室，测试账号权限已核验", "enabled": True})
            assert response.is_success, response.text
            accounts[tier] = response.json()
        grants[kind] = create("access-grants", {"principal_id": principal["id"], "resource_id": resource["id"], "normal_account_id": accounts["normal"]["id"], "privileged_account_id": accounts["privileged"]["id"], "allow_privileged": True})
        resources[kind] = resource
    clients = {mode: httpx.Client(base_url=BASE, timeout=20, headers={"X-DeeBee-API-Key": info["key"]}) for mode, info in keys.items()}
    yield {"admin": admin, "clients": clients, "resources": resources, "grants": grants, "principal": principal, "keys": keys}
    for client in clients.values():
        client.close()
    admin.close()


def poll(client, execution_id):
    for _ in range(80):
        response = client.get("/api/v1/executions/" + execution_id)
        assert response.is_success, response.text
        result = response.json()
        if result["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
            return result
        time.sleep(.1)
    pytest.fail("execution did not settle")


def submit(lab, kind, mode="normal", command="", sql="", write=False):
    client = lab["clients"][mode]
    path = "/api/v1/ssh/executions" if kind == "ssh" else "/api/v1/db/executions" if write else "/api/v1/db/queries"
    body = {"resource_id": lab["resources"][kind]["id"], "mode": mode, "idempotency_key": uuid.uuid4().hex}
    body.update({"command": command} if kind == "ssh" else {"sql": sql})
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    return poll(client, response.json()["execution_id"])


def test_real_ssh_accounts_and_nonzero_exit(lab):
    for mode, expected in (("normal", "lab_reader"), ("privileged", "lab_operator")):
        result = submit(lab, "ssh", mode, command="id -un")
        assert result["status"] == "succeeded", result
        assert result["result"]["stdout"].strip() == expected
    result = submit(lab, "ssh", command="printf failure >&2; exit 7")
    assert result["status"] == "failed" and result["result"]["exit_code"] == 7
    assert submit(lab, "ssh", command="sudo -n touch /opt/privileged/approved")["status"] == "failed"
    assert submit(lab, "ssh", "privileged", command="sudo -n touch /opt/privileged/approved")["status"] == "succeeded"


@pytest.mark.parametrize("kind", ["mysql", "postgresql"])
def test_real_database_query_write_scope_and_schema(lab, kind):
    result = submit(lab, kind, sql="SELECT id, name, amount FROM items ORDER BY id")
    assert result["status"] == "succeeded", result
    assert result["result"]["rows"][0][0] == 1
    assert result["result"]["rows"][0][2] == "12.50"
    assert result["result"]["affected_rows"] == 0
    result = submit(lab, kind, "privileged", sql="UPDATE items SET name='Alpha' WHERE id=1", write=True)
    assert result["status"] == "succeeded", result
    client = lab["clients"]["normal"]
    denied = client.post("/api/v1/db/queries", json={"resource_id": lab["resources"][kind]["id"], "sql": "SELECT * FROM restricted_lab.secrets", "idempotency_key": uuid.uuid4().hex})
    assert denied.status_code == 403
    schema = client.get(f"/api/v1/resources/{lab['resources'][kind]['id']}/schema")
    assert schema.is_success, schema.text
    assert any(c["table"] == "items" for c in schema.json()["columns"])


def test_real_idempotency_and_privilege_cap(lab):
    client = lab["clients"]["normal"]
    body = {"resource_id": lab["resources"]["ssh"]["id"], "mode": "normal", "command": "id", "idempotency_key": uuid.uuid4().hex}
    first = client.post("/api/v1/ssh/executions", json=body)
    second = client.post("/api/v1/ssh/executions", json=body)
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert client.post("/api/v1/ssh/executions", json=body | {"command": "whoami"}).status_code == 409
    denied = client.post("/api/v1/ssh/executions", json=body | {"mode": "privileged", "idempotency_key": uuid.uuid4().hex})
    assert denied.status_code == 403


def test_independent_mcp_client(lab):
    async def run():
        async with streamablehttp_client(BASE + "/mcp/", headers={"X-DeeBee-API-Key": lab["keys"]["normal"]["key"]}) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "deebee"
                tools = await session.list_tools()
                assert {"identity.me", "resources.list", "ssh.exec", "db.query"}.issubset({t.name for t in tools.tools})
                result = await session.call_tool("resources.list", {})
                assert not result.isError
                assert len(result.structuredContent["resources"]) == 3
                submitted = await session.call_tool("ssh.exec", {"resource_id": lab["resources"]["ssh"]["id"], "command": "id -un", "idempotency_key": uuid.uuid4().hex})
                assert not submitted.isError, submitted
                for _ in range(80):
                    execution = await session.call_tool("executions.get", {"execution_id": submitted.structuredContent["execution_id"]})
                    if execution.structuredContent["status"] == "succeeded":
                        assert execution.structuredContent["result"]["stdout"].strip() == "lab_reader"
                        break
                    await asyncio.sleep(.1)
                else:
                    pytest.fail("MCP execution did not finish")
    asyncio.run(run())


def test_local_mapping_login(lab):
    with httpx.Client(base_url=BASE) as client:
        response = client.post("/api/v1/local/login", json={"username": lab["principal"]["username"], "password": "lab_local_test_password"})
        assert response.is_success, response.text
        assert client.get("/api/v1/me").json()["principal"]["id"] == lab["principal"]["id"]
        assert len(client.get("/api/v1/resources").json()["resources"]) == 3
        assert client.post("/api/v1/logout").status_code == 403
        assert client.post("/api/v1/logout", headers={"X-CSRF-Token": response.json()["csrf"]}).is_success
        assert client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize("kind", ["mysql", "postgresql"])
def test_real_result_limit_and_account_isolation(lab, kind):
    from concurrent.futures import ThreadPoolExecutor
    admin = lab["admin"]
    other = admin.post("/api/admin/v1/principals", json={"name": "Concurrent " + kind, "kind": "service"}).json()
    granted = admin.post("/api/admin/v1/access-grants", json={"principal_id": other["id"], "resource_id": lab["resources"][kind]["id"], "normal_account_id": lab["grants"][kind]["normal_account_id"]})
    assert granted.is_success
    other_key = admin.post("/api/admin/v1/api-keys", json={"name": "Concurrent read", "principal_id": other["id"]}).json()["key"]
    other_client = httpx.Client(base_url=BASE, headers={"X-DeeBee-API-Key": other_key})
    def run(mode):
        client = other_client if mode == "normal" else lab["clients"]["privileged"]
        response = client.post("/api/v1/db/queries", json={"resource_id": lab["resources"][kind]["id"], "mode": mode, "sql": "SELECT CURRENT_USER()", "idempotency_key": uuid.uuid4().hex})
        assert response.is_success, response.text
        result = poll(client, response.json()["execution_id"])
        assert result["status"] == "succeeded", result
        return result["result"]["rows"][0][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(run, ["normal", "privileged"]))
    other_client.close()
    assert "lab_reader" in values[0] and "lab_operator" in values[1]
    response = lab["clients"]["normal"].post("/api/v1/db/queries", json={"resource_id": lab["resources"][kind]["id"], "sql": "SELECT * FROM items ORDER BY id", "max_rows": 1, "idempotency_key": uuid.uuid4().hex})
    result = poll(lab["clients"]["normal"], response.json()["execution_id"])
    assert result["status"] == "succeeded", result
    assert len(result["result"]["rows"]) == 1 and result["result"]["truncated"]


def test_real_privilege_revoke_running_and_execution_ownership(lab):
    admin = lab["admin"]
    key = admin.post("/api/admin/v1/api-keys", json={"name": "revoke-lab", "principal_id": lab["principal"]["id"]}).json()
    client = httpx.Client(base_url=BASE, headers={"X-DeeBee-API-Key": key["key"]})
    try:
        response = client.post("/api/v1/ssh/executions", json={"resource_id": lab["resources"]["ssh"]["id"], "command": "sleep 30; id -un", "idempotency_key": uuid.uuid4().hex})
        assert response.status_code == 202, response.text
        execution_id = response.json()["execution_id"]
        other = admin.post("/api/admin/v1/principals", json={"name": "Other lab identity", "kind": "service"}).json()
        other_key = admin.post("/api/admin/v1/api-keys", json={"name": "Other lab key", "principal_id": other["id"]}).json()["key"]
        assert httpx.get(BASE + "/api/v1/executions/" + execution_id, headers={"X-DeeBee-API-Key": other_key}).status_code == 404
        assert admin.post("/api/admin/v1/api-keys/" + key["record"]["id"] + "/revoke").is_success
        assert client.get("/api/v1/me").status_code == 403
        for _ in range(60):
            result = admin.get("/api/admin/v1/executions/" + execution_id).json()
            if result["status"] in {"unknown", "failed", "cancelled"}:
                break
            time.sleep(.1)
        else:
            pytest.fail("running execution did not react to revocation")
        grant = next(g for g in admin.get("/api/admin/v1/access-grants").json()["items"] if g["id"] == lab["grants"]["ssh"]["id"])
        response = admin.patch("/api/admin/v1/access-grants/" + grant["id"], json={"expected_version": grant["version"], "allow_privileged": False})
        assert response.is_success
        denied = lab["clients"]["privileged"].post("/api/v1/ssh/executions", json={"resource_id": lab["resources"]["ssh"]["id"], "mode": "privileged", "command": "id -un", "idempotency_key": uuid.uuid4().hex})
        assert denied.status_code == 403
        assert submit(lab, "ssh", command="id -un")["result"]["stdout"].strip() == "lab_reader"
    finally:
        client.close()


@pytest.mark.parametrize("kind,port", [("mysql", 19306), ("postgresql", 19432)])
def test_legacy_workbench_real_query_and_transaction(lab, kind, port):
    admin = lab["admin"]
    response = admin.post("/api/connections", json={"name": "原工作台隔离验收 " + kind, "driver": kind,
        "host": "127.0.0.1", "port": port, "user": "lab_operator", "password": "lab_operator_test_password",
        "default_database": "access_lab", "default_schema": "public" if kind == "postgresql" else ""})
    assert response.status_code == 201, response.text
    profile = response.json()
    session = admin.post("/api/sessions", json={"profile_id": profile["id"], "database": "access_lab", "schema": "public" if kind == "postgresql" else "", "autocommit": False}).json()
    path = "/api/sessions/" + session["id"]
    try:
        assert admin.post(path + "/query", json={"sql": "UPDATE items SET name='Rollback test' WHERE id=1"}).is_success
        assert admin.post(path + "/rollback").is_success
        result = admin.post(path + "/query", json={"sql": "SELECT name FROM items WHERE id=1"})
        assert result.is_success and "Alpha" in result.text
    finally:
        admin.delete(path)
    # Kept in the isolated legacy store for real browser regression, not copied
    # into the agent resource directory or auto-published externally.


def test_wrong_ssh_fingerprint_fails_before_authentication(lab, monkeypatch):
    from deebee.access.drivers import ssh_connect
    resource = lab["resources"]["ssh"] | {"host_key": "SHA256:not-the-server"}
    with pytest.raises(Exception, match="Host key"):
        asyncio.run(ssh_connect(resource, {"username": "lab_reader", "auth_method": "password"}, {"password": "lab_reader_test_password"}))


def test_database_tls_verification_not_silently_downgraded(lab):
    from deebee.access.drivers import db_connect
    for kind in ("mysql", "postgresql"):
        with pytest.raises(Exception):
            db_connect(lab["resources"][kind] | {"tls": True}, {"username": "lab_reader"}, {"password": "lab_reader_test_password"})


def test_real_timeout_and_output_limit(lab):
    client = lab["clients"]["normal"]
    body = {"resource_id": lab["resources"]["ssh"]["id"], "command": "sleep 5; id", "timeout_seconds": 1, "idempotency_key": uuid.uuid4().hex}
    response = client.post("/api/v1/ssh/executions", json=body)
    result = poll(client, response.json()["execution_id"])
    assert result["status"] == "unknown" and result["error"]["code"] == "TIMEOUT"
    result = submit(lab, "ssh", command="head -c 1200000 /dev/zero | tr '\\000' x")
    assert result["status"] == "succeeded" and result["result"]["truncated"]
    assert len(result["result"]["stdout"].encode()) <= 1048576


def test_real_legacy_reference_import_does_not_modify_original(lab):
    admin = lab["admin"]
    before = admin.get("/api/connections").json()
    candidates = admin.get("/api/admin/v1/legacy-connections/preview").json()["items"]
    candidate = next(c for c in candidates if c["type"] == "mysql")
    response = admin.post("/api/admin/v1/legacy-connections/import", json={"profile_id": candidate["id"], "fingerprint": candidate["fingerprint"]})
    assert response.is_success, response.text
    imported = response.json()
    assert not imported["published"] and not imported["account"]["enabled"]
    assert admin.get("/api/connections").json() == before
    resource = imported["resource"]
    assert admin.patch("/api/admin/v1/resources/"+resource["id"], json={"expected_version": resource["version"], "tls": False}).is_success
    tested = admin.post("/api/admin/v1/accounts/"+imported["account"]["id"]+"/test").json()
    assert tested["test_result"]["connected"]
    assert tested["test_result"]["actual_user"].startswith("lab_operator")


def test_real_ssh_private_key_authentication(lab):
    import asyncssh
    from deebee.access.drivers import ssh_connect
    key = asyncssh.generate_private_key("ssh-ed25519")
    # Disposable container only; only the generated public key enters the target.
    subprocess.run(["docker", "compose", "-f", "tests/access_lab/compose.yml", "exec", "-T", "-u", "lab_reader", "ssh",
                    "sh", "-c", "umask 077; mkdir -p /home/lab_reader/.ssh; tee -a /home/lab_reader/.ssh/authorized_keys >/dev/null"],
                   input=key.export_public_key(), check=True, capture_output=True)
    async def run():
        connection = await ssh_connect(lab["resources"]["ssh"], {"username": "lab_reader", "auth_method": "private_key"},
                                       {"private_key": key.export_private_key().decode()})
        try:
            result = await connection.run("id -un", check=True)
            assert result.stdout.strip() == "lab_reader"
        finally:
            connection.close()
            await connection.wait_closed()
    asyncio.run(run())
