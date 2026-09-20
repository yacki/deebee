from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from .auth import Authenticator, PASSWORDS
from .models import AccessError, AuthContext, ENTITY_MODELS, KeyCreate, SCOPES
from .store import AccessStore, encode, new_id


SECRET_FIELDS = {"password", "private_key", "passphrase", "service_secret", "client_secret"}
INTERNAL_FIELDS = {"password_hash", "digest", "credential_ref", "legacy_fingerprint"}
META_FIELDS = {"id", "version", "created_at", "updated_at"}


def public_entity(value: dict) -> dict:
    return {k: v for k, v in value.items() if k not in SECRET_FIELDS | INTERNAL_FIELDS} | {
        "has_credentials": bool(value.get("credential_ref") or value.get("password_hash") or (value.get("resource_id") and value.get("legacy_profile_id")))
    }


class AccessService:
    def __init__(self, store: AccessStore):
        self.store = store
        self.auth = Authenticator(store)
        self.on_change = None
        self.legacy = None

    def account_secret(self, account):
        if account.get("legacy_profile_id"):
            if not self.legacy:
                raise AccessError("LEGACY_UNAVAILABLE", "原连接引用不可用", 503)
            return self.legacy.credentials(account)
        return self.store.secret(account["credential_ref"])

    async def save(self, kind: str, values: dict, actor: str, entity_id: str | None = None,
                   *, create_id: str | None = None, source_urls_validated: bool = False) -> dict:
        if kind not in ENTITY_MODELS:
            raise AccessError("INVALID_ENTITY", "不支持的配置类型")
        old = self.store.get(kind, entity_id) if entity_id else None
        expected = values.get("expected_version")
        if old and expected != old["version"]:
            raise AccessError("VERSION_CONFLICT", "配置已变化，请刷新后重试", 409)
        model = ENTITY_MODELS[kind]
        previous = {k: v for k, v in (old or {}).items() if k in model.model_fields}
        supplied = {k: v for k, v in values.items() if k != "expected_version"}
        body = model.model_validate(previous | supplied).model_dump()
        if old and old.get("legacy_profile_id"):
            locked = ("host", "port", "type", "database") if kind == "resources" else ("username", "auth_method")
            if any(body.get(k) != old.get(k) for k in locked) or any(body.get(k) for k in SECRET_FIELDS):
                raise AccessError("LEGACY_READ_ONLY", "引用账号/地址/凭据请在原连接中修改，再刷新引用核验")
            body.update({k: old[k] for k in ("legacy_profile_id", "legacy_fingerprint")})
        entity_id = entity_id or create_id or new_id(kind[:-1])
        if create_id and self.store.db.execute("SELECT 1 FROM entities WHERE id=?", (create_id,)).fetchone():
            raise AccessError("DUPLICATE_MAPPING", "配置 ID 已存在", 409)
        if kind == "sources":
            if old and (old["type"] != body["type"] or old.get("issuer", "") != body.get("issuer", "") or old["validation_mode"] != body["validation_mode"]):
                raise AccessError("IMMUTABLE_IDENTITY_NAMESPACE", "身份源类型、issuer 和验证方式不可改，请新增身份源")
            for field in ("issuer", "verify_endpoint", "introspection_endpoint"):
                if body.get(field) and not source_urls_validated:
                    await self.auth.validate_url(body[field])
            if body["enabled"] and body["validation_mode"] in {"external_http", "introspection"} and not body.get("service_secret") and not (old or {}).get("credential_ref"):
                raise AccessError("SERVICE_CREDENTIAL_REQUIRED", "在线验证服务必须配置服务认证")
        with self.store.transaction():
            if old and self.store.get(kind, entity_id)["version"] != expected:
                raise AccessError("VERSION_CONFLICT", "配置已变化", 409)
            if kind == "principals":
                if body["kind"] == "service" and (body["username"] or body["password"]):
                    raise AccessError("INVALID_ARGUMENT", "服务身份不能配置本地密码")
                from ..config import settings
                if body["username"] and body["username"] == settings.admin_user:
                    raise AccessError("RESERVED_IDENTITY", "本地管理员用户名保留，不能绑定外部身份")
                if body["password"]:
                    if len(body["password"]) < 12:
                        raise AccessError("WEAK_PASSWORD", "本地密码至少 12 位")
                    body["password_hash"] = PASSWORDS.hash(body.pop("password"))
                    self.store.db.execute("DELETE FROM local_sessions WHERE principal_id=?", (entity_id,))
                elif old and old.get("password_hash"):
                    body["password_hash"] = old["password_hash"]
                body.pop("password", None)
                if body["enabled"] and body["username"] and not body.get("password_hash"):
                    raise AccessError("PASSWORD_REQUIRED", "启用本地用户名需要配置密码")
            if kind == "bindings":
                source = self.store.get("sources", body["source_id"])
                self.store.get("principals", body["principal_id"])
                if not source["enabled"] and body["enabled"]:
                    raise AccessError("IDENTITY_DISABLED", "请先启用身份源")
            if kind == "accounts":
                resource = self.store.get("resources", body["resource_id"])
                if old and old["resource_id"] != body["resource_id"]:
                    raise AccessError("INVALID_ARGUMENT", "账号不能移动到另一个资源")
                if resource["type"] != "ssh" and body["auth_method"] != "password":
                    raise AccessError("INVALID_ARGUMENT", "数据库账号仅支持密码认证")
                changed = any(body.get(k) != (old or {}).get(k) for k in ("username", "tier", "auth_method")) or any(body.get(k) for k in SECRET_FIELDS)
                body["test_result"] = {} if changed else (old or {}).get("test_result", {})
                if body["enabled"]:
                    result = body["test_result"]
                    if not result.get("connected") or result.get("resource_version") != resource["version"]:
                        raise AccessError("ACCOUNT_NOT_VERIFIED", "请先保存禁用账号并完成连接与权限核验")
                    if not body["permission_confirmed"] or not body["permission_note"]:
                        raise AccessError("PERMISSION_CONFIRMATION_REQUIRED", "请确认目标权限并填写核验说明")
                    if body["tier"] == "normal" and not result.get("normal_safe"):
                        raise AccessError("UNSAFE_NORMAL_ACCOUNT", "检测到高权限或无法确认权限，不能发布为普通账号")
                    body["verified_by"] = actor
                    body["verified_at"] = time.time()
            if kind == "grants":
                self.store.get("principals", body["principal_id"])
                resource = self.store.get("resources", body["resource_id"])
                for field, tier in (("normal_account_id", "normal"), ("privileged_account_id", "privileged")):
                    if body.get(field):
                        account = self.store.get("accounts", body[field])
                        if account["resource_id"] != resource["id"] or account["tier"] != tier:
                            raise AccessError("ACCOUNT_SCOPE_MISMATCH", "账号必须属于当前资源且匹配普通/特权级别")
                        if body["enabled"] and not account["enabled"]:
                            raise AccessError("ACCOUNT_DISABLED", "授权引用的账号尚未启用")
                if body["expires_at"] is not None and body["expires_at"] <= time.time():
                    raise AccessError("INVALID_ARGUMENT", "授权有效期必须晚于当前时间")
            if kind in {"sources", "accounts"}:
                current_secret = self.store.secret(old["credential_ref"]) if old and old.get("credential_ref") else {}
                additions = {k: body.pop(k) for k in SECRET_FIELDS if k in body}
                current_secret.update({k: v for k, v in additions.items() if v})
                if current_secret:
                    body["credential_ref"] = self.store.save_secret(current_secret, (old or {}).get("credential_ref"))
                if kind == "accounts" and body["enabled"] and not current_secret and not body.get("legacy_profile_id"):
                    raise AccessError("CREDENTIAL_REQUIRED", "请配置目标账号凭据")
            result = self.store.put(kind, entity_id, body, expected)
            self.store.audit(actor, f"{kind}.update" if old else f"{kind}.create", entity_id,
                             {"before": public_entity(old) if old else None, "after": public_entity(result)})
        if self.on_change:
            self.on_change()
        return public_entity(result)

    def create_key(self, values: dict, actor: str) -> dict:
        body = KeyCreate.model_validate(values).model_dump()
        if set(body["scopes"]) - SCOPES:
            raise AccessError("INVALID_ARGUMENT", "不支持的 scope")
        with self.store.transaction():
            principal = self.store.get("principals", body["principal_id"])
            source = self.store.get("sources", body["source_id"])
            if not principal["enabled"] or not source["enabled"] or source["validation_mode"] != "managed":
                raise AccessError("IDENTITY_DISABLED", "主体或本地 Key 来源不可用")
            for resource_id in body["resource_ids"]:
                self.store.get("resources", resource_id)
            subject = "principal:" + principal["id"]
            binding = next((b for b in self.store.list("bindings") if b["source_id"] == source["id"] and b["subject"] == subject), None)
            if binding and (binding["principal_id"] != principal["id"] or not binding["enabled"]):
                raise AccessError("IDENTITY_DISABLED", "该 Key 的身份绑定已停用或被更改")
            if not binding:
                binding = self.store.put("bindings", new_id("binding"), {"source_id": source["id"], "subject": subject, "principal_id": principal["id"], "enabled": True})
            key_id = secrets.token_hex(12)
            plaintext = f"dbk_{key_id}_{secrets.token_urlsafe(32)}"
            record = self.store.put("keys", key_id, {"name": body["name"], "source_id": source["id"], "principal_id": principal["id"],
                "subject": subject, "scopes": body["scopes"], "resource_ids": body["resource_ids"], "enabled": True,
                "expires_at": time.time() + body["expires_in_days"] * 86400, "digest": self.store.digest(plaintext)})
            self.store.audit(actor, "keys.create", key_id, {"principal_id": principal["id"], "scopes": body["scopes"], "expires_at": record["expires_at"]})
        return {"key": plaintext, "record": public_entity(record), "warning": "仅显示一次，请安全保存；不会再次提供原文"}

    def revoke_key(self, key_id: str, actor: str):
        with self.store.transaction():
            item = self.store.get("keys", key_id)
            result = self.store.put("keys", key_id, item | {"enabled": False}, item["version"])
            self.store.audit(actor, "keys.revoke", key_id)
        if self.on_change:
            self.on_change()
        return public_entity(result)

    def authorize(self, ctx: AuthContext, resource_id: str, mode: str = "normal", action: str = "resources:read") -> tuple[dict, dict, dict]:
        if ctx.expires_at <= time.time():
            raise AccessError("TOKEN_EXPIRED", "身份凭据已到期", 401)
        principal = self.store.get("principals", ctx.principal_id, required=False)
        if not principal or not principal["enabled"]:
            raise AccessError("IDENTITY_DISABLED", "内部身份已停用", 403)
        if ctx.method != "local":
            source = self.store.get("sources", ctx.source_id, required=False)
            binding = self.store.get("bindings", ctx.binding_id, required=False)
            if not source or not source["enabled"] or not binding or not binding["enabled"] or binding["principal_id"] != ctx.principal_id:
                raise AccessError("IDENTITY_DISABLED", "身份来源或映射已失效", 403)
            if ctx.method == "api_key":
                key = self.store.get("keys", ctx.credential_id, required=False)
                if not key or not key["enabled"] or key["expires_at"] <= time.time():
                    raise AccessError("IDENTITY_DISABLED", "API-Key 已失效", 403)
        resource = self.store.get("resources", resource_id, required=False)
        grant = next((g for g in self.store.list("grants") if g["principal_id"] == ctx.principal_id and g["resource_id"] == resource_id), None)
        if not resource or not resource["enabled"] or not grant or not grant["enabled"] or (grant.get("expires_at") is not None and grant["expires_at"] <= time.time()) or (ctx.resource_ids and resource_id not in ctx.resource_ids):
            raise AccessError("RESOURCE_NOT_FOUND", "资源不存在或无访问权限", 404)
        if resource.get("legacy_profile_id"):
            if not self.legacy:
                raise AccessError("LEGACY_UNAVAILABLE", "原连接引用不可用", 503)
            self.legacy.checked(resource)
        if mode not in {"normal", "privileged"}:
            raise AccessError("INVALID_ARGUMENT", "账号模式无效")
        if action not in ctx.scopes or action not in grant["actions"]:
            raise AccessError("INSUFFICIENT_SCOPE", "当前凭据或授权不允许该操作", 403)
        if mode == "privileged" and (not grant["allow_privileged"] or "privilege:use" not in ctx.scopes or "privilege:use" not in grant["actions"]):
            raise AccessError("PRIVILEGE_DENIED", "此资源未允许使用特权账号", 403)
        account_id = grant.get("privileged_account_id" if mode == "privileged" else "normal_account_id")
        account = self.store.get("accounts", account_id or "", required=False)
        if not account or not account["enabled"] or account["resource_id"] != resource_id or account["tier"] != mode:
            raise AccessError("ACCOUNT_UNAVAILABLE", "账号当前不可用", 403)
        if account.get("test_result", {}).get("resource_version") != resource["version"]:
            raise AccessError("ACCOUNT_NOT_VERIFIED", "资源配置已变化，需要重新核验账号", 403)
        test = account.get("test_result", {})
        if not test.get("connected") or not account.get("permission_confirmed") or (mode == "normal" and not test.get("normal_safe")):
            raise AccessError("ACCOUNT_NOT_VERIFIED", "账号连接或权限核验未通过", 403)
        return resource, account, grant

    def projected(self, ctx: AuthContext, resource_id: str) -> dict:
        resource, _, _ = self.authorize(ctx, resource_id)
        modes = []
        for mode in ("normal", "privileged"):
            try:
                _, account, grant = self.authorize(ctx, resource_id, mode)
            except AccessError:
                continue
            actions = ["ssh.exec"] if resource["type"] == "ssh" else ["db.schema", "db.query"] + (["db.execute"] if mode == "privileged" else [])
            scopes = {"ssh.exec": "ssh:exec", "db.schema": "db:query", "db.query": "db:query", "db.execute": "db:write"}
            modes.append({"mode": mode, "account_alias": account["name"], "username": account["username"],
                          "actions": [a for a in actions if scopes[a] in ctx.scopes and scopes[a] in grant["actions"]], "limits": grant["limits"]})
        return {"id": resource["id"], "name": resource["name"], "type": resource["type"], "database": resource["database"],
                "default_mode": "normal", "access_modes": modes}

    def resources(self, ctx: AuthContext) -> list[dict]:
        result = []
        for resource in self.store.list("resources"):
            try:
                result.append(self.projected(ctx, resource["id"]))
            except AccessError as exc:
                if exc.status not in {403, 404}:
                    raise
        return result

    def me(self, ctx: AuthContext) -> dict:
        principal = self.store.get("principals", ctx.principal_id)
        return {"principal": {"id": principal["id"], "name": principal["name"], "kind": principal["kind"]},
                "source_id": ctx.source_id, "auth_method": ctx.method, "scopes": sorted(ctx.scopes), "expires_at": ctx.expires_at}

    def cursor(self, ctx: AuthContext, purpose: str, offset: int) -> str:
        import base64
        value = encode({"p": ctx.principal_id, "b": ctx.binding_id, "purpose": purpose, "offset": offset, "exp": time.time() + 600})
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=") + "." + self.store.digest(value)

    def offset(self, ctx: AuthContext, purpose: str, cursor: str) -> int:
        if not cursor:
            return 0
        import base64
        import json
        try:
            encoded, sig = cursor.split(".")
            value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            data = json.loads(value)
            if not secrets.compare_digest(sig, self.store.digest(value)) or data["p"] != ctx.principal_id or data["b"] != ctx.binding_id or data["purpose"] != purpose or data["exp"] <= time.time():
                raise ValueError()
            offset = int(data["offset"])
            if offset < 0:
                raise ValueError()
            return offset
        except (ValueError, TypeError, KeyError) as exc:
            raise AccessError("INVALID_CURSOR", "分页游标无效", 400) from exc
