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
from dataclasses import asdict, dataclass
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # Allows a MySQL-only install to start with PostgreSQL disabled.
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

from .config import settings
from .mysql import DeeBeeError, clean_row


POSTGRES_DATA_TYPE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\s+[A-Za-z][A-Za-z0-9_]*)*"
    r"(?:\(\d+(?:\s*,\s*\d+)?\))?(?:\[\])*$"
)


@dataclass(frozen=True)
class PostgresProfile:
    id: str
    name: str
    host: str
    port: int
    user: str
    password: str
    default_database: str = "postgres"
    default_schema: str = "public"

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("password")
        value["driver"] = "postgresql"
        return value


@dataclass
class PostgresSession:
    id: str
    connection: Any
    profile_id: str
    database: str
    schema: str
    autocommit: bool
    workspace_id: str
    lock: threading.RLock
    running: bool = False


def quote_ident(value: str) -> str:
    if not value or "\x00" in value:
        raise DeeBeeError("无效的数据库标识符")
    return '"' + value.replace('"', '""') + '"'


def qualified(schema: str, name: str) -> str:
    return f"{quote_ident(schema or 'public')}.{quote_ident(name)}"


def _constraint_clause(definition: str, prefix: str) -> str:
    value = definition.strip()
    if value.upper().startswith(prefix + " (") and value.endswith(")"):
        return value[len(prefix) + 2 : -1]
    return value


class PostgresWorkbench:
    def __init__(self, include_default: bool = True) -> None:
        default = PostgresProfile(
            id="postgres-default",
            name=settings.postgres_name,
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            default_database=settings.postgres_database,
            default_schema=settings.postgres_schema,
        )
        self.profiles: dict[str, PostgresProfile] = {default.id: default} if include_default else {}
        self.sessions: dict[str, PostgresSession] = {}
        self._guard = threading.RLock()

    def _require_driver(self) -> None:
        if psycopg is None:
            raise DeeBeeError("PostgreSQL 驱动未安装，请安装 psycopg[binary]")

    def _connect(
        self, profile: PostgresProfile, database: str = "", *, autocommit: bool = True
    ) -> Any:
        self._require_driver()
        try:
            return psycopg.connect(  # type: ignore[union-attr]
                host=profile.host,
                port=profile.port,
                user=profile.user,
                password=profile.password,
                dbname=database or profile.default_database or "postgres",
                autocommit=autocommit,
                connect_timeout=10,
                row_factory=dict_row,
                options="-c statement_timeout=120000",
            )
        except Exception as exc:
            raise self._error(exc) from exc

    def _error(self, exc: Exception) -> DeeBeeError:
        message = getattr(exc, "diag", None)
        primary = getattr(message, "message_primary", None) if message else None
        code = getattr(exc, "sqlstate", None)
        return DeeBeeError(primary or str(exc), code)  # type: ignore[arg-type]

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.public() for profile in self.profiles.values()]

    def require_profile(self, profile_id: str) -> PostgresProfile:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise DeeBeeError("连接不存在")
        return profile

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self.test_connection(self.require_profile(profile_id))

    def test_connection(self, profile: PostgresProfile) -> dict[str, Any]:
        started = time.perf_counter()
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT version() AS version, current_user AS account, "
                    "current_database() AS database"
                )
                info = cursor.fetchone()
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                **(info or {}),
            }
        finally:
            conn.close()

    def create_session(
        self, profile_id: str, database: str, autocommit: bool,
        workspace_id: str = "", schema: str = "",
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        database = database or profile.default_database
        schema = schema or profile.default_schema or "public"
        conn = self._connect(profile, database, autocommit=autocommit)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SET search_path TO {quote_ident(schema)}, pg_catalog")
            if not autocommit:
                conn.commit()
        except Exception as exc:
            conn.close()
            raise self._error(exc) from exc
        session = PostgresSession(
            id=str(uuid.uuid4()), connection=conn, profile_id=profile_id,
            database=database, schema=schema, autocommit=autocommit,
            workspace_id=workspace_id, lock=threading.RLock(),
        )
        with self._guard:
            self.sessions[session.id] = session
        return self.session_public(session)

    def session_public(self, session: PostgresSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "profile_id": session.profile_id,
            "database": session.database,
            "schema": session.schema,
            "autocommit": session.autocommit,
            "workspace_id": session.workspace_id,
            "thread_id": session.connection.info.backend_pid,
        }

    def require_session(self, session_id: str) -> PostgresSession:
        with self._guard:
            session = self.sessions.get(session_id)
        if not session or session.connection.closed:
            raise DeeBeeError("查询会话已失效，请重新打开标签")
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
            ids = [key for key, value in self.sessions.items() if value.workspace_id == workspace_id]
        for session_id in ids:
            self.close_session(session_id)
        return len(ids)

    def set_autocommit(self, session_id: str, enabled: bool) -> dict[str, Any]:
        session = self.require_session(session_id)
        with session.lock:
            if session.connection.info.transaction_status != 0:
                session.connection.commit()
            session.connection.autocommit = enabled
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
        session = self.require_session(session_id)
        if not session.running:
            return False
        session.connection.cancel()
        return True

    def execute(self, session_id: str, sql: str, limit: int = 1000) -> dict[str, Any]:
        if not sql.strip():
            raise DeeBeeError("请输入 SQL")
        session = self.require_session(session_id)
        started = time.perf_counter()
        results: list[dict[str, Any]] = []
        with session.lock:
            try:
                session.running = True
                with session.connection.cursor() as cursor:
                    cursor.execute(sql)
                    index = 0
                    while True:
                        if cursor.description:
                            columns = [
                                {"name": item.name, "type": str(item.type_code), "nullable": True}
                                for item in cursor.description
                            ]
                            raw_rows = cursor.fetchmany(limit + 1)
                            results.append({
                                "index": index,
                                "kind": "rows",
                                "columns": columns,
                                "rows": [clean_row(row) for row in raw_rows[:limit]],
                                "row_count": min(len(raw_rows), limit),
                                "truncated": len(raw_rows) > limit,
                            })
                        else:
                            results.append({
                                "index": index, "kind": "mutation",
                                "affected_rows": max(cursor.rowcount, 0), "last_insert_id": 0,
                            })
                        index += 1
                        if not cursor.nextset():
                            break
                if session.autocommit:
                    session.connection.commit()
                status = {
                    "server_version": session.connection.info.server_version,
                    "database": session.database,
                    "schema": session.schema,
                    "backend_pid": session.connection.info.backend_pid,
                }
                return {
                    "ok": True, "results": results,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    "autocommit": session.autocommit, "warnings": [], "status": status,
                }
            except Exception as exc:
                if session.autocommit:
                    session.connection.rollback()
                raise self._error(exc) from exc
            finally:
                session.running = False

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT datname AS name, pg_encoding_to_char(encoding) AS charset, "
                    "datcollate AS collation FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def schemas(self, profile_id: str, database: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT n.nspname AS name, r.rolname AS owner, "
                    "(n.nspname LIKE 'pg_%' OR n.nspname = 'information_schema') AS system "
                    "FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner "
                    "WHERE n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%' "
                    "ORDER BY system, n.nspname"
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def objects(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT c.relname AS name, CASE c.relkind WHEN 'v' THEN 'VIEW' "
                    "WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'p' THEN 'PARTITIONED TABLE' ELSE 'TABLE' END AS object_type, "
                    "GREATEST(c.reltuples::bigint,0) AS estimated_rows, "
                    "obj_description(c.oid,'pg_class') AS comment, 'PostgreSQL' AS engine, "
                    "pg_relation_size(c.oid) AS data_length, pg_indexes_size(c.oid) AS index_length "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m') ORDER BY c.relname",
                    (schema,),
                )
                relations = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT p.oid AS object_id, p.proname AS name, CASE p.prokind WHEN 'p' THEN 'PROCEDURE' "
                    "ELSE 'FUNCTION' END AS object_type, pg_get_function_result(p.oid) AS data_type, "
                    "pg_get_function_identity_arguments(p.oid) AS identity_arguments "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=%s ORDER BY p.proname", (schema,),
                )
                routines = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT t.tgname AS name, 'TRIGGER' AS object_type, c.relname AS table_name, "
                    "CASE WHEN t.tgenabled='D' THEN 'DISABLED' ELSE 'ENABLED' END AS status "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY t.tgname", (schema,),
                )
                triggers = [clean_row(row) for row in cursor.fetchall()]
            return {
                "database": database, "schema": schema,
                "tables": [row for row in relations if "TABLE" in row["object_type"]],
                "views": [row for row in relations if "VIEW" in row["object_type"]],
                "routines": routines, "triggers": triggers, "events": [],
            }
        finally:
            conn.close()

    def catalog(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT c.relname AS table_name, CASE WHEN c.relkind IN ('v','m') THEN 'VIEW' ELSE 'TABLE' END AS object_type, "
                    "a.attname AS column_name, format_type(a.atttypid,a.atttypmod) AS data_type, "
                    "NOT a.attnotnull AS nullable, a.attnum AS position "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                    "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m') ORDER BY c.relname,a.attnum",
                    (schema,),
                )
                tables: dict[str, dict[str, Any]] = {}
                for row in cursor.fetchall():
                    table = tables.setdefault(row["table_name"], {
                        "name": row["table_name"], "object_type": row["object_type"], "columns": []
                    })
                    table["columns"].append({
                        "name": row["column_name"], "data_type": row["data_type"],
                        "nullable": row["nullable"], "key": "",
                    })
                cursor.execute(
                    "SELECT p.proname AS name, CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS object_type, "
                    "pg_get_function_result(p.oid) AS data_type FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY p.proname",
                    (schema,),
                )
                routines = [clean_row(row) for row in cursor.fetchall()]
            return {"database": database, "schema": schema, "tables": list(tables.values()), "routines": routines}
        finally:
            conn.close()

    def create_database(
        self, profile_id: str, name: str, charset: str = "UTF8", collation: str = ""
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, autocommit=True)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE {quote_ident(name)} ENCODING 'UTF8'")
            return {"ok": True, "name": name, "charset": "UTF8", "collation": collation}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def drop_database(self, profile_id: str, name: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        if name == profile.default_database:
            raise DeeBeeError("不能删除当前连接使用的默认数据库")
        conn = self._connect(profile, autocommit=True)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE {quote_ident(name)}")
            return {"ok": True}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def table_schema(
        self, profile_id: str, database: str, table: str, schema: str = ""
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS data_type, "
                    "NOT a.attnotnull AS nullable, pg_get_expr(d.adbin,d.adrelid) AS default, "
                    "CASE WHEN a.attidentity<>'' THEN 'IDENTITY' WHEN a.attgenerated<>'' THEN 'GENERATED' ELSE '' END AS extra, "
                    "col_description(a.attrelid,a.attnum) AS comment, a.attnum AS position "
                    "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                    "WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                    (schema, table),
                )
                columns = []
                for raw in cursor.fetchall():
                    row = clean_row(raw)
                    default = str(row.get("default") or "")
                    generation = ""
                    if row.get("extra") == "GENERATED":
                        generation = default
                        row["default"] = None
                    elif row.get("extra") == "IDENTITY":
                        row["default"] = None
                    elif default.startswith("nextval("):
                        row["extra"] = "AUTO_INCREMENT"
                        row["default"] = None
                    row["comment"] = row.get("comment") or ""
                    row["generation"] = generation
                    columns.append(row)
                if not columns:
                    raise DeeBeeError("表不存在")
                cursor.execute(
                    "SELECT ic.relname AS name, i.indisunique AS unique, am.amname AS type, i.indisprimary AS primary, "
                    "ARRAY(SELECT pg_get_indexdef(i.indexrelid,k+1,true) FROM generate_subscripts(i.indkey,1) AS k ORDER BY k) AS columns "
                    "FROM pg_index i JOIN pg_class tc ON tc.oid=i.indrelid JOIN pg_namespace n ON n.oid=tc.relnamespace "
                    "JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am am ON am.oid=ic.relam "
                    "WHERE n.nspname=%s AND tc.relname=%s ORDER BY ic.relname", (schema, table),
                )
                index_rows = cursor.fetchall()
                primary_key = next((list(row["columns"]) for row in index_rows if row["primary"]), [])
                primary_key_name = next((row["name"] for row in index_rows if row["primary"]), "")
                indexes = [
                    {"name": row["name"], "unique": row["unique"], "type": row["type"], "columns": list(row["columns"])}
                    for row in index_rows if not row["primary"]
                ]
                cursor.execute(
                    "SELECT con.conname AS name, ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum,ord) "
                    "JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum ORDER BY k.ord) AS columns, "
                    "rn.nspname AS referenced_schema, rc.relname AS referenced_table, "
                    "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum,ord) "
                    "JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=k.attnum ORDER BY k.ord) AS referenced_columns, "
                    "CASE con.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END AS on_delete, "
                    "CASE con.confupdtype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END AS on_update "
                    "FROM pg_constraint con JOIN pg_class tc ON tc.oid=con.conrelid JOIN pg_namespace n ON n.oid=tc.relnamespace "
                    "JOIN pg_class rc ON rc.oid=con.confrelid JOIN pg_namespace rn ON rn.oid=rc.relnamespace "
                    "WHERE con.contype='f' AND n.nspname=%s AND tc.relname=%s ORDER BY con.conname", (schema, table),
                )
                foreign_keys = [clean_row(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT con.conname AS name, pg_get_constraintdef(con.oid,true) AS definition "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE con.contype='c' AND n.nspname=%s AND c.relname=%s ORDER BY con.conname", (schema, table),
                )
                checks = [{"name": row["name"], "clause": _constraint_clause(row["definition"], "CHECK")} for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT obj_description(c.oid,'pg_class') AS comment FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s", (schema, table),
                )
                meta = cursor.fetchone() or {}
            definitions = [self._column_sql(column) for column in columns]
            if primary_key:
                definitions.append("PRIMARY KEY (" + ", ".join(quote_ident(item) for item in primary_key) + ")")
            definitions.extend(self._foreign_sql(item) for item in foreign_keys)
            definitions.extend(
                f"CONSTRAINT {quote_ident(item['name'])} CHECK ({item['clause']})"
                for item in checks
            )
            create_sql = f"CREATE TABLE {qualified(schema, table)} (\n  " + ",\n  ".join(definitions) + "\n);"
            if indexes:
                create_sql += "\n" + "\n".join(
                    self._index_sql(schema, table, item) + ";" for item in indexes
                )
            if meta.get("comment"):
                create_sql += "\nCOMMENT ON TABLE " + qualified(schema, table) + " IS '" + str(meta["comment"]).replace("'", "''") + "';"
            for column in columns:
                if column.get("comment"):
                    create_sql += "\nCOMMENT ON COLUMN " + qualified(schema, table) + "." + quote_ident(column["name"]) + " IS '" + str(column["comment"]).replace("'", "''") + "';"
            return {
                "database": database, "schema": schema, "table": table, "columns": columns,
                "indexes": indexes, "primary_key": primary_key, "primary_key_name": primary_key_name,
                "foreign_keys": foreign_keys,
                "checks": checks, "create_sql": create_sql, "engine": "PostgreSQL",
                "charset": "UTF8", "collation": "", "comment": meta.get("comment") or "",
            }
        except DeeBeeError:
            raise
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def table_data(
        self, profile_id: str, database: str, table: str, page: int, page_size: int,
        filters: list[dict[str, Any]], sort: dict[str, Any] | None, schema: str = "", limit: int = 1000,
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        meta = self.table_schema(profile_id, database, table, schema)
        names = {column["name"] for column in meta["columns"]}
        conditions: list[str] = []
        params: list[Any] = []
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for item in filters:
            column = item.get("column", "")
            if column not in names:
                raise DeeBeeError("筛选字段不存在")
            operator = item.get("operator", "eq")
            field = quote_ident(column)
            if operator == "is_null":
                conditions.append(f"{field} IS NULL")
            elif operator == "not_null":
                conditions.append(f"{field} IS NOT NULL")
            elif operator == "contains":
                conditions.append(f"CAST({field} AS TEXT) ILIKE %s")
                params.append(f"%{item.get('value', '')}%")
            else:
                conditions.append(f"{field} {operators.get(operator, '=')} %s")
                params.append(item.get("value"))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = ""
        if sort:
            if sort.get("column") not in names:
                raise DeeBeeError("排序字段不存在")
            direction = "DESC" if str(sort.get("direction")).lower() == "desc" else "ASC"
            order = f" ORDER BY {quote_ident(str(sort['column']))} {direction}"
        target = qualified(schema, table)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM (SELECT 1 FROM {target}{where} LIMIT %s) AS limited_rows",
                    [*params, limit + 1],
                )
                detected_total = int(cursor.fetchone()["total"])
                total = min(detected_total, limit)
                limited = detected_total > limit
                offset = (page - 1) * page_size
                fetch_size = max(0, min(page_size, limit - offset))
                rows: list[dict[str, Any]] = []
                if fetch_size:
                    cursor.execute(
                        f"SELECT * FROM {target}{where}{order} LIMIT %s OFFSET %s",
                        [*params, fetch_size, offset],
                    )
                    rows = [clean_row(row) for row in cursor.fetchall()]
            return {"columns": meta["columns"], "primary_key": meta["primary_key"], "rows": rows, "page": page, "page_size": page_size, "total": total, "limited": limited}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def _mutation(self, profile_id: str, database: str, sql: str, params: list[Any]) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                returned = cursor.fetchone() if cursor.description else None
                affected = max(cursor.rowcount, 0)
            return {"ok": True, "affected_rows": affected, "last_insert_id": next(iter(returned.values()), 0) if returned else 0, "row": clean_row(returned) if returned else None}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def insert_row(self, profile_id: str, database: str, table: str, values: dict[str, Any], schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        if not values:
            sql = f"INSERT INTO {qualified(schema, table)} DEFAULT VALUES RETURNING *"
            return self._mutation(profile_id, database, sql, [])
        fields = list(values)
        sql = f"INSERT INTO {qualified(schema, table)} ({', '.join(quote_ident(field) for field in fields)}) VALUES ({', '.join(['%s'] * len(fields))}) RETURNING *"
        return self._mutation(profile_id, database, sql, [values[field] for field in fields])

    def update_row(self, profile_id: str, database: str, table: str, key: dict[str, Any], changes: dict[str, Any], schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        if not key or not changes:
            raise DeeBeeError("更新条件和字段不能为空")
        sql = f"UPDATE {qualified(schema, table)} SET " + ", ".join(f"{quote_ident(name)}=%s" for name in changes) + " WHERE " + " AND ".join(f"{quote_ident(name)} IS NOT DISTINCT FROM %s" for name in key)
        return self._mutation(profile_id, database, sql, [*changes.values(), *key.values()])

    def delete_row(self, profile_id: str, database: str, table: str, key: dict[str, Any], schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        if not key:
            raise DeeBeeError("删除条件不能为空")
        sql = f"DELETE FROM {qualified(schema, table)} WHERE " + " AND ".join(f"{quote_ident(name)} IS NOT DISTINCT FROM %s" for name in key)
        return self._mutation(profile_id, database, sql, list(key.values()))

    def bulk_insert(self, profile_id: str, database: str, table: str, rows: list[dict[str, Any]], schema: str = "") -> dict[str, Any]:
        if not rows:
            return {"ok": True, "affected_rows": 0}
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        fields = list(rows[0])
        if not fields or any(list(row) != fields for row in rows):
            raise DeeBeeError("导入数据的字段必须一致")
        sql = f"INSERT INTO {qualified(schema, table)} ({', '.join(quote_ident(field) for field in fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, [[row.get(field) for field in fields] for row in rows])
            return {"ok": True, "affected_rows": len(rows)}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def _column_sql(self, column: dict[str, Any]) -> str:
        name = quote_ident(str(column.get("name", "")))
        data_type = str(column.get("data_type", "TEXT")).strip()
        extra = str(column.get("extra", "")).upper()
        if not POSTGRES_DATA_TYPE.fullmatch(data_type):
            raise DeeBeeError(f"不支持的数据类型：{data_type}")
        if "AUTO_INCREMENT" in extra:
            data_type = "BIGSERIAL" if data_type.upper().startswith("BIG") else "SERIAL"
        parts = [name, data_type]
        if "IDENTITY" in extra:
            parts.append("GENERATED ALWAYS AS IDENTITY")
        if "GENERATED" in extra and column.get("generation"):
            parts.append(f"GENERATED ALWAYS AS ({column['generation']}) STORED")
        if not column.get("nullable", True):
            parts.append("NOT NULL")
        default = column.get("default")
        if default not in (None, "") and "SERIAL" not in data_type.upper():
            parts.append(f"DEFAULT {self._default_sql(default)}")
        return " ".join(parts)

    @staticmethod
    def _default_sql(default: Any) -> str:
        raw = str(default)
        safe_expression = (
            re.fullmatch(r"[-+]?\d+(?:\.\d+)?|NULL|TRUE|FALSE|CURRENT_(?:DATE|TIME|TIMESTAMP)|now\(\)", raw, re.I)
            or re.fullmatch(r"'(?:''|[^'])*'::[A-Za-z][A-Za-z0-9_ ]*(?:\(\d+(?:,\d+)?\))?", raw)
            or re.fullmatch(r"nextval\('[A-Za-z0-9_.'\"]+'::regclass\)", raw)
        )
        return raw if safe_expression else "'" + raw.replace("'", "''") + "'"

    def _index_sql(self, schema: str, table: str, index: dict[str, Any]) -> str:
        unique = "UNIQUE " if index.get("unique") else ""
        index_type = str(index.get("type") or "BTREE").upper()
        if index_type not in {"BTREE", "HASH", "GIN", "GIST", "BRIN"}:
            raise DeeBeeError(f"PostgreSQL 不支持索引类型：{index_type}")
        if index.get("unique") and index_type != "BTREE":
            raise DeeBeeError("PostgreSQL 只有 BTREE 索引支持唯一约束")
        columns = ", ".join(quote_ident(str(item)) for item in index.get("columns", []))
        return f"CREATE {unique}INDEX {quote_ident(str(index.get('name', '')))} ON {qualified(schema, table)} USING {index_type} ({columns})"

    def _foreign_sql(self, fk: dict[str, Any]) -> str:
        columns = ", ".join(quote_ident(str(item)) for item in fk.get("columns", []))
        ref_columns = ", ".join(quote_ident(str(item)) for item in fk.get("referenced_columns", []))
        ref_schema = str(fk.get("referenced_schema") or "")
        ref_table = qualified(ref_schema, str(fk.get("referenced_table", ""))) if ref_schema else quote_ident(str(fk.get("referenced_table", "")))
        on_delete = str(fk.get("on_delete", "NO ACTION")).upper()
        on_update = str(fk.get("on_update", "NO ACTION")).upper()
        allowed = {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}
        if on_delete not in allowed or on_update not in allowed:
            raise DeeBeeError("不支持的外键动作")
        return f"CONSTRAINT {quote_ident(str(fk.get('name', '')))} FOREIGN KEY ({columns}) REFERENCES {ref_table} ({ref_columns}) ON DELETE {on_delete} ON UPDATE {on_update}"

    def preview_ddl(self, profile_id: str, spec: dict[str, Any], current_table: str | None = None) -> list[str]:
        database = str(spec.get("database", ""))
        profile = self.require_profile(profile_id)
        schema = str(spec.get("schema") or profile.default_schema)
        table = str(spec.get("table", ""))
        columns = list(spec.get("columns") or [])
        primary_key = list(spec.get("primary_key") or [])
        indexes = list(spec.get("indexes") or [])
        foreign_keys = list(spec.get("foreign_keys") or [])
        checks = list(spec.get("checks") or [])
        if not table or not columns:
            raise DeeBeeError("表名和字段不能为空")
        for column in columns:
            self._column_sql(column)
        if not current_table:
            definitions = [self._column_sql(column) for column in columns]
            if primary_key:
                definitions.append("PRIMARY KEY (" + ", ".join(quote_ident(item) for item in primary_key) + ")")
            definitions.extend(self._foreign_sql(fk) for fk in foreign_keys)
            definitions.extend(f"CONSTRAINT {quote_ident(str(check.get('name','')))} CHECK ({check.get('clause','')})" for check in checks)
            statements = [f"CREATE TABLE {qualified(schema, table)} (\n  " + ",\n  ".join(definitions) + "\n)"]
            statements.extend(self._index_sql(schema, table, index) for index in indexes)
            if spec.get("comment"):
                statements.append(f"COMMENT ON TABLE {qualified(schema, table)} IS '" + str(spec["comment"]).replace("'", "''") + "'")
            for column in columns:
                if column.get("comment"):
                    statements.append(
                        f"COMMENT ON COLUMN {qualified(schema, table)}.{quote_ident(str(column['name']))} IS '"
                        + str(column["comment"]).replace("'", "''") + "'"
                    )
            return statements
        source = self.table_schema(profile_id, database, current_table, schema)
        target = qualified(schema, current_table)
        statements: list[str] = []
        if table != current_table:
            statements.append(f"ALTER TABLE {target} RENAME TO {quote_ident(table)}")
            target = qualified(schema, table)
        old_columns = {item["name"]: item for item in source["columns"]}
        new_columns = {str(item.get("name")): item for item in columns}
        for name in old_columns.keys() - new_columns.keys():
            statements.append(f"ALTER TABLE {target} DROP COLUMN {quote_ident(name)}")
        for name, column in new_columns.items():
            if name not in old_columns:
                statements.append(f"ALTER TABLE {target} ADD COLUMN {self._column_sql(column)}")
                continue
            old = old_columns[name]
            old_extra = str(old.get("extra") or "").upper()
            new_extra = str(column.get("extra") or "").upper()
            old_generation = str(old.get("generation") or "").strip()
            new_generation = str(column.get("generation") or "").strip()
            if old_generation != new_generation or ("GENERATED" in old_extra) != ("GENERATED" in new_extra):
                raise DeeBeeError(f"PostgreSQL 不能安全地原地修改生成字段 {name}；请新建字段并迁移引用")
            if ("AUTO_INCREMENT" in old_extra) != ("AUTO_INCREMENT" in new_extra):
                raise DeeBeeError(f"PostgreSQL 不能在现有字段 {name} 上切换 SERIAL；请改用 IDENTITY")
            if str(old["data_type"]).lower() != str(column.get("data_type", "")).lower():
                statements.append(f"ALTER TABLE {target} ALTER COLUMN {quote_ident(name)} TYPE {column['data_type']} USING {quote_ident(name)}::{column['data_type']}")
            if bool(old["nullable"]) != bool(column.get("nullable", True)):
                statements.append(f"ALTER TABLE {target} ALTER COLUMN {quote_ident(name)} " + ("DROP" if column.get("nullable", True) else "SET") + " NOT NULL")
            old_default = old.get("default")
            new_default = column.get("default")
            if ("IDENTITY" in old_extra) != ("IDENTITY" in new_extra):
                if "IDENTITY" in new_extra and old_default not in (None, ""):
                    statements.append(f"ALTER TABLE {target} ALTER COLUMN {quote_ident(name)} DROP DEFAULT")
                identity_action = "ADD GENERATED ALWAYS AS IDENTITY" if "IDENTITY" in new_extra else "DROP IDENTITY IF EXISTS"
                statements.append(f"ALTER TABLE {target} ALTER COLUMN {quote_ident(name)} {identity_action}")
            if old_default != new_default and "IDENTITY" not in new_extra and "GENERATED" not in new_extra:
                default_action = "DROP DEFAULT" if new_default in (None, "") else f"SET DEFAULT {self._default_sql(new_default)}"
                statements.append(f"ALTER TABLE {target} ALTER COLUMN {quote_ident(name)} {default_action}")
            if str(old.get("comment") or "") != str(column.get("comment") or ""):
                comment = column.get("comment")
                value = "NULL" if not comment else "'" + str(comment).replace("'", "''") + "'"
                statements.append(f"COMMENT ON COLUMN {target}.{quote_ident(name)} IS {value}")
        if source["primary_key"] != primary_key:
            if source["primary_key"]:
                statements.append(
                    f"ALTER TABLE {target} DROP CONSTRAINT {quote_ident(str(source['primary_key_name']))}"
                )
            if primary_key:
                statements.append(
                    f"ALTER TABLE {target} ADD PRIMARY KEY ("
                    + ", ".join(quote_ident(item) for item in primary_key) + ")"
                )
        old_indexes = {item["name"]: item for item in source["indexes"]}
        new_indexes = {str(item.get("name")): item for item in indexes}
        for name in old_indexes.keys() - new_indexes.keys():
            statements.append(f"DROP INDEX {qualified(schema, name)}")
        for name, index in new_indexes.items():
            if name not in old_indexes or old_indexes[name]["columns"] != index.get("columns") or old_indexes[name]["unique"] != bool(index.get("unique")) or str(old_indexes[name].get("type") or "BTREE").upper() != str(index.get("type") or "BTREE").upper():
                if name in old_indexes:
                    statements.append(f"DROP INDEX {qualified(schema, name)}")
                statements.append(self._index_sql(schema, table, index))
        old_fk = {item["name"]: item for item in source["foreign_keys"]}
        new_fk = {str(item.get("name")): item for item in foreign_keys}
        for name in old_fk.keys() - new_fk.keys():
            statements.append(f"ALTER TABLE {target} DROP CONSTRAINT {quote_ident(name)}")
        for name, fk in new_fk.items():
            if name not in old_fk or old_fk[name] != fk:
                if name in old_fk:
                    statements.append(f"ALTER TABLE {target} DROP CONSTRAINT {quote_ident(name)}")
                statements.append(f"ALTER TABLE {target} ADD {self._foreign_sql(fk)}")
        old_checks = {item["name"]: item for item in source["checks"]}
        new_checks = {str(item.get("name")): item for item in checks}
        for name in old_checks.keys() - new_checks.keys():
            statements.append(f"ALTER TABLE {target} DROP CONSTRAINT {quote_ident(name)}")
        for name, check in new_checks.items():
            if name not in old_checks or old_checks[name]["clause"] != check.get("clause"):
                if name in old_checks:
                    statements.append(f"ALTER TABLE {target} DROP CONSTRAINT {quote_ident(name)}")
                statements.append(f"ALTER TABLE {target} ADD CONSTRAINT {quote_ident(name)} CHECK ({check.get('clause','')})")
        if str(source.get("comment") or "") != str(spec.get("comment") or ""):
            comment = spec.get("comment")
            value = "NULL" if not comment else "'" + str(comment).replace("'", "''") + "'"
            statements.append(f"COMMENT ON TABLE {target} IS {value}")
        return statements

    def apply_ddl(self, profile_id: str, database: str, statements: list[str]) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database, autocommit=False)
        try:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            conn.commit()
            return {"ok": True, "statements": len(statements)}
        except Exception as exc:
            conn.rollback()
            raise self._error(exc) from exc
        finally:
            conn.close()

    def table_action(
        self, profile_id: str, database: str, table: str, action: str,
        *, target: str = "", with_data: bool = False, schema: str = "",
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        source = qualified(schema, table)
        if action == "drop":
            sql = f"DROP TABLE {source}"
        elif action in {"empty", "truncate"}:
            sql = f"TRUNCATE TABLE {source}"
        elif action == "rename":
            sql = f"ALTER TABLE {source} RENAME TO {quote_ident(target)}"
        elif action == "duplicate":
            sql = f"CREATE TABLE {qualified(schema, target)} AS TABLE {source}" if with_data else f"CREATE TABLE {qualified(schema, target)} (LIKE {source} INCLUDING ALL)"
        elif action in {"analyze", "optimize"}:
            sql = f"ANALYZE {source}"
        elif action in {"check", "repair"}:
            raise DeeBeeError("PostgreSQL 不支持该维护命令")
        else:
            raise DeeBeeError("不支持的表操作")
        result = self.execute_script(profile_id, database, sql, schema)
        return {"ok": True, "results": result.get("results", [])}

    def object_ddl(self, profile_id: str, database: str, kind: str, name: str, object_id: int | None = None, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                if kind in {"view", "materialized view"}:
                    cursor.execute("SELECT pg_get_viewdef(%s::regclass,true) AS body", (f"{quote_ident(schema)}.{quote_ident(name)}",))
                    row = cursor.fetchone()
                    keyword = "CREATE MATERIALIZED VIEW" if kind == "materialized view" else "CREATE OR REPLACE VIEW"
                    sql = f"{keyword} {qualified(schema, name)} AS\n{str(row['body']).rstrip(';')};" if row else ""
                elif kind in {"function", "procedure"}:
                    expected_kind = "p" if kind == "procedure" else "f"
                    if object_id is not None:
                        cursor.execute(
                            "SELECT pg_get_functiondef(p.oid) AS sql FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid=%s AND n.nspname=%s AND p.proname=%s AND p.prokind=%s",
                            (object_id, schema, name, expected_kind),
                        )
                    else:
                        cursor.execute(
                            "SELECT pg_get_functiondef(p.oid) AS sql FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s AND p.proname=%s AND p.prokind=%s ORDER BY p.oid LIMIT 1",
                            (schema, name, expected_kind),
                        )
                    row = cursor.fetchone(); sql = row["sql"] if row else ""
                elif kind == "trigger":
                    cursor.execute(
                        "SELECT pg_get_triggerdef(t.oid,true) AS sql FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND t.tgname=%s AND NOT t.tgisinternal",
                        (schema, name),
                    )
                    row = cursor.fetchone(); sql = (row["sql"] + ";") if row else ""
                elif kind == "table":
                    sql = self.table_schema(profile_id, database, name, schema)["create_sql"]
                else:
                    raise DeeBeeError("不支持的对象类型")
            if not sql:
                raise DeeBeeError("对象不存在")
            return {"sql": sql}
        finally:
            conn.close()

    def object_action(self, profile_id: str, database: str, kind: str, name: str, action: str, object_id: int | None = None, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        if action in {"enable", "disable"} and kind == "trigger":
            objects = self.objects(profile_id, database, schema)["triggers"]
            item = next((row for row in objects if row["name"] == name), None)
            if not item:
                raise DeeBeeError("触发器不存在")
            verb = "ENABLE" if action == "enable" else "DISABLE"
            return self.execute_script(profile_id, database, f"ALTER TABLE {qualified(schema, item['table_name'])} {verb} TRIGGER {quote_ident(name)}", schema)
        if action != "drop":
            raise DeeBeeError("PostgreSQL 不支持该对象操作")
        normalized = "MATERIALIZED VIEW" if kind == "materialized view" else kind.upper()
        if normalized not in {"VIEW", "MATERIALIZED VIEW", "FUNCTION", "PROCEDURE", "TRIGGER"}:
            raise DeeBeeError("不支持的对象类型")
        if normalized == "TRIGGER":
            item = next((row for row in self.objects(profile_id, database, schema)["triggers"] if row["name"] == name), None)
            if not item:
                raise DeeBeeError("触发器不存在")
            sql = f"DROP TRIGGER {quote_ident(name)} ON {qualified(schema, item['table_name'])}"
        elif normalized in {"FUNCTION", "PROCEDURE"}:
            if object_id is None:
                raise DeeBeeError("缺少函数/过程签名，请刷新对象列表后重试")
            conn = self._connect(profile, database)
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_get_function_identity_arguments(p.oid) AS args, p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid=%s AND n.nspname=%s AND p.proname=%s",
                        (object_id, schema, name),
                    )
                    routine = cursor.fetchone()
            finally:
                conn.close()
            expected_kind = "p" if normalized == "PROCEDURE" else "f"
            if not routine or routine["prokind"] != expected_kind:
                raise DeeBeeError("函数/过程不存在或签名已变化")
            sql = f"DROP {normalized} {qualified(schema, name)}({routine['args']})"
        else:
            sql = f"DROP {normalized} {qualified(schema, name)}"
        return self.execute_script(profile_id, database, sql, schema)

    def search_objects(self, profile_id: str, database: str, term: str, schema: str = "") -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        pattern = f"%{term}%"
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 'table' AS kind,c.relname AS name,c.relkind::text AS detail FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m') AND c.relname ILIKE %s "
                    "UNION ALL SELECT 'column',c.relname||'.'||a.attname,format_type(a.atttypid,a.atttypmod) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped AND a.attname ILIKE %s ORDER BY name LIMIT 200",
                    (schema, pattern, schema, pattern),
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def grants(self, profile_id: str, database: str = "", table: str = "", schema: str = "") -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                if table:
                    cursor.execute(
                        "SELECT grantee,privilege_type,is_grantable FROM information_schema.table_privileges WHERE table_schema=%s AND table_name=%s ORDER BY grantee,privilege_type",
                        (schema, table),
                    )
                else:
                    cursor.execute(
                        "SELECT r.rolname AS grantee, p.privilege_type, "
                        "CASE WHEN n.nspowner=r.oid OR r.rolsuper THEN 'YES' ELSE 'NO' END AS is_grantable "
                        "FROM pg_namespace n CROSS JOIN pg_roles r "
                        "CROSS JOIN (VALUES ('USAGE'),('CREATE')) AS p(privilege_type) "
                        "WHERE n.nspname=%s AND has_schema_privilege(r.oid,n.oid,p.privilege_type) "
                        "ORDER BY r.rolname,p.privilege_type",
                        (schema,),
                    )
                return [clean_row(row) for row in cursor.fetchall()]
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def execute_script(self, profile_id: str, database: str, sql: str, schema: str = "") -> dict[str, Any]:
        session = self.create_session(profile_id, database, True, schema=schema)
        try:
            result = self.execute(session["id"], sql, 1000)
            return {"ok": True, "statements": len(result["results"]), "results": result["results"]}
        finally:
            self.close_session(session["id"])

    def _literal(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float, decimal.Decimal)):
            return str(value)
        if isinstance(value, (dt.date, dt.time)):
            value = value.isoformat()
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return "'" + str(value).replace("'", "''") + "'"

    def dump_sql(self, profile_id: str, database: str, table: str = "", include_data: bool = True, schema: str = "") -> str:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema
        names = [table] if table else [item["name"] for item in self.objects(profile_id, database, schema)["tables"]]
        chunks: list[str] = [f"-- DeeBee PostgreSQL dump: {database}.{schema}"]
        conn = self._connect(profile, database)
        try:
            for name in names:
                chunks.extend(["", self.table_schema(profile_id, database, name, schema)["create_sql"]])
                if include_data:
                    with conn.cursor() as cursor:
                        cursor.execute(f"SELECT * FROM {qualified(schema, name)}")
                        rows = cursor.fetchall()
                        columns = [item.name for item in cursor.description or []]
                    for row in rows:
                        chunks.append(
                            f"INSERT INTO {qualified(schema, name)} ({', '.join(quote_ident(item) for item in columns)}) VALUES ({', '.join(self._literal(row[item]) for item in columns)});"
                        )
            return "\n".join(chunks) + "\n"
        finally:
            conn.close()

    def generate_data(self, profile_id: str, database: str, table: str, count: int, schema: str = "") -> dict[str, Any]:
        meta = self.table_schema(profile_id, database, table, schema)
        columns = [item for item in meta["columns"] if item.get("extra") not in {"IDENTITY", "GENERATED"} and item.get("default") is None]
        rows: list[dict[str, Any]] = []
        for index in range(count):
            row: dict[str, Any] = {}
            for column in columns:
                kind = str(column["data_type"]).lower()
                if column["nullable"] and random.random() < 0.08:
                    row[column["name"]] = None
                elif any(token in kind for token in ("int", "serial")):
                    row[column["name"]] = random.randint(1, 1_000_000)
                elif any(token in kind for token in ("numeric", "decimal", "real", "double")):
                    row[column["name"]] = round(random.random() * 10000, 2)
                elif "bool" in kind:
                    row[column["name"]] = bool(random.getrandbits(1))
                elif "date" in kind or "time" in kind:
                    row[column["name"]] = dt.datetime.now().isoformat(sep=" ")
                elif "json" in kind:
                    row[column["name"]] = json.dumps({"index": index + 1})
                else:
                    row[column["name"]] = "test_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            rows.append(row)
        return self.bulk_insert(profile_id, database, table, rows, schema)


workbench = PostgresWorkbench()
