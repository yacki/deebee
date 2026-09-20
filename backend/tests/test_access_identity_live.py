from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = os.getenv("DEEBEE_ACCESS_LIVE_URL", "")
IDP = "http://127.0.0.1:18991"
pytestmark = pytest.mark.skipif(not BASE, reason="isolated identity lab not requested")


@pytest.fixture(scope="module")
def identity_lab():
    with httpx.Client(base_url=BASE, timeout=20) as admin, httpx.Client(base_url=IDP) as idp:
        login = admin.post("/api/auth/login", json={"username": "admin", "password": "deebee"})
        admin.headers["Authorization"] = "Bearer " + login.json()["token"]
        idp.post("/fixture/reset")
        def create(kind, body):
            r = admin.post("/api/admin/v1/" + kind, json=body)
            assert r.is_success, r.text
            return r.json()
        principal = create("principals", {"name": "可信身份验收 " + uuid.uuid4().hex[:6], "kind": "human"})
        sources = {}
        for mode in ("external_http", "jwt", "introspection"):
            body = {"name": "验收 " + mode, "type": "api_key" if mode == "external_http" else "oidc", "validation_mode": mode, "enabled": True,
                    "audiences": ["deebee" if mode == "external_http" else BASE], "required_scopes": ["deebee:access"]}
            if mode == "external_http":
                body.update(verify_endpoint=IDP + "/verify-key", service_secret="lab_verifier_service_secret")
            else:
                body.update(issuer=IDP)
                if mode == "introspection":
                    body.update(introspection_endpoint=IDP + "/introspect", service_client_id="deebee-lab", service_secret="lab_verifier_service_secret")
                else:
                    body.update(browser_login=True, client_id="deebee-browser")
            source = create("identity-sources", body)
            create("identity-bindings", {"source_id": source["id"], "subject": "lab_subject", "principal_id": principal["id"]})
            sources[mode] = source
        # Attach the already verified real lab resources to this mapped identity.
        resources = admin.get("/api/admin/v1/resources").json()["items"]
        accounts = admin.get("/api/admin/v1/accounts").json()["items"]
        chosen = {}
        for resource in reversed(resources):
            matching = [a for a in accounts if a["resource_id"] == resource["id"] and a["enabled"]]
            normal = next((a for a in matching if a["tier"] == "normal"), None)
            if resource["type"] not in chosen and normal:
                create("access-grants", {"principal_id": principal["id"], "resource_id": resource["id"], "normal_account_id": normal["id"]})
                chosen[resource["type"]] = resource
        assert len(chosen) == 3, "Run the real access suite before the identity suite"
        yield {"admin": admin, "idp": idp, "sources": sources, "principal": principal, "resources": chosen}


def headers(lab, mode, **changes):
    source = lab["sources"][mode]
    result = {"X-DeeBee-Identity-Source": source["id"]}
    if mode == "external_http":
        result["X-DeeBee-API-Key"] = "lab_external_key_test_only"
    else:
        token = lab["idp"].post("/fixture/token", json={"opaque": mode == "introspection", **changes}).json()["access_token"]
        result["Authorization"] = "Bearer " + token
    return result


@pytest.mark.parametrize("mode", ["external_http", "jwt", "introspection"])
def test_external_source_identity_and_resource_projection(identity_lab, mode):
    with httpx.Client(base_url=BASE, headers=headers(identity_lab, mode)) as client:
        result = client.get("/api/v1/me")
        assert result.is_success, result.text
        assert result.json()["principal"]["id"] == identity_lab["principal"]["id"]
        assert len(client.get("/api/v1/resources").json()["resources"]) == 3
        assert client.get("/api/admin/v1/principals").status_code in {401, 403}
        assert client.get("/api/connections").status_code != 200


@pytest.mark.parametrize("changes", [{"typ": "JWT"}, {"claims": {"aud": "another-service"}}, {"claims": {"iss": "https://untrusted.invalid"}}, {"claims": {"exp": 1}}, {"claims": {"sub": "unmapped"}}, {"claims": {"scope": "resources:read"}}])
def test_invalid_oidc_tokens_rejected(identity_lab, changes):
    result = httpx.get(BASE + "/api/v1/me", headers=headers(identity_lab, "jwt", **changes))
    assert result.status_code in {401, 403}, result.text


def test_online_introspection_revocation(identity_lab):
    h = headers(identity_lab, "introspection")
    assert httpx.get(BASE + "/api/v1/me", headers=h).is_success
    identity_lab["idp"].post("/fixture/revoke", json={"token": h["Authorization"][7:]})
    assert httpx.get(BASE + "/api/v1/me", headers=h).status_code == 401


def test_oidc_browser_code_pkce_state_nonce(identity_lab):
    source_id = identity_lab["sources"]["jwt"]["id"]
    with httpx.Client(base_url=BASE, follow_redirects=True) as client:
        result = client.get(f"/api/auth/oidc/{source_id}/start")
        assert result.is_success, result.text
        assert str(result.url).endswith("/#access-user")
        assert client.get("/api/v1/me").json()["principal"]["id"] == identity_lab["principal"]["id"]
        assert client.get("/api/admin/v1/principals").status_code == 401
        forged = client.get(f"/api/auth/oidc/{source_id}/callback?state=forged&code=forged")
        assert forged.status_code == 401


@pytest.mark.parametrize("mode", ["external_http", "jwt", "introspection"])
def test_external_auth_independent_mcp_execution(identity_lab, mode):
    auth_headers = headers(identity_lab, mode)
    async def run():
        async with streamablehttp_client(BASE + "/mcp/", headers=auth_headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                identity = await session.call_tool("identity.me", {})
                assert not identity.isError
                for kind, resource in identity_lab["resources"].items():
                    tool = "ssh.exec" if kind == "ssh" else "db.query"
                    args = {"resource_id": resource["id"], "idempotency_key": uuid.uuid4().hex}
                    args.update({"command": "id -un"} if kind == "ssh" else {"sql": "SELECT id FROM items ORDER BY id"})
                    submitted = await session.call_tool(tool, args)
                    assert not submitted.isError, submitted
                    for _ in range(100):
                        result = await session.call_tool("executions.get", {"execution_id": submitted.structuredContent["execution_id"]})
                        assert not result.isError, result
                        if result.structuredContent["status"] in {"succeeded", "failed", "unknown", "cancelled"}:
                            assert result.structuredContent["status"] == "succeeded", result
                            break
                        await asyncio.sleep(.1)
                    else:
                        pytest.fail("MCP execution timeout")
    asyncio.run(run())
