from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from .audit import RESPONSE_PREVIEW_BYTES, preview_value
from .models import AccessError


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def private_key_file(path: Path, factory) -> bytes:
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        value = factory()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as handle:
            handle.write(value + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return value


class AccessStore:
    """Single-instance transactional control plane; secrets use an independent key.

    Typed entities use JSON bodies with SQL uniqueness indexes and an explicit
    foreign-key reference table. Each admin mutation and its audit commit together.
    """

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self.cipher = Fernet(private_key_file(directory / "credentials.key", Fernet.generate_key))
        self.pepper = private_key_file(directory / "api-key.pepper", lambda: secrets.token_hex(32).encode())
        self.lock = threading.RLock()
        self.depth = 0
        self.db = sqlite3.connect(directory / "access.sqlite3", check_same_thread=False, isolation_level=None)
        os.chmod(directory / "access.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
                version INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
            CREATE UNIQUE INDEX IF NOT EXISTS unique_binding ON entities(
                json_extract(body,'$.source_id'),json_extract(body,'$.subject')) WHERE kind='bindings';
            CREATE UNIQUE INDEX IF NOT EXISTS unique_grant ON entities(
                json_extract(body,'$.principal_id'),json_extract(body,'$.resource_id')) WHERE kind='grants';
            CREATE UNIQUE INDEX IF NOT EXISTS unique_username ON entities(json_extract(body,'$.username'))
                WHERE kind='principals' AND json_extract(body,'$.username') != '';
            CREATE TABLE IF NOT EXISTS entity_refs (
                owner_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
                target_id TEXT REFERENCES entities(id) ON DELETE RESTRICT,
                field TEXT NOT NULL, PRIMARY KEY(owner_id,field)
            );
            CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, binding_id TEXT NOT NULL,
                idem TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, body TEXT NOT NULL,
                payload_cipher TEXT NOT NULL, result_cipher TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_sessions (
                digest TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES entities(id),
                expires_at REAL NOT NULL, csrf TEXT NOT NULL
            );
        """)
        self._migrate_audit()
        with self.transaction():
            if not self.get("sources", "local_keys", required=False):
                self.put("sources", "local_keys", {"name": "本地 API-Key", "type": "api_key", "validation_mode": "managed", "enabled": True})
    def recover_executions(self):
        # Only call after acquiring the process-wide singleton lock. A second
        # worker inspecting this store must never change the first worker's jobs.
        with self.transaction():
            for row in self.db.execute("SELECT id,body FROM executions").fetchall():
                body = json.loads(row["body"])
                if body["status"] in {"queued", "running", "cancel_requested"}:
                    body.update(status="unknown", error={"code": "PROCESS_RESTARTED", "message": "服务重启，远端结果需核实，未自动重放"})
                    self.db.execute("UPDATE executions SET body=?,updated_at=? WHERE id=?", (encode(body), time.time(), row["id"]))

    def cleanup(self):
        now = time.time()
        with self.transaction():
            self.db.execute("DELETE FROM local_sessions WHERE expires_at<?", (now,))
            # Keep execution/idempotency tombstones for 90 days; clear sensitive
            # payloads/results after 7 days without making an old key executable.
            self.db.execute("UPDATE executions SET payload_cipher='',result_cipher=NULL WHERE updated_at<? AND json_extract(body,'$.status') IN ('succeeded','failed','cancelled','unknown')", (now - 7 * 86400,))
            self.db.execute("DELETE FROM executions WHERE updated_at<? AND json_extract(body,'$.status') IN ('succeeded','failed','cancelled','unknown')", (now - 90 * 86400,))
            # Audit previews are the durable management archive. Zero means keep
            # indefinitely; operators can explicitly opt into a retention limit.
            try:
                audit_days = max(0, int(os.getenv("DEEBEE_AUDIT_RETENTION_DAYS", "0")))
            except ValueError:
                audit_days = 0
            if audit_days:
                self.db.execute("DELETE FROM audit WHERE at<?", (now - audit_days * 86400,))

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self.db.execute("BEGIN IMMEDIATE")
            self.depth += 1
            try:
                yield
                if outer:
                    self.db.execute("COMMIT")
            except Exception:
                if outer:
                    self.db.execute("ROLLBACK")
                raise
            finally:
                self.depth -= 1

    def get(self, kind: str, entity_id: str, *, required: bool = True) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM entities WHERE id=? AND kind=?", (entity_id, kind)).fetchone()
        if row is None:
            if required:
                raise AccessError("NOT_FOUND", "记录不存在", 404)
            return None
        return json.loads(row["body"]) | {"id": row["id"], "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list(self, kind: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id FROM entities WHERE kind=? ORDER BY created_at,id", (kind,)).fetchall()
            return [self.get(kind, row["id"]) for row in rows]

    def put(self, kind: str, entity_id: str, body: dict, expected_version: int | None = None) -> dict:
        with self.transaction():
            old = self.get(kind, entity_id, required=False)
            if old and expected_version != old["version"]:
                raise AccessError("VERSION_CONFLICT", "配置已变化，请刷新后重试", 409)
            clean = {k: v for k, v in body.items() if k not in {"id", "version", "created_at", "updated_at"}}
            now = time.time()
            try:
                if old:
                    self.db.execute("UPDATE entities SET body=?,version=version+1,updated_at=? WHERE id=?", (encode(clean), now, entity_id))
                else:
                    self.db.execute("INSERT INTO entities VALUES(?,?,?,1,?,?)", (entity_id, kind, encode(clean), now, now))
            except sqlite3.IntegrityError as exc:
                raise AccessError("DUPLICATE_MAPPING", "用户名、身份绑定或资源授权已存在", 409) from exc
            refs = {key: value for key, value in clean.items() if key in {
                "source_id", "principal_id", "resource_id", "normal_account_id", "privileged_account_id"
            } and value}
            self.db.execute("DELETE FROM entity_refs WHERE owner_id=?", (entity_id,))
            for key, value in refs.items():
                self.db.execute("INSERT INTO entity_refs VALUES(?,?,?)", (entity_id, value, key))
            return self.get(kind, entity_id)

    def _migrate_audit(self):
        columns = {
            "request_id": "TEXT NOT NULL DEFAULT ''",
            "surface": "TEXT NOT NULL DEFAULT ''",
            "actor_name": "TEXT NOT NULL DEFAULT ''",
            "actor_kind": "TEXT NOT NULL DEFAULT ''",
            "auth_method": "TEXT NOT NULL DEFAULT ''",
            "source_id": "TEXT NOT NULL DEFAULT ''",
            "subject": "TEXT NOT NULL DEFAULT ''",
            "credential_id": "TEXT NOT NULL DEFAULT ''",
            "client_id": "TEXT NOT NULL DEFAULT ''",
            "method": "TEXT NOT NULL DEFAULT ''",
            "path": "TEXT NOT NULL DEFAULT ''",
            "operation": "TEXT NOT NULL DEFAULT ''",
            "resource_id": "TEXT NOT NULL DEFAULT ''",
            "account_id": "TEXT NOT NULL DEFAULT ''",
            "account_username": "TEXT NOT NULL DEFAULT ''",
            "mode": "TEXT NOT NULL DEFAULT ''",
            "status_code": "INTEGER NOT NULL DEFAULT 0",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            "client_ip": "TEXT NOT NULL DEFAULT ''",
            "user_agent": "TEXT NOT NULL DEFAULT ''",
            "request_preview": "TEXT NOT NULL DEFAULT ''",
            "response_preview": "TEXT NOT NULL DEFAULT ''",
            "request_truncated": "INTEGER NOT NULL DEFAULT 0",
            "response_truncated": "INTEGER NOT NULL DEFAULT 0",
            "error_code": "TEXT NOT NULL DEFAULT ''",
        }
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(audit)").fetchall()}
        with self.transaction():
            for name, definition in columns.items():
                if name not in existing:
                    self.db.execute(f"ALTER TABLE audit ADD COLUMN {name} {definition}")
            self.db.execute("CREATE INDEX IF NOT EXISTS audit_at ON audit(at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS audit_actor ON audit(actor)")
            self.db.execute("CREATE INDEX IF NOT EXISTS audit_operation ON audit(operation)")
            self.db.execute("CREATE INDEX IF NOT EXISTS audit_surface ON audit(surface)")
            # Old rows cannot gain request/response data retroactively. Label
            # them honestly so the UI does not confuse them with new events.
            self.db.execute("UPDATE audit SET actor_name=actor WHERE actor_name='' AND surface=''")
            self.db.execute("UPDATE audit SET operation=action WHERE operation='' AND surface=''")
            self.db.execute("UPDATE audit SET actor_kind='unknown',auth_method='legacy_unknown',surface='legacy_audit' WHERE surface=''")

    def audit(self, actor: str, action: str, object_id: str, detail: dict | None = None, **fields):
        names = (
            "request_id", "surface", "actor_name", "actor_kind", "auth_method", "source_id", "subject",
            "credential_id", "client_id", "method", "path", "operation", "resource_id", "account_id",
            "account_username", "mode", "status_code", "duration_ms", "client_ip", "user_agent",
            "request_preview", "response_preview", "request_truncated", "response_truncated", "error_code",
        )
        values = {name: fields.get(name, "") for name in names}
        values["actor_name"] = values["actor_name"] or actor
        values["operation"] = values["operation"] or action
        if not values["surface"]:
            values["surface"] = "control_plane"
            values["auth_method"] = values["auth_method"] or "control_plane_event"
            values["status_code"] = values["status_code"] or 200
            if not values["response_preview"] and detail:
                values["response_preview"], values["response_truncated"] = preview_value(detail, limit=RESPONSE_PREVIEW_BYTES)
            principal = self.get("principals", actor, required=False)
            if principal:
                values["actor_name"] = principal.get("name") or principal.get("username") or actor
                values["actor_kind"] = principal.get("kind", "unknown")
            elif actor == os.getenv("DEEBEE_ADMIN_USER", "admin"):
                values["actor_kind"] = "local_admin"
            else:
                values["actor_kind"] = values["actor_kind"] or "unknown"
        values["request_truncated"] = int(bool(values["request_truncated"]))
        values["response_truncated"] = int(bool(values["response_truncated"]))
        values["status_code"] = int(values["status_code"] or 0)
        values["duration_ms"] = int(values["duration_ms"] or 0)
        with self.lock:
            columns = "at,actor,action,object_id,detail," + ",".join(names)
            placeholders = ",".join("?" for _ in range(5 + len(names)))
            self.db.execute(
                f"INSERT INTO audit({columns}) VALUES({placeholders})",
                (time.time(), actor, action, object_id, encode(detail or {}), *(values[name] for name in names)),
            )

    def audit_event(self, audit_id: int) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM audit WHERE id=?", (audit_id,)).fetchone()
        if row is None:
            raise AccessError("NOT_FOUND", "审计记录不存在", 404)
        return dict(row) | {"detail": json.loads(row["detail"]), "request_truncated": bool(row["request_truncated"]),
                            "response_truncated": bool(row["response_truncated"])}

    def audits(
        self,
        limit: int = 100,
        before: int | None = None,
        *,
        actor: str = "",
        operation: str = "",
        surface: str = "",
        status_code: int | None = None,
        from_at: float | None = None,
        to_at: float | None = None,
    ) -> list[dict]:
        clauses, values = ["id<?"], [before or 2**63 - 1]
        for column, value in (("actor", actor), ("operation", operation), ("surface", surface)):
            if value:
                clauses.append(f"{column} LIKE ?")
                values.append("%" + value + "%")
        if status_code is not None:
            clauses.append("status_code=?")
            values.append(status_code)
        if from_at is not None:
            clauses.append("at>=?")
            values.append(from_at)
        if to_at is not None:
            clauses.append("at<=?")
            values.append(to_at)
        values.append(min(max(limit, 1), 500))
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM audit WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", values
            ).fetchall()
        return [dict(row) | {"detail": json.loads(row["detail"]), "request_truncated": bool(row["request_truncated"]),
                             "response_truncated": bool(row["response_truncated"])} for row in rows]

    def secret(self, secret_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT ciphertext FROM credentials WHERE id=?", (secret_id,)).fetchone()
        if row is None:
            raise AccessError("CREDENTIAL_UNAVAILABLE", "连接凭据不可用", 503)
        return self.decrypt(row["ciphertext"])

    def save_secret(self, value: dict, secret_id: str | None = None) -> str:
        secret_id = secret_id or new_id("sec")
        with self.lock:
            self.db.execute("INSERT INTO credentials VALUES(?,?) ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext",
                            (secret_id, self.encrypt(value)))
        return secret_id

    def encrypt(self, value: Any) -> str:
        return self.cipher.encrypt(encode(value).encode()).decode()

    def decrypt(self, value: str) -> Any:
        return json.loads(self.cipher.decrypt(value.encode()))

    def digest(self, value: str) -> str:
        return hmac.new(self.pepper, value.encode(), hashlib.sha256).hexdigest()

    def close(self):
        with self.lock:
            self.db.close()
