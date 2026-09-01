from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class ConnectionStore:
    """Small encrypted JSON store for user-managed connection secrets."""

    def __init__(
        self, path: Path, secret: str, defaults: list[dict[str, Any]] | None = None
    ) -> None:
        self.path = path
        self._guard = threading.RLock()
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._cipher = Fernet(key)
        if not self.path.exists():
            self._write(defaults or [])

    def _encrypt(self, password: str) -> str:
        return self._cipher.encrypt(password.encode("utf-8")).decode("ascii")

    def _decrypt(self, password: str) -> str:
        try:
            return self._cipher.decrypt(password.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise RuntimeError(
                "无法解密已保存的连接密钥，请确认 DEEBEE_TOKEN_SECRET 未发生变化"
            ) from exc

    def _serialized(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        encrypted: list[dict[str, Any]] = []
        for record in records:
            item = dict(record)
            item["password"] = self._encrypt(str(item.get("password", "")))
            item["private_key"] = self._encrypt(str(item.get("private_key", "")))
            encrypted.append(item)
        return {"version": 2, "connections": encrypted}

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self._serialized(records), ensure_ascii=False, indent=2
        ) + "\n"
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def list(self) -> list[dict[str, Any]]:
        with self._guard:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                stored = payload["connections"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError("数据库连接配置文件损坏，无法读取") from exc
            if not isinstance(stored, list):
                raise RuntimeError("数据库连接配置文件格式无效")
            records: list[dict[str, Any]] = []
            for value in stored:
                if not isinstance(value, dict):
                    raise RuntimeError("数据库连接配置文件格式无效")
                item = dict(value)
                item["password"] = self._decrypt(str(item.get("password", "")))
                private_key = item.get("private_key")
                item["private_key"] = self._decrypt(str(private_key)) if private_key else ""
                records.append(item)
            return records

    def replace(self, records: list[dict[str, Any]]) -> None:
        with self._guard:
            self._write(records)
