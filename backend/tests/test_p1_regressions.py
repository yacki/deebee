from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from deebee.jobs import JobManager
from deebee.main import DataRequest, _dangerous_ddl, _sql_export_literal, export_data
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


def test_export_uses_the_unbounded_full_table_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deebee import main

    calls: list[tuple[Any, ...]] = []

    def export_table(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return {
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [{"id": 1}, {"id": 2}],
        }

    monkeypatch.setattr(main.workbench, "export_table", export_table)
    response = asyncio.run(export_data("profile", "app", "items", format="csv", _="admin"))
    assert response.body == b"\xef\xbb\xbfid\r\n1\r\n2\r\n"
    assert calls == [("profile", "app", "items", "")]


def test_dangerous_ddl_detection_covers_leading_drop_and_postgres_type_changes() -> None:
    assert _dangerous_ddl('DROP INDEX "public"."idx_items"')
    assert _dangerous_ddl('ALTER TABLE "items" ALTER COLUMN "amount" TYPE numeric(20,2)')
    assert _dangerous_ddl("TRUNCATE TABLE items")
    assert not _dangerous_ddl('ALTER TABLE "items" RENAME COLUMN "old" TO "new"')


def test_job_cancel_is_terminal_only_after_the_worker_stops() -> None:
    manager = JobManager()
    started = threading.Event()
    worker_stopped = threading.Event()
    hook_called = threading.Event()

    def task(_, cancelled: threading.Event) -> None:
        started.set()
        while not cancelled.wait(0.005):
            pass
        time.sleep(0.02)
        worker_stopped.set()

    created = manager.create("test", task, on_cancel=hook_called.set)
    assert started.wait(1)
    cancelling = manager.cancel(created["id"])
    assert cancelling["status"] == "cancelling"
    assert hook_called.wait(1)
    for _ in range(200):
        status = manager.public(created["id"])
        if status["status"] == "cancelled":
            break
        time.sleep(0.005)
    assert status["status"] == "cancelled"
    assert worker_stopped.is_set()
    manager._executor.shutdown(wait=True)


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


def test_cancelled_bulk_import_rolls_back_the_entire_mysql_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = threading.Event()

    class Cursor:
        rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def executemany(self, _sql: str, rows: list[list[Any]]) -> None:
            self.rowcount = len(rows)
            cancelled.set()

    class Connection:
        committed = False
        rolled_back = False
        def cursor(self): return Cursor()
        def commit(self): self.committed = True
        def rollback(self): self.rolled_back = True
        def close(self): pass

    connection = Connection()
    workbench = MySQLWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "require_profile", lambda *_: mysql_profile())
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {"columns": [{"name": "id"}]})
    monkeypatch.setattr(workbench, "_connect", lambda *_args, **_kwargs: connection)
    rows = [{"id": index} for index in range(600)]
    with pytest.raises(DeeBeeError, match="任务已取消"):
        workbench.bulk_insert("mysql-test", "app", "items", rows, cancelled=cancelled)
    assert connection.rolled_back and not connection.committed


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


def test_designers_rename_columns_without_drop_or_add(monkeypatch: pytest.MonkeyPatch) -> None:
    current = {
        "columns": [{"name": "id", "data_type": "integer", "nullable": False, "default": None, "extra": "", "generation": "", "comment": ""}],
        "primary_key": ["id"], "primary_key_name": "items_pkey",
        "indexes": [{"name": "idx_items_id", "columns": ["id"], "unique": False, "type": "BTREE"}],
        "foreign_keys": [], "checks": [{"name": "chk_id", "clause": "id > 0"}],
        "engine": "InnoDB", "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci", "comment": "",
    }
    columns = [{"name": "item_id", "original_name": "id", "data_type": "integer", "nullable": False, "default": None, "extra": "", "generation": "", "comment": ""}]
    desired = {
        "database": "app", "schema": "public", "table": "items", "columns": columns,
        "primary_key": ["item_id"],
        "indexes": [{"name": "idx_items_id", "columns": ["item_id"], "unique": False, "type": "BTREE"}],
        "foreign_keys": [], "checks": [{"name": "chk_id", "clause": "item_id > 0"}],
        "engine": "InnoDB", "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci", "comment": "",
    }

    mysql = MySQLWorkbench(include_default=False)
    mysql_statements = mysql._alter_statements("app", "items", current, desired)
    assert mysql_statements == ['ALTER TABLE `app`.`items` RENAME COLUMN `id` TO `item_id`']

    postgres = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(postgres, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(postgres, "table_schema", lambda *_: current)
    pg_desired = {**desired, "engine": "PostgreSQL", "charset": "UTF8", "collation": ""}
    pg_statements = postgres.preview_ddl("pg-test", pg_desired, "items")
    assert pg_statements == ['ALTER TABLE "public"."items" RENAME COLUMN "id" TO "item_id"']


def test_postgres_table_actions_distinguish_delete_truncate_and_atomic_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed_scripts: list[str] = []
    workbench = PostgresWorkbench(include_default=False)
    monkeypatch.setattr(workbench, "require_profile", lambda *_: postgres_profile())
    monkeypatch.setattr(
        workbench,
        "execute_script",
        lambda _profile, _database, sql, _schema: executed_scripts.append(sql) or {"results": []},
    )
    workbench.table_action("pg-test", "app", "items", "empty", schema="public")
    workbench.table_action("pg-test", "app", "items", "truncate", schema="public")
    assert executed_scripts == ['DELETE FROM "public"."items"', 'TRUNCATE TABLE "public"."items"']

    statements: list[str] = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql: str): statements.append(sql)

    class Connection:
        committed = False
        rolled_back = False
        def cursor(self): return Cursor()
        def commit(self): self.committed = True
        def rollback(self): self.rolled_back = True
        def close(self): pass

    connection = Connection()
    monkeypatch.setattr(workbench, "_connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(workbench, "table_schema", lambda *_: {
        "columns": [
            {"name": "id", "extra": "IDENTITY", "generation": ""},
            {"name": "label", "extra": "", "generation": ""},
            {"name": "slug", "extra": "GENERATED", "generation": "lower(label)"},
        ]
    })
    result = workbench.table_action(
        "pg-test", "app", "items", "duplicate", target="items_copy", with_data=True, schema="public"
    )
    assert connection.committed and not connection.rolled_back
    assert statements == result["statements"]
    assert statements[0] == 'CREATE TABLE "public"."items_copy" (LIKE "public"."items" INCLUDING ALL)'
    assert 'OVERRIDING SYSTEM VALUE SELECT "id", "label" FROM "public"."items"' in statements[1]
    assert statements[-1].startswith("SELECT setval(pg_get_serial_sequence(")


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
