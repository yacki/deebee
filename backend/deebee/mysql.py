from __future__ import annotations

import datetime as dt
import decimal
import json
import random
import re
import string
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import pymysql
from pymysql.constants import CLIENT, FIELD_TYPE
from pymysql.cursors import DictCursor

from .config import settings


TYPE_NAMES = {
    value: name
    for name, value in vars(FIELD_TYPE).items()
    if name.isupper() and isinstance(value, int)
}
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
DATA_TYPE = re.compile(r"^[A-Za-z]+(?:\((?:[A-Za-z0-9_,' \".\-]+)\))?(?:\s+(?:UNSIGNED|ZEROFILL))*$", re.IGNORECASE)


class DeeBeeError(RuntimeError):
    def __init__(self, message: str, code: int | str | None = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    host: str
    port: int
    user: str
    password: str
    default_database: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    transport: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("password")
        value.pop("transport")
        value["driver"] = "mysql"
        return value


@dataclass
class DbSession:
    id: str
    connection: pymysql.Connection
    profile_id: str
    database: str
    autocommit: bool
    workspace_id: str
    lock: threading.RLock
    running_thread_id: int | None = None


def quote_ident(value: str) -> str:
    if not value or "\x00" in value:
        raise DeeBeeError("无效的数据库标识符")
    return "`" + value.replace("`", "``") + "`"


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return {"$binary": data.hex(), "size": len(data)}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: json_value(value) for key, value in row.items()}


class MySQLWorkbench:
    def __init__(self, include_default: bool = True) -> None:
        default = Profile(
            id="mysql-default",
            name=settings.mysql_name,
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            default_database=settings.mysql_database,
        )
        self.profiles: dict[str, Profile] = {default.id: default} if include_default else {}
        self.sessions: dict[str, DbSession] = {}
        self._guard = threading.RLock()

    def _connect(
        self, profile: Profile, database: str = "", *, autocommit: bool = True
    ) -> pymysql.Connection:
        from .transports import transport_manager

        host, port = transport_manager.endpoint(
            profile.id, profile.host, profile.port, profile.transport
        )
        try:
            return pymysql.connect(
                host=host,
                port=port,
                user=profile.user,
                password=profile.password,
                database=database or None,
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=autocommit,
                connect_timeout=10,
                read_timeout=120,
                write_timeout=120,
                client_flag=CLIENT.MULTI_STATEMENTS,
            )
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            message = str(exc.args[1] if len(exc.args) > 1 else exc)
            raise DeeBeeError(message, code) from exc

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.public() for profile in self.profiles.values()]

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self.test_connection(self.require_profile(profile_id))

    def test_connection(self, profile: Profile) -> dict[str, Any]:
        started = time.perf_counter()
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version, CURRENT_USER() AS account")
                info = cursor.fetchone()
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                **(info or {}),
            }
        finally:
            conn.close()

    def require_profile(self, profile_id: str) -> Profile:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise DeeBeeError("连接不存在")
        return profile

    def create_session(
        self, profile_id: str, database: str, autocommit: bool, workspace_id: str = ""
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        database = database or profile.default_database
        conn = self._connect(profile, database, autocommit=autocommit)
        session = DbSession(
            id=str(uuid.uuid4()),
            connection=conn,
            profile_id=profile_id,
            database=database,
            autocommit=autocommit,
            workspace_id=workspace_id,
            lock=threading.RLock(),
        )
        with self._guard:
            self.sessions[session.id] = session
        return self.session_public(session)

    def session_public(self, session: DbSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "profile_id": session.profile_id,
            "database": session.database,
            "autocommit": session.autocommit,
            "workspace_id": session.workspace_id,
            "thread_id": session.connection.thread_id(),
        }

    def require_session(self, session_id: str) -> DbSession:
        with self._guard:
            session = self.sessions.get(session_id)
        if not session:
            raise DeeBeeError("查询会话已失效，请重新打开标签")
        try:
            # Never reconnect a session transparently: doing so would silently
            # discard an open transaction while the UI still reports manual
            # transaction mode.
            session.connection.ping(reconnect=False)
        except pymysql.MySQLError as exc:
            with self._guard:
                self.sessions.pop(session_id, None)
            try:
                session.connection.close()
            except Exception:
                pass
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            raise DeeBeeError("查询会话已失效，请重新打开标签", code) from exc
        return session

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        return self.session_public(self.require_session(session_id))

    def close_session(self, session_id: str) -> None:
        with self._guard:
            session = self.sessions.pop(session_id, None)
        if session:
            try:
                session.connection.rollback()
            finally:
                session.connection.close()

    def cleanup_workspace(self, workspace_id: str) -> int:
        if not workspace_id:
            return 0
        with self._guard:
            session_ids = [
                session_id for session_id, session in self.sessions.items()
                if session.workspace_id == workspace_id
            ]
        for session_id in session_ids:
            self.close_session(session_id)
        return len(session_ids)

    def set_autocommit(self, session_id: str, enabled: bool) -> dict[str, Any]:
        session = self.require_session(session_id)
        with session.lock:
            session.connection.autocommit(enabled)
            session.autocommit = enabled
        return self.session_public(session)

    def commit(self, session_id: str) -> None:
        session = self.require_session(session_id)
        with session.lock:
            session.connection.commit()

    def rollback(self, session_id: str) -> None:
        session = self.require_session(session_id)
        with session.lock:
            session.connection.rollback()

    def cancel(self, session_id: str) -> bool:
        # Do not ping the active connection here: ping waits behind a running
        # statement and would make cancellation impossible until it finished.
        with self._guard:
            session = self.sessions.get(session_id)
        if not session:
            raise DeeBeeError("查询会话已失效，请重新打开标签")
        thread_id = session.running_thread_id
        if not thread_id:
            return False
        profile = self.require_profile(session.profile_id)
        killer = self._connect(profile)
        try:
            with killer.cursor() as cursor:
                cursor.execute(f"KILL QUERY {int(thread_id)}")
            return True
        finally:
            killer.close()

    def execute(self, session_id: str, sql: str, limit: int = 1000) -> dict[str, Any]:
        if not sql.strip():
            raise DeeBeeError("请输入 SQL")
        session = self.require_session(session_id)
        started = time.perf_counter()
        results: list[dict[str, Any]] = []
        with session.lock:
            try:
                session.running_thread_id = session.connection.thread_id()
                with session.connection.cursor() as cursor:
                    cursor.execute(sql)
                    index = 0
                    while True:
                        index += 1
                        if cursor.description:
                            raw_rows = cursor.fetchmany(limit + 1)
                            truncated = len(raw_rows) > limit
                            rows = [clean_row(row) for row in raw_rows[:limit]]
                            columns = [
                                {
                                    "name": item[0],
                                    "type": TYPE_NAMES.get(item[1], str(item[1])),
                                    "nullable": item[6],
                                }
                                for item in cursor.description
                            ]
                            results.append(
                                {
                                    "index": index,
                                    "kind": "rows",
                                    "columns": columns,
                                    "rows": rows,
                                    "row_count": len(rows),
                                    "truncated": truncated,
                                }
                            )
                        else:
                            results.append(
                                {
                                    "index": index,
                                    "kind": "mutation",
                                    "affected_rows": max(cursor.rowcount, 0),
                                    "last_insert_id": cursor.lastrowid,
                                }
                            )
                        if not cursor.nextset():
                            break
                with session.connection.cursor() as status_cursor:
                    status_cursor.execute("SHOW WARNINGS LIMIT 100")
                    warnings = [clean_row(row) for row in status_cursor.fetchall()]
                    status_cursor.execute(
                        "SELECT CONNECTION_ID() AS connection_id, DATABASE() AS current_database, "
                        "@@autocommit AS autocommit, @@transaction_isolation AS transaction_isolation, "
                        "@@character_set_client AS character_set_client"
                    )
                    session_status = clean_row(status_cursor.fetchone() or {})
                return {
                    "ok": True,
                    "results": results,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "autocommit": session.autocommit,
                    "warnings": warnings,
                    "status": session_status,
                }
            except pymysql.MySQLError as exc:
                code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
                message = str(exc.args[1] if len(exc.args) > 1 else exc)
                raise DeeBeeError(message, code) from exc
            finally:
                session.running_thread_id = None

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT SCHEMA_NAME AS name, DEFAULT_CHARACTER_SET_NAME AS charset, "
                    "DEFAULT_COLLATION_NAME AS collation FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME"
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def objects(self, profile_id: str, database: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME AS name, TABLE_TYPE AS object_type, ENGINE AS engine, "
                    "TABLE_ROWS AS estimated_rows, TABLE_COMMENT AS comment, DATA_LENGTH AS data_length, "
                    "INDEX_LENGTH AS index_length, CREATE_TIME AS created_at, UPDATE_TIME AS updated_at, "
                    "TABLE_COLLATION AS collation "
                    "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_TYPE, TABLE_NAME",
                    (database,),
                )
                table_rows = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS object_type "
                    "FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=%s ORDER BY ROUTINE_NAME",
                    (database,),
                )
                routines = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT TRIGGER_NAME AS name, EVENT_MANIPULATION AS event, EVENT_OBJECT_TABLE AS table_name "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s ORDER BY TRIGGER_NAME",
                    (database,),
                )
                triggers = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT EVENT_NAME AS name, STATUS AS status, EVENT_TYPE AS event_type, "
                    "EXECUTE_AT AS execute_at, INTERVAL_VALUE AS interval_value, INTERVAL_FIELD AS interval_field "
                    "FROM information_schema.EVENTS WHERE EVENT_SCHEMA=%s ORDER BY EVENT_NAME",
                    (database,),
                )
                events = [clean_row(row) for row in cursor.fetchall()]
            return {
                "tables": [row for row in table_rows if row["object_type"] == "BASE TABLE"],
                "views": [row for row in table_rows if row["object_type"] == "VIEW"],
                "routines": routines,
                "triggers": triggers,
                "events": events,
            }
        finally:
            conn.close()

    def catalog(self, profile_id: str, database: str) -> dict[str, Any]:
        """Return one compact metadata snapshot used by the SQL completion provider."""
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT t.TABLE_NAME AS table_name, t.TABLE_TYPE AS object_type, c.COLUMN_NAME AS column_name, "
                    "c.COLUMN_TYPE AS data_type, c.IS_NULLABLE='YES' AS nullable, c.COLUMN_KEY AS column_key "
                    "FROM information_schema.TABLES t LEFT JOIN information_schema.COLUMNS c "
                    "ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME "
                    "WHERE t.TABLE_SCHEMA=%s ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION",
                    (database,),
                )
                tables: dict[str, dict[str, Any]] = {}
                for row in cursor.fetchall():
                    item = tables.setdefault(row["table_name"], {
                        "name": row["table_name"], "object_type": row["object_type"], "columns": []
                    })
                    if row.get("column_name"):
                        item["columns"].append({
                            "name": row["column_name"], "data_type": row["data_type"],
                            "nullable": bool(row["nullable"]), "key": row["column_key"],
                        })
                cursor.execute(
                    "SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS object_type, DATA_TYPE AS data_type "
                    "FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=%s ORDER BY ROUTINE_NAME",
                    (database,),
                )
                routines = [clean_row(row) for row in cursor.fetchall()]
            return {"database": database, "tables": list(tables.values()), "routines": routines}
        finally:
            conn.close()

    def create_database(self, profile_id: str, name: str, charset: str = "utf8mb4", collation: str = "utf8mb4_unicode_ci") -> dict[str, Any]:
        if not IDENTIFIER.fullmatch(name):
            raise DeeBeeError("数据库名称无效")
        if not IDENTIFIER.fullmatch(charset) or not IDENTIFIER.fullmatch(collation):
            raise DeeBeeError("字符集或排序规则无效")
        profile = self.require_profile(profile_id)
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE {quote_ident(name)} CHARACTER SET {charset} COLLATE {collation}")
            return {"ok": True, "database": name}
        finally:
            conn.close()

    def drop_database(self, profile_id: str, name: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE {quote_ident(name)}")
            return {"ok": True, "database": name}
        finally:
            conn.close()

    def table_action(
        self, profile_id: str, database: str, table: str, action: str,
        *, target: str = "", with_data: bool = False,
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        source = f"{quote_ident(database)}.{quote_ident(table)}"
        statements: list[str] = []
        if action == "drop":
            statements = [f"DROP TABLE {source}"]
        elif action == "empty":
            statements = [f"DELETE FROM {source}"]
        elif action == "truncate":
            statements = [f"TRUNCATE TABLE {source}"]
        elif action == "rename":
            if not target:
                raise DeeBeeError("新表名不能为空")
            statements = [f"RENAME TABLE {source} TO {quote_ident(database)}.{quote_ident(target)}"]
        elif action == "duplicate":
            if not target:
                raise DeeBeeError("目标表名不能为空")
            destination = f"{quote_ident(database)}.{quote_ident(target)}"
            statements = [f"CREATE TABLE {destination} LIKE {source}"]
            if with_data:
                with conn.cursor() as metadata_cursor:
                    metadata_cursor.execute(
                        "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND EXTRA NOT LIKE '%%GENERATED%%' "
                        "ORDER BY ORDINAL_POSITION",
                        (database, table),
                    )
                    writable = [row["name"] for row in metadata_cursor.fetchall()]
                fields = ", ".join(quote_ident(name) for name in writable)
                statements.append(f"INSERT INTO {destination} ({fields}) SELECT {fields} FROM {source}")
        elif action in {"check", "analyze", "optimize", "repair"}:
            statements = [f"{action.upper()} TABLE {source}"]
        else:
            raise DeeBeeError("不支持的表操作")
        try:
            result_rows: list[dict[str, Any]] = []
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
                    if cursor.description:
                        result_rows.extend(clean_row(row) for row in cursor.fetchall())
            return {"ok": True, "action": action, "statements": statements, "results": result_rows}
        except pymysql.MySQLError as exc:
            raise DeeBeeError(str(exc.args[1] if len(exc.args) > 1 else exc), exc.args[0] if exc.args else None) from exc
        finally:
            conn.close()

    def grants(self, profile_id: str, database: str = "", table: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT CURRENT_USER() AS account")
                account = (cursor.fetchone() or {}).get("account", "")
                cursor.execute("SHOW GRANTS")
                rows = cursor.fetchall()
            grants = [next(iter(row.values())) for row in rows]
            return {"account": account, "database": database, "table": table, "grants": grants}
        finally:
            conn.close()

    def object_ddl(self, profile_id: str, database: str, kind: str, name: str, object_id: int | None = None) -> dict[str, Any]:
        allowed = {"table": "TABLE", "view": "VIEW", "function": "FUNCTION", "procedure": "PROCEDURE", "trigger": "TRIGGER", "event": "EVENT"}
        keyword = allowed.get(kind.lower())
        if not keyword:
            raise DeeBeeError("不支持的对象类型")
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW CREATE {keyword} {quote_ident(database)}.{quote_ident(name)}")
                row = cursor.fetchone() or {}
            sql = next((str(value) for key, value in row.items() if str(key).startswith("Create ")), "")
            return {"kind": kind, "name": name, "sql": sql, "metadata": clean_row(row)}
        finally:
            conn.close()

    def object_action(self, profile_id: str, database: str, kind: str, name: str, action: str, object_id: int | None = None) -> dict[str, Any]:
        allowed = {"view": "VIEW", "function": "FUNCTION", "procedure": "PROCEDURE", "trigger": "TRIGGER", "event": "EVENT"}
        keyword = allowed.get(kind.lower())
        if not keyword:
            raise DeeBeeError("不支持的对象类型")
        target = f"{quote_ident(database)}.{quote_ident(name)}"
        if action == "drop":
            sql = f"DROP {keyword} {target}"
        elif kind.lower() == "event" and action in {"enable", "disable"}:
            sql = f"ALTER EVENT {target} {action.upper()}"
        else:
            raise DeeBeeError("不支持的对象操作")
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
            return {"ok": True, "statement": sql}
        finally:
            conn.close()

    def search_objects(self, profile_id: str, database: str, term: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        pattern = f"%{term}%"
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 'table' AS kind, TABLE_NAME AS name, TABLE_COMMENT AS detail FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND (TABLE_NAME LIKE %s OR TABLE_COMMENT LIKE %s) "
                    "UNION ALL SELECT 'column', CONCAT(TABLE_NAME,'.',COLUMN_NAME), COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND COLUMN_NAME LIKE %s "
                    "UNION ALL SELECT LOWER(ROUTINE_TYPE), ROUTINE_NAME, DATA_TYPE FROM information_schema.ROUTINES "
                    "WHERE ROUTINE_SCHEMA=%s AND ROUTINE_NAME LIKE %s ORDER BY kind, name LIMIT 500",
                    (database, pattern, pattern, database, pattern, database, pattern),
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def execute_script(self, profile_id: str, database: str, sql: str) -> dict[str, Any]:
        session = self.create_session(profile_id, database, True)
        try:
            response = self.execute(session["id"], sql, 1000)
            return {"ok": True, "statements": len(response["results"]), "elapsed_ms": response["elapsed_ms"], "results": response["results"]}
        finally:
            self.close_session(session["id"])

    def dump_sql(self, profile_id: str, database: str, table: str = "", include_data: bool = True) -> str:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                if table:
                    names = [table]
                else:
                    cursor.execute("SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (database,))
                    names = [row["name"] for row in cursor.fetchall()]
                output = ["SET NAMES utf8mb4;", "SET FOREIGN_KEY_CHECKS=0;"]
                for name in names:
                    cursor.execute(f"SHOW CREATE TABLE {quote_ident(database)}.{quote_ident(name)}")
                    create = (cursor.fetchone() or {}).get("Create Table", "")
                    output.extend([f"DROP TABLE IF EXISTS {quote_ident(name)};", f"{create};"])
                    if include_data:
                        cursor.execute(f"SELECT * FROM {quote_ident(database)}.{quote_ident(name)}")
                        columns = [item[0] for item in cursor.description or []]
                        while True:
                            rows = cursor.fetchmany(500)
                            if not rows:
                                break
                            for row in rows:
                                values = ", ".join(self._literal(row.get(column)) for column in columns)
                                output.append(f"INSERT INTO {quote_ident(name)} ({', '.join(quote_ident(column) for column in columns)}) VALUES ({values});")
                output.append("SET FOREIGN_KEY_CHECKS=1;")
            return "\n".join(output) + "\n"
        finally:
            conn.close()

    def _literal(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "0x" + bytes(value).hex()
        escaped = str(value).replace("\\", "\\\\").replace("'", "''")
        return f"'{escaped}'"

    def generate_data(
        self, profile_id: str, database: str, table: str, count: int,
        *, cancelled: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        schema = self.table_schema(profile_id, database, table)
        writable = [column for column in schema["columns"] if "AUTO_INCREMENT" not in column.get("extra", "") and not column.get("generation")]
        rows: list[dict[str, Any]] = []
        for index in range(count):
            if cancelled and cancelled.is_set():
                raise DeeBeeError("任务已取消")
            row: dict[str, Any] = {}
            for column in writable:
                name, data_type = column["name"], column["data_type"].upper()
                if column.get("nullable") and index % 11 == 0:
                    row[name] = None
                elif "INT" in data_type:
                    row[name] = random.randint(1, 100000)
                elif any(item in data_type for item in ("DECIMAL", "FLOAT", "DOUBLE")):
                    row[name] = round(random.random() * 10000, 2)
                elif "DATE" in data_type and "TIME" not in data_type:
                    row[name] = (dt.date.today() - dt.timedelta(days=index % 365)).isoformat()
                elif "TIME" in data_type:
                    row[name] = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
                elif "BOOL" in data_type or "BIT" in data_type:
                    row[name] = index % 2
                else:
                    row[name] = f"sample_{index + 1}_{''.join(random.choices(string.ascii_lowercase, k=5))}"
            rows.append(row)
        return self.bulk_insert(
            profile_id, database, table, rows,
            cancelled=cancelled, progress=progress,
        )

    def table_schema(self, profile_id: str, database: str, table: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS data_type, IS_NULLABLE='YES' AS nullable, "
                    "COLUMN_DEFAULT AS `default`, EXTRA AS extra, COLUMN_COMMENT AS comment, "
                    "CHARACTER_SET_NAME AS charset, COLLATION_NAME AS collation, ORDINAL_POSITION AS position, "
                    "GENERATION_EXPRESSION AS generation "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                    (database, table),
                )
                columns = [clean_row(row) for row in cursor.fetchall()]
                # MySQL exposes DEFAULT_GENERATED as an internal metadata flag;
                # it is not valid syntax in a column definition. Preserve only
                # user-expressible attributes for round-tripping the designer.
                for column in columns:
                    extra = str(column.get("extra", ""))
                    column["extra"] = extra.replace("DEFAULT_GENERATED", "").strip().upper()
                cursor.execute(
                    "SELECT INDEX_NAME AS name, NON_UNIQUE=0 AS is_unique, SEQ_IN_INDEX AS seq, "
                    "COLUMN_NAME AS column_name, INDEX_TYPE AS index_type "
                    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                    "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                    (database, table),
                )
                index_rows = cursor.fetchall()
                indexes: dict[str, dict[str, Any]] = {}
                for row in index_rows:
                    item = indexes.setdefault(
                        row["name"],
                        {"name": row["name"], "unique": bool(row["is_unique"]), "type": row["index_type"], "columns": []},
                    )
                    item["columns"].append(row["column_name"])
                cursor.execute(
                    "SELECT k.CONSTRAINT_NAME AS name, k.COLUMN_NAME AS column_name, "
                    "k.REFERENCED_TABLE_NAME AS referenced_table, k.REFERENCED_COLUMN_NAME AS referenced_column, "
                    "r.UPDATE_RULE AS on_update, r.DELETE_RULE AS on_delete, k.ORDINAL_POSITION AS seq "
                    "FROM information_schema.KEY_COLUMN_USAGE k "
                    "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
                    "AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
                    "WHERE k.TABLE_SCHEMA=%s AND k.TABLE_NAME=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL "
                    "ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION",
                    (database, table),
                )
                fk_rows = cursor.fetchall()
                foreign_keys: dict[str, dict[str, Any]] = {}
                for row in fk_rows:
                    item = foreign_keys.setdefault(
                        row["name"],
                        {
                            "name": row["name"], "columns": [], "referenced_columns": [],
                            "referenced_table": row["referenced_table"], "on_update": row["on_update"],
                            "on_delete": row["on_delete"],
                        },
                    )
                    item["columns"].append(row["column_name"])
                    item["referenced_columns"].append(row["referenced_column"])
                cursor.execute(
                    "SELECT c.CHECK_CLAUSE AS clause, c.CONSTRAINT_NAME AS name "
                    "FROM information_schema.CHECK_CONSTRAINTS c "
                    "JOIN information_schema.TABLE_CONSTRAINTS t "
                    "ON t.CONSTRAINT_SCHEMA=c.CONSTRAINT_SCHEMA AND t.CONSTRAINT_NAME=c.CONSTRAINT_NAME "
                    "WHERE t.TABLE_SCHEMA=%s AND t.TABLE_NAME=%s AND t.CONSTRAINT_TYPE='CHECK'",
                    (database, table),
                )
                checks = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(f"SHOW CREATE TABLE {quote_ident(database)}.{quote_ident(table)}")
                create_row = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT ENGINE AS engine, TABLE_COLLATION AS collation, TABLE_COMMENT AS comment "
                    "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (database, table),
                )
                table_options = cursor.fetchone() or {}
            return {
                "database": database,
                "table": table,
                "columns": columns,
                "indexes": list(indexes.values()),
                "primary_key": indexes.get("PRIMARY", {}).get("columns", []),
                "foreign_keys": list(foreign_keys.values()),
                "checks": checks,
                "create_sql": create_row.get("Create Table", ""),
                "engine": table_options.get("engine") or "InnoDB",
                "charset": str(table_options.get("collation") or "utf8mb4_0900_ai_ci").split("_", 1)[0],
                "collation": table_options.get("collation") or "utf8mb4_0900_ai_ci",
                "comment": table_options.get("comment") or "",
            }
        finally:
            conn.close()

    def table_data(
        self,
        profile_id: str,
        database: str,
        table: str,
        page: int,
        page_size: int,
        filters: list[dict[str, Any]],
        sort: dict[str, str] | None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        schema = self.table_schema(profile_id, database, table)
        allowed = {column["name"] for column in schema["columns"]}
        clauses: list[str] = []
        values: list[Any] = []
        operators = {
            "eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
        }
        for item in filters:
            column = str(item.get("column", ""))
            op = str(item.get("operator", "eq"))
            if column not in allowed:
                raise DeeBeeError(f"筛选字段不存在：{column}")
            if op == "contains":
                clauses.append(f"{quote_ident(column)} LIKE %s")
                values.append(f"%{item.get('value', '')}%")
            elif op in ("is_null", "not_null"):
                clauses.append(f"{quote_ident(column)} IS {'NOT ' if op == 'not_null' else ''}NULL")
            elif op in operators:
                clauses.append(f"{quote_ident(column)} {operators[op]} %s")
                values.append(item.get("value"))
            else:
                raise DeeBeeError(f"不支持的筛选运算符：{op}")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = ""
        if sort and sort.get("column") in allowed:
            direction = "DESC" if str(sort.get("direction", "asc")).lower() == "desc" else "ASC"
            order = f" ORDER BY {quote_ident(str(sort['column']))} {direction}"
        elif schema["primary_key"]:
            order = " ORDER BY " + ", ".join(quote_ident(item) for item in schema["primary_key"])
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                target = f"{quote_ident(database)}.{quote_ident(table)}"
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM (SELECT 1 FROM {target}{where} LIMIT %s) AS limited_rows",
                    [*values, limit + 1],
                )
                detected_total = int((cursor.fetchone() or {}).get("total", 0))
                total = min(detected_total, limit)
                limited = detected_total > limit
                offset = max(page - 1, 0) * page_size
                fetch_size = max(0, min(page_size, limit - offset))
                rows: list[dict[str, Any]] = []
                if fetch_size:
                    cursor.execute(
                        f"SELECT * FROM {target}{where}{order} LIMIT %s OFFSET %s",
                        [*values, fetch_size, offset],
                    )
                    rows = [clean_row(row) for row in cursor.fetchall()]
            return {
                "columns": schema["columns"], "primary_key": schema["primary_key"], "rows": rows,
                "page": page, "page_size": page_size, "total": total, "limited": limited,
            }
        finally:
            conn.close()

    def export_table(self, profile_id: str, database: str, table: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = self.table_schema(profile_id, database, table)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {quote_ident(table)}")
                rows = [clean_row(row) for row in cursor.fetchall()]
            return {"columns": schema["columns"], "rows": rows}
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            message = str(exc.args[1] if len(exc.args) > 1 else exc)
            raise DeeBeeError(message, code) from exc
        finally:
            conn.close()

    def insert_row(self, profile_id: str, database: str, table: str, values: dict[str, Any]) -> dict[str, Any]:
        schema = self.table_schema(profile_id, database, table)
        allowed = {column["name"] for column in schema["columns"]}
        fields = [field for field in values if field in allowed]
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                if fields:
                    placeholders = ", ".join(["%s"] * len(fields))
                    sql = f"INSERT INTO {quote_ident(table)} ({', '.join(quote_ident(f) for f in fields)}) VALUES ({placeholders})"
                    cursor.execute(sql, [values[field] for field in fields])
                else:
                    cursor.execute(f"INSERT INTO {quote_ident(table)} () VALUES ()")
                return {"affected_rows": cursor.rowcount, "last_insert_id": cursor.lastrowid}
        except pymysql.MySQLError as exc:
            raise DeeBeeError(str(exc.args[1] if len(exc.args) > 1 else exc), exc.args[0] if exc.args else None) from exc
        finally:
            conn.close()

    def update_row(
        self, profile_id: str, database: str, table: str, key: dict[str, Any], changes: dict[str, Any]
    ) -> dict[str, Any]:
        schema = self.table_schema(profile_id, database, table)
        primary = schema["primary_key"]
        if not primary or any(field not in key for field in primary):
            raise DeeBeeError("更新记录需要完整主键")
        allowed = {column["name"] for column in schema["columns"]}
        fields = [field for field in changes if field in allowed]
        if not fields:
            raise DeeBeeError("没有需要保存的修改")
        sets = ", ".join(f"{quote_ident(field)}=%s" for field in fields)
        where = " AND ".join(f"{quote_ident(field)} <=> %s" for field in primary)
        params = [changes[field] for field in fields] + [key[field] for field in primary]
        return self._mutation(profile_id, database, f"UPDATE {quote_ident(table)} SET {sets} WHERE {where} LIMIT 1", params)

    def delete_row(self, profile_id: str, database: str, table: str, key: dict[str, Any]) -> dict[str, Any]:
        schema = self.table_schema(profile_id, database, table)
        primary = schema["primary_key"]
        if not primary or any(field not in key for field in primary):
            raise DeeBeeError("删除记录需要完整主键")
        where = " AND ".join(f"{quote_ident(field)} <=> %s" for field in primary)
        return self._mutation(
            profile_id, database, f"DELETE FROM {quote_ident(table)} WHERE {where} LIMIT 1",
            [key[field] for field in primary],
        )

    def _mutation(self, profile_id: str, database: str, sql: str, params: list[Any]) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return {"affected_rows": cursor.rowcount}
        except pymysql.MySQLError as exc:
            raise DeeBeeError(str(exc.args[1] if len(exc.args) > 1 else exc), exc.args[0] if exc.args else None) from exc
        finally:
            conn.close()

    def bulk_insert(
        self, profile_id: str, database: str, table: str, rows: list[dict[str, Any]],
        *, cancelled: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not rows:
            raise DeeBeeError("导入文件没有数据")
        schema = self.table_schema(profile_id, database, table)
        allowed = {column["name"] for column in schema["columns"]}
        fields = [field for field in rows[0] if field in allowed]
        if not fields:
            raise DeeBeeError("导入文件的列与数据表不匹配")
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database, autocommit=False)
        try:
            with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(fields))
                sql = f"INSERT INTO {quote_ident(table)} ({', '.join(quote_ident(f) for f in fields)}) VALUES ({placeholders})"
                affected = 0
                for start in range(0, len(rows), 500):
                    if cancelled and cancelled.is_set():
                        raise DeeBeeError("任务已取消")
                    batch = rows[start : start + 500]
                    cursor.executemany(sql, [[row.get(field) for field in fields] for row in batch])
                    affected += max(cursor.rowcount, 0)
                    if progress:
                        progress(min(start + len(batch), len(rows)), len(rows))
                if cancelled and cancelled.is_set():
                    raise DeeBeeError("任务已取消")
            conn.commit()
            return {"affected_rows": affected, "columns": fields}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def preview_ddl(self, profile_id: str, spec: dict[str, Any], current_table: str | None = None) -> list[str]:
        database = str(spec.get("database", ""))
        table = str(spec.get("table", ""))
        if not database or not table:
            raise DeeBeeError("数据库和表名不能为空")
        columns = spec.get("columns") or []
        if not columns:
            raise DeeBeeError("数据表至少需要一个字段")
        if current_table:
            current = self.table_schema(profile_id, database, current_table)
            return self._alter_statements(database, current_table, current, spec)
        definitions = [self._column_sql(item) for item in columns]
        primary = [str(item) for item in spec.get("primary_key", []) if item]
        if primary:
            definitions.append("PRIMARY KEY (" + ", ".join(quote_ident(item) for item in primary) + ")")
        for index in spec.get("indexes", []):
            if index.get("name") == "PRIMARY":
                continue
            definitions.append(self._index_sql(index))
        for fk in spec.get("foreign_keys", []):
            definitions.append(self._foreign_key_sql(fk))
        for check in spec.get("checks", []):
            definitions.append(self._check_sql(check))
        body = ",\n  ".join(definitions)
        engine = str(spec.get("engine", "InnoDB"))
        charset = str(spec.get("charset", "utf8mb4"))
        collation = str(spec.get("collation", "utf8mb4_unicode_ci"))
        if not all(IDENTIFIER.fullmatch(item) for item in (engine, charset, collation)):
            raise DeeBeeError("表选项无效")
        comment = str(spec.get("comment", "")).replace("'", "''")
        options = f"ENGINE={engine} DEFAULT CHARSET={charset} COLLATE={collation}"
        if comment:
            options += f" COMMENT='{comment}'"
        return [f"CREATE TABLE {quote_ident(database)}.{quote_ident(table)} (\n  {body}\n) {options}"]

    def _column_sql(self, column: dict[str, Any]) -> str:
        name = str(column.get("name", ""))
        data_type = str(column.get("data_type", "")).strip().upper()
        if not name or not DATA_TYPE.fullmatch(data_type) or any(token in data_type for token in (";", "--", "/*", "*/")):
            raise DeeBeeError(f"字段 {name or '(未命名)'} 的类型无效")
        parts = [quote_ident(name), data_type]
        generation = str(column.get("generation", "")).strip()
        default = column.get("default")
        if generation:
            if any(token in generation for token in (";", "--", "/*", "*/")):
                raise DeeBeeError(f"字段 {name} 的生成表达式无效")
            storage = "STORED" if "STORED" in str(column.get("extra", "")).upper() else "VIRTUAL"
            parts.extend([f"GENERATED ALWAYS AS ({generation})", storage])
            parts.append("NULL" if column.get("nullable", True) else "NOT NULL")
        else:
            parts.append("NULL" if column.get("nullable", True) else "NOT NULL")
            if default is not None and default != "":
                raw = str(default)
                if raw.upper() in {"NULL", "CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()"} or re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
                    parts.extend(["DEFAULT", raw])
                else:
                    escaped = raw.replace("'", "''")
                    parts.extend(["DEFAULT", f"'{escaped}'"])
        extra = str(column.get("extra", "")).strip().upper()
        allowed_extra = {"", "AUTO_INCREMENT", "ON UPDATE CURRENT_TIMESTAMP", "AUTO_INCREMENT ON UPDATE CURRENT_TIMESTAMP", "VIRTUAL GENERATED", "STORED GENERATED"}
        if extra not in allowed_extra:
            raise DeeBeeError(f"字段 {name} 的附加属性无效")
        if extra and not generation:
            parts.append(extra)
        comment = str(column.get("comment", ""))
        if comment:
            parts.extend(["COMMENT", "'" + comment.replace("'", "''") + "'"])
        return " ".join(parts)

    def _index_sql(self, index: dict[str, Any]) -> str:
        name = str(index.get("name", ""))
        columns = [str(item) for item in index.get("columns", []) if item]
        if not name or not columns:
            raise DeeBeeError("索引名称和字段不能为空")
        index_type = str(index.get("type") or "BTREE").upper()
        if index_type not in {"BTREE", "HASH", "FULLTEXT"}:
            raise DeeBeeError(f"MySQL 不支持索引类型：{index_type}")
        if index_type == "FULLTEXT":
            if index.get("unique"):
                raise DeeBeeError("FULLTEXT 索引不能设置为唯一索引")
            return f"FULLTEXT KEY {quote_ident(name)} ({', '.join(quote_ident(item) for item in columns)})"
        prefix = "UNIQUE KEY" if index.get("unique") else "KEY"
        return f"{prefix} {quote_ident(name)} ({', '.join(quote_ident(item) for item in columns)}) USING {index_type}"

    def _foreign_key_sql(self, fk: dict[str, Any]) -> str:
        name = str(fk.get("name", ""))
        columns = [str(item) for item in fk.get("columns", []) if item]
        referenced_table = str(fk.get("referenced_table", ""))
        referenced = [str(item) for item in fk.get("referenced_columns", []) if item]
        if not name or not columns or not referenced_table or len(columns) != len(referenced):
            raise DeeBeeError("外键定义不完整")
        on_delete = str(fk.get("on_delete", "RESTRICT")).upper()
        on_update = str(fk.get("on_update", "RESTRICT")).upper()
        rules = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION"}
        if on_delete not in rules or on_update not in rules:
            raise DeeBeeError("外键动作无效")
        return (
            f"CONSTRAINT {quote_ident(name)} FOREIGN KEY ({', '.join(quote_ident(item) for item in columns)}) "
            f"REFERENCES {quote_ident(referenced_table)} ({', '.join(quote_ident(item) for item in referenced)}) "
            f"ON DELETE {on_delete} ON UPDATE {on_update}"
        )

    def _check_sql(self, check: dict[str, Any]) -> str:
        name = str(check.get("name", ""))
        clause = str(check.get("clause", "")).strip()
        if not name or not clause:
            raise DeeBeeError("CHECK 约束名称和表达式不能为空")
        if ";" in clause or "--" in clause or "/*" in clause or "*/" in clause:
            raise DeeBeeError("CHECK 表达式包含不安全的 SQL 标记")
        return f"CONSTRAINT {quote_ident(name)} CHECK ({clause})"

    def _alter_statements(
        self, database: str, table: str, current: dict[str, Any], desired: dict[str, Any]
    ) -> list[str]:
        target = f"{quote_ident(database)}.{quote_ident(table)}"
        statements: list[str] = []
        current_columns = {item["name"]: item for item in current["columns"]}
        rename_map: dict[str, str] = {}
        for column in desired.get("columns", []):
            old_name = str(column.get("original_name") or "")
            new_name = str(column.get("name") or "")
            if not old_name or old_name == new_name or old_name not in current_columns:
                continue
            if new_name in current_columns:
                raise DeeBeeError(f"字段改名目标已存在：{new_name}")
            statements.append(
                f"ALTER TABLE {target} RENAME COLUMN {quote_ident(old_name)} TO {quote_ident(new_name)}"
            )
            old = current_columns.pop(old_name)
            current_columns[new_name] = {**old, "name": new_name}
            rename_map[old_name] = new_name
        desired_columns = {str(item.get("name")): item for item in desired.get("columns", [])}
        for name, column in desired_columns.items():
            definition = self._column_sql(column)
            if name not in current_columns:
                statements.append(f"ALTER TABLE {target} ADD COLUMN {definition}")
            else:
                old = current_columns[name]
                normalized_old = {
                    "name": old["name"], "data_type": str(old["data_type"]).upper(),
                    "nullable": bool(old["nullable"]), "default": old["default"],
                    "extra": str(old["extra"]).upper(), "comment": old["comment"], "generation": old.get("generation", ""),
                }
                normalized_new = {
                    "name": name, "data_type": str(column.get("data_type", "")).upper(),
                    "nullable": bool(column.get("nullable", True)), "default": column.get("default"),
                    "extra": str(column.get("extra", "")).upper(), "comment": str(column.get("comment", "")), "generation": str(column.get("generation", "")),
                }
                if normalized_old != normalized_new:
                    statements.append(f"ALTER TABLE {target} MODIFY COLUMN {definition}")
        for name in current_columns:
            if name not in desired_columns:
                statements.append(f"ALTER TABLE {target} DROP COLUMN {quote_ident(name)}")
        current_pk = [rename_map.get(item, item) for item in current.get("primary_key", [])]
        desired_pk = desired.get("primary_key", [])
        if current_pk != desired_pk:
            if current_pk:
                statements.append(f"ALTER TABLE {target} DROP PRIMARY KEY")
            if desired_pk:
                statements.append(f"ALTER TABLE {target} ADD PRIMARY KEY ({', '.join(quote_ident(item) for item in desired_pk)})")
        current_indexes = {
            item["name"]: {
                **item,
                "columns": [rename_map.get(column, column) for column in item.get("columns", [])],
            }
            for item in current.get("indexes", []) if item["name"] != "PRIMARY"
        }
        desired_indexes = {item["name"]: item for item in desired.get("indexes", []) if item.get("name") != "PRIMARY"}
        for name, old in current_indexes.items():
            new = desired_indexes.get(name)
            if not new or old["columns"] != new.get("columns", []) or bool(old["unique"]) != bool(new.get("unique")) or str(old.get("type") or "BTREE").upper() != str(new.get("type") or "BTREE").upper():
                statements.append(f"ALTER TABLE {target} DROP INDEX {quote_ident(name)}")
        for name, index in desired_indexes.items():
            old = current_indexes.get(name)
            if not old or old["columns"] != index.get("columns", []) or bool(old["unique"]) != bool(index.get("unique")) or str(old.get("type") or "BTREE").upper() != str(index.get("type") or "BTREE").upper():
                statements.append(f"ALTER TABLE {target} ADD {self._index_sql(index)}")
        current_fks = {
            item["name"]: {
                **item,
                "columns": [rename_map.get(column, column) for column in item.get("columns", [])],
                "referenced_columns": [
                    rename_map.get(column, column) for column in item.get("referenced_columns", [])
                ] if item.get("referenced_table") in {table, desired.get("table", table)} else item.get("referenced_columns", []),
            }
            for item in current.get("foreign_keys", [])
        }
        desired_fks = {item["name"]: item for item in desired.get("foreign_keys", [])}
        for name, old in current_fks.items():
            new = desired_fks.get(name)
            if not new or any(old.get(key) != new.get(key) for key in ("columns", "referenced_table", "referenced_columns", "on_delete", "on_update")):
                statements.append(f"ALTER TABLE {target} DROP FOREIGN KEY {quote_ident(name)}")
        for name, fk in desired_fks.items():
            old = current_fks.get(name)
            if not old or any(old.get(key) != fk.get(key) for key in ("columns", "referenced_table", "referenced_columns", "on_delete", "on_update")):
                statements.append(f"ALTER TABLE {target} ADD {self._foreign_key_sql(fk)}")
        current_checks: dict[str, dict[str, Any]] = {}
        for item in current.get("checks", []):
            clause = str(item.get("clause", ""))
            for old_name, new_name in rename_map.items():
                clause = re.sub(rf"\b{re.escape(old_name)}\b", new_name, clause)
            current_checks[item["name"]] = {**item, "clause": clause}
        desired_checks = {item["name"]: item for item in desired.get("checks", [])}
        for name, old in current_checks.items():
            new = desired_checks.get(name)
            if not new or str(old.get("clause", "")).strip() != str(new.get("clause", "")).strip():
                statements.append(f"ALTER TABLE {target} DROP CHECK {quote_ident(name)}")
        for name, check in desired_checks.items():
            old = current_checks.get(name)
            if not old or str(old.get("clause", "")).strip() != str(check.get("clause", "")).strip():
                statements.append(f"ALTER TABLE {target} ADD {self._check_sql(check)}")
        option_parts: list[str] = []
        for field, keyword in (("engine", "ENGINE"), ("charset", "DEFAULT CHARACTER SET"), ("collation", "COLLATE")):
            desired_value = str(desired.get(field, current.get(field, "")))
            current_value = str(current.get(field, ""))
            if desired_value and desired_value != current_value:
                if not IDENTIFIER.fullmatch(desired_value):
                    raise DeeBeeError("表选项无效")
                option_parts.append(f"{keyword}={desired_value}")
        if str(desired.get("comment", "")) != str(current.get("comment", "")):
            option_parts.append("COMMENT='" + str(desired.get("comment", "")).replace("'", "''") + "'")
        if option_parts:
            statements.append(f"ALTER TABLE {target} " + " ".join(option_parts))
        return statements

    def apply_ddl(self, profile_id: str, database: str, statements: list[str]) -> dict[str, Any]:
        if not statements:
            return {"applied": 0, "statements": []}
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            return {"applied": len(statements), "statements": statements}
        except pymysql.MySQLError as exc:
            raise DeeBeeError(str(exc.args[1] if len(exc.args) > 1 else exc), exc.args[0] if exc.args else None) from exc
        finally:
            conn.close()


workbench = MySQLWorkbench()
