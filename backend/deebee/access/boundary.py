import secrets
import time
from urllib.parse import parse_qsl

from starlette.responses import JSONResponse

from .audit import CAPTURE_BYTES, parse_json, preview_bytes, preview_value, sanitize
from .models import AccessError


def _header(scope: dict, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1", errors="replace")
    return ""


class AccessRequestBoundary:
    """Rate-limit the access surface and archive all user-facing API activity."""

    def __init__(self, app, authenticator, store):
        self.app, self.auth, self.store = app, authenticator, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "").removeprefix(scope.get("root_path", "")) or "/"
        method = scope.get("method", "GET").upper()
        audited = method != "OPTIONS" and (path.startswith("/api/") or path.startswith("/mcp")) and path != "/api/health"
        if not audited:
            return await self.app(scope, receive, send)

        request_id = secrets.token_hex(12)
        scope.setdefault("state", {})["audit_request_id"] = request_id
        started = time.monotonic()
        request_capture = bytearray()
        response_capture = bytearray()
        request_total = response_total = 0
        status_code = 500
        response_content_type = ""
        request_content_type = _header(scope, b"content-type")
        bounded = path.startswith(("/api/v1/", "/api/admin/v1/", "/api/auth/oidc/", "/mcp"))

        async def capture_receive():
            nonlocal request_total
            message = await receive()
            chunk = message.get("body", b"")
            request_total += len(chunk)
            if len(request_capture) < CAPTURE_BYTES:
                request_capture.extend(chunk[: CAPTURE_BYTES - len(request_capture)])
            return message

        async def capture_send(message):
            nonlocal status_code, response_total, response_content_type
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                for key, value in headers:
                    if key.lower() == b"content-type":
                        response_content_type = value.decode("latin-1", errors="replace")
                if not any(key.lower() == b"x-request-id" for key, _ in headers):
                    headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                response_total += len(chunk)
                if len(response_capture) < CAPTURE_BYTES:
                    response_capture.extend(chunk[: CAPTURE_BYTES - len(response_capture)])
            await send(message)

        try:
            if bounded:
                self.auth.rate("access-ip:" + str((scope.get("client") or ("unknown",))[0]), 1200)
                messages = []
                while True:
                    message = await capture_receive()
                    if message["type"] == "http.disconnect":
                        return
                    if request_total > 262144:
                        raise AccessError("REQUEST_TOO_LARGE", "请求体不能超过 256 KiB", 413)
                    messages.append(message)
                    if not message.get("more_body", False):
                        break

                async def replay():
                    return messages.pop(0) if messages else await receive()

                await self.app(scope, replay, capture_send)
            else:
                await self.app(scope, capture_receive, capture_send)
        except AccessError as exc:
            await JSONResponse(
                {"error": {"code": exc.code, "message": exc.message}, "request_id": request_id}, status_code=exc.status
            )(scope, receive, capture_send)
        finally:
            self._archive(
                scope, path, method, request_id, started, status_code,
                bytes(request_capture), request_total, request_content_type,
                bytes(response_capture), response_total, response_content_type,
            )

    def _archive(
        self, scope: dict, path: str, method: str, request_id: str, started: float, status_code: int,
        request_raw: bytes, request_total: int, request_content_type: str,
        response_raw: bytes, response_total: int, response_content_type: str,
    ) -> None:
        identity = scope.get("state", {}).get("audit_identity", {})
        request_json = parse_json(request_raw) if request_total == len(request_raw) else None
        response_json = parse_json(response_raw) if response_total == len(response_raw) else None
        query = {}
        for key, value in parse_qsl(scope.get("query_string", b"").decode("utf-8", errors="replace"), keep_blank_values=True):
            query[key] = "[REDACTED]" if key.lower() in {"code", "state", "token", "api_key", "access_token"} else value

        request_preview, request_truncated = preview_bytes(
            request_raw, request_content_type, total_bytes=request_total, already_truncated=request_total > len(request_raw)
        )
        override = scope.get("state", {}).get("audit_request_override")
        if override is not None:
            request_preview, request_truncated = preview_value(override)
        if not request_preview and query:
            request_preview, request_truncated = preview_value({"query": query})
        if path == "/api/admin/v1/audit-events" and method == "GET":
            response_preview, response_truncated = preview_value({"archived_records_returned": True, "bytes": response_total})
        else:
            response_preview, response_truncated = preview_bytes(
                response_raw, response_content_type, total_bytes=response_total, already_truncated=response_total > len(response_raw)
            )

        route = scope.get("route")
        route_name = getattr(route, "name", "") if route else ""
        surface = "mcp" if path.startswith("/mcp") else "admin" if path.startswith("/api/admin/v1/") else "access_ui" if path.startswith(("/api/v1/", "/api/auth/oidc/", "/api/access/")) else "workbench"
        operation = self._operation(surface, method, path, route_name, request_json)
        body = request_json if isinstance(request_json, dict) else {}
        result = response_json if isinstance(response_json, dict) else {}
        values = body
        result_values = result
        if surface == "mcp":
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            values = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            rpc_result = result.get("result") if isinstance(result.get("result"), dict) else {}
            result_values = rpc_result.get("structuredContent") if isinstance(rpc_result.get("structuredContent"), dict) else {}
        resource_id = str(values.get("resource_id") or query.get("resource_id") or "")
        mode = str(values.get("mode") or query.get("mode") or "")
        account_id = str(values.get("account_id") or "")
        account_username = str(values.get("username") or "")
        execution_id = str(result_values.get("execution_id") or values.get("execution_id") or "")
        if execution_id:
            try:
                import json

                with self.store.lock:
                    row = self.store.db.execute("SELECT body FROM executions WHERE id=?", (execution_id,)).fetchone()
                if row:
                    execution_body = json.loads(row[0])
                    resource_id = resource_id or execution_body.get("resource_id", "")
                    mode = mode or execution_body.get("mode", "")
                    account_id = account_id or execution_body.get("account_id", "")
                    account_username = account_username or execution_body.get("username", "")
            except Exception:
                pass

        actor = str(identity.get("principal_id") or identity.get("actor_id") or "anonymous")
        actor_name = str(identity.get("actor_name") or body.get("username") or "anonymous")
        if actor != "anonymous" and (not actor_name or actor_name == actor):
            try:
                actor_name = self.store.get("principals", actor)["name"]
            except Exception:
                actor_name = actor_name or actor
        if resource_id and not account_id and actor != "anonymous":
            try:
                grant = next(
                    item for item in self.store.list("grants")
                    if item.get("principal_id") == actor and item.get("resource_id") == resource_id and item.get("enabled")
                )
                mode = mode or "normal"
                account_id = str(grant.get("privileged_account_id") if mode == "privileged" else grant.get("normal_account_id") or "")
                if account_id:
                    account_username = str(self.store.get("accounts", account_id).get("username", ""))
            except Exception:
                pass
        error_code = ""
        if isinstance(result.get("error"), dict):
            error_code = str(result["error"].get("code", ""))
        self.store.audit(
            actor, operation, execution_id or resource_id or path,
            {"query": sanitize(query), "route": route_name, "request_bytes": request_total, "response_bytes": response_total},
            request_id=request_id, surface=surface, actor_name=actor_name,
            actor_kind=str(identity.get("actor_kind") or ("anonymous" if actor == "anonymous" else "human")),
            auth_method=str(identity.get("auth_method") or "none"), source_id=str(identity.get("source_id") or ""),
            subject=str(identity.get("subject") or ""), credential_id=str(identity.get("credential_id") or ""),
            client_id=str(identity.get("client_id") or ""), method=method, path=path, operation=operation,
            resource_id=resource_id, account_id=account_id, account_username=account_username, mode=mode,
            status_code=status_code, duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            client_ip=str((scope.get("client") or ("unknown",))[0]), user_agent=_header(scope, b"user-agent")[:256],
            request_preview=request_preview, response_preview=response_preview,
            request_truncated=request_truncated, response_truncated=response_truncated, error_code=error_code,
        )

    @staticmethod
    def _operation(surface: str, method: str, path: str, route_name: str, request_json) -> str:
        if surface == "mcp" and isinstance(request_json, dict):
            rpc_method = str(request_json.get("method", "request"))
            if rpc_method == "tools/call":
                name = ((request_json.get("params") or {}).get("name") or "unknown")
                return "mcp.tool." + str(name)
            return "mcp." + rpc_method.replace("/", ".")
        if path.endswith("/auth/login") or path.endswith("/local/login"):
            return surface + ".auth.login"
        return surface + "." + (route_name or (method.lower() + "." + path.strip("/").replace("/", ".")))
