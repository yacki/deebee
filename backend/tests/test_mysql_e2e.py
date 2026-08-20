from __future__ import annotations

import csv
import io
import threading
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook

from deebee.main import app
from deebee.mysql import workbench


DATABASE = "deebee_e2e"
TABLE = "deebee_api_test_items"
DDL_TABLE = "deebee_api_test_designer"
PARENT_TABLE = "deebee_api_test_parent"
client = TestClient(app)


def auth_headers() -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "deebee"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def query(headers: dict[str, str], session_id: str, sql: str):
    response = client.post(
        f"/api/sessions/{session_id}/query", headers=headers, json={"sql": sql, "limit": 1000}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_mysql_workflow():
    headers = auth_headers()

    profiles = client.get("/api/connections", headers=headers)
    assert profiles.status_code == 200
    profile = profiles.json()[0]
    assert profile["driver"] == "mysql"
    assert "password" not in profile

    connection = client.post(f"/api/connections/{profile['id']}/test", headers=headers)
    assert connection.status_code == 200, connection.text
    assert connection.json()["ok"] is True

    databases = client.get(f"/api/connections/{profile['id']}/databases", headers=headers)
    assert DATABASE in {item["name"] for item in databases.json()}

    session = client.post(
        "/api/sessions",
        headers=headers,
        json={"profile_id": profile["id"], "database": DATABASE, "autocommit": True},
    )
    assert session.status_code == 200, session.text
    session_id = session.json()["id"]
    inspected = client.get(f"/api/sessions/{session_id}", headers=headers)
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["database"] == DATABASE
    try:
        query(headers, session_id, f"DROP TABLE IF EXISTS `{TABLE}`")
        created = query(
            headers,
            session_id,
            f"""CREATE TABLE `{TABLE}` (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                amount DECIMAL(12,2) NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_{TABLE}_name (name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        )
        assert created["results"][0]["kind"] == "mutation"

        multi = query(
            headers,
            session_id,
            f"INSERT INTO `{TABLE}` (name, amount) VALUES ('Alpha', 12.50), ('Beta', 30.25); "
            f"SELECT id, name, amount FROM `{TABLE}` ORDER BY id;",
        )
        assert len(multi["results"]) == 2
        assert multi["results"][0]["affected_rows"] == 2
        assert [row["name"] for row in multi["results"][1]["rows"]] == ["Alpha", "Beta"]

        schema = client.get(
            f"/api/schema/{profile['id']}/{DATABASE}/{TABLE}", headers=headers
        )
        assert schema.status_code == 200, schema.text
        assert schema.json()["primary_key"] == ["id"]
        assert any(item["name"] == f"uq_{TABLE}_name" for item in schema.json()["indexes"])

        page = client.post(
            "/api/data/read",
            headers=headers,
            json={
                "profile_id": profile["id"], "database": DATABASE, "table": TABLE,
                "page": 1, "page_size": 100,
                "filters": [{"column": "name", "operator": "contains", "value": "Al"}],
                "sort": {"column": "id", "direction": "desc"},
            },
        )
        assert page.status_code == 200, page.text
        assert page.json()["total"] == 1
        alpha = page.json()["rows"][0]

        updated = client.patch(
            "/api/data/rows", headers=headers,
            json={"profile_id": profile["id"], "database": DATABASE, "table": TABLE, "key": {"id": alpha["id"]}, "changes": {"status": "archived", "amount": "44.10"}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["affected_rows"] == 1

        inserted = client.post(
            "/api/data/rows", headers=headers,
            json={"profile_id": profile["id"], "database": DATABASE, "table": TABLE, "values": {"name": "Gamma", "status": "pending", "amount": "9.90"}},
        )
        assert inserted.status_code == 200, inserted.text
        gamma_id = inserted.json()["last_insert_id"]

        exported = client.get(
            "/api/data/export",
            headers=headers,
            params={"profile_id": profile["id"], "database": DATABASE, "table": TABLE, "format": "csv"},
        )
        assert exported.status_code == 200
        assert "Alpha" in exported.text and "Gamma" in exported.text

        excel_export = client.get(
            "/api/data/export", headers=headers,
            params={"profile_id": profile["id"], "database": DATABASE, "table": TABLE, "format": "xlsx"},
        )
        assert excel_export.status_code == 200
        assert excel_export.content.startswith(b"PK")

        upload = io.StringIO()
        writer = csv.writer(upload)
        writer.writerow(["name", "status", "amount"])
        writer.writerow(["Imported", "active", "77.70"])
        imported = client.post(
            "/api/data/import",
            headers=headers,
            params={"profile_id": profile["id"], "database": DATABASE, "table": TABLE},
            files={"file": ("items.csv", upload.getvalue().encode(), "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["affected_rows"] == 1

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "status", "amount"])
        sheet.append(["Excel Imported", "active", 88.8])
        excel_buffer = io.BytesIO()
        workbook.save(excel_buffer)
        excel_import = client.post(
            "/api/data/import", headers=headers,
            params={"profile_id": profile["id"], "database": DATABASE, "table": TABLE},
            files={"file": ("items.xlsx", excel_buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert excel_import.status_code == 200, excel_import.text
        assert excel_import.json()["affected_rows"] == 1

        deleted = client.request(
            "DELETE", "/api/data/rows", headers=headers,
            json={"profile_id": profile["id"], "database": DATABASE, "table": TABLE, "key": {"id": gamma_id}},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["affected_rows"] == 1

        manual = client.post(
            "/api/sessions", headers=headers,
            json={"profile_id": profile["id"], "database": DATABASE, "autocommit": False},
        ).json()
        try:
            query(headers, manual["id"], f"INSERT INTO `{TABLE}` (name) VALUES ('Rollback me')")
            rollback = client.post(f"/api/sessions/{manual['id']}/rollback", headers=headers)
            assert rollback.status_code == 200
            check = query(headers, session_id, f"SELECT COUNT(*) AS total FROM `{TABLE}` WHERE name='Rollback me'")
            assert check["results"][0]["rows"][0]["total"] == 0
        finally:
            client.delete(f"/api/sessions/{manual['id']}", headers=headers)
    finally:
        query(headers, session_id, f"DROP TABLE IF EXISTS `{TABLE}`")
        client.delete(f"/api/sessions/{session_id}", headers=headers)


def test_table_designer_create_and_alter():
    headers = auth_headers()
    profile_id = client.get("/api/connections", headers=headers).json()[0]["id"]
    cleanup = workbench.create_session(profile_id, DATABASE, True)
    try:
        workbench.execute(cleanup["id"], f"DROP TABLE IF EXISTS `{DDL_TABLE}`")
        workbench.execute(cleanup["id"], f"DROP TABLE IF EXISTS `{PARENT_TABLE}`")
        workbench.execute(cleanup["id"], f"CREATE TABLE `{PARENT_TABLE}` (id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB")
    finally:
        workbench.close_session(cleanup["id"])

    spec = {
        "database": DATABASE,
        "table": DDL_TABLE,
        "columns": [
            {"name": "id", "data_type": "BIGINT UNSIGNED", "nullable": False, "default": None, "extra": "AUTO_INCREMENT", "comment": "主键"},
            {"name": "code", "data_type": "VARCHAR(64)", "nullable": False, "default": None, "extra": "", "comment": "业务编号"},
            {"name": "parent_id", "data_type": "BIGINT UNSIGNED", "nullable": True, "default": None, "extra": "", "comment": "父记录"},
        ],
        "primary_key": ["id"],
        "indexes": [
            {"name": f"uq_{DDL_TABLE}_code", "unique": True, "columns": ["code"]},
            {"name": f"idx_{DDL_TABLE}_parent", "unique": False, "columns": ["parent_id"]},
        ],
        "foreign_keys": [{"name": f"fk_{DDL_TABLE}_parent", "columns": ["parent_id"], "referenced_table": PARENT_TABLE, "referenced_columns": ["id"], "on_delete": "SET NULL", "on_update": "CASCADE"}],
        "checks": [{"name": f"chk_{DDL_TABLE}_code", "clause": "CHAR_LENGTH(code) > 0"}],
    }
    preview = client.post("/api/ddl/preview", headers=headers, json={"profile_id": profile_id, "spec": spec, "current_table": None})
    assert preview.status_code == 200, preview.text
    statements = preview.json()["statements"]
    assert statements[0].startswith("CREATE TABLE")
    applied = client.post("/api/ddl/apply", headers=headers, json={"profile_id": profile_id, "spec": spec, "current_table": None, "expected_statements": statements})
    assert applied.status_code == 200, applied.text

    spec["columns"].append({"name": "label", "data_type": "VARCHAR(120)", "nullable": True, "default": None, "extra": "", "comment": "显示名称"})
    alter = client.post("/api/ddl/preview", headers=headers, json={"profile_id": profile_id, "spec": spec, "current_table": DDL_TABLE})
    assert alter.status_code == 200, alter.text
    alter_statements = alter.json()["statements"]
    assert any("ADD COLUMN `label`" in item for item in alter_statements)
    saved = client.post("/api/ddl/apply", headers=headers, json={"profile_id": profile_id, "spec": spec, "current_table": DDL_TABLE, "expected_statements": alter_statements})
    assert saved.status_code == 200, saved.text
    schema = client.get(f"/api/schema/{profile_id}/{DATABASE}/{DDL_TABLE}", headers=headers).json()
    assert "label" in {item["name"] for item in schema["columns"]}
    assert f"chk_{DDL_TABLE}_code" in {item["name"] for item in schema["checks"]}
    assert f"fk_{DDL_TABLE}_parent" in {item["name"] for item in schema["foreign_keys"]}

    cleanup = workbench.create_session(profile_id, DATABASE, True)
    try:
        workbench.execute(cleanup["id"], f"DROP TABLE `{DDL_TABLE}`")
        workbench.execute(cleanup["id"], f"DROP TABLE `{PARENT_TABLE}`")
    finally:
        workbench.close_session(cleanup["id"])


def test_query_cancellation():
    profile_id = workbench.list_profiles()[0]["id"]
    session = workbench.create_session(profile_id, DATABASE, True)
    result: dict[str, object] = {}

    def run_sleep():
        started = time.perf_counter()
        try:
            workbench.execute(session["id"], "SELECT SLEEP(5) AS finished")
            result["completed"] = True
        except Exception as exc:  # cancellation is expected to raise the driver error
            result["error"] = str(exc)
        finally:
            result["elapsed"] = time.perf_counter() - started

    thread = threading.Thread(target=run_sleep)
    thread.start()
    time.sleep(0.4)
    assert workbench.cancel(session["id"]) is True
    thread.join(timeout=3)
    workbench.close_session(session["id"])
    assert not thread.is_alive()
    # MySQL may return SLEEP()=1 instead of raising, but it must stop promptly.
    assert float(result["elapsed"]) < 3


def test_workspace_session_cleanup():
    profile_id = workbench.list_profiles()[0]["id"]
    first = workbench.create_session(profile_id, DATABASE, True, "pytest-workspace")
    second = workbench.create_session(profile_id, DATABASE, False, "pytest-workspace")
    assert first["id"] != second["id"]
    assert workbench.cleanup_workspace("pytest-workspace") == 2
    assert first["id"] not in workbench.sessions
    assert second["id"] not in workbench.sessions
