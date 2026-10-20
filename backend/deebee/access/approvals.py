"""Approval policy runs before any target connection or execution is started."""
from __future__ import annotations

import hashlib
import re
from typing import Literal

import sqlglot
from sqlglot import exp
from pydantic import Field, model_validator

from .models import AccessError, Model
from .store import encode, new_id


BUILTINS = {
    "db_write": ("database", "数据库数据变更", "INSERT、UPDATE、DELETE 等数据变更。"),
    "db_ddl": ("database", "数据库结构变更", "CREATE、ALTER、DROP、TRUNCATE 等结构变更。"),
    "db_unbounded": ("database", "无 WHERE 的更新和删除", "按 SQL 语法树判断，注释和字符串不能充当 WHERE。"),
    "ssh_delete": ("ssh", "文件删除和磁盘写入", "rm、rmdir、shred、dd、mkfs、wipefs 等命令。"),
    "ssh_privilege": ("ssh", "提权和权限修改", "sudo、su、chmod、chown、用户及权限管理。"),
    "ssh_system": ("ssh", "系统和服务变更", "关机重启、服务管理、进程终止、防火墙及软件包管理。"),
    "ssh_other": ("ssh", "其他 Shell 命令（保守兜底）", "仅放行少量固定只读命令；复合命令、脚本、解释器、重定向和无法判断的输入都需审批，防止命令变形绕过。"),
}
TEMPLATES = [
    {"template_id": key, "name": name, "description": description, "protocol": protocol,
     "match_type": "builtin", "pattern": key, "enabled": True, "resource_ids": [], "expires_minutes": 30}
    for key, (protocol, name, description) in BUILTINS.items()
]
SIMPLE_READ = re.compile(r"(?:id(?: -[ugGnrz]+)?|whoami|pwd|uname(?: -[asnrvmpio]+)?|uptime|df(?: -[hikPT]+)?|free(?: -[bkmghw]+)?|ls)\Z")


class ApprovalRule(Model):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    protocol: Literal["ssh", "database"]
    match_type: Literal["builtin", "contains", "prefix", "all"] = "contains"
    pattern: str = Field(default="", max_length=500)
    resource_ids: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    expires_minutes: int = Field(default=30, ge=1, le=1440)

    @model_validator(mode="after")
    def matcher(self):
        if self.match_type == "builtin":
            if self.pattern not in BUILTINS or BUILTINS[self.pattern][0] != self.protocol:
                raise ValueError("内置规则与协议不匹配")
        elif self.match_type != "all" and not self.pattern.strip():
            raise ValueError("请填写匹配内容")
        self.resource_ids = sorted(set(self.resource_ids))
        return self


class RuleChange(ApprovalRule):
    expected_version: int | None = None


class Decision(Model):
    decision: Literal["approve", "reject"]
    comment: str = Field(min_length=1, max_length=2000)
    request_hash: str = Field(min_length=64, max_length=64)


class ApprovalPolicy:
    def __init__(self, store):
        self.store = store
        # A migration marker prevents deleted/disabled defaults reappearing.
        with store.transaction():
            if not store.get("migrations", "approval_defaults_v1", required=False):
                for template in TEMPLATES:
                    self.save({k: v for k, v in template.items() if k != "template_id"}, "system")
                store.put("migrations", "approval_defaults_v1", {"installed": True})

    def rules(self, resource=None):
        rules = [r for r in self.store.list("approval_rules") if not r.get("deleted_at")]
        if resource is None:
            return rules
        protocol = "ssh" if resource["type"] == "ssh" else "database" if resource["type"] in {"mysql", "postgresql"} else ""
        return [r for r in rules if r["enabled"] and r["protocol"] == protocol and
                (not r["resource_ids"] or resource["id"] in r["resource_ids"])]

    def revision(self, resource):
        return hashlib.sha256(encode([(r["id"], r["version"]) for r in self.rules(resource)]).encode()).hexdigest()

    def save(self, values, actor, rule_id=None, *, create_id=None):
        with self.store.transaction():
            old = self.store.get("approval_rules", rule_id) if rule_id else None
            if old and (old.get("deleted_at") or values.get("expected_version") != old["version"]):
                raise AccessError("VERSION_CONFLICT", "规则已变化，请刷新后重试", 409)
            fields = ApprovalRule.model_fields
            supplied = {k: v for k, v in values.items() if k != "expected_version"}
            body = ApprovalRule.model_validate({k: v for k, v in (old or {}).items() if k in fields} | supplied).model_dump()
            for resource_id in body["resource_ids"]:
                resource = self.store.get("resources", resource_id)
                allowed = {"ssh"} if body["protocol"] == "ssh" else {"mysql", "postgresql"}
                if resource.get("deleted_at") or resource["type"] not in allowed:
                    raise AccessError("INVALID_ARGUMENT", "规则只能关联相同协议的有效资源")
            if not old and len(self.rules()) >= 200:
                raise AccessError("RULE_LIMIT", "最多配置 200 条审批规则")
            result = self.store.put("approval_rules", rule_id or create_id or new_id("rule"), body, old["version"] if old else None)
            self.store.audit(actor, "approval_rule.update" if old else "approval_rule.create", result["id"], body)
            return result

    def delete(self, rule_id, expected_version, actor):
        import time
        with self.store.transaction():
            old = self.store.get("approval_rules", rule_id)
            if old.get("deleted_at") or old["version"] != expected_version:
                raise AccessError("VERSION_CONFLICT", "规则已变化，请刷新后重试", 409)
            self.store.put("approval_rules", rule_id, old | {"deleted_at": time.time(), "enabled": False}, expected_version)
            self.store.audit(actor, "approval_rule.delete", rule_id, {"name": old["name"]})
        return {"deleted": True}

    def match(self, resource, tool, body):
        if tool not in {"ssh.exec", "db.query", "db.execute"}:
            return []
        rules = self.rules(resource)
        if not rules:
            return []
        text = body["command"] if tool == "ssh.exec" else body["sql"]
        tree = None
        if tool != "ssh.exec":
            tree = sqlglot.parse_one(text, read="mysql" if resource["type"] == "mysql" else "postgres")
            # Canonical AST text excludes comments, retaining raw text as a second
            # candidate for administrator-defined literal matchers.
            canonical = tree.sql(comments=False)
        else:
            canonical = text
        candidates = [" ".join(value.lower().split()) for value in (text, canonical)]
        matched = []
        for rule in rules:
            pattern = " ".join(rule["pattern"].lower().split())
            kind = rule["match_type"]
            hit = kind == "all" or kind == "contains" and any(pattern in value for value in candidates) or kind == "prefix" and any(value.startswith(pattern) for value in candidates)
            if kind == "builtin":
                if pattern == "db_write":
                    hit = any(isinstance(n, (exp.Insert, exp.Update, exp.Delete, exp.Merge)) for n in tree.walk())
                elif pattern == "db_ddl":
                    hit = isinstance(tree, (exp.Create, exp.Alter, exp.Drop, exp.TruncateTable))
                elif pattern == "db_unbounded":
                    hit = any(isinstance(n, (exp.Update, exp.Delete)) and not n.args.get("where") for n in tree.walk())
                elif pattern == "ssh_other":
                    hit = not SIMPLE_READ.fullmatch(text.strip()) or body.get("elevation") == "sudo"
                else:
                    commands = {
                        "ssh_delete": "rm|rmdir|shred|dd|mkfs(?:\\.[a-z0-9]+)?|wipefs|truncate",
                        "ssh_privilege": "sudo|su|chmod|chown|chgrp|useradd|userdel|usermod|groupadd|groupdel|passwd|visudo|setfacl",
                        "ssh_system": "shutdown|reboot|halt|poweroff|systemctl|service|kill|killall|pkill|iptables|nft|ufw|apt|apt-get|yum|dnf|rpm",
                    }
                    hit = bool(re.search(r"(?:^|[\s/;&|()])(?:" + commands[pattern] + r")(?:\s|$)", text, re.I))
                    if pattern == "ssh_privilege" and body.get("elevation") == "sudo":
                        hit = True
            if hit:
                matched.append({k: rule[k] for k in ("id", "name", "version", "expires_minutes")})
        return matched
