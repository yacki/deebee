"""Atomic, secret-free configuration import, including a rollback-only preview."""
from __future__ import annotations

import re

from .models import AccessError, ENTITY_MODELS
from .service import SECRET_FIELDS, INTERNAL_FIELDS, META_FIELDS, public_entity


NAMES = {"identity-sources": "sources", "principals": "principals", "resources": "resources",
         "accounts": "accounts", "identity-bindings": "bindings", "access-grants": "grants"}


class PreviewRollback(Exception):
    pass


async def import_configuration(service, config: dict, actor: str, *, preview: bool):
    if config.get("version") != 1 or set(config) - {"version", *NAMES}:
        raise AccessError("INVALID_ARGUMENT", "配置版本或分组无效")
    items, seen = [], set()
    for name, kind in NAMES.items():
        group = config.get(name, [])
        if not isinstance(group, list) or len(group) > 500:
            raise AccessError("INVALID_ARGUMENT", "配置分组必须是数组且不超过 500 条")
        for item in group:
            if not isinstance(item, dict):
                raise AccessError("INVALID_ARGUMENT", "配置记录必须是对象")
            entity_id = item.get("id", "")
            if not isinstance(entity_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", entity_id) or entity_id in seen:
                raise AccessError("INVALID_ARGUMENT", "配置 ID 缺失、重复或格式无效")
            seen.add(entity_id)
            fields = ENTITY_MODELS[kind].model_fields
            if set(item) & (SECRET_FIELDS | INTERNAL_FIELDS) or set(item) - set(fields) - META_FIELDS - {"has_credentials", "test_result", "verified_at", "verified_by", "legacy_profile_id"}:
                raise AccessError("INVALID_ARGUMENT", "导入不接受秘密、内部字段或未知字段")
            values = {k: v for k, v in item.items() if k in fields}
            old = service.store.get(kind, entity_id, required=False)
            if item.get("legacy_profile_id") and (not old or old.get("legacy_profile_id") != item["legacy_profile_id"]):
                raise AccessError("LEGACY_REFERENCE_REQUIRED", "原连接引用需先通过迁移预览建立，不能通过配置伪造")
            if old and item.get("version") != old["version"]:
                raise AccessError("VERSION_CONFLICT", "导入版本已过期，请重新导出", 409)
            if kind == "sources":
                for field in ("issuer", "verify_endpoint", "introspection_endpoint"):
                    value = values.get(field, (old or {}).get(field))
                    if value:
                        await service.auth.validate_url(value)
            items.append((kind, entity_id, values, old))
    if len(items) > 1000:
        raise AccessError("INVALID_ARGUMENT", "一次最多导入 1000 条配置")
    changes = []
    try:
        # No asynchronous network work occurs in this transaction. save() only
        # performs synchronous validation/storage when source URLs are prechecked.
        with service.store.transaction():
            for kind, entity_id, values, old in items:
                if old and old["version"] != service.store.get(kind, entity_id)["version"]:
                    raise AccessError("VERSION_CONFLICT", "导入过程中配置发生变化", 409)
                if old and all(old.get(k) == v for k, v in values.items()):
                    action = "unchanged"
                else:
                    await service.save(kind, values | ({"expected_version": old["version"]} if old else {}), actor,
                                       old["id"] if old else None, create_id=None if old else entity_id,
                                       source_urls_validated=True)
                    action = "update" if old else "create"
                changes.append({"kind": kind, "id": entity_id, "action": action})
            service.store.audit(actor, "config.import", "access", {"count": len(changes)})
            if preview:
                raise PreviewRollback()
    except PreviewRollback:
        pass
    return {"changes": changes, "updated": sum(c["action"] != "unchanged" for c in changes), "preview": preview,
            "warning": "不删除记录、不导入秘密。新增身份源/账号可先禁用导入，再配置凭据和核验；更新必须携带最新 version。"}
