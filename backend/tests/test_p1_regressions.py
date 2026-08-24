from __future__ import annotations

from typing import Any

import pytest

from deebee.main import DataRequest, _sql_export_literal
from deebee.mysql import DeeBeeError, MySQLWorkbench, Profile
from deebee.postgres import PostgresProfile, PostgresWorkbench


def mysql_profile() -> Profile:
    return Profile("mysql-test", "MySQL", "localhost", 3306, "root", "", "app")


def postgres_profile() -> PostgresProfile:
    return PostgresProfile("pg-test", "PostgreSQL", "localhost", 5432, "postgres", "", "app", "public")


def test_sql_export_literals_preserve_complex_values() -> None:
    assert _sql_export_literal({"enabled": True}, "postgresql", "jsonb") == "'{\"enabled\":true}'"
    assert _sql_export_literal([1, 2, 3], "postgresql", "integer[]") == "ARRAY[1, 2, 3]"
    assert _sql_export_literal({"$binary": "00ff", "size": 2}, "postgresql", "bytea") == "decode('00ff','hex')"
    assert _sql_export_literal({"$binary": "00ff", "size": 2}, "mysql", "blob") == "X'00ff'"
    assert _sql_export_literal({"path": r"a\b"}, "mysql", "json") == "'" + r'{"path":"a\\\\b"}' + "'"


def test_table_data_request_has_a_hard_1000_row_limit() -> None:
    request = DataRequest(database="app", table="items")
    assert request.limit == 1000
    with pytest.raises(ValueError):
        DataRequest(database="app", table="items", limit=1001)


def test_mysql_table_browser_caps_count_and_page_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, Any]] = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql: str, params: Any = None): executed.append((sql, params))
        def fetchone(self): return {"total": 1001}
        def fetchall(self): return [{"id": 901}]

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    workbench = MySQLWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {"columns": [{"name": "id"}], "primary_key": ["id"]})
    monkeypatch.setattr(workbench, "require_profile", lambda *_: mysql_profile())
    monkeypatch.setattr(workbench, "_connect", lambda *_: Connection())
    result = workbench.table_data("mysql-test", "app", "items", 10, 100, [], None, limit=1000)

    assert "SELECT COUNT(*)" in executed[0][0] and "LIMIT %s" in executed[0][0]
    assert executed[0][1] == [1001]
    assert executed[1][1] == [100, 900]
    assert result["total"] == 1000
    assert result["limited"] is True


def test_postgres_table_browser_caps_count_and_page_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, Any]] = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql: str, params: Any = None): executed.append((sql, params))
        def fetchone(self): return {"total": 1001}
        def fetchall(self): return [{"id": 901}]

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    workbench = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {"columns": [{"name": "id"}], "primary_key": ["id"]})
    monkeypatch.setattr(workbench, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(workbench, "_connect", lambda *_: Connection())
    result = workbench.table_data("pg-test", "app", "items", 10, 100, [], None, schema="public", limit=1000)

    assert "SELECT COUNT(*)" in executed[0][0] and "LIMIT %s" in executed[0][0]
    assert executed[0][1] == [1001]
    assert executed[1][1] == [100, 900]
    assert result["total"] == 1000
    assert result["limited"] is True


def test_mysql_index_type_is_not_ignored() -> None:
    workbench = MySQLWorkbench(include_default=False)
    assert "USING HASH" in workbench._index_sql({"name": "idx_value", "columns": ["value"], "type": "HASH"})
    assert workbench._index_sql({"name": "idx_body", "columns": ["body"], "type": "FULLTEXT"}).startswith("FULLTEXT KEY")
    with pytest.raises(DeeBeeError, match="FULLTEXT"):
        workbench._index_sql({"name": "idx_body", "columns": ["body"], "type": "FULLTEXT", "unique": True})


def test_mysql_can_insert_using_only_column_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, Any]] = []

    class Cursor:
        rowcount = 1
        lastrowid = 7

        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql: str, params: Any = None): executed.append((sql, params))

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    workbench = MySQLWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {"columns": [{"name": "id"}]})
    monkeypatch.setattr(workbench, "require_profile", lambda *_: mysql_profile())
    monkeypatch.setattr(workbench, "_connect", lambda *_: Connection())
    result = workbench.insert_row("mysql-test", "app", "items", {})
    assert executed == [("INSERT INTO `items` () VALUES ()", None)]
    assert result["last_insert_id"] == 7


def test_postgres_designer_applies_index_default_and_identity_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    workbench = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {
        "columns": [{"name": "id", "data_type": "bigint", "nullable": False, "default": None, "extra": "", "generation": "", "comment": ""}],
        "primary_key": [], "primary_key_name": "", "indexes": [], "foreign_keys": [], "checks": [], "comment": "",
    })
    spec = {
        "database": "app", "schema": "public", "table": "items",
        "columns": [{"name": "id", "data_type": "bigint", "nullable": False, "default": None, "extra": "IDENTITY", "generation": "", "comment": ""}],
        "primary_key": [], "indexes": [{"name": "idx_id", "columns": ["id"], "unique": False, "type": "HASH"}],
        "foreign_keys": [], "checks": [], "comment": "",
    }
    statements = workbench.preview_ddl("pg-test", spec, "items")
    assert 'ALTER TABLE "public"."items" ALTER COLUMN "id" ADD GENERATED ALWAYS AS IDENTITY' in statements
    assert 'USING HASH ("id")' in statements[-1]

    spec["columns"][0]["extra"] = ""
    spec["columns"][0]["default"] = "CURRENT_DATE"
    statements = workbench.preview_ddl("pg-test", spec, "items")
    assert 'ALTER TABLE "public"."items" ALTER COLUMN "id" SET DEFAULT CURRENT_DATE' in statements


def test_postgres_designer_rejects_unsafe_generated_expression_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    workbench = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {
        "columns": [{"name": "total", "data_type": "integer", "nullable": True, "default": None, "extra": "GENERATED", "generation": "price * quantity", "comment": ""}],
        "primary_key": [], "primary_key_name": "", "indexes": [], "foreign_keys": [], "checks": [], "comment": "",
    })
    spec = {"database": "app", "schema": "public", "table": "items", "columns": [{"name": "total", "data_type": "integer", "nullable": True, "default": None, "extra": "GENERATED", "generation": "price * 2", "comment": ""}], "primary_key": [], "indexes": [], "foreign_keys": [], "checks": [], "comment": ""}
    with pytest.raises(DeeBeeError, match="不能安全地原地修改生成字段"):
        workbench.preview_ddl("pg-test", spec, "items")


def test_postgres_materialized_view_and_overloaded_routine_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [{"body": "SELECT 1 AS value"}, {"args": "integer, text", "prokind": "f"}]

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): pass
        def fetchone(self): return responses.pop(0)

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    workbench = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(workbench, "_connect", lambda *_: Connection())
    ddl = workbench.object_ddl("pg-test", "app", "materialized view", "mv_items", schema="public")
    assert ddl["sql"].startswith('CREATE MATERIALIZED VIEW "public"."mv_items" AS')

    executed: list[str] = []
    monkeypatch.setattr(workbench, "execute_script", lambda _profile, _database, sql, _schema: executed.append(sql) or {"results": []})
    workbench.object_action("pg-test", "app", "function", "calculate", "drop", object_id=42, schema="public")
    assert executed == ['DROP FUNCTION "public"."calculate"(integer, text)']
