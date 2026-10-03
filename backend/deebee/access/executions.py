from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import suppress

from .audit import RESPONSE_PREVIEW_BYTES, preview_value
from .drivers import RunningHandle, database_execute, database_schema, inspect_account, ssh_execute
from .models import AccessError, AuthContext, ExecutionInput
from .operations import KubernetesInput, SSHInspectInput, SSH_CHECKS, kubernetes_execute, namespace_for
from .service import AccessService, public_entity
from .sql import prepare_sql
from .store import encode, new_id


ACTION_SCOPES = {"ssh.exec": "ssh:exec", "db.query": "db:query", "db.execute": "db:write", "ssh.inspect": "ssh:inspect", "k8s.read": "k8s:read", "k8s.restart": "k8s:write"}
TERMINAL = {"succeeded", "failed", "cancelled", "unknown"}


class ExecutionManager:
    def __init__(self, service: AccessService):
        self.service, self.store = service, service.store
        self.tasks: dict[str, asyncio.Task] = {}
        self.active: dict[str, tuple[AuthContext, RunningHandle]] = {}
        self.changed = asyncio.Event()
        self.service.on_change = self.changed.set
        self.monitor_task = None

    async def start(self):
        self.monitor_task = asyncio.create_task(self.monitor())

    async def close(self):
        if self.monitor_task:
            self.monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor_task
        for execution_id in list(self.active):
            await self.cancel_internal(execution_id, "SERVICE_STOPPING")
        if self.tasks:
            await asyncio.wait(list(self.tasks.values()), timeout=3)
        for task in list(self.tasks.values()):
            task.cancel()

    async def monitor(self):
        last_cleanup = 0
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=30)
            self.changed.clear()
            if time.time() - last_cleanup > 3600:
                self.store.cleanup()
                last_cleanup = time.time()
            for execution_id, (ctx, _) in list(self.active.items()):
                try:
                    fresh = await self.service.auth.revalidate(ctx)
                    body = self.get_body(execution_id)
                    _, account, grant = self.service.authorize(fresh, body["resource_id"], body["mode"], ACTION_SCOPES[body["tool"]])
                    if account["version"] != body["account_version"] or grant["version"] != body["grant_version"]:
                        raise AccessError("AUTHORIZATION_CHANGED", "执行授权版本已变化", 403)
                except Exception:
                    await self.cancel_internal(execution_id, "AUTHORIZATION_REVOKED")

    def get_body(self, execution_id: str) -> dict:
        with self.store.lock:
            row = self.store.db.execute("SELECT body FROM executions WHERE id=?", (execution_id,)).fetchone()
        if not row:
            raise AccessError("EXECUTION_NOT_FOUND", "执行记录不存在或无权限", 404)
        return json.loads(row["body"])

    def update(self, execution_id: str, changes: dict, result: dict | None = None):
        with self.store.transaction():
            body = self.get_body(execution_id) | changes
            payload_row = self.store.db.execute("SELECT payload_cipher FROM executions WHERE id=?", (execution_id,)).fetchone()
            request_payload = self.store.decrypt(payload_row[0]) if payload_row and payload_row[0] else {"expired": True}
            self.store.db.execute("UPDATE executions SET body=?,updated_at=? WHERE id=?", (encode(body), time.time(), execution_id))
            if result is not None:
                self.store.db.execute("UPDATE executions SET result_cipher=? WHERE id=?", (self.store.encrypt(result), execution_id))
            request_preview, request_truncated = preview_value(request_payload)
            response_preview, response_truncated = preview_value(result if result is not None else {
                "status": body["status"], "error": body.get("error")
            }, limit=RESPONSE_PREVIEW_BYTES)
            principal = self.store.get("principals", body["owner"], required=False) or {}
            self.store.audit(
                body["owner"], "execution." + body["status"], execution_id,
                {k: body[k] for k in ("resource_id", "account_id", "username", "mode", "source_id", "binding_id", "subject", "tool")},
                surface="execution_worker", actor_name=principal.get("name", body["subject"]),
                actor_kind=principal.get("kind", "service"), auth_method="delegated_execution",
                source_id=body["source_id"], subject=body["subject"], credential_id=body.get("credential_id", ""),
                client_id=body.get("client_id", ""), operation="execution." + body["status"],
                resource_id=body["resource_id"], account_id=body["account_id"], account_username=body["username"],
                mode=body["mode"], status_code=200 if body["status"] == "succeeded" else 500 if body["status"] in {"failed", "unknown"} else 202,
                request_preview=request_preview, response_preview=response_preview,
                request_truncated=request_truncated, response_truncated=response_truncated,
                error_code=str((body.get("error") or {}).get("code", "")),
            )
        return body

    async def submit(self, ctx: AuthContext, tool: str, values: dict) -> dict:
        model = KubernetesInput if tool.startswith("k8s.") else SSHInspectInput if tool == "ssh.inspect" else ExecutionInput
        body = model.model_validate(values).model_dump()
        action = ACTION_SCOPES[tool]
        resource, account, grant = self.service.authorize(ctx, body["resource_id"], body["mode"], action)
        if tool.startswith("k8s."):
            if resource["type"] != "k8s":
                raise AccessError("INVALID_ARGUMENT", "此操作需要 Kubernetes 资源")
            namespace_for(resource, body["namespace"])
            if (tool == "k8s.restart") != (body["operation"] == "restart"):
                raise AccessError("INVALID_ARGUMENT", "restart 必须使用 k8s.restart；只读工具不接受变更")
            if tool == "k8s.restart" and body["mode"] != "privileged":
                raise AccessError("PRIVILEGE_DENIED", "重启必须显式使用特权模式", 403)
        elif tool == "ssh.inspect":
            if resource["type"] != "ssh":
                raise AccessError("INVALID_ARGUMENT", "此检查需要 SSH 资源")
        elif tool == "ssh.exec":
            if resource["type"] != "ssh" or not body["command"] or body["sql"] or body["parameters"]:
                raise AccessError("INVALID_ARGUMENT", "SSH 执行需要 SSH 资源和命令，不接受 SQL/参数")
            if body["elevation"] == "sudo" and body["mode"] != "privileged":
                raise AccessError("PRIVILEGE_DENIED", "sudo 提权必须显式使用特权模式", 403)
        else:
            if body.get("elevation", "none") != "none":
                raise AccessError("INVALID_ARGUMENT", "提权只适用于 SSH 命令")
            if resource["type"] not in {"mysql", "postgresql"} or body["command"]:
                raise AccessError("INVALID_ARGUMENT", "数据库执行只接受 MySQL/PostgreSQL 和 SQL")
            if tool == "db.execute" and body["mode"] != "privileged":
                raise AccessError("PRIVILEGE_DENIED", "数据库变更必须显式使用特权模式", 403)
            prepare_sql(body["sql"], body["parameters"], resource, tool == "db.execute")
        self.service.auth.rate("submit:" + ctx.credential_id, 60)
        idem = encode([ctx.principal_id, ctx.binding_id, tool, body["resource_id"], body["idempotency_key"]])
        request_hash = hashlib.sha256(encode(body).encode()).hexdigest()
        with self.store.transaction():
            old = self.store.db.execute("SELECT id,request_hash,body FROM executions WHERE idem=?", (idem,)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise AccessError("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求", 409)
                return self.public(json.loads(old["body"]))
            running = [self.get_body(k) for k in self.tasks]
            if len(running) >= 50 or sum(b["owner"] == ctx.principal_id for b in running) >= 3 or sum(b["resource_id"] == resource["id"] for b in running) >= 5:
                raise AccessError("RATE_LIMITED", "执行并发达到上限", 429)
            execution_id = new_id("exec")
            record = {"id": execution_id, "execution_id": execution_id, "owner": ctx.principal_id, "binding_id": ctx.binding_id,
                      "source_id": ctx.source_id, "subject": ctx.subject, "credential_id": ctx.credential_id, "client_id": ctx.client_id,
                      "tool": tool, "resource_id": resource["id"], "mode": body["mode"], "account_id": account["id"],
                      "username": account["username"], "account_alias": account["name"], "resource_version": resource["version"],
                      "account_version": account["version"], "grant_version": grant["version"], "status": "queued", "created_at": time.time(),
                      "request_hash": request_hash, "auth_expires_at": ctx.expires_at}
            self.store.db.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (execution_id, ctx.principal_id, ctx.binding_id, idem, request_hash, encode(record), self.store.encrypt(body), None, time.time(), time.time()))
            request_preview, request_truncated = preview_value(body)
            response_preview, response_truncated = preview_value(self.public(record), limit=RESPONSE_PREVIEW_BYTES)
            principal = self.store.get("principals", ctx.principal_id, required=False) or {}
            self.store.audit(
                ctx.principal_id, "execution.queued", execution_id, record,
                surface="execution_worker", actor_name=principal.get("name", ctx.subject), actor_kind=principal.get("kind", "service"),
                auth_method=ctx.method, source_id=ctx.source_id, subject=ctx.subject, credential_id=ctx.credential_id,
                client_id=ctx.client_id, operation="execution.queued", resource_id=resource["id"], account_id=account["id"],
                account_username=account["username"], mode=body["mode"], status_code=202,
                request_preview=request_preview, response_preview=response_preview,
                request_truncated=request_truncated, response_truncated=response_truncated,
            )
        handle = RunningHandle()
        self.active[execution_id] = (ctx, handle)
        task = asyncio.create_task(self.run(execution_id, ctx, body, handle))
        self.tasks[execution_id] = task
        task.add_done_callback(lambda _: (self.tasks.pop(execution_id, None), self.active.pop(execution_id, None)))
        return self.public(record)

    async def run(self, execution_id: str, ctx: AuthContext, body: dict, handle: RunningHandle):
        work = None
        try:
            fresh = await self.service.auth.revalidate(ctx)
            record = self.get_body(execution_id)
            resource, account, grant = self.service.authorize(fresh, body["resource_id"], body["mode"], ACTION_SCOPES[record["tool"]])
            if (resource["version"], account["version"], grant["version"]) != (record["resource_version"], record["account_version"], record["grant_version"]):
                raise AccessError("AUTHORIZATION_CHANGED", "排队期间配置已变化", 403)
            if handle.cancelled.is_set():
                raise AccessError("CANCELLED", "尚未执行，已取消", 409)
            secret = self.service.account_secret(account)
            timeout = min(body["timeout_seconds"], resource["timeout_seconds"], grant["limits"]["timeout_seconds"], max(0, fresh.expires_at - time.time()))
            max_bytes = min(resource["max_output_bytes"], grant["limits"]["max_output_bytes"])
            self.update(execution_id, {"status": "running", "started_at": time.time()})
            if record["tool"].startswith("k8s."):
                work = asyncio.create_task(kubernetes_execute(resource, account, secret, body, max_bytes, handle))
            elif record["tool"] in {"ssh.exec", "ssh.inspect"}:
                command = SSH_CHECKS[body["check"]] if record["tool"] == "ssh.inspect" else body["command"]
                work = asyncio.create_task(ssh_execute(resource, account, secret, command, max_bytes, handle,
                                                      elevation=body.get("elevation", "none")))
            else:
                work = asyncio.create_task(asyncio.to_thread(database_execute, resource, account, secret, body["sql"], body["parameters"],
                    record["tool"] == "db.execute", max(1, int(timeout)), min(body["max_rows"], resource["max_rows"], grant["limits"]["max_rows"]), max_bytes, handle))
            done, _ = await asyncio.wait([work], timeout=timeout)
            if not done:
                with suppress(Exception):
                    await handle.cancel()
                self.update(execution_id, {"status": "unknown", "ended_at": time.time(), "error": {"code": "TIMEOUT", "message": "执行超时，已发起终止；远端结果需核实"}})
                # Keep the concurrency slot occupied until the driver really finishes.
                with suppress(Exception):
                    await work
                return
            result = work.result()
            if handle.cancelled.is_set():
                self.update(execution_id, {"status": "unknown", "ended_at": time.time(), "error": {"code": "CANCELLATION_UNCONFIRMED", "message": "取消与完成竞争，远端结果需核实"}}, result)
            else:
                status = "failed" if record["tool"] in {"ssh.exec", "ssh.inspect"} and result.get("exit_code") != 0 else "succeeded"
                self.update(execution_id, {"status": status, "ended_at": time.time()}, result)
        except AccessError as exc:
            status = "unknown" if exc.code == "CANCELLATION_UNCONFIRMED" else "cancelled" if exc.code == "CANCELLED" else "failed"
            self.update(execution_id, {"status": status, "ended_at": time.time(), "error": {"code": exc.code, "message": exc.message}})
        except asyncio.CancelledError:
            self.update(execution_id, {"status": "unknown", "ended_at": time.time(), "error": {"code": "SERVICE_STOPPING", "message": "服务停止，远端状态待核实"}})
            if work:
                work.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            raise
        except Exception:
            # Driver exceptions may contain SQL literals or credentials. Never return raw text.
            record = self.get_body(execution_id)
            uncertain = record["tool"] in {"db.execute", "ssh.exec", "k8s.restart"} and record["status"] == "running"
            self.update(execution_id, {"status": "unknown" if uncertain or handle.cancelled.is_set() else "failed", "ended_at": time.time(),
                                      "error": {"code": "TARGET_EXECUTION_FAILED", "message": "目标执行失败，请核验目标状态和连接配置；未自动重试"}})

    @staticmethod
    def public(body: dict) -> dict:
        return {k: v for k, v in body.items() if k in {"id", "execution_id", "resource_id", "mode", "tool", "status", "created_at", "started_at", "ended_at", "error"}} | {
            "account": {"alias": body["account_alias"], "username": body["username"]}
        }

    def get(self, ctx: AuthContext, execution_id: str, cursor: str = "") -> dict:
        body = self.get_body(execution_id)
        if body["owner"] != ctx.principal_id or body["binding_id"] != ctx.binding_id:
            raise AccessError("EXECUTION_NOT_FOUND", "执行记录不存在或无权限", 404)
        self.service.authorize(ctx, body["resource_id"], body["mode"], ACTION_SCOPES[body["tool"]])
        result = self.public(body)
        with self.store.lock:
            row = self.store.db.execute("SELECT result_cipher FROM executions WHERE id=?", (execution_id,)).fetchone()
        if row and row[0]:
            payload = self.store.decrypt(row[0])
            if "rows" in payload:
                offset = self.service.offset(ctx, execution_id, cursor)
                rows = payload["rows"]
                payload["rows"] = rows[offset:offset + 200]
                payload["next_cursor"] = self.service.cursor(ctx, execution_id, offset + 200) if offset + 200 < len(rows) else None
            result["result"] = payload
        elif body["status"] in TERMINAL and time.time() - body.get("ended_at", body["created_at"]) > 7 * 86400:
            result["result_expired"] = True
        return result

    async def cancel_internal(self, execution_id: str, reason: str):
        body = self.get_body(execution_id)
        if body["status"] in TERMINAL:
            return self.public(body)
        body = self.update(execution_id, {"status": "cancel_requested", "cancel_reason": reason})
        if execution_id in self.active:
            with suppress(Exception):
                await self.active[execution_id][1].cancel()
        return self.public(body)

    async def cancel(self, ctx: AuthContext, execution_id: str):
        self.get(ctx, execution_id)
        return await self.cancel_internal(execution_id, "CALLER_REQUESTED")

    async def test_account(self, account_id: str, actor: str):
        account = self.store.get("accounts", account_id)
        resource = self.store.get("resources", account["resource_id"])
        if account.get("deleted_at") or resource.get("deleted_at"):
            raise AccessError("RESOURCE_DELETED", "资源或账号已删除，不能检查连接", 409)
        try:
            if resource.get("connection_sync", {}).get("state", "synced") != "synced":
                raise AccessError("CONNECTION_NOT_READY", resource["connection_sync"]["reason"], 409)
            if resource["type"] not in {"ssh", "mysql", "postgresql", "k8s"} or (resource["type"] in {"mysql", "postgresql"} and not resource["database"]):
                raise AccessError("CONNECTION_NOT_READY", "资源类型或数据库配置尚未就绪", 409)
            result = await asyncio.wait_for(inspect_account(resource, account, self.service.account_secret(account)), 20)
        except Exception:
            result = {"connected": False, "normal_safe": False, "message": "连接或权限测试失败，请检查地址、主机指纹/TLS 与账号凭据"}
        result.update(resource_version=resource["version"], tested_at=time.time(), tested_by=actor)
        with self.store.transaction():
            current = self.store.get("accounts", account_id)
            if current["version"] != account["version"] or self.store.get("resources", resource["id"])["version"] != resource["version"]:
                raise AccessError("VERSION_CONFLICT", "测试期间配置变化，请重新测试", 409)
            updated = self.store.put("accounts", account_id, account | {"test_result": result}, account["version"])
            self.store.audit(actor, "accounts.test", account_id, result)
        self.changed.set()
        return public_entity(updated)

    async def schema(self, ctx: AuthContext, resource_id: str, mode: str = "normal", cursor: str = "", table: str = ""):
        resource, account, grant = self.service.authorize(ctx, resource_id, mode, "db:query")
        if resource["type"] not in {"mysql", "postgresql"}:
            raise AccessError("INVALID_ARGUMENT", "此资源不是数据库")
        rows = await asyncio.wait_for(asyncio.to_thread(database_schema, resource, account, self.service.account_secret(account)), 30)
        fresh = self.service.authorize(await self.service.auth.revalidate(ctx), resource_id, mode, "db:query")
        if any(old["version"] != new["version"] or old["id"] != new["id"] for old, new in zip((resource, account, grant), fresh)):
            raise AccessError("AUTHORIZATION_CHANGED", "读取期间授权已变化，请重新发起", 403)
        purpose = "schema:" + resource_id + ":" + mode + ":" + table
        if table:
            rows = [r for r in rows if r["table"] == table]
        offset = self.service.offset(ctx, purpose, cursor)
        return {"columns": rows[offset:offset + 200], "next_cursor": self.service.cursor(ctx, purpose, offset + 200) if offset + 200 < min(len(rows), 10000) else None,
                "truncated": len(rows) > 10000}
