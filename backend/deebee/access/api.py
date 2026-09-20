from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError

from ..config import settings
from ..security import verify_token
from .auth import PASSWORDS
from .executions import ExecutionManager
from .models import AccessError, AuthContext, ExecutionInput, KINDS, SCOPES
from .service import AccessService, public_entity
from .store import AccessStore, encode


def headers_for(request: Request) -> dict:
    for name in ("authorization", "x-deebee-api-key", "x-deebee-identity-source"):
        if len(request.headers.getlist(name)) > 1:
            raise AccessError("AMBIGUOUS_CREDENTIALS", "不允许重复认证头", 400)
    if any(name in request.query_params for name in ("token", "api_key", "access_token")):
        raise AccessError("INVALID_ARGUMENT", "身份凭据不得放在 URL")
    return {k: request.headers[k] for k in ("authorization", "x-deebee-api-key", "x-deebee-identity-source") if k in request.headers}


def install_access(app: FastAPI, directory: Path | None = None, *, legacy=None):
    store = AccessStore(directory or Path(os.getenv("DEEBEE_ACCESS_DIR", str(settings.connections_file.parent / "access-v1"))))
    service = AccessService(store)
    if legacy is not None:
        from .legacy import LegacyBridge
        from .connection_pool import ConnectionPool
        service.legacy = LegacyBridge(store, legacy)
        app.state.connection_pool = ConnectionPool(service.legacy)
    from .boundary import AccessRequestBoundary
    app.add_middleware(AccessRequestBoundary, authenticator=service.auth, store=store)
    executions = ExecutionManager(service)
    app.state.access = service
    app.state.access_executions = executions
    public_url = os.getenv("DEEBEE_PUBLIC_URL", "").rstrip("/")
    cookie_path = settings.base_path or "/"
    insecure_test = os.getenv("DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL", "") == "1"
    browser_sessions: dict[str, dict] = {}
    oauth_states: dict[str, dict] = {}

    def base(request: Request) -> str:
        return public_url or str(request.base_url).split("://", 1)[0] + "://" + request.url.netloc + request.scope.get("root_path", "").rstrip("/")

    def error_response(exc: AccessError, request_id: str = ""):
        headers = {}
        if exc.status == 401:
            metadata = (public_url or settings.base_path) + "/.well-known/oauth-protected-resource"
            headers["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata}"'
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}, "request_id": request_id or secrets.token_hex(12)}, status_code=exc.status, headers=headers)

    @app.exception_handler(AccessError)
    async def access_error(request: Request, exc: AccessError):
        return error_response(exc)

    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: ValidationError):
        # Do not echo pydantic's `input` fields: management inputs contain secrets.
        issues = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False, include_context=False))
        return error_response(AccessError("INVALID_ARGUMENT", issues))

    async def admin(request: Request) -> str:
        headers = headers_for(request)
        if headers.get("x-deebee-api-key") or headers.get("x-deebee-identity-source"):
            raise AccessError("ADMIN_REQUIRED", "管理接口仅支持本地管理员交互式登录", 403)
        value = headers.get("authorization", "")
        user = verify_token(value[7:]) if value.startswith("Bearer ") else None
        if user != settings.admin_user:
            raise AccessError("ADMIN_REQUIRED", "请使用本地管理员登录", 401)
        request.state.audit_identity = {
            "actor_id": user,
            "actor_name": user,
            "actor_kind": "local_admin",
            "auth_method": "local_admin_token",
            "source_id": "legacy_admin",
            "subject": user,
            "credential_id": "legacy:" + hashlib.sha256(value.encode()).hexdigest()[:24],
        }
        return user

    def set_context_identity(request: Request, ctx: AuthContext) -> None:
        principal = store.get("principals", ctx.principal_id, required=False) or {}
        request.state.audit_identity = {
            "principal_id": ctx.principal_id,
            "actor_name": principal.get("name") or principal.get("username") or ctx.principal_id,
            "actor_kind": principal.get("kind", "service"),
            "auth_method": ctx.method,
            "source_id": ctx.source_id,
            "subject": ctx.subject,
            "credential_id": ctx.credential_id,
            "client_id": ctx.client_id,
        }

    async def context(request: Request) -> AuthContext:
        headers = headers_for(request)
        cookie = request.cookies.get("deebee_access", "")
        if headers and cookie:
            raise AccessError("AMBIGUOUS_CREDENTIALS", "请只提交一种身份凭据", 400)
        if cookie in browser_sessions:
            session = browser_sessions[cookie]
            if session["expires"] <= time.time():
                browser_sessions.pop(cookie, None)
                raise AccessError("TOKEN_EXPIRED", "OIDC 会话已到期，请重新登录", 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
                raise AccessError("CSRF_REJECTED", "会话校验失败", 403)
            ctx = await service.auth.authenticate(session["headers"])
        else:
            ctx = await service.auth.authenticate(headers, session_token=cookie, allow_session=True)
            if cookie and request.method not in {"GET", "HEAD", "OPTIONS"}:
                service.auth.csrf(cookie, request.headers.get("x-csrf-token", ""))
        service.auth.rate("request:" + ctx.credential_id, 240)
        set_context_identity(request, ctx)
        return ctx

    management = APIRouter(prefix="/api/admin/v1", dependencies=[Depends(admin)])

    @management.get("/legacy-connections/preview")
    async def legacy_preview():
        if not service.legacy:
            raise AccessError("LEGACY_UNAVAILABLE", "此部署没有原连接管理器", 503)
        return service.legacy.preview()

    @management.post("/legacy-connections/import")
    async def legacy_import(body: dict, actor: str = Depends(admin)):
        if not service.legacy:
            raise AccessError("LEGACY_UNAVAILABLE", "此部署没有原连接管理器", 503)
        result = service.legacy.refresh(body.get("profile_id"), body.get("fingerprint"), actor)
        executions.changed.set()
        return result

    @management.get("/status")
    async def status(request: Request):
        return {"enabled": True, "mcp_url": base(request) + "/mcp/", "api_url": base(request) + "/api/v1",
                "protocols": ["ssh", "mysql", "postgresql"], "local_login_preserved": True}

    @management.get("/audit-events")
    async def audits(
        limit: int = 100, before: int | None = None, actor: str = "", operation: str = "", surface: str = "",
        status_code: int | None = None, from_at: float | None = None, to_at: float | None = None,
    ):
        return {"items": store.audits(limit, before, actor=actor, operation=operation, surface=surface,
                                      status_code=status_code, from_at=from_at, to_at=to_at)}

    @management.get("/audit-events/{audit_id}")
    async def audit_event(audit_id: int):
        return store.audit_event(audit_id)

    @management.get("/executions")
    async def list_executions(actor: str = Depends(admin)):
        with store.lock:
            rows = store.db.execute("SELECT id FROM executions ORDER BY created_at DESC LIMIT 200").fetchall()
        store.audit(actor, "admin.executions.list", "executions")
        return {"items": [executions.get_body(row[0]) for row in rows]}

    @management.get("/executions/{execution_id}")
    async def admin_execution(execution_id: str, actor: str = Depends(admin)):
        body = executions.get_body(execution_id)
        with store.lock:
            row = store.db.execute("SELECT payload_cipher,result_cipher FROM executions WHERE id=?", (execution_id,)).fetchone()
        store.audit(actor, "admin.execution.read", execution_id)
        return body | {
            "request": store.decrypt(row[0]) if row and row[0] else None,
            "request_expired": not bool(row and row[0]),
            "result": store.decrypt(row[1]) if row and row[1] else None,
            "result_expired": body.get("status") in {"succeeded", "failed", "cancelled", "unknown"} and not bool(row and row[1]),
        }

    @management.post("/executions/{execution_id}/cancel")
    async def admin_cancel(execution_id: str, actor: str = Depends(admin)):
        store.audit(actor, "admin.execution.cancel", execution_id)
        return await executions.cancel_internal(execution_id, "ADMIN_REQUESTED")

    @management.post("/api-keys")
    async def create_key(body: dict, actor: str = Depends(admin)):
        return service.create_key(body, actor)

    @management.get("/api-keys")
    async def list_keys():
        return {"items": [public_entity(k) for k in store.list("keys")]}

    @management.post("/api-keys/{key_id}/revoke")
    async def revoke(key_id: str, actor: str = Depends(admin)):
        return service.revoke_key(key_id, actor)

    @management.post("/accounts/{account_id}/test")
    async def test_account(account_id: str, actor: str = Depends(admin)):
        return await executions.test_account(account_id, actor)

    @management.post("/identity-sources/{source_id}/test")
    async def test_source(source_id: str, body: dict, actor: str = Depends(admin)):
        source = store.get("sources", source_id)
        if body.get("credential"):
            header = "x-deebee-api-key" if source["type"] == "api_key" else "authorization"
            value = body["credential"] if header == "x-deebee-api-key" else "Bearer " + body["credential"]
            ctx = await service.auth.authenticate({header: value, "x-deebee-identity-source": source_id})
            result = service.me(ctx)
        elif source["type"] == "oidc":
            metadata = await service.auth.discovery(source, refresh=True)
            await service.auth.jwks(source, refresh=True)
            result = {"issuer": metadata["issuer"], "discovery_ok": True, "jwks_ok": True, "note": "发现成功不代表用户已完成绑定授权"}
        else:
            result = {"configured": True, "note": "外部 Key 的真实验证需要提供测试凭据；本地 Key 在签发后测试"}
        store.audit(actor, "sources.test", source_id)
        return result

    @management.post("/access-preview")
    async def preview(body: dict):
        principal = store.get("principals", body["principal_id"])
        ctx = AuthContext(principal["id"], "local", principal["id"], "preview", "preview", "local",
                          frozenset(body.get("scopes", SCOPES)), time.time() + 60, resource_ids=tuple(body.get("resource_ids", [])))
        return {"resources": service.resources(ctx), "note": "预览资源授权与给定凭据上限；实际调用还需身份验证服务/绑定/凭据有效"}

    # Configuration names in the public API remain descriptive; storage uses short internal names.
    names = {"identity-sources": "sources", "identity-bindings": "bindings", "access-grants": "grants",
             "principals": "principals", "resources": "resources", "accounts": "accounts"}

    @management.get("/access-config/export")
    async def export_config(actor: str = Depends(admin)):
        store.audit(actor, "config.export", "access")
        return {"version": 1, **{name: [public_entity(v) for v in store.list(kind) if not v.get("deleted_at")] for name, kind in names.items()}}

    @management.post("/access-config/import-preview")
    async def import_preview(body: dict, actor: str = Depends(admin)):
        from .configuration import import_configuration
        return await import_configuration(service, body, actor, preview=True)

    @management.post("/access-config/import")
    async def import_config(body: dict, actor: str = Depends(admin)):
        from .configuration import import_configuration
        return await import_configuration(service, body, actor, preview=False)

    @management.get("/{collection}")
    async def list_entities(collection: str):
        kind = names.get(collection)
        if not kind:
            raise AccessError("NOT_FOUND", "管理功能不存在", 404)
        if kind in {"resources", "accounts"} and legacy is not None:
            if await asyncio.to_thread(app.state.connection_pool.sync):
                executions.changed.set()
        return {"items": [public_entity(v) for v in store.list(kind) if not v.get("deleted_at")]}

    @management.delete("/resources/{resource_id}")
    async def delete_resource(resource_id: str, body: dict, actor: str = Depends(admin)):
        return service.delete_resource(resource_id, body, actor)

    @management.post("/{collection}", status_code=201)
    async def create_entity(collection: str, body: dict, actor: str = Depends(admin)):
        return await service.save(names.get(collection, ""), body, actor)

    @management.patch("/{collection}/{entity_id}")
    async def update_entity(collection: str, entity_id: str, body: dict, actor: str = Depends(admin)):
        return await service.save(names.get(collection, ""), body, actor, entity_id)

    @management.post("/principals/{principal_id}/reset-password")
    async def reset_password(principal_id: str, body: dict, actor: str = Depends(admin)):
        if not body.get("password"):
            raise AccessError("INVALID_ARGUMENT", "需要新密码")
        return await service.save("principals", {"password": body["password"], "expected_version": body.get("expected_version")}, actor, principal_id)

    app.include_router(management)
    data = APIRouter(prefix="/api/v1")

    @data.get("/me")
    async def me(request: Request, ctx: AuthContext = Depends(context)):
        result = service.me(ctx)
        cookie = request.cookies.get("deebee_access", "")
        if cookie in browser_sessions:
            result["csrf"] = browser_sessions[cookie]["csrf"]
        elif cookie:
            with store.lock:
                row = store.db.execute("SELECT csrf FROM local_sessions WHERE digest=?", (store.digest(cookie),)).fetchone()
            result["csrf"] = row[0] if row else ""
        return result

    @data.get("/resources")
    async def resources(cursor: str = "", limit: int = 100, type: str = "", ctx: AuthContext = Depends(context)):
        return resource_page(ctx, cursor, limit, type)

    def resource_page(ctx, cursor="", limit=100, type=""):
        items = service.resources(ctx)
        if type:
            items = [r for r in items if r["type"] == type]
        offset = service.offset(ctx, "resources:" + type, cursor)
        limit = max(1, min(limit, 100))
        return {"resources": items[offset:offset + limit], "next_cursor": service.cursor(ctx, "resources:" + type, offset + limit) if offset + limit < len(items) else None}

    @data.get("/resources/{resource_id}")
    async def resource(resource_id: str, ctx: AuthContext = Depends(context)):
        return service.projected(ctx, resource_id)

    @data.get("/resources/{resource_id}/schema")
    async def schema(resource_id: str, mode: str = "normal", cursor: str = "", table: str = "", ctx: AuthContext = Depends(context)):
        return await executions.schema(ctx, resource_id, mode, cursor, table)

    @data.post("/ssh/executions", status_code=202)
    async def ssh(body: ExecutionInput, ctx: AuthContext = Depends(context)):
        return await executions.submit(ctx, "ssh.exec", body.model_dump())

    @data.post("/db/queries", status_code=202)
    async def query(body: ExecutionInput, ctx: AuthContext = Depends(context)):
        return await executions.submit(ctx, "db.query", body.model_dump())

    @data.post("/db/executions", status_code=202)
    async def execute(body: ExecutionInput, ctx: AuthContext = Depends(context)):
        return await executions.submit(ctx, "db.execute", body.model_dump())

    @data.get("/executions/{execution_id}")
    async def execution(execution_id: str, cursor: str = "", ctx: AuthContext = Depends(context)):
        return executions.get(ctx, execution_id, cursor)

    @data.post("/executions/{execution_id}/cancel")
    async def cancel(execution_id: str, ctx: AuthContext = Depends(context)):
        return await executions.cancel(ctx, execution_id)

    @data.post("/local/login")
    async def local_login(request: Request, body: dict):
        username = str(body.get("username", ""))
        request.state.audit_identity = {"actor_name": username or "anonymous", "actor_kind": "human", "auth_method": "password", "source_id": "local", "subject": username}
        token, csrf = service.auth.login(username, str(body.get("password", "")), request.client.host if request.client else "unknown")
        principal = next((item for item in store.list("principals") if item.get("username") == username), None)
        if principal:
            request.state.audit_identity.update(principal_id=principal["id"], actor_name=principal["name"], credential_id="local:" + principal["id"])
        response = JSONResponse({"csrf": csrf})
        response.set_cookie("deebee_access", token, httponly=True, secure=not insecure_test, samesite="strict", path=cookie_path, max_age=3600)
        return response

    @data.post("/local/password")
    async def change_password(body: dict, ctx: AuthContext = Depends(context)):
        if not isinstance(body.get("new_password"), str) or len(body["new_password"]) < 12:
            raise AccessError("WEAK_PASSWORD", "新密码至少 12 位")
        if ctx.method != "local":
            raise AccessError("LOCAL_LOGIN_REQUIRED", "请使用本地密码登录", 403)
        principal = store.get("principals", ctx.principal_id)
        try:
            PASSWORDS.verify(principal["password_hash"], body.get("current_password", ""))
        except Exception as exc:
            raise AccessError("UNAUTHENTICATED", "当前密码错误", 401) from exc
        await service.save("principals", {"password": body.get("new_password", ""), "expected_version": principal["version"]}, ctx.principal_id, ctx.principal_id)
        return {"changed": True, "login_required": True}

    @data.post("/logout")
    async def logout(request: Request, ctx: AuthContext = Depends(context)):
        token = request.cookies.get("deebee_access", "")
        browser_sessions.pop(token, None)
        with store.transaction():
            store.db.execute("DELETE FROM local_sessions WHERE digest=?", (store.digest(token),))
            store.audit(ctx.principal_id, "session.logout", ctx.principal_id)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("deebee_access", path=cookie_path)
        return response

    app.include_router(data)

    @app.get("/api/access/login-options")
    async def login_options():
        return {"sources": [{"id": s["id"], "name": s["name"]} for s in store.list("sources") if s.get("type") == "oidc" and s.get("enabled") and s.get("browser_login")]}

    @app.get("/api/auth/oidc/{source_id}/start")
    async def oidc_start(source_id: str, request: Request):
        source = service.auth.enabled_source(source_id, "oidc")
        if not source.get("browser_login"):
            raise AccessError("OIDC_LOGIN_DISABLED", "该来源未启用浏览器登录", 403)
        if not public_url:
            raise AccessError("PUBLIC_URL_REQUIRED", "OIDC 浏览器登录需要固定 DEEBEE_PUBLIC_URL", 503)
        metadata = await service.auth.discovery(source)
        await service.auth.validate_url(metadata["authorization_endpoint"], source)
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        for key in list(oauth_states):
            if oauth_states[key]["expires"] < time.time():
                del oauth_states[key]
        if len(oauth_states) > 1000:
            raise AccessError("RATE_LIMITED", "登录请求过多", 429)
        service.auth.rate("oidc-start:" + (request.client.host if request.client else "unknown"), 20)
        callback = public_url + f"/api/auth/oidc/{source_id}/callback"
        oauth_states[state] = {"source_id": source_id, "nonce": nonce, "verifier": verifier, "redirect_uri": callback, "expires": time.time() + 300}
        query = urlencode({"client_id": source["client_id"], "response_type": "code", "redirect_uri": callback, "scope": " ".join(sorted({"openid", *source.get("required_scopes", []), *SCOPES})),
                           "state": state, "nonce": nonce, "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="), "code_challenge_method": "S256", "resource": source["audiences"][0]})
        response = RedirectResponse(metadata["authorization_endpoint"] + "?" + query)
        response.set_cookie("deebee_oidc_state", state, httponly=True, secure=not insecure_test, samesite="lax", path=cookie_path, max_age=300)
        return response

    @app.get("/api/auth/oidc/{source_id}/callback")
    async def oidc_callback(source_id: str, request: Request, state: str = "", code: str = ""):
        expected = request.cookies.get("deebee_oidc_state", "")
        flow = oauth_states.pop(state, None)
        if not expected or not secrets.compare_digest(expected, state) or not flow or flow["source_id"] != source_id or flow["expires"] <= time.time() or not code:
            raise AccessError("OIDC_STATE_REJECTED", "OIDC 登录请求无效或已过期", 401)
        source = service.auth.enabled_source(source_id, "oidc")
        metadata = await service.auth.discovery(source)
        credentials = store.secret(source["credential_ref"]) if source.get("credential_ref") else {}
        payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"], "client_id": source["client_id"], "code_verifier": flow["verifier"]}
        kwargs = {"auth": (source["client_id"], credentials["client_secret"])} if credentials.get("client_secret") else {}
        tokens = await service.auth.http_json(metadata["token_endpoint"], source, method="POST", data=payload, **kwargs)
        id_claims = await service.auth.decode_jwt(tokens.get("id_token", ""), source, id_token=True, nonce=flow["nonce"])
        auth_headers = {"authorization": "Bearer " + tokens.get("access_token", ""), "x-deebee-identity-source": source_id}
        ctx = await service.auth.authenticate(auth_headers)
        set_context_identity(request, ctx)
        if id_claims["sub"] != ctx.subject:
            raise AccessError("SUBJECT_MISMATCH", "OIDC 身份声明不一致", 401)
        session = secrets.token_urlsafe(32)
        for key in list(browser_sessions):
            if browser_sessions[key]["expires"] <= time.time():
                del browser_sessions[key]
        if len(browser_sessions) >= 1000:
            raise AccessError("RATE_LIMITED", "登录会话过多", 429)
        browser_sessions[session] = {"headers": auth_headers, "csrf": secrets.token_urlsafe(24), "expires": ctx.expires_at}
        response = RedirectResponse(public_url + "/#access-user")
        response.set_cookie("deebee_access", session, httponly=True, secure=not insecure_test, samesite="strict", path=cookie_path, max_age=max(1, int(ctx.expires_at - time.time())))
        response.delete_cookie("deebee_oidc_state", path=cookie_path)
        store.audit(ctx.principal_id, "oidc.login", ctx.binding_id)
        return response

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def metadata(request: Request):
        return {"resource": base(request), "authorization_servers": [s["issuer"] for s in store.list("sources") if s["type"] == "oidc" and s["enabled"]],
                "scopes_supported": sorted(SCOPES), "bearer_methods_supported": ["header"]}

    server = Server("deebee", version="1.0.0")
    object_schema = {"type": "object", "additionalProperties": True}
    text_prop = {"type": "string"}
    definitions = {
        "identity.me": ("返回经过验证的 DeeBee 系统账号与凭据上限。", {}),
        "resources.list": ("仅列出当前身份可以使用的 SSH/MySQL/PostgreSQL 资源和账号模式，不返回秘密。", {"cursor": text_prop, "type": text_prop, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        "resources.get": ("读取一个获准资源的可用账号模式。", {"resource_id": text_prop}),
        "db.schema": ("读取获准数据库的结构。", {"resource_id": text_prop, "mode": {"enum": ["normal", "privileged"]}, "cursor": text_prop, "table": text_prop}),
        "executions.get": ("查询自己的执行状态和分页结果。unknown 表示需核实远端状态，不能自动重放变更。", {"execution_id": text_prop, "cursor": text_prop}),
        "executions.cancel": ("请求终止自己的执行；不保证已提交修改回滚或 SSH 子进程停止。", {"execution_id": text_prop}),
    }
    for tool, desc in (("ssh.exec", "在授权 SSH 资源执行独立非交互命令。普通 OS 账号不等于只读；显式 privileged 才使用已授权特权账号。"),
                       ("db.query", "以真正只读账号和只读事务执行受支持的单条 SELECT，使用命名参数。"),
                       ("db.execute", "以已授权特权账号执行单条业务数据或表结构变更；可能产生不可逆修改，无逐次人工审批。")):
        schema = ExecutionInput.model_json_schema()
        properties = schema["properties"].copy()
        if tool == "ssh.exec":
            properties.pop("sql")
            properties.pop("parameters")
            properties.pop("max_rows")
        else:
            properties.pop("command")
        definitions[tool] = (desc, properties)

    @server.list_tools()
    async def list_tools():
        result = []
        for name, (description, properties) in definitions.items():
            required = [key for key in ("resource_id", "execution_id", "idempotency_key") if key in properties]
            if name == "ssh.exec":
                required.append("command")
            elif name in {"db.query", "db.execute"}:
                required.append("sql")
            result.append(types.Tool(name=name, description=description,
                inputSchema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
                outputSchema=object_schema,
                annotations=types.ToolAnnotations(readOnlyHint=name in {"identity.me", "resources.list", "resources.get", "db.schema", "db.query", "executions.get"},
                                                 destructiveHint=name in {"ssh.exec", "db.execute"}, openWorldHint=True)))
        return result

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            request = server.request_context.request
            ctx = request.state.access_context
            if name == "identity.me":
                result = service.me(ctx)
            elif name == "resources.list":
                result = resource_page(ctx, **arguments)
            elif name == "resources.get":
                result = service.projected(ctx, arguments["resource_id"])
            elif name == "db.schema":
                result = await executions.schema(ctx, **arguments)
            elif name == "executions.get":
                result = executions.get(ctx, **arguments)
            elif name == "executions.cancel":
                result = await executions.cancel(ctx, **arguments)
            elif name in ACTION_TOOLS:
                result = await executions.submit(ctx, name, arguments)
            else:
                raise AccessError("UNKNOWN_TOOL", "工具不存在", 404)
            return types.CallToolResult(content=[types.TextContent(type="text", text=encode(result))], structuredContent=result)
        except AccessError as exc:
            result = {"error": {"code": exc.code, "message": exc.message}, "request_id": secrets.token_hex(12)}
            return types.CallToolResult(content=[types.TextContent(type="text", text=encode(result))], structuredContent=result, isError=True)
        except ValidationError:
            result = {"error": {"code": "INVALID_ARGUMENT", "message": "工具参数无效"}}
            return types.CallToolResult(content=[types.TextContent(type="text", text=encode(result))], structuredContent=result, isError=True)

    host = urlsplit(public_url).netloc
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"] + ([host] if host else [])
    manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(allowed_hosts=allowed_hosts,
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"] + ([urlsplit(public_url).scheme + "://" + host] if host else [])),
        max_request_body_size=262144)

    class MCPBoundary:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return
            request = Request(scope, receive)
            try:
                ctx = await service.auth.authenticate(headers_for(request))
                service.auth.rate("mcp:" + ctx.credential_id, 240)
                scope.setdefault("state", {})["access_context"] = ctx
                set_context_identity(request, ctx)
            except AccessError as exc:
                await error_response(exc)(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)

    boundary = MCPBoundary()
    app.mount("/mcp", boundary)

    @asynccontextmanager
    async def lifespan(_):
        import fcntl
        lock_file = open(store.directory / "instance.lock", "a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError("DeeBee access V1 requires a single worker per access directory")
        pool_task = None
        try:
            if legacy is not None:
                await asyncio.to_thread(app.state.connection_pool.sync)
                pool_task = asyncio.create_task(reconcile_connections())
            store.recover_executions()
            store.cleanup()
            await executions.start()
            async with manager.run():
                yield
        finally:
            if pool_task:
                pool_task.cancel()
                with suppress(asyncio.CancelledError):
                    await pool_task
            await executions.close()
            browser_sessions.clear()
            oauth_states.clear()
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    async def reconcile_connections():
        import logging
        while True:
            await asyncio.sleep(30)
            try:
                if await asyncio.to_thread(app.state.connection_pool.sync):
                    executions.changed.set()
            except Exception:
                # Do not print connection records or exception text containing
                # credentials. The next read/startup also retries this projection.
                logging.getLogger(__name__).error("Connection pool synchronization failed; retrying in 30 seconds")

    app.router.lifespan_context = lifespan
    return lifespan


ACTION_TOOLS = {"ssh.exec", "db.query", "db.execute"}
