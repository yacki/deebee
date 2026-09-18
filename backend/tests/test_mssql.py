from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deebee import mssql
from deebee.main import ConnectionTestBody
from deebee.mssql import SQLServerProfile, SQLServerWorkbench
from deebee.mysql import DeeBeeError
from deebee.workbench import DatabaseWorkbenches


class FakeCursor:
    def __init__(self, statements: list[tuple[str, Any]]) -> None:
        self.statements = statements
        self._rows: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append((sql, params))
        if "sys.master_files" in sql:
            self._rows = [
                {"name": "sample", "type_desc": "ROWS", "size_mb": 8},
                {"name": "sample_log", "type_desc": "LOG", "size_mb": 8},
            ]

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class FakeConnection:
    def __init__(self, statements: list[tuple[str, Any]]) -> None:
        self.statements = statements
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.statements)

    def close(self) -> None:
        self.closed = True


def test_sql_server_profile_is_validated_encrypted_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "connections.json"
    manager = DatabaseWorkbenches(store_path=path, defaults=[])
    saved = manager.create_connection({
        "driver": "mssql", "name": "ERP", "host": "sql.internal", "port": 1433,
        "user": "sa", "password": "sql-secret", "default_database": "master",
        "default_schema": "dbo", "options": {"encryption": "require", "read_only": True},
    })
    assert saved["driver"] == "mssql"
    assert saved["options"] == {"encryption": "require", "read_only": True}
    assert "sql-secret" not in path.read_text(encoding="utf-8")

    restored = DatabaseWorkbenches(store_path=path, defaults=[]).list_profiles()[0]
    assert restored["default_database"] == "master"
    assert restored["default_schema"] == "dbo"

    with pytest.raises(DeeBeeError, match="加密策略"):
        manager.create_connection({
            "driver": "mssql", "name": "Bad", "host": "sql.internal", "port": 1433,
            "user": "sa", "password": "", "options": {"encryption": "invalid"},
        })


def test_sql_server_connection_model_and_driver_options(monkeypatch: Any) -> None:
    body = ConnectionTestBody.model_validate({
        "driver": "mssql", "name": "SQL Server", "host": "sql.internal", "port": 1433,
        "user": "sa", "password": "secret", "default_database": "master",
        "default_schema": "dbo", "options": {"encryption": "require", "read_only": True},
    })
    assert body.driver == "mssql"
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> FakeConnection:
        captured.update(kwargs)
        return FakeConnection([])

    monkeypatch.setattr(mssql.pymssql, "connect", fake_connect)
    profile = SQLServerProfile(
        id="sql", name="SQL", host="sql.internal", port=1433, user="sa",
        password="secret", options={"encryption": "require", "read_only": True},
    )
    connection = SQLServerWorkbench()._connect(profile)
    assert captured["database"] == "master"
    assert captured["encryption"] == "require"
    assert captured["read_only"] is True
    connection.close()


def test_create_sql_server_database_uses_dialect_specific_options(monkeypatch: Any) -> None:
    statements: list[tuple[str, Any]] = []
    connection = FakeConnection(statements)
    monkeypatch.setattr(mssql.pymssql, "connect", lambda **_: connection)
    engine = SQLServerWorkbench()
    engine.profiles["sql"] = SQLServerProfile(
        id="sql", name="SQL", host="sql.internal", port=1433, user="sa", password="secret"
    )

    result = engine.create_database(
        "sql", "sample", "Unicode", "Chinese_PRC_CI_AS",
        {"recovery_model": "FULL", "data_size_mb": 32, "log_size_mb": 16, "filegrowth_mb": 64},
    )
    sql = [item[0] for item in statements]
    assert sql[0] == "CREATE DATABASE [sample] COLLATE Chinese_PRC_CI_AS"
    assert any("NAME=N'sample', SIZE=32MB, FILEGROWTH=64MB" in item for item in sql)
    assert any("NAME=N'sample_log', SIZE=16MB, FILEGROWTH=64MB" in item for item in sql)
    assert sql[-1] == "ALTER DATABASE [sample] SET RECOVERY FULL"
    assert result["recovery_model"] == "FULL"
    assert connection.closed is True

    with pytest.raises(DeeBeeError, match="恢复模式"):
        engine.create_database("sql", "bad", options={"recovery_model": "MAGIC"})
