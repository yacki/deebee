from __future__ import annotations

import threading
from pathlib import Path

import pytest

from deebee.jobs import JobManager
from deebee.sql_import import execute_sql_file, iter_sql_batches, iter_sql_statements


def test_streaming_parser_ignores_delimiters_inside_quotes_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "quoted.sql"
    path.write_text(
        "-- header; still a comment\n"
        "CREATE TABLE demo (value varchar(100));\n"
        "INSERT INTO demo VALUES ('semi;colon'), ('it\\'s fine'); # comment;\n"
        "/* block; comment */ INSERT INTO demo VALUES (\"double;quote\");\n",
        encoding="utf-8",
    )

    statements = list(iter_sql_statements(path, "mysql"))

    assert len(statements) == 3
    assert "header; still a comment" in statements[0].sql
    assert "semi;colon" in statements[1].sql
    assert "block; comment" in statements[2].sql
    assert statements[-1].end_byte == path.stat().st_size


def test_streaming_parser_supports_mysql_delimiter_directives(tmp_path: Path) -> None:
    path = tmp_path / "procedure.sql"
    path.write_text(
        "DELIMITER $$\n"
        "CREATE PROCEDURE p() BEGIN SELECT 'a;b'; SELECT 2; END$$\n"
        "DELIMITER ;\n"
        "CALL p();\n",
        encoding="utf-8",
    )

    statements = list(iter_sql_statements(path, "mysql"))

    assert [item.sql.split(None, 1)[0] for item in statements] == ["CREATE", "CALL"]
    assert "SELECT 'a;b'; SELECT 2;" in statements[0].sql


def test_sql_file_executor_batches_and_reports_byte_progress(tmp_path: Path) -> None:
    path = tmp_path / "many.sql"
    path.write_text("\n".join(f"INSERT INTO demo VALUES ({index});" for index in range(620)), encoding="utf-8")
    executed: list[str] = []
    updates: list[tuple[int, int, int]] = []

    result = execute_sql_file(
        path,
        "mysql",
        executed.append,
        progress=lambda done, total, statements: updates.append((done, total, statements)),
    )

    assert result["statements"] == 620
    assert result["batches"] == 3
    assert len(executed) == 3
    assert updates[-1] == (path.stat().st_size, path.stat().st_size, 620)


def test_sql_file_executor_stops_before_next_batch_when_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "cancel.sql"
    path.write_text("\n".join(f"SELECT {index};" for index in range(400)), encoding="utf-8")
    cancelled = threading.Event()
    calls = 0

    def execute(_sql: str) -> None:
        nonlocal calls
        calls += 1
        cancelled.set()

    with pytest.raises(RuntimeError, match="任务已取消"):
        execute_sql_file(path, "mysql", execute, cancelled=cancelled)
    assert calls == 1


def test_job_list_includes_metadata_and_most_recent_first() -> None:
    manager = JobManager()
    first = manager.create("import", lambda *_: {"ok": True}, metadata={"filename": "one.csv"})
    second = manager.create("sql-file", lambda *_: {"ok": True}, metadata={"filename": "two.sql"})
    jobs = manager.list()

    assert [item["id"] for item in jobs[:2]] == [second["id"], first["id"]]
    assert jobs[0]["metadata"]["filename"] == "two.sql"
    manager._executor.shutdown(wait=True)
