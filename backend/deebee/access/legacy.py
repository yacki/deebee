"""Read-only compatibility bridge: legacy profiles remain the source of truth.

Verified snapshots never overwrite the original profile and never copy its
credentials. Drift invalidates external access until an explicit refresh/test.
"""
from __future__ import annotations

import copy

from .models import AccessError, Resource, Account
from .store import encode


class LegacyBridge:
    def __init__(self, store, workbench):
        self.store, self.workbench = store, workbench

    def records(self):
        with self.workbench._guard:
            return copy.deepcopy(list(self.workbench._records.values()))

    def record(self, profile_id):
        row = next((r for r in self.records() if r["id"] == profile_id), None)
        if not row:
            raise AccessError("LEGACY_PROFILE_CHANGED", "原连接已删除或不可用，外部访问已阻断", 403)
        return row

    def fingerprint(self, row):
        # Keyed digest covers credentials too, without exposing a password hash.
        return self.store.digest(encode(row))

    def checked(self, entity):
        row = self.record(entity["legacy_profile_id"])
        if self.fingerprint(row) != entity["legacy_fingerprint"]:
            raise AccessError("LEGACY_PROFILE_CHANGED", "原连接已变化，请刷新引用并重新核验账号", 403)
        return row

    def credentials(self, account):
        row = self.checked(account)
        return {"password": row.get("password", ""), "private_key": row.get("private_key", ""), "passphrase": ""}

    def preview(self):
        result = []
        for row in self.records():
            if row["driver"] not in {"ssh", "mysql", "postgresql"}:
                continue
            options = row.get("options", {})
            reason = "V1 不迁移隧道/代理连接；请另建直连资源" if options.get("ssh_tunnel") or options.get("proxy_enabled") else ""
            if row["driver"] != "ssh" and not row.get("default_database"):
                reason = "请先在原连接指定一个默认数据库"
            result.append({"id": row["id"], "name": row["name"], "type": row["driver"], "host": row["host"],
                           "port": row["port"], "username": row["user"], "database": row.get("default_database", ""),
                           "fingerprint": self.fingerprint(row), "supported": not reason, "reason": reason})
        return {"items": result, "warning": "只建立禁用引用，不改写旧连接、不复制凭据、不新增授权。原连接变更将阻断引用，需重新同步和核验。"}

    def refresh(self, profile_id, fingerprint, actor):
        with self.workbench._guard, self.store.transaction():
            item = next((r for r in self.preview()["items"] if r["id"] == profile_id), None)
            if not item or not item["supported"]:
                raise AccessError("MIGRATION_UNSUPPORTED", "该原连接不能迁移到 V1")
            if item["fingerprint"] != fingerprint:
                raise AccessError("VERSION_CONFLICT", "原连接已变化，请重新预览", 409)
            row = self.record(profile_id)
            suffix = self.store.digest(profile_id)[:24]
            resource_id, account_id = "legacy_resource_" + suffix, "legacy_account_" + suffix
            old_resource = self.store.get("resources", resource_id, required=False)
            old_account = self.store.get("accounts", account_id, required=False)
            fields = {k: v for k, v in (old_resource or {}).items() if k in Resource.model_fields}
            fields.update(name=row["name"], type=row["driver"], host=row["host"], port=row["port"],
                          database=row.get("default_database", ""), enabled=False)
            fields.setdefault("schemas", [row.get("default_schema") or "public"])
            fields.setdefault("host_key", row.get("options", {}).get("host_key_fingerprint", ""))
            resource = Resource.model_validate(fields).model_dump()
            provenance = {"legacy_profile_id": profile_id, "legacy_fingerprint": fingerprint}
            resource = self.store.put("resources", resource_id, resource | provenance, (old_resource or {}).get("version"))
            account = Account(name="原连接账号（待核验）", resource_id=resource_id, username=row["user"],
                              tier=(old_account or {}).get("tier", "privileged"),
                              auth_method=row.get("options", {}).get("auth_method", "password")).model_dump()
            for key in ("password", "private_key", "passphrase"):
                account.pop(key)
            account = self.store.put("accounts", account_id, account | provenance | {"test_result": {}}, (old_account or {}).get("version"))
            self.store.audit(actor, "legacy.reference.refresh", profile_id, {"resource_id": resource_id, "account_id": account_id})
        from .service import public_entity
        return {"resource": public_entity(resource), "account": public_entity(account), "published": False}
