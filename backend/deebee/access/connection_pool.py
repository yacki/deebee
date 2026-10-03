"""Idempotent projection of saved administrator connections into the resource pool.

Connections remain the credential source. Pool membership never creates an
external grant or substitutes a workbench connection test for permission checks.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .models import Account, Resource
from .store import encode


SUPPORTED = {"ssh", "mysql", "postgresql", "k8s"}


class ConnectionPool:
    def __init__(self, bridge):
        self.bridge = bridge
        self.store = bridge.store

    def sync(self, actor="system:connection-pool") -> int:
        # Same lock order as the compatibility bridge. The connection file is
        # the durable desired state; startup and periodic reconciliation repair
        # a crash between its commit and this SQLite transaction.
        with self.bridge.workbench._guard, self.store.transaction():
            records = {row["id"]: row for row in self.bridge.records()}
            changed = 0
            for row in records.values():
                changed += self._upsert(row, actor)
            for resource in self.store.list("resources"):
                if not resource.get("connection_sync") or resource.get("legacy_profile_id") in records:
                    continue
                if resource["connection_sync"]["state"] == "deleted":
                    continue
                metadata = resource["connection_sync"] | {"state": "deleted", "reason": "原连接已删除", "updated_by": actor}
                self.store.put("resources", resource["id"], resource | {"enabled": False, "connection_sync": metadata}, resource["version"])
                self._invalidate_accounts(resource["id"])
                self.store.audit(actor, "connection_pool.removed", resource["id"], {"profile_id": resource["legacy_profile_id"]})
                changed += 1
            return changed

    def _upsert(self, row, actor):
        suffix = self.store.digest(row["id"])[:24]
        resource_id, account_id = "legacy_resource_" + suffix, "legacy_account_" + suffix
        old = self.store.get("resources", resource_id, required=False)
        if old and old.get("deleted_at"):
            return 0
        fingerprint = self.bridge.fingerprint(row)
        compatible_fingerprints = {fingerprint, self.store.digest(encode(row))}
        reason = self._reason(row)
        state = "unsupported" if row["driver"] not in SUPPORTED else "needs_configuration" if reason else "synced"
        if (old and old.get("legacy_fingerprint") == fingerprint and old["name"] == row["name"]
                and old.get("connection_sync", {}).get("state") == state):
            return 0
        source_changed = not old or old.get("legacy_fingerprint") not in compatible_fingerprints or old.get("connection_sync", {}).get("state") == "deleted"
        fields = {k: v for k, v in (old or {}).items() if k in Resource.model_fields}
        fields.update(name=row["name"], type=row["driver"], host=row["host"], port=row["port"],
                      database=row.get("default_database", ""), schemas=[row.get("default_schema") or "public"])
        if row["driver"] == "k8s":
            options = row.get("options", {})
            fields["namespaces"] = [options.get("namespace") or "default"]
            parsed = urlsplit(row["host"] if "://" in row["host"] else "https://" + row["host"])
            fields["host"] = parsed.hostname or row["host"]
            fields["port"] = parsed.port or row["port"]
            fields["tls"] = parsed.scheme != "http"
        if row["driver"] == "ssh":
            fields["host_key"] = row.get("options", {}).get("host_key_fingerprint", "")
        if source_changed or reason:
            fields["enabled"] = False
        metadata = {"state": state, "reason": reason, "created_by": (old or {}).get("connection_sync", {}).get("created_by", actor), "updated_by": actor}
        provenance = {"legacy_profile_id": row["id"], "legacy_fingerprint": fingerprint, "connection_sync": metadata}
        resource = self.store.put("resources", resource_id, Resource.model_validate(fields).model_dump() | provenance, (old or {}).get("version"))
        if source_changed:
            self._invalidate_accounts(resource_id)
        else:
            # Renaming a connection does not invalidate an otherwise identical
            # account verification, but it does advance the resource version.
            for account in self.store.list("accounts"):
                test = account.get("test_result", {})
                if account["resource_id"] != resource_id:
                    continue
                changes = {}
                if test.get("resource_version") == old["version"]:
                    changes["test_result"] = test | {"resource_version": resource["version"]}
                if account.get("legacy_profile_id") == row["id"] and account.get("legacy_fingerprint") in compatible_fingerprints:
                    changes.update(provenance)
                if changes:
                    self.store.put("accounts", account["id"], account | changes, account["version"])
        if row["driver"] in SUPPORTED:
            account = self.store.get("accounts", account_id, required=False)
            if not account or source_changed:
                fields = {k: v for k, v in (account or {}).items() if k in Account.model_fields}
                fields.update(resource_id=resource_id, name=(account or {}).get("name", "前台连接账号（待核验）"),
                              username=row["user"], auth_method=row.get("options", {}).get("auth_method", "password") if row["driver"] == "ssh" else "password",
                              tier=(account or {}).get("tier", "privileged"), enabled=False, permission_confirmed=False, permission_note="")
                body = Account.model_validate(fields).model_dump(exclude={"password", "private_key", "passphrase"})
                self.store.put("accounts", account_id, body | provenance | {"test_result": {}}, (account or {}).get("version"))
        self.store.audit(actor, "connection_pool.synced", resource_id, {"profile_id": row["id"], "state": state, "source_changed": source_changed})
        return 1

    def _invalidate_accounts(self, resource_id):
        for account in self.store.list("accounts"):
            if account["resource_id"] == resource_id:
                self.store.put("accounts", account["id"], account | {
                    "enabled": False, "permission_confirmed": False, "permission_note": "", "test_result": {}
                }, account["version"])

    @staticmethod
    def _reason(row):
        if row["driver"] not in SUPPORTED:
            return "已加入资源管理；此连接类型暂不支持 MCP 操作"
        options = row.get("options", {})
        if options.get("ssh_tunnel") or options.get("proxy_enabled"):
            return "已加入资源管理；MCP 执行端暂不支持此连接的隧道或代理"
        if row["driver"] == "k8s":
            parsed = urlsplit(row["host"] if "://" in row["host"] else "https://" + row["host"])
            if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
                return "K8s MCP 需要不带路径、认证信息或查询参数的 API Server 地址"
        if row["driver"] == "k8s" and row.get("options", {}).get("k8s_auth_method", "token") != "token":
            return "K8s MCP 当前使用 Bearer Token；kubeconfig 连接仍可在工作台使用"
        if row["driver"] in {"mysql", "postgresql"} and not row.get("default_database"):
            return "已加入资源管理；请在前台连接中指定默认数据库后启用 MCP 资源"
        if row["driver"] == "ssh" and not options.get("host_key_fingerprint", "").startswith("SHA256:"):
            return "已加入资源管理；请在前台连接中核验 SSH 主机指纹"
        return ""
