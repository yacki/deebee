from __future__ import annotations

import datetime as dt
import decimal
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

try:
    import pymssql
except ModuleNotFoundError:  # Keep non-SQL Server installations importable.
    pymssql = None  # type: ignore[assignment]

from .mysql import DeeBeeError, clean_row


@dataclass(frozen=True)
class SQLServerProfile:
    id: str
    name: str
    host: str
    port: int
    user: str
    password: str
    default_database: str = "master"
    default_schema: str = "dbo"
    options: dict[str, Any] | None = None
    transport: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("password")
        value.pop("transport")
        value["driver"] = "mssql"
        return value


@dataclass
class SQLServerSession:
    id: str
    connection: Any
    profile_id: str
    database: str
    schema: str
    autocommit: bool
    workspace_id: str
    lock: threading.RLock
    spid: int | None = None


def quote_ident(value: str) -> str:
    if not value or "\x00" in value:
        raise DeeBeeError("无效的数据库标识符")
    return "[" + value.replace("]", "]]" ) + "]"


def qualified(schema: str, name: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(name)}"


def _type_sql(row: dict[str, Any]) -> str:
    data_type = str(row.get("data_type") or "nvarchar").lower()
    if data_type in {"varchar", "char", "varbinary", "binary"}:
        size = row.get("max_length")
        return f"{data_type}({'max' if size == -1 else size})"
    if data_type in {"nvarchar", "nchar"}:
        size = row.get("max_length")
        return f"{data_type}({'max' if size == -1 else max(1, int(size or 2) // 2)})"
    if data_type in {"decimal", "numeric"}:
        return f"{data_type}({row.get('precision', 18)},{row.get('scale', 0)})"
    if data_type in {"datetime2", "datetimeoffset", "time"} and row.get("scale") is not None:
        return f"{data_type}({row['scale']})"
    return data_type


class SQLServerWorkbench:
    def __init__(self) -> None:
        self.profiles: dict[str, SQLServerProfile] = {}
        self.sessions: dict[str, SQLServerSession] = {}
        self._guard = threading.RLock()

    @staticmethod
    def _require_driver() -> None:
        if pymssql is None:
            raise DeeBeeError("SQL Server 驱动未安装，请安装 pymssql")

    def _connect(
        self, profile: SQLServerProfile, database: str = "", *, autocommit: bool = True
    ) -> Any:
        self._require_driver()
        options = profile.options or {}
        from .transports import transport_manager

        host, port = transport_manager.endpoint(
            profile.id, profile.host, profile.port, profile.transport
        )
        try:
            return pymssql.connect(
                server=host,
                port=str(port),
                user=profile.user,
                password=profile.password,
                database=database or profile.default_database or "master",
                login_timeout=10,
                timeout=120,
                charset="UTF-8",
                as_dict=True,
                appname="DeeBee",
                encryption=options.get("encryption", "request"),
                read_only=bool(options.get("read_only", False)),
                autocommit=autocommit,
                tds_version="7.4",
                use_datetime2=True,
            )
        except Exception as exc:
            raise self._error(exc) from exc

    @staticmethod
    def _error(exc: Exception) -> DeeBeeError:
        args = getattr(exc, "args", ())
        code = args[0] if args and isinstance(args[0], (int, str)) else None
        message = str(args[-1] if args else exc)
        return DeeBeeError(message, code)

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.public() for profile in self.profiles.values()]

    def require_profile(self, profile_id: str) -> SQLServerProfile:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise DeeBeeError("连接不存在")
        return profile

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        return self.test_connection(self.require_profile(profile_id))

    def test_connection(self, profile: SQLServerProfile) -> dict[str, Any]:
        started = time.perf_counter()
        conn = self._connect(profile)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version, DB_NAME() AS database")
                info = cursor.fetchone() or {}
            return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1), **clean_row(info)}
        finally:
            conn.close()

    def create_session(
        self, profile_id: str, database: str, autocommit: bool,
        workspace_id: str = "", schema: str = "",
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        database = database or profile.default_database or "master"
        schema = schema or profile.default_schema or "dbo"
        conn = self._connect(profile, database, autocommit=autocommit)
        with conn.cursor() as cursor:
            cursor.execute("SELECT @@SPID AS spid")
            row = cursor.fetchone() or {}
        session = SQLServerSession(
            id=str(uuid.uuid4()), connection=conn, profile_id=profile_id,
            database=database, schema=schema, autocommit=autocommit,
            workspace_id=workspace_id, lock=threading.RLock(), spid=int(row.get("spid") or 0),
        )
        with self._guard:
            self.sessions[session.id] = session
        return self.session_public(session)

    @staticmethod
    def session_public(session: SQLServerSession) -> dict[str, Any]:
        return {
            "id": session.id, "profile_id": session.profile_id,
            "database": session.database, "schema": session.schema,
            "autocommit": session.autocommit, "workspace_id": session.workspace_id,
            "thread_id": session.spid,
        }

    def require_session(self, session_id: str) -> SQLServerSession:
        with self._guard:
            session = self.sessions.get(session_id)
        if not session:
            raise DeeBeeError("查询会话已失效，请重新打开标签")
        return session

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        session = self.require_session(session_id)
        try:
            with session.lock, session.connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
        except Exception as exc:
            self.close_session(session_id)
            raise DeeBeeError("查询会话已失效，请重新打开标签") from exc
        return self.session_public(session)

    def close_session(self, session_id: str) -> None:
        with self._guard:
            session = self.sessions.pop(session_id, None)
        if session:
            try:
                session.connection.rollback()
            except Exception:
                pass
            session.connection.close()

    def cleanup_workspace(self, workspace_id: str) -> int:
        if not workspace_id:
            return 0
        ids = [item.id for item in self.sessions.values() if item.workspace_id == workspace_id]
        for session_id in ids:
            self.close_session(session_id)
        return len(ids)

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
        session = self.require_session(session_id)
        if not session.spid:
            return False
        profile = self.require_profile(session.profile_id)
        killer = self._connect(profile, "master")
        try:
            with killer.cursor() as cursor:
                cursor.execute(f"KILL {int(session.spid)}")
            return True
        except Exception:
            return False
        finally:
            killer.close()

    def execute(self, session_id: str, sql: str, limit: int = 1000) -> dict[str, Any]:
        session = self.require_session(session_id)
        started = time.perf_counter()
        results: list[dict[str, Any]] = []
        try:
            with session.lock, session.connection.cursor() as cursor:
                cursor.execute(sql)
                index = 0
                while True:
                    if cursor.description:
                        rows = cursor.fetchmany(limit + 1)
                        columns = [
                            {"name": item[0], "type": str(item[1]), "nullable": True}
                            for item in cursor.description
                        ]
                        results.append({
                            "index": index, "kind": "rows", "columns": columns,
                            "rows": [clean_row(row) for row in rows[:limit]],
                            "row_count": min(len(rows), limit), "truncated": len(rows) > limit,
                        })
                    else:
                        results.append({"index": index, "kind": "mutation", "affected_rows": max(cursor.rowcount, 0)})
                    index += 1
                    if not cursor.nextset():
                        break
            return {"ok": True, "results": results, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "autocommit": session.autocommit}
        except Exception as exc:
            raise self._error(exc) from exc

    def databases(self, profile_id: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, "master")
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, collation_name AS collation, recovery_model_desc AS recovery_model FROM sys.databases WHERE state_desc='ONLINE' ORDER BY name")
                return [{**clean_row(row), "charset": "Unicode"} for row in cursor.fetchall()]
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def schemas(self, profile_id: str, database: str) -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT s.name, USER_NAME(s.principal_id) AS owner, "
                    "CASE WHEN s.name IN ('db_owner','db_accessadmin','db_securityadmin','db_ddladmin','db_backupoperator','db_datareader','db_datawriter','db_denydatareader','db_denydatawriter','guest','INFORMATION_SCHEMA','sys') THEN 1 ELSE 0 END AS system "
                    "FROM sys.schemas s ORDER BY system, s.name"
                )
                return [{**clean_row(row), "system": bool(row.get("system"))} for row in cursor.fetchall()]
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def objects(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema or "dbo"
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT o.name, o.object_id, CASE o.type WHEN 'U' THEN 'BASE TABLE' ELSE 'VIEW' END AS object_type, "
                    "SUM(COALESCE(p.rows,0)) AS estimated_rows FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                    "LEFT JOIN sys.partitions p ON p.object_id=o.object_id AND p.index_id IN (0,1) "
                    "WHERE s.name=%s AND o.type IN ('U','V') GROUP BY o.name,o.object_id,o.type ORDER BY o.name", (schema,),
                )
                base = cursor.fetchall()
                cursor.execute(
                    "SELECT o.name,o.object_id,CASE o.type WHEN 'P' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS object_type "
                    "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name=%s AND o.type IN ('P','PC','FN','IF','TF','FS','FT') ORDER BY o.name", (schema,),
                )
                routines = cursor.fetchall()
                cursor.execute(
                    "SELECT tr.name,tr.object_id,'TRIGGER' AS object_type,OBJECT_NAME(tr.parent_id) AS table_name,CASE WHEN tr.is_disabled=1 THEN 'DISABLED' ELSE 'ENABLED' END AS status "
                    "FROM sys.triggers tr JOIN sys.objects o ON o.object_id=tr.parent_id JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name=%s ORDER BY tr.name", (schema,),
                )
                triggers = cursor.fetchall()
            return {
                "tables": [clean_row(row) for row in base if row["object_type"] == "BASE TABLE"],
                "views": [clean_row(row) for row in base if row["object_type"] == "VIEW"],
                "routines": [clean_row(row) for row in routines],
                "triggers": [clean_row(row) for row in triggers], "events": [],
            }
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def catalog(self, profile_id: str, database: str, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema or "dbo"
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT c.TABLE_NAME AS table_name,t.TABLE_TYPE AS object_type,c.COLUMN_NAME AS name,c.DATA_TYPE AS data_type,CASE c.IS_NULLABLE WHEN 'YES' THEN 1 ELSE 0 END AS nullable,c.ORDINAL_POSITION AS position "
                    "FROM INFORMATION_SCHEMA.COLUMNS c JOIN INFORMATION_SCHEMA.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
                    "WHERE c.TABLE_SCHEMA=%s ORDER BY c.TABLE_NAME,c.ORDINAL_POSITION", (schema,),
                )
                tables: dict[str, dict[str, Any]] = {}
                for row in cursor.fetchall():
                    tables.setdefault(row["table_name"], {"name": row["table_name"], "object_type": row["object_type"], "columns": []})["columns"].append({
                        "name": row["name"], "data_type": row["data_type"], "nullable": bool(row["nullable"]), "key": "",
                    })
                cursor.execute(
                    "SELECT ROUTINE_NAME AS name,ROUTINE_TYPE AS object_type,DATA_TYPE AS data_type FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA=%s ORDER BY ROUTINE_NAME", (schema,),
                )
                routines = [clean_row(row) for row in cursor.fetchall()]
            return {"database": database, "schema": schema, "tables": list(tables.values()), "routines": routines}
        finally:
            conn.close()

    def create_database(
        self, profile_id: str, name: str, charset: str = "Unicode", collation: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        options = options or {}
        recovery = str(options.get("recovery_model", "SIMPLE")).upper()
        if recovery not in {"SIMPLE", "FULL", "BULK_LOGGED"}:
            raise DeeBeeError("SQL Server 恢复模式无效")
        try:
            data_size = int(options.get("data_size_mb", 32))
            log_size = int(options.get("log_size_mb", 16))
            growth = int(options.get("filegrowth_mb", 64))
        except (TypeError, ValueError) as exc:
            raise DeeBeeError("数据库文件大小必须是整数") from exc
        if not 1 <= data_size <= 1048576 or not 1 <= log_size <= 1048576 or not 1 <= growth <= 1048576:
            raise DeeBeeError("数据库文件大小必须在 1 MB 到 1 TB 之间")
        if collation and not all(char.isalnum() or char == "_" for char in collation):
            raise DeeBeeError("SQL Server 排序规则无效")
        conn = self._connect(profile, "master")
        created = False
        try:
            with conn.cursor() as cursor:
                sql = f"CREATE DATABASE {quote_ident(name)}"
                if collation:
                    sql += f" COLLATE {collation}"
                cursor.execute(sql)
                created = True
                cursor.execute(
                    "SELECT name,type_desc,size*8/1024 AS size_mb FROM sys.master_files WHERE database_id=DB_ID(%s)", (name,),
                )
                files = cursor.fetchall()
                for item in files:
                    requested = log_size if item["type_desc"] == "LOG" else data_size
                    current = int(item.get("size_mb") or 0)
                    size = max(requested, current)
                    logical_name = str(item["name"]).replace("'", "''")
                    cursor.execute(
                        f"ALTER DATABASE {quote_ident(name)} MODIFY FILE (NAME=N'{logical_name}', SIZE={size}MB, FILEGROWTH={growth}MB)"
                    )
                cursor.execute(f"ALTER DATABASE {quote_ident(name)} SET RECOVERY {recovery}")
            return {"ok": True, "name": name, "charset": "Unicode", "collation": collation, "recovery_model": recovery}
        except Exception as exc:
            if created:
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(f"DROP DATABASE {quote_ident(name)}")
                except Exception:
                    pass
            raise self._error(exc) from exc
        finally:
            conn.close()

    def drop_database(self, profile_id: str, name: str) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        if name.lower() in {"master", "model", "msdb", "tempdb"} or name == profile.default_database:
            raise DeeBeeError("不能删除系统数据库或当前连接使用的默认数据库")
        conn = self._connect(profile, "master")
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE {quote_ident(name)}")
            return {"ok": True}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def table_schema(self, profile_id: str, database: str, table: str, schema: str = "") -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema or "dbo"
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT c.name,t.name AS data_type,c.max_length,c.precision,c.scale,c.is_nullable AS nullable,dc.definition AS [default],CASE WHEN c.is_identity=1 THEN 'IDENTITY' ELSE '' END AS extra,c.column_id AS position "
                    "FROM sys.columns c JOIN sys.types t ON t.user_type_id=c.user_type_id JOIN sys.tables tb ON tb.object_id=c.object_id JOIN sys.schemas s ON s.schema_id=tb.schema_id LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id "
                    "WHERE s.name=%s AND tb.name=%s ORDER BY c.column_id", (schema, table),
                )
                raw_columns = cursor.fetchall()
                if not raw_columns:
                    raise DeeBeeError("表不存在")
                columns = [{**clean_row(row), "data_type": _type_sql(row), "comment": "", "generation": ""} for row in raw_columns]
                cursor.execute(
                    "SELECT i.name,i.is_unique AS [unique],i.is_primary_key AS [primary],c.name AS column_name,ic.key_ordinal "
                    "FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id JOIN sys.tables tb ON tb.object_id=i.object_id JOIN sys.schemas s ON s.schema_id=tb.schema_id "
                    "WHERE s.name=%s AND tb.name=%s AND i.is_hypothetical=0 AND ic.is_included_column=0 ORDER BY i.index_id,ic.key_ordinal", (schema, table),
                )
                index_map: dict[str, dict[str, Any]] = {}
                for row in cursor.fetchall():
                    entry = index_map.setdefault(row["name"], {"name": row["name"], "unique": bool(row["unique"]), "primary": bool(row["primary"]), "type": "BTREE", "columns": []})
                    entry["columns"].append(row["column_name"])
                primary_key = next((item["columns"] for item in index_map.values() if item["primary"]), [])
                indexes = [{key: value for key, value in item.items() if key != "primary"} for item in index_map.values() if not item["primary"]]
                cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(%s)) AS definition", (f"{schema}.{table}",))
                definition = (cursor.fetchone() or {}).get("definition") or ""
            if not definition:
                parts = [f"{quote_ident(item['name'])} {item['data_type']}" + (" IDENTITY(1,1)" if item["extra"] else "") + (" NULL" if item["nullable"] else " NOT NULL") for item in columns]
                if primary_key:
                    parts.append("PRIMARY KEY (" + ", ".join(quote_ident(item) for item in primary_key) + ")")
                definition = f"CREATE TABLE {qualified(schema, table)} (\n  " + ",\n  ".join(parts) + "\n);"
            return {"database": database, "schema": schema, "table": table, "columns": columns, "indexes": indexes, "primary_key": primary_key, "foreign_keys": [], "checks": [], "create_sql": definition, "engine": "SQL Server", "charset": "Unicode", "collation": "", "comment": ""}
        finally:
            conn.close()

    def table_data(
        self, profile_id: str, database: str, table: str, page: int, page_size: int,
        filters: list[dict[str, Any]], sort: dict[str, Any] | None, schema: str = "", limit: int = 1000,
    ) -> dict[str, Any]:
        profile = self.require_profile(profile_id)
        schema = schema or profile.default_schema or "dbo"
        meta = self.table_schema(profile_id, database, table, schema)
        names = {item["name"] for item in meta["columns"]}
        conditions: list[str] = []
        params: list[Any] = []
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for item in filters:
            column = str(item.get("column", ""))
            if column not in names:
                raise DeeBeeError("筛选字段不存在")
            operator = str(item.get("operator", "eq"))
            field = quote_ident(column)
            if operator in {"is_null", "not_null"}:
                conditions.append(f"{field} IS {'NOT ' if operator == 'not_null' else ''}NULL")
            elif operator == "contains":
                conditions.append(f"CAST({field} AS nvarchar(max)) LIKE %s")
                params.append(f"%{item.get('value', '')}%")
            else:
                conditions.append(f"{field} {operators.get(operator, '=')} %s")
                params.append(item.get("value"))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order_column = str((sort or {}).get("column") or (meta["primary_key"][0] if meta["primary_key"] else meta["columns"][0]["name"]))
        if order_column not in names:
            raise DeeBeeError("排序字段不存在")
        direction = "DESC" if str((sort or {}).get("direction", "asc")).lower() == "desc" else "ASC"
        offset = (page - 1) * page_size
        fetch_size = max(0, min(page_size, limit - offset))
        conn = self._connect(profile, database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT_BIG(*) AS total FROM (SELECT TOP ({limit + 1}) 1 AS value FROM {qualified(schema, table)}{where}) AS limited_rows", params)
                detected_total = int((cursor.fetchone() or {}).get("total") or 0)
                rows: list[dict[str, Any]] = []
                if fetch_size:
                    cursor.execute(f"SELECT * FROM {qualified(schema, table)}{where} ORDER BY {quote_ident(order_column)} {direction} OFFSET {offset} ROWS FETCH NEXT {fetch_size} ROWS ONLY", params)
                    rows = [clean_row(row) for row in cursor.fetchall()]
            return {"columns": meta["columns"], "primary_key": meta["primary_key"], "rows": rows, "page": page, "page_size": page_size, "total": min(detected_total, limit), "limited": detected_total > limit}
        finally:
            conn.close()

    def _mutation(self, profile_id: str, database: str, sql: str, params: list[Any]) -> dict[str, Any]:
        conn = self._connect(self.require_profile(profile_id), database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                affected = max(cursor.rowcount, 0)
            return {"ok": True, "affected_rows": affected, "last_insert_id": 0}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    def insert_row(self, profile_id: str, database: str, table: str, values: dict[str, Any], schema: str = "") -> dict[str, Any]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        if not values:
            return self._mutation(profile_id, database, f"INSERT INTO {qualified(schema, table)} DEFAULT VALUES", [])
        fields = list(values)
        sql = f"INSERT INTO {qualified(schema, table)} ({', '.join(quote_ident(item) for item in fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
        return self._mutation(profile_id, database, sql, [values[item] for item in fields])

    def update_row(self, profile_id: str, database: str, table: str, key: dict[str, Any], changes: dict[str, Any], schema: str = "") -> dict[str, Any]:
        if not key or not changes:
            raise DeeBeeError("更新条件和字段不能为空")
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        sql = f"UPDATE {qualified(schema, table)} SET " + ", ".join(f"{quote_ident(name)}=%s" for name in changes) + " WHERE " + " AND ".join(f"(({quote_ident(name)}=%s) OR ({quote_ident(name)} IS NULL AND %s IS NULL))" for name in key)
        params = [*changes.values()]
        for value in key.values():
            params.extend([value, value])
        return self._mutation(profile_id, database, sql, params)

    def delete_row(self, profile_id: str, database: str, table: str, key: dict[str, Any], schema: str = "") -> dict[str, Any]:
        if not key:
            raise DeeBeeError("删除条件不能为空")
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        sql = f"DELETE FROM {qualified(schema, table)} WHERE " + " AND ".join(f"(({quote_ident(name)}=%s) OR ({quote_ident(name)} IS NULL AND %s IS NULL))" for name in key)
        params: list[Any] = []
        for value in key.values():
            params.extend([value, value])
        return self._mutation(profile_id, database, sql, params)

    def export_table(self, profile_id: str, database: str, table: str, schema: str = "") -> dict[str, Any]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        meta = self.table_schema(profile_id, database, table, schema)
        conn = self._connect(self.require_profile(profile_id), database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {qualified(schema, table)}")
                rows = [clean_row(row) for row in cursor.fetchall()]
            return {"columns": meta["columns"], "rows": rows}
        finally:
            conn.close()

    def bulk_insert(
        self, profile_id: str, database: str, table: str, rows: list[dict[str, Any]],
        schema: str = "", *, cancelled: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not rows:
            return {"ok": True, "affected_rows": 0}
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        fields = list(rows[0])
        if not fields or any(list(row) != fields for row in rows):
            raise DeeBeeError("导入数据的字段必须一致")
        sql = f"INSERT INTO {qualified(schema, table)} ({', '.join(quote_ident(item) for item in fields)}) VALUES ({', '.join(['%s'] * len(fields))})"
        conn = self._connect(self.require_profile(profile_id), database, autocommit=False)
        try:
            with conn.cursor() as cursor:
                for start in range(0, len(rows), 500):
                    if cancelled and cancelled.is_set():
                        raise DeeBeeError("任务已取消")
                    batch = rows[start:start + 500]
                    cursor.executemany(sql, [[row.get(field) for field in fields] for row in batch])
                    if progress:
                        progress(min(start + len(batch), len(rows)), len(rows))
            conn.commit()
            return {"ok": True, "affected_rows": len(rows)}
        except Exception as exc:
            conn.rollback()
            raise self._error(exc) from exc
        finally:
            conn.close()

    def table_action(self, profile_id: str, database: str, table: str, action: str, *, target: str = "", with_data: bool = False, schema: str = "") -> dict[str, Any]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        name = qualified(schema, table)
        if action == "drop":
            sql = f"DROP TABLE {name}"
        elif action == "truncate":
            sql = f"TRUNCATE TABLE {name}"
        elif action in {"delete", "empty"}:
            sql = f"DELETE FROM {name}"
        elif action == "rename" and target:
            escaped_target = target.replace("'", "''")
            escaped_source = f"{schema}.{table}".replace("'", "''")
            sql = f"EXEC sp_rename N'{escaped_source}', N'{escaped_target}'"
        elif action == "analyze":
            sql = f"UPDATE STATISTICS {name}"
        else:
            raise DeeBeeError("SQL Server 不支持此表操作")
        return self.execute_script(profile_id, database, sql, schema)

    def object_ddl(self, profile_id: str, database: str, kind: str, name: str, object_id: int | None = None, schema: str = "") -> dict[str, Any]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        if kind == "table":
            definition = self.table_schema(profile_id, database, name, schema)["create_sql"]
        else:
            conn = self._connect(self.require_profile(profile_id), database)
            try:
                with conn.cursor() as cursor:
                    if object_id is None:
                        cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(%s)) AS definition", (f"{schema}.{name}",))
                    else:
                        cursor.execute("SELECT OBJECT_DEFINITION(%s) AS definition", (object_id,))
                    definition = (cursor.fetchone() or {}).get("definition")
            finally:
                conn.close()
        if not definition:
            raise DeeBeeError("无法读取对象定义")
        return {"name": name, "kind": kind, "sql": definition}

    def object_action(self, profile_id: str, database: str, kind: str, name: str, action: str, object_id: int | None = None, schema: str = "") -> dict[str, Any]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        if action in {"enable", "disable"} and kind == "trigger":
            conn = self._connect(self.require_profile(profile_id), database)
            try:
                with conn.cursor() as cursor:
                    if object_id is None:
                        cursor.execute("SELECT OBJECT_SCHEMA_NAME(parent_id) AS parent_schema,OBJECT_NAME(parent_id) AS parent_name FROM sys.triggers WHERE object_id=OBJECT_ID(%s)", (f"{schema}.{name}",))
                    else:
                        cursor.execute("SELECT OBJECT_SCHEMA_NAME(parent_id) AS parent_schema,OBJECT_NAME(parent_id) AS parent_name FROM sys.triggers WHERE object_id=%s", (object_id,))
                    parent = cursor.fetchone() or {}
            finally:
                conn.close()
            if not parent.get("parent_name"):
                raise DeeBeeError("触发器不存在")
            return self.execute_script(profile_id, database, f"{action.upper()} TRIGGER {quote_ident(name)} ON {qualified(parent['parent_schema'], parent['parent_name'])}", schema)
        if action != "drop":
            raise DeeBeeError("SQL Server 不支持此对象操作")
        types = {"view": "VIEW", "procedure": "PROCEDURE", "function": "FUNCTION", "trigger": "TRIGGER", "table": "TABLE"}
        object_type = types.get(kind)
        if not object_type:
            raise DeeBeeError("不支持的对象类型")
        return self.execute_script(profile_id, database, f"DROP {object_type} {qualified(schema, name)}", schema)

    def search_objects(self, profile_id: str, database: str, term: str, schema: str = "") -> list[dict[str, Any]]:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        conn = self._connect(self.require_profile(profile_id), database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT TOP (200) CASE o.type WHEN 'U' THEN 'table' WHEN 'V' THEN 'view' WHEN 'P' THEN 'procedure' ELSE 'function' END AS kind,o.name FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name=%s AND o.name LIKE %s UNION ALL SELECT TOP (200) 'column',tb.name+'.'+c.name FROM sys.columns c JOIN sys.tables tb ON tb.object_id=c.object_id JOIN sys.schemas s ON s.schema_id=tb.schema_id WHERE s.name=%s AND c.name LIKE %s",
                    (schema, f"%{term}%", schema, f"%{term}%"),
                )
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def grants(self, profile_id: str, database: str = "", table: str = "", schema: str = "") -> list[dict[str, Any]]:
        profile = self.require_profile(profile_id)
        conn = self._connect(profile, database or profile.default_database)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT USER_NAME(grantee_principal_id) AS grantee,permission_name AS privilege,state_desc AS state,OBJECT_NAME(major_id) AS object_name FROM sys.database_permissions ORDER BY grantee,privilege")
                return [clean_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def execute_script(self, profile_id: str, database: str, sql: str, schema: str = "") -> dict[str, Any]:
        conn = self._connect(self.require_profile(profile_id), database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
            return {"ok": True}
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            conn.close()

    @staticmethod
    def _literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "0x" + bytes(value).hex()
        if isinstance(value, (dt.datetime, dt.date, dt.time)):
            value = value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
        return "N'" + str(value).replace("'", "''") + "'"

    def dump_sql(self, profile_id: str, database: str, table: str = "", include_data: bool = True, schema: str = "") -> str:
        schema = schema or self.require_profile(profile_id).default_schema or "dbo"
        tables = [table] if table else [item["name"] for item in self.objects(profile_id, database, schema)["tables"]]
        blocks = [f"USE {quote_ident(database)};", "GO"]
        for table_name in tables:
            meta = self.table_schema(profile_id, database, table_name, schema)
            blocks.extend([meta["create_sql"], "GO"])
            if include_data:
                exported = self.export_table(profile_id, database, table_name, schema)
                fields = [item["name"] for item in exported["columns"]]
                for row in exported["rows"]:
                    blocks.append(f"INSERT INTO {qualified(schema, table_name)} ({', '.join(quote_ident(item) for item in fields)}) VALUES ({', '.join(self._literal(row.get(item)) for item in fields)});")
        return "\n".join(blocks) + "\n"

    def generate_data(self, profile_id: str, database: str, table: str, count: int, schema: str = "", *, cancelled: threading.Event | None = None, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        raise DeeBeeError("SQL Server 暂不支持自动生成测试数据")

    def preview_ddl(self, profile_id: str, spec: dict[str, Any], current_table: str | None = None) -> list[str]:
        raise DeeBeeError("SQL Server 请通过查询控制台执行建表语句")

    def apply_ddl(self, profile_id: str, database: str, statements: list[str]) -> dict[str, Any]:
        return self.execute_script(profile_id, database, "\n".join(statements))


workbench = SQLServerWorkbench()
