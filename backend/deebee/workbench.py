from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .connection_drivers import (
    ClickHouseProfile,
    ClickHouseWorkbench,
    MongoDBProfile,
    MongoDBWorkbench,
    RedisProfile,
    RedisWorkbench,
    clickhouse_workbench,
    mongodb_workbench,
    redis_workbench,
)
from .connection_store import ConnectionStore
from .config import settings
from .mysql import DeeBeeError, MySQLWorkbench, Profile, workbench as mysql_workbench
from .mssql import SQLServerProfile, SQLServerWorkbench, workbench as mssql_workbench
from .postgres import (
    PostgresProfile,
    PostgresWorkbench,
    workbench as postgres_workbench,
)
from .remote_connections import RemoteConnectionWorkbench, RemoteProfile, remote_workbench
from .transports import transport_manager


DATABASE_DRIVERS = {"mysql", "postgresql", "mssql", "redis", "clickhouse", "mongodb"}
TRANSPORT_OPTIONS = {
    "ssh_tunnel",
    "ssh_host",
    "ssh_port",
    "ssh_user",
    "ssh_auth_method",
    "ssh_host_key_mode",
    "ssh_host_key_fingerprint",
    "ssh_keepalive_seconds",
    "proxy_enabled",
    "proxy_type",
    "proxy_host",
    "proxy_port",
    "proxy_user",
    "proxy_dns",
}


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
            SQLServerWorkbench(),
            RedisWorkbench(),
            ClickHouseWorkbench(),
            MongoDBWorkbench(),
            RemoteConnectionWorkbench(),
        ]
        self._driver_engines = {
            "mysql": next(engine for engine in self.engines if isinstance(engine, MySQLWorkbench)),
            "postgresql": next(
                engine for engine in self.engines if isinstance(engine, PostgresWorkbench)
            ),
            "mssql": next(
                engine for engine in self.engines if isinstance(engine, SQLServerWorkbench)
            ),
            "redis": next(engine for engine in self.engines if isinstance(engine, RedisWorkbench)),
            "clickhouse": next(
                engine for engine in self.engines if isinstance(engine, ClickHouseWorkbench)
            ),
            "mongodb": next(
                engine for engine in self.engines if isinstance(engine, MongoDBWorkbench)
            ),
            "ssh": next(
                engine for engine in self.engines if isinstance(engine, RemoteConnectionWorkbench)
            ),
            "rdp": next(
                engine for engine in self.engines if isinstance(engine, RemoteConnectionWorkbench)
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
        supported = {"mysql", "postgresql", "mssql", "redis", "clickhouse", "mongodb", "ssh", "rdp"}
        if driver not in supported:
            raise DeeBeeError("不支持的连接类型")
        name = str(value.get("name", "")).strip()
        host = str(value.get("host", "")).strip()
        user = str(value.get("user", "")).strip()
        if not name:
            raise DeeBeeError("连接名称不能为空")
        if not host:
            raise DeeBeeError("主机地址不能为空")
        if driver in {"mysql", "postgresql", "mssql", "clickhouse", "ssh"} and not user:
            raise DeeBeeError("用户名不能为空")
        password = str(value.get("password", ""))
        if driver == "mongodb" and password and not user:
            raise DeeBeeError("MongoDB 使用密码认证时必须填写用户名")
        try:
            default_ports = {
                "mysql": 3306,
                "postgresql": 5432,
                "mssql": 1433,
                "redis": 6379,
                "clickhouse": 8123,
                "mongodb": 27017,
                "ssh": 22,
                "rdp": 3389,
            }
            port = int(value.get("port", default_ports[driver]))
        except (TypeError, ValueError) as exc:
            raise DeeBeeError("端口必须是数字") from exc
        if not 1 <= port <= 65535:
            raise DeeBeeError("端口必须在 1 到 65535 之间")
        raw_options = value.get("options") or {}
        if not isinstance(raw_options, dict):
            raise DeeBeeError("连接选项格式无效")
        allowed_options = {
            "mysql": set(),
            "postgresql": set(),
            "mssql": {"encryption", "read_only"},
            "redis": {"tls", "verify_tls"},
            "clickhouse": {"tls", "verify_tls"},
            "mongodb": {"tls", "verify_tls", "auth_database", "direct_connection"},
            "ssh": {"auth_method", "strict_host_key", "host_key_fingerprint"},
            "rdp": {"domain", "security", "ignore_certificate", "color_depth", "timezone"},
        }[driver]
        if driver in DATABASE_DRIVERS:
            allowed_options |= TRANSPORT_OPTIONS
        unknown_options = set(raw_options) - allowed_options
        if unknown_options:
            raise DeeBeeError(f"{driver} 不支持连接选项：{', '.join(sorted(unknown_options))}")
        options: dict[str, Any] = {}
        for key in {"tls", "verify_tls", "direct_connection", "strict_host_key", "ignore_certificate"} & allowed_options:
            option = raw_options.get(key, key == "verify_tls")
            if not isinstance(option, bool):
                raise DeeBeeError(f"连接选项 {key} 必须是布尔值")
            options[key] = option
        if driver == "mongodb":
            options["auth_database"] = str(raw_options.get("auth_database", "admin")).strip() or "admin"
        if driver == "mssql":
            encryption = str(raw_options.get("encryption", "request")).strip().lower()
            if encryption not in {"off", "request", "require"}:
                raise DeeBeeError("SQL Server 加密策略无效")
            read_only = raw_options.get("read_only", False)
            if not isinstance(read_only, bool):
                raise DeeBeeError("连接选项 read_only 必须是布尔值")
            options.update(encryption=encryption, read_only=read_only)
        if driver == "ssh":
            auth_method = str(raw_options.get("auth_method", "password")).strip()
            if auth_method not in {"password", "private_key"}:
                raise DeeBeeError("SSH 认证方式无效")
            options.update(
                auth_method=auth_method,
                strict_host_key=bool(raw_options.get("strict_host_key", True)),
                host_key_fingerprint=str(raw_options.get("host_key_fingerprint", "")).strip(),
            )
        if driver == "rdp":
            security = str(raw_options.get("security", "any")).strip().lower()
            if security not in {"any", "nla", "tls", "rdp"}:
                raise DeeBeeError("RDP 安全模式无效")
            try:
                color_depth = int(raw_options.get("color_depth", 24))
            except (TypeError, ValueError) as exc:
                raise DeeBeeError("RDP 色深必须是数字") from exc
            if color_depth not in {16, 24, 32}:
                raise DeeBeeError("RDP 色深仅支持 16、24 或 32 位")
            options.update(
                domain=str(raw_options.get("domain", "")).strip(),
                security=security,
                ignore_certificate=bool(raw_options.get("ignore_certificate", False)),
                color_depth=color_depth,
                timezone=str(raw_options.get("timezone", "Etc/UTC")).strip() or "Etc/UTC",
            )
        if driver in DATABASE_DRIVERS and TRANSPORT_OPTIONS & set(raw_options):
            ssh_tunnel = raw_options.get("ssh_tunnel", False)
            proxy_enabled = raw_options.get("proxy_enabled", False)
            proxy_dns = raw_options.get("proxy_dns", True)
            if not all(isinstance(item, bool) for item in (ssh_tunnel, proxy_enabled, proxy_dns)):
                raise DeeBeeError("SSH 隧道和代理开关必须是布尔值")
            ssh_auth_method = str(raw_options.get("ssh_auth_method", "password")).strip()
            if ssh_auth_method not in {"password", "private_key"}:
                raise DeeBeeError("SSH 隧道认证方式无效")
            host_key_mode = str(raw_options.get("ssh_host_key_mode", "accept_new")).strip()
            if host_key_mode not in {"accept_new", "strict", "insecure"}:
                raise DeeBeeError("SSH 隧道主机密钥策略无效")
            proxy_type = str(raw_options.get("proxy_type", "socks5")).strip().lower()
            if proxy_type not in {"socks5", "http"}:
                raise DeeBeeError("仅支持 SOCKS5 和 HTTP CONNECT 代理")
            try:
                ssh_port = int(raw_options.get("ssh_port", 22))
                proxy_port = int(raw_options.get("proxy_port", 1080))
                keepalive = int(raw_options.get("ssh_keepalive_seconds", 30))
            except (TypeError, ValueError) as exc:
                raise DeeBeeError("隧道和代理端口必须是数字") from exc
            if not 1 <= ssh_port <= 65535 or not 1 <= proxy_port <= 65535:
                raise DeeBeeError("隧道和代理端口必须在 1 到 65535 之间")
            if not 0 <= keepalive <= 300:
                raise DeeBeeError("SSH 保活间隔必须在 0 到 300 秒之间")
            ssh_host = str(raw_options.get("ssh_host", "")).strip()
            ssh_user = str(raw_options.get("ssh_user", "")).strip()
            fingerprint = str(raw_options.get("ssh_host_key_fingerprint", "")).strip()
            proxy_host = str(raw_options.get("proxy_host", "")).strip()
            if ssh_tunnel and (not ssh_host or not ssh_user):
                raise DeeBeeError("启用 SSH 隧道后必须填写 SSH 主机和用户名")
            if ssh_tunnel and host_key_mode == "strict" and not fingerprint:
                raise DeeBeeError("严格校验需要先填写 SSH 主机密钥指纹")
            if proxy_enabled and not proxy_host:
                raise DeeBeeError("启用代理后必须填写代理主机")
            options.update(
                ssh_tunnel=ssh_tunnel,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                ssh_user=ssh_user,
                ssh_auth_method=ssh_auth_method,
                ssh_host_key_mode=host_key_mode,
                ssh_host_key_fingerprint=fingerprint,
                ssh_keepalive_seconds=keepalive,
                proxy_enabled=proxy_enabled,
                proxy_type=proxy_type,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                proxy_user=str(raw_options.get("proxy_user", "")).strip(),
                proxy_dns=proxy_dns,
            )

        default_databases = {
            "mysql": "",
            "postgresql": "postgres",
            "mssql": "master",
            "redis": "0",
            "clickhouse": "default",
            "mongodb": "",
            "ssh": "",
            "rdp": "",
        }
        default_database = str(value.get("default_database", "")).strip() or default_databases[driver]
        if driver == "redis":
            try:
                redis_database = int(default_database)
            except ValueError as exc:
                raise DeeBeeError("Redis 逻辑数据库编号必须是非负整数") from exc
            if redis_database < 0:
                raise DeeBeeError("Redis 逻辑数据库编号必须是非负整数")
            default_database = str(redis_database)

        return {
            "id": profile_id or f"{driver}-{uuid.uuid4()}",
            "driver": driver,
            "name": name,
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "private_key": str(value.get("private_key", "")),
            "ssh_password": str(value.get("ssh_password", "")),
            "ssh_private_key": str(value.get("ssh_private_key", "")),
            "proxy_password": str(value.get("proxy_password", "")),
            "default_database": default_database,
            "default_schema": str(value.get("default_schema", "")).strip()
            or ({"postgresql": "public", "mssql": "dbo"}.get(driver, "")),
            "options": options,
        }

    @staticmethod
    def _transport(record: dict[str, Any]) -> dict[str, Any]:
        if record["driver"] not in DATABASE_DRIVERS:
            return {}
        options = record["options"]
        if not options.get("ssh_tunnel") and not options.get("proxy_enabled"):
            return {}
        return {
            **{key: options.get(key) for key in TRANSPORT_OPTIONS},
            "ssh_password": record.get("ssh_password", ""),
            "ssh_private_key": record.get("ssh_private_key", ""),
            "proxy_password": record.get("proxy_password", ""),
        }

    @staticmethod
    def _profile(
        record: dict[str, Any],
    ) -> Profile | PostgresProfile | SQLServerProfile | RedisProfile | ClickHouseProfile | MongoDBProfile | RemoteProfile:
        if record["driver"] == "postgresql":
            return PostgresProfile(
                id=record["id"], name=record["name"], host=record["host"],
                port=record["port"], user=record["user"], password=record["password"],
                default_database=record["default_database"],
                default_schema=record["default_schema"],
                options=dict(record["options"]), transport=DatabaseWorkbenches._transport(record),
            )
        if record["driver"] == "mssql":
            return SQLServerProfile(
                id=record["id"], name=record["name"], host=record["host"],
                port=record["port"], user=record["user"], password=record["password"],
                default_database=record["default_database"],
                default_schema=record["default_schema"], options=dict(record["options"]),
                transport=DatabaseWorkbenches._transport(record),
            )
        connection_profile_types = {
            "redis": RedisProfile,
            "clickhouse": ClickHouseProfile,
            "mongodb": MongoDBProfile,
        }
        if record["driver"] in connection_profile_types:
            profile_type = connection_profile_types[record["driver"]]
            return profile_type(
                id=record["id"], name=record["name"], host=record["host"],
                port=record["port"], user=record["user"], password=record["password"],
                default_database=record["default_database"],
                default_schema=record["default_schema"], options=dict(record["options"]),
                transport=DatabaseWorkbenches._transport(record),
            )
        if record["driver"] in {"ssh", "rdp"}:
            return RemoteProfile(
                id=record["id"], driver=record["driver"], name=record["name"],
                host=record["host"], port=record["port"], user=record["user"],
                password=record["password"], private_key=record["private_key"],
                options=dict(record["options"]),
            )
        return Profile(
            id=record["id"], name=record["name"], host=record["host"],
            port=record["port"], user=record["user"], password=record["password"],
            default_database=record["default_database"], options=dict(record["options"]),
            transport=DatabaseWorkbenches._transport(record),
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
            for secret in ("private_key", "ssh_password", "ssh_private_key", "proxy_password"):
                if value.get(secret) is None:
                    merged[secret] = current.get(secret, "")
            record = self._normalize_record(merged, profile_id=profile_id)
            previous = current
            self._close_profile_sessions(profile_id)
            transport_manager.close(profile_id)
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
            transport_manager.close(profile_id)
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
            for secret in ("private_key", "ssh_password", "ssh_private_key", "proxy_password"):
                if value.get(secret) is None:
                    candidate[secret] = current.get(secret, "")
        test_profile_id = f"connection-test-{uuid.uuid4()}"
        record = self._normalize_record(candidate, profile_id=test_profile_id)
        engine = self._driver_engines[record["driver"]]
        try:
            result = engine.test_connection(self._profile(record))
            if record["driver"] in DATABASE_DRIVERS:
                result.update(transport_manager.metadata(test_profile_id))
            return result
        finally:
            transport_manager.close(test_profile_id)

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
        if isinstance(engine, (PostgresWorkbench, SQLServerWorkbench)):
            return function(*args, schema=schema, **kwargs)
        return function(*args, **kwargs)

    def list_profiles(self) -> list[dict[str, Any]]:
        return [
            self._engine(profile_id).require_profile(profile_id).public()
            for profile_id in self._records
        ]

    def profile_driver(self, profile_id: str) -> str:
        return self._engine(profile_id).require_profile(profile_id).public()["driver"]

    def remote_profile(self, profile_id: str, driver: str) -> RemoteProfile:
        profile = self._engine(profile_id).require_profile(profile_id)
        if not isinstance(profile, RemoteProfile) or profile.driver != driver:
            raise DeeBeeError("远程连接类型不匹配")
        return profile

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        result = self._engine(profile_id).test_profile(profile_id)
        if self.profile_driver(profile_id) in DATABASE_DRIVERS:
            result.update(transport_manager.metadata(profile_id))
        return result

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        return self._engine(profile_id).databases(profile_id)

    def schemas(self, profile_id: str, database: str) -> list[dict[str, Any]]:
        engine = self._engine(profile_id)
        if isinstance(engine, (PostgresWorkbench, SQLServerWorkbench)):
            return engine.schemas(profile_id, database)
        return []

    def objects(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "objects", profile_id, database, schema=schema)

    def catalog(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        return self._schema_call(self._engine(profile_id), "catalog", profile_id, database, schema=schema)

    def create_database(
        self, profile_id: str, name: str, charset: str, collation: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = self._engine(profile_id)
        if isinstance(engine, SQLServerWorkbench):
            return engine.create_database(profile_id, name, charset, collation, options)
        return engine.create_database(profile_id, name, charset, collation)

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


workbench = DatabaseWorkbenches(
    engines=[
        mysql_workbench,
        postgres_workbench,
        mssql_workbench,
        redis_workbench,
        clickhouse_workbench,
        mongodb_workbench,
        remote_workbench,
    ]
)
