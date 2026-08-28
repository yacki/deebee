from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .connection_store import ConnectionStore
from .config import settings
from .mysql import DeeBeeError, MySQLWorkbench, Profile, workbench as mysql_workbench
from .postgres import (
    PostgresProfile,
    PostgresWorkbench,
    workbench as postgres_workbench,
)


def _environment_connections() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if settings.mysql_enabled:
        records.append(
            {
                "id": "mysql-default",
                "driver": "mysql",
                "name": settings.mysql_name,
                "host": settings.mysql_host,
                "port": settings.mysql_port,
                "user": settings.mysql_user,
                "password": settings.mysql_password,
                "default_database": settings.mysql_database,
                "default_schema": "",
            }
        )
    if settings.postgres_enabled:
        records.append(
            {
                "id": "postgres-default",
                "driver": "postgresql",
                "name": settings.postgres_name,
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "user": settings.postgres_user,
                "password": settings.postgres_password,
                "default_database": settings.postgres_database,
                "default_schema": settings.postgres_schema,
            }
        )
    return records


class DatabaseWorkbenches:
    """Driver router that keeps the public API independent from database dialects."""

    def __init__(
        self,
        *,
        store_path: Path | None = None,
        defaults: list[dict[str, Any]] | None = None,
        engines: list[Any] | None = None,
    ) -> None:
        self.engines: list[Any] = engines or [
            MySQLWorkbench(include_default=False),
            PostgresWorkbench(include_default=False),
        ]
        self._driver_engines = {
            "mysql": next(engine for engine in self.engines if isinstance(engine, MySQLWorkbench)),
            "postgresql": next(
                engine for engine in self.engines if isinstance(engine, PostgresWorkbench)
            ),
        }
        for engine in self.engines:
            engine.profiles.clear()
        self._guard = threading.RLock()
        self._store = ConnectionStore(
            store_path or settings.connections_file,
            settings.token_secret,
            _environment_connections() if defaults is None else defaults,
        )
        self._records: dict[str, dict[str, Any]] = {}
        for stored in self._store.list():
            record = self._normalize_record(stored, profile_id=str(stored.get("id", "")))
            self._records[record["id"]] = record
            self._driver_engines[record["driver"]].profiles[record["id"]] = self._profile(record)
        self._session_engines: dict[str, Any] = {}

    @staticmethod
    def _normalize_record(value: dict[str, Any], profile_id: str = "") -> dict[str, Any]:
        driver = str(value.get("driver", "")).strip().lower()
        if driver not in {"mysql", "postgresql"}:
            raise DeeBeeError("数据库类型仅支持 MySQL 或 PostgreSQL")
        name = str(value.get("name", "")).strip()
        host = str(value.get("host", "")).strip()
        user = str(value.get("user", "")).strip()
        if not name:
            raise DeeBeeError("连接名称不能为空")
        if not host:
            raise DeeBeeError("主机地址不能为空")
        if not user:
            raise DeeBeeError("用户名不能为空")
        try:
            port = int(value.get("port", 5432 if driver == "postgresql" else 3306))
        except (TypeError, ValueError) as exc:
            raise DeeBeeError("端口必须是数字") from exc
        if not 1 <= port <= 65535:
            raise DeeBeeError("端口必须在 1 到 65535 之间")
        return {
            "id": profile_id or f"{driver}-{uuid.uuid4()}",
            "driver": driver,
            "name": name,
            "host": host,
            "port": port,
            "user": user,
            "password": str(value.get("password", "")),
            "default_database": str(value.get("default_database", "")).strip()
            or ("postgres" if driver == "postgresql" else ""),
            "default_schema": str(value.get("default_schema", "")).strip()
            or ("public" if driver == "postgresql" else ""),
        }

    @staticmethod
    def _profile(record: dict[str, Any]) -> Profile | PostgresProfile:
        if record["driver"] == "postgresql":
            return PostgresProfile(
                id=record["id"], name=record["name"], host=record["host"],
                port=record["port"], user=record["user"], password=record["password"],
                default_database=record["default_database"],
                default_schema=record["default_schema"],
            )
        return Profile(
            id=record["id"], name=record["name"], host=record["host"],
            port=record["port"], user=record["user"], password=record["password"],
            default_database=record["default_database"],
        )

    def _close_profile_sessions(self, profile_id: str) -> None:
        engine = self._engine(profile_id)
        session_ids = [
            session_id for session_id, session in engine.sessions.items()
            if session.profile_id == profile_id
        ]
        for session_id in session_ids:
            engine.close_session(session_id)
            self._session_engines.pop(session_id, None)

    def create_connection(self, value: dict[str, Any]) -> dict[str, Any]:
        record = self._normalize_record(value)
        with self._guard:
            self._records[record["id"]] = record
            try:
                self._store.replace(list(self._records.values()))
            except Exception:
                self._records.pop(record["id"], None)
                raise
            profile = self._profile(record)
            self._driver_engines[record["driver"]].profiles[record["id"]] = profile
        return profile.public()

    def update_connection(self, profile_id: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._guard:
            current = self._records.get(profile_id)
            if not current:
                raise DeeBeeError("连接不存在")
            if value.get("driver") and value["driver"] != current["driver"]:
                raise DeeBeeError("不能修改已保存连接的数据库类型")
            merged = {**current, **{key: item for key, item in value.items() if item is not None}}
            if value.get("password") is None:
                merged["password"] = current["password"]
            record = self._normalize_record(merged, profile_id=profile_id)
            previous = current
            self._close_profile_sessions(profile_id)
            self._records[profile_id] = record
            try:
                self._store.replace(list(self._records.values()))
            except Exception:
                self._records[profile_id] = previous
                raise
            profile = self._profile(record)
            self._driver_engines[record["driver"]].profiles[profile_id] = profile
        return profile.public()

    def delete_connection(self, profile_id: str) -> None:
        with self._guard:
            current = self._records.get(profile_id)
            if not current:
                raise DeeBeeError("连接不存在")
            remaining = [
                record for record_id, record in self._records.items()
                if record_id != profile_id
            ]
            self._close_profile_sessions(profile_id)
            self._store.replace(remaining)
            self._driver_engines[current["driver"]].profiles.pop(profile_id, None)
            self._records.pop(profile_id, None)

    def test_connection(
        self, value: dict[str, Any], profile_id: str = ""
    ) -> dict[str, Any]:
        candidate = dict(value)
        if profile_id:
            current = self._records.get(profile_id)
            if not current:
                raise DeeBeeError("连接不存在")
            candidate = {**current, **{key: item for key, item in value.items() if item is not None}}
            if value.get("password") is None:
                candidate["password"] = current["password"]
        record = self._normalize_record(candidate, profile_id="connection-test")
        engine = self._driver_engines[record["driver"]]
        return engine.test_connection(self._profile(record))

    def _engine(self, profile_id: str) -> Any:
        for engine in self.engines:
            if profile_id in engine.profiles:
                return engine
        raise DeeBeeError("连接不存在")

    def _session_engine(self, session_id: str) -> Any:
        engine = self._session_engines.get(session_id)
        if engine is not None:
            return engine
        for candidate in self.engines:
            if session_id in candidate.sessions:
                self._session_engines[session_id] = candidate
                return candidate
        raise DeeBeeError("查询会话已失效，请重新打开标签")

    @staticmethod
    def _schema_call(engine: Any, method: str, *args: Any, schema: str = "", **kwargs: Any) -> Any:
        function = getattr(engine, method)
        if isinstance(engine, PostgresWorkbench):
            return function(*args, schema=schema, **kwargs)
        return function(*args, **kwargs)

    def list_profiles(self) -> list[dict[str, Any]]:
        return [
            self._engine(profile_id).require_profile(profile_id).public()
            for profile_id in self._records
        ]

    def profile_driver(self, profile_id: str) -> str:
        return self._engine(profile_id).require_profile(profile_id).public()["driver"]

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self._engine(profile_id).test_profile(profile_id)

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        return self._engine(profile_id).databases(profile_id)

    def schemas(self, profile_id: str, database: str) -> list[dict[str, Any]]:
        engine = self._engine(profile_id)
        if isinstance(engine, PostgresWorkbench):
            return engine.schemas(profile_id, database)
        return []

    def objects(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "objects", profile_id, database, schema=schema)

    def catalog(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "catalog", profile_id, database, schema=schema)

    def create_database(self, profile_id: str, name: str, charset: str, collation: str) -> dict[str, Any]:
        return self._engine(profile_id).create_database(profile_id, name, charset, collation)

    def drop_database(self, profile_id: str, name: str) -> dict[str, Any]:
        return self._engine(profile_id).drop_database(profile_id, name)

    def create_session(
        self, profile_id: str, database: str, autocommit: bool,
        workspace_id: str = "", schema: str = "",
    ) -> dict[str, Any]:
        engine = self._engine(profile_id)
        result = self._schema_call(
            engine, "create_session", profile_id, database, autocommit,
            workspace_id, schema=schema,
        )
        self._session_engines[result["id"]] = engine
        return result

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        return self._session_engine(session_id).inspect_session(session_id)

    def close_session(self, session_id: str) -> None:
        engine = self._session_engine(session_id)
        engine.close_session(session_id)
        self._session_engines.pop(session_id, None)

    def cleanup_workspace(self, workspace_id: str) -> int:
        closed = sum(engine.cleanup_workspace(workspace_id) for engine in self.engines)
        self._session_engines = {
            session_id: engine for session_id, engine in self._session_engines.items()
            if session_id in engine.sessions
        }
        return closed

    def set_autocommit(self, session_id: str, enabled: bool) -> dict[str, Any]:
        return self._session_engine(session_id).set_autocommit(session_id, enabled)

    def execute(self, session_id: str, sql: str, limit: int = 1000) -> dict[str, Any]:
        return self._session_engine(session_id).execute(session_id, sql, limit)

    def cancel(self, session_id: str) -> bool:
        return self._session_engine(session_id).cancel(session_id)

    def commit(self, session_id: str) -> None:
        self._session_engine(session_id).commit(session_id)

    def rollback(self, session_id: str) -> None:
        self._session_engine(session_id).rollback(session_id)

    def table_schema(self, profile_id: str, database: str, table: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "table_schema", profile_id, database, table, schema=schema)

    def table_data(
        self, profile_id: str, database: str, table: str, page: int, page_size: int,
        filters: list[dict[str, Any]], sort: dict[str, Any] | None, schema: str = "",
        limit: int = 1000,
    ) -> dict[str, Any]:
        return self._schema_call(
            self._engine(profile_id), "table_data", profile_id, database, table,
            page, page_size, filters, sort, schema=schema, limit=limit,
        )

    def insert_row(self, profile_id: str, database: str, table: str, values: dict[str, Any], schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "insert_row", profile_id, database, table, values, schema=schema)

    def update_row(
        self, profile_id: str, database: str, table: str, key: dict[str, Any],
        changes: dict[str, Any], schema: str = "",
    ) -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "update_row", profile_id, database, table, key, changes, schema=schema)

    def delete_row(self, profile_id: str, database: str, table: str, key: dict[str, Any], schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "delete_row", profile_id, database, table, key, schema=schema)

    def export_table(
        self, profile_id: str, database: str, table: str, schema: str = ""
    ) -> dict[str, Any]:
        return self._schema_call(
            self._engine(profile_id), "export_table", profile_id, database, table,
            schema=schema,
        )

    def bulk_insert(
        self, profile_id: str, database: str, table: str, rows: list[dict[str, Any]],
        schema: str = "", *, cancelled: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        return self._schema_call(
            self._engine(profile_id), "bulk_insert", profile_id, database, table, rows,
            schema=schema, cancelled=cancelled, progress=progress,
        )

    def preview_ddl(self, profile_id: str, spec: dict[str, Any], current_table: str | None = None) -> list[str]:
        return self._engine(profile_id).preview_ddl(profile_id, spec, current_table)

    def apply_ddl(self, profile_id: str, database: str, statements: list[str]) -> dict[str, Any]:
        return self._engine(profile_id).apply_ddl(profile_id, database, statements)

    def table_action(
        self, profile_id: str, database: str, table: str, action: str,
        *, target: str = "", with_data: bool = False, schema: str = "",
    ) -> dict[str, Any]:
        return self._schema_call(
            self._engine(profile_id), "table_action", profile_id, database, table, action,
            target=target, with_data=with_data, schema=schema,
        )

    def object_ddl(self, profile_id: str, database: str, kind: str, name: str, schema: str = "", object_id: int | None = None) -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "object_ddl", profile_id, database, kind, name, object_id=object_id, schema=schema)

    def object_action(
        self, profile_id: str, database: str, kind: str, name: str, action: str,
        schema: str = "", object_id: int | None = None,
    ) -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "object_action", profile_id, database, kind, name, action, object_id=object_id, schema=schema)

    def search_objects(self, profile_id: str, database: str, term: str, schema: str = "") -> list[dict[str, Any]]:
        return self._schema_call(self._engine(profile_id), "search_objects", profile_id, database, term, schema=schema)

    def grants(self, profile_id: str, database: str = "", table: str = "", schema: str = "") -> Any:
        return self._schema_call(self._engine(profile_id), "grants", profile_id, database, table, schema=schema)

    def generate_data(
        self, profile_id: str, database: str, table: str, count: int,
        schema: str = "", *, cancelled: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        return self._schema_call(
            self._engine(profile_id), "generate_data", profile_id, database, table, count,
            schema=schema, cancelled=cancelled, progress=progress,
        )

    def execute_script(self, profile_id: str, database: str, sql: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "execute_script", profile_id, database, sql, schema=schema)

    def dump_sql(
        self, profile_id: str, database: str, table: str = "",
        include_data: bool = True, schema: str = "",
    ) -> str:
        return self._schema_call(
            self._engine(profile_id), "dump_sql", profile_id, database, table,
            include_data, schema=schema,
        )


workbench = DatabaseWorkbenches(engines=[mysql_workbench, postgres_workbench])
