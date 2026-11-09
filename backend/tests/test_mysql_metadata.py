from __future__ import annotations

import pymysql
import pytest

from deebee.mysql import MySQLWorkbench, Profile


@pytest.fixture
def metadata_workbench(monkeypatch):
    workbench = MySQLWorkbench(include_default=False)
    workbench.profiles["mysql-test"] = Profile(
        "mysql-test", "MySQL", "localhost", 3306, "reader", "", "idmesh"
    )
    state = {"check_error": None, "connections": [], "queries": []}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            state["queries"].append((sql, params))
            if "information_schema.CHECK_CONSTRAINTS" in sql:
                if state["check_error"] is not None:
                    raise state["check_error"]
                self.rows = [{"name": "positive_id", "clause": "`id` > 0"}]
            elif "information_schema.COLUMNS" in sql:
                self.rows = [
                    {"name": "id", "data_type": "int", "nullable": False, "extra": ""},
                    {"name": "label", "data_type": "varchar(80)", "nullable": True, "extra": ""},
                ]
            elif "information_schema.STATISTICS" in sql:
                self.rows = [{
                    "name": "PRIMARY", "is_unique": True, "seq": 1,
                    "column_name": "id", "index_type": "BTREE",
                }]
            elif "information_schema.KEY_COLUMN_USAGE" in sql:
                self.rows = []
            elif sql.startswith("SHOW CREATE TABLE"):
                self.rows = [{"Create Table": "CREATE TABLE `__core_user` (`id` int PRIMARY KEY)"}]
            elif "information_schema.TABLES" in sql:
                self.rows = [{"engine": "InnoDB", "collation": "utf8mb4_general_ci", "comment": ""}]
            elif sql.startswith("SELECT COUNT(*)"):
                self.rows = [{"total": 2}]
            elif sql.startswith("SELECT *"):
                self.rows = [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}]
            else:
                raise AssertionError(f"Unexpected query: {sql}")

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    def connect(*args, **kwargs):
        connection = Connection()
        state["connections"].append(connection)
        return connection

    monkeypatch.setattr(workbench, "_connect", connect)
    return workbench, state


@pytest.mark.parametrize("error", [
    pymysql.OperationalError(1109, "Unknown table 'check_constraints' in information_schema"),
    pymysql.ProgrammingError(1146, "Table 'information_schema.CHECK_CONSTRAINTS' doesn't exist"),
])
def test_browse_table_without_check_constraint_metadata(metadata_workbench, error):
    workbench, state = metadata_workbench
    state["check_error"] = error

    schema = workbench.table_schema("mysql-test", "idmesh", "__core_user")
    assert schema["table"] == "__core_user"
    assert schema["checks"] == []
    assert schema["primary_key"] == ["id"]
    assert schema["engine"] == "InnoDB"
    assert schema["create_sql"].startswith("CREATE TABLE `__core_user`")

    data = workbench.table_data("mysql-test", "idmesh", "__core_user", 1, 100, [], None)
    assert data["rows"] == [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}]
    assert data["total"] == 2
    assert [column["name"] for column in data["columns"]] == ["id", "label"]
    assert any("FROM `idmesh`.`__core_user`" in sql for sql, _ in state["queries"])
    assert all(connection.closed for connection in state["connections"])


def test_supported_check_constraints_are_preserved(metadata_workbench):
    workbench, _ = metadata_workbench
    schema = workbench.table_schema("mysql-test", "idmesh", "__core_user")
    assert schema["checks"] == [{"name": "positive_id", "clause": "`id` > 0"}]


@pytest.mark.parametrize("error", [
    pymysql.OperationalError(1142, "SELECT command denied"),
    pymysql.OperationalError(2013, "Lost connection to MySQL server during query"),
])
def test_check_metadata_errors_are_not_silenced(metadata_workbench, error):
    workbench, state = metadata_workbench
    state["check_error"] = error
    with pytest.raises(type(error)) as raised:
        workbench.table_schema("mysql-test", "idmesh", "__core_user")
    assert raised.value is error
    assert all(connection.closed for connection in state["connections"])
