"""Browser sessions use server-selected accounts, never administrator profiles."""
from __future__ import annotations

import asyncio
import time

from fastapi import Depends, Request, WebSocket

from .models import AccessError, Model


class SessionInput(Model):
    resource_id: str
    mode: str = "normal"
    access_option_id: str = ""


def remote_profile(service, resource, account):
    from ..remote_connections import RemoteProfile

    secret = service.account_secret(account)
    return RemoteProfile(
        id=resource["id"], driver=resource["type"], name=resource["name"],
        host=resource["host"], port=resource["port"], user=account["username"],
        password=secret.get("passphrase", "") if account["auth_method"] == "private_key" else secret.get("password", ""), private_key=secret.get("private_key", ""),
        options={"auth_method": account["auth_method"],
                 "host_key_fingerprint": resource.get("host_key", ""), "strict_host_key": True,
                 "domain": resource.get("domain", ""), "security": resource.get("rdp_security", "nla"),
                 "ignore_certificate": False},
    )


class AuthorizedSession:
    def __init__(self, service, ctx, resource, account, grant, refresh):
        self.service, self.ctx, self.refresh = service, ctx, refresh
        self.resource, self.account, self.grant = resource, account, grant
        self.action = resource["type"] + ":connect"
        self.profile = remote_profile(service, resource, account)
        self.next_refresh = 0

    async def ssh_connection(self):
        # Use the same pre-authentication fingerprint and algorithm checks as
        # command execution; a host may advertise several different host keys.
        from .drivers import ssh_connect
        return await ssh_connect(self.resource, self.account, self.service.account_secret(self.account))

    async def check(self):
        if self.service.store.get("principals", self.ctx.principal_id)["kind"] != "human":
            raise AccessError("BROWSER_LOGIN_REQUIRED", "交互会话需要人员身份", 403)
        if time.monotonic() >= self.next_refresh:
            self.ctx = await self.refresh()
            self.next_refresh = time.monotonic() + 1
        self.service.authorize(self.ctx, self.resource["id"], self.account["tier"], self.action, self.grant["access_option_id"])

    async def watch(self):
        while True:
            await self.check()
            await asyncio.sleep(0.5)

    def audit(self, action, error=""):
        principal = self.service.store.get("principals", self.ctx.principal_id)
        self.service.store.audit(
            self.ctx.principal_id, action, self.resource["id"],
            {"grant_id": self.grant["id"], "role_id": self.grant["role_id"], "role_name": self.grant["role_name"],
             "membership_id": self.grant["membership_id"], "error": error},
            surface="access_ui", resource_id=self.resource["id"], account_id=self.account["id"],
            account_username=self.account["username"], mode=self.account["tier"],
            source_id=self.ctx.source_id, subject=self.ctx.subject, auth_method=self.ctx.method,
            actor_name=principal["name"], actor_kind=principal["kind"],
            credential_id=self.ctx.credential_id, client_id=self.ctx.client_id,
            status_code=403 if error else 200, error_code=error,
        )


def install_sessions(app, service, context, handlers, public_url):
    from urllib.parse import urlsplit
    from .store import new_id

    pending = {}

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(request: Request, body: SessionInput, ctx=Depends(context)):
        principal = service.store.get("principals", ctx.principal_id)
        if not request.cookies.get("deebee_access") or principal["kind"] != "human":
            raise AccessError("BROWSER_LOGIN_REQUIRED", "交互会话需要人员浏览器登录", 403)
        resource, _, _ = service.authorize(ctx, body.resource_id, body.mode, access_option_id=body.access_option_id)
        if resource["type"] not in handlers:
            raise AccessError("UNSUPPORTED_PROTOCOL", "此资源不支持交互会话")
        resource, account, grant = service.authorize(ctx, body.resource_id, body.mode, resource["type"] + ":connect", body.access_option_id)
        for session_id, session in list(pending.items()):
            if session["expires_at"] <= time.time():
                del pending[session_id]
        if len(pending) >= 1000:
            raise AccessError("RATE_LIMITED", "待连接会话过多", 429)
        session_id = new_id("session")
        pending[session_id] = {"ctx": ctx, "resource": resource, "account": account, "grant": grant, "expires_at": time.time() + 30}
        return {"id": session_id, "protocol": resource["type"], "expires_in": 30}

    @app.websocket("/api/v1/sessions/{session_id}/{protocol}")
    async def connect(websocket: WebSocket, session_id: str, protocol: str):
        try:
            expected = urlsplit(public_url or str(websocket.base_url).replace("ws:", "http:").replace("wss:", "https:"))
            origin = urlsplit(websocket.headers.get("origin", ""))
            if (origin.scheme, origin.netloc) != (expected.scheme, expected.netloc):
                raise AccessError("ORIGIN_REJECTED", "会话来源无效", 403)
            if not websocket.cookies.get("deebee_access"):
                raise AccessError("BROWSER_LOGIN_REQUIRED", "需要人员浏览器登录", 401)
            ctx = await context(websocket)
            ticket = pending.get(session_id)
            if not ticket or ticket["expires_at"] <= time.time() or protocol != ticket["resource"]["type"]:
                raise AccessError("SESSION_EXPIRED", "请重新创建会话", 403)
            owner = ticket["ctx"]
            if (ctx.principal_id, ctx.binding_id, ctx.credential_id) != (owner.principal_id, owner.binding_id, owner.credential_id):
                raise AccessError("SESSION_DENIED", "此会话不属于当前登录身份", 403)
            del pending[session_id]
            session = AuthorizedSession(service, ctx, ticket["resource"], ticket["account"], ticket["grant"], lambda: context(websocket))
            await session.check()
            websocket.state.access_session = session
            session.audit(protocol + ".session.open")
        except AccessError:
            await websocket.close(code=4403)
            return
        await handlers[protocol](websocket, session.resource["id"])
