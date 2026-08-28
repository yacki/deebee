from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient

from deebee.main import api_app as app


DATABASE = "deebee_e2e"
OPS_DATABASE = "deebee_e2e_database_ops"
TABLE = "deebee_replica_actions"
COPY_TABLE = "deebee_replica_actions_copy"
RENAMED_TABLE = "deebee_replica_actions_renamed"
SCRIPT_TABLE = "deebee_replica_script"
VIEW = "deebee_replica_view"
FUNCTION = "deebee_replica_function"
client = TestClient(app)


def headers() -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "deebee"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def open_session(auth: dict[str, str], database: str = DATABASE) -> str:
    response = client.post("/api/sessions", headers=auth, json={
        "profile_id": "mysql-default", "database": database, "autocommit": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def query(auth: dict[str, str], session_id: str, sql: str) -> dict:
    response = client.post(f"/api/sessions/{session_id}/query", headers=auth, json={"sql": sql})
    assert response.status_code == 200, response.text
    return response.json()


def action(auth: dict[str, str], table: str, operation: str, **extra) -> dict:
    response = client.post("/api/objects/table/action", headers=auth, json={
        "profile_id": "mysql-default", "database": DATABASE, "table": table,
        "action": operation, **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_database_object_and_completion_workflows():
    auth = headers()
    session_id = open_session(auth)
    try:
        query(auth, session_id, f"DROP VIEW IF EXISTS `{VIEW}`; DROP FUNCTION IF EXISTS `{FUNCTION}`; DROP TABLE IF EXISTS `{RENAMED_TABLE}`, `{COPY_TABLE}`, `{TABLE}`")
        query(auth, session_id, f"""CREATE TABLE `{TABLE}` (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(80) NOT NULL,
            amount DECIMAL(10,2) NULL,
            computed DECIMAL(12,2) GENERATED ALWAYS AS (amount * 2) STORED,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_replica_name (name)
        ) ENGINE=InnoDB COMMENT='replica action fixture'""")
        query(auth, session_id, f"INSERT INTO `{TABLE}` (name, amount) VALUES ('seed', 12.50)")
        query(auth, session_id, f"CREATE VIEW `{VIEW}` AS SELECT id, name FROM `{TABLE}`")
        query(auth, session_id, f"CREATE FUNCTION `{FUNCTION}`(value INT) RETURNS INT DETERMINISTIC RETURN value * 2")

        objects = client.get(
            f"/api/connections/mysql-default/databases/{DATABASE}/objects", headers=auth
        )
        assert objects.status_code == 200, objects.text
        table = next(item for item in objects.json()["tables"] if item["name"] == TABLE)
        assert table["engine"] == "InnoDB"
        assert table["comment"] == "replica action fixture"
        assert "data_length" in table and "collation" in table
        assert "events" in objects.json()

        catalog = client.get(
            f"/api/connections/mysql-default/databases/{DATABASE}/catalog", headers=auth
        )
        assert catalog.status_code == 200, catalog.text
        metadata = next(item for item in catalog.json()["tables"] if item["name"] == TABLE)
        assert {item["name"] for item in metadata["columns"]} >= {"id", "name", "amount", "computed"}

        schema = client.get(f"/api/schema/mysql-default/{DATABASE}/{TABLE}", headers=auth)
        assert schema.status_code == 200, schema.text
        computed = next(item for item in schema.json()["columns"] if item["name"] == "computed")
        assert "amount" in computed["generation"]
        assert schema.json()["engine"] == "InnoDB"

        ddl = client.get(
            f"/api/objects/mysql-default/{DATABASE}/table/{TABLE}/ddl", headers=auth
        )
        assert ddl.status_code == 200, ddl.text
        assert f"CREATE TABLE `{TABLE}`" in ddl.json()["sql"]

        found = client.get(
            f"/api/search/mysql-default/{DATABASE}", headers=auth, params={"q": "replica"}
        )
        assert found.status_code == 200, found.text
        assert any(item["name"] == TABLE for item in found.json())

        grants = client.get(
            f"/api/privileges/mysql-default/{DATABASE}", headers=auth, params={"table": TABLE}
        )
        assert grants.status_code == 200, grants.text
        assert grants.json()["account"] and grants.json()["grants"]

        for kind, name in (("view", VIEW), ("function", FUNCTION)):
            definition = client.get(
                f"/api/objects/mysql-default/{DATABASE}/{kind}/{name}/ddl", headers=auth
            )
            assert definition.status_code == 200, definition.text
            assert name in definition.json()["sql"]
            dropped = client.post("/api/objects/action", headers=auth, json={
                "profile_id": "mysql-default", "database": DATABASE,
                "kind": kind, "name": name, "action": "drop",
            })
            assert dropped.status_code == 200, dropped.text

        action(auth, TABLE, "duplicate", target=COPY_TABLE, with_data=True)
        copied = query(auth, session_id, f"SELECT COUNT(*) AS total FROM `{COPY_TABLE}`")
        assert copied["results"][0]["rows"][0]["total"] == 1
        action(auth, COPY_TABLE, "rename", target=RENAMED_TABLE)
        for operation in ("check", "analyze", "optimize", "repair"):
            maintained = action(auth, RENAMED_TABLE, operation)
            assert maintained["results"]
        action(auth, RENAMED_TABLE, "empty")
        empty = query(auth, session_id, f"SELECT COUNT(*) AS total FROM `{RENAMED_TABLE}`")
        assert empty["results"][0]["rows"][0]["total"] == 0
        action(auth, RENAMED_TABLE, "truncate")
        action(auth, RENAMED_TABLE, "drop")
    finally:
        query(auth, session_id, f"DROP VIEW IF EXISTS `{VIEW}`; DROP FUNCTION IF EXISTS `{FUNCTION}`; DROP TABLE IF EXISTS `{RENAMED_TABLE}`, `{COPY_TABLE}`, `{TABLE}`")
        client.delete(f"/api/sessions/{session_id}", headers=auth)


def test_wizards_dump_script_and_generated_data_roundtrip():
    auth = headers()
    session_id = open_session(auth)
    try:
        query(auth, session_id, f"DROP TABLE IF EXISTS `{SCRIPT_TABLE}`, `{TABLE}`")
        query(auth, session_id, f"CREATE TABLE `{TABLE}` (id BIGINT AUTO_INCREMENT PRIMARY KEY, label VARCHAR(100) NULL, score INT NULL, created_at DATE NULL)")

        generated = client.post("/api/data/generate", headers=auth, json={
            "profile_id": "mysql-default", "database": DATABASE, "table": TABLE, "count": 25,
        })
        assert generated.status_code == 200, generated.text
        assert generated.json()["affected_rows"] == 25

        dumped = client.get("/api/sql/dump", headers=auth, params={
            "profile_id": "mysql-default", "database": DATABASE, "table": TABLE,
            "include_data": True,
        })
        assert dumped.status_code == 200, dumped.text
        assert "CREATE TABLE" in dumped.text and "INSERT INTO" in dumped.text

        action(auth, TABLE, "drop")
        restored = client.post(
            "/api/sql/execute-file", headers=auth,
            params={"profile_id": "mysql-default", "database": DATABASE},
            files={"file": ("restore.sql", dumped.content, "application/sql")},
        )
        assert restored.status_code == 200, restored.text
        restored_count = query(auth, session_id, f"SELECT COUNT(*) AS total FROM `{TABLE}`")
        assert restored_count["results"][0]["rows"][0]["total"] == 25

        script = f"CREATE TABLE `{SCRIPT_TABLE}` (id INT PRIMARY KEY, note VARCHAR(30)); INSERT INTO `{SCRIPT_TABLE}` VALUES (1, 'ok'); SELECT * FROM `{SCRIPT_TABLE}`;"
        executed = client.post(
            "/api/sql/execute-file", headers=auth,
            params={"profile_id": "mysql-default", "database": DATABASE},
            files={"file": ("fixture.sql", io.BytesIO(script.encode()), "application/sql")},
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["statements"] == 3
        assert executed.json()["results"][-1]["rows"][0]["note"] == "ok"
    finally:
        query(auth, session_id, f"DROP TABLE IF EXISTS `{SCRIPT_TABLE}`, `{TABLE}`")
        client.delete(f"/api/sessions/{session_id}", headers=auth)


def test_create_and_delete_database_with_confirmation_api():
    auth = headers()
    cleanup = open_session(auth, "")
    try:
        query(auth, cleanup, f"DROP DATABASE IF EXISTS `{OPS_DATABASE}`")
        created = client.post("/api/databases", headers=auth, json={
            "profile_id": "mysql-default", "name": OPS_DATABASE,
            "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
        })
        assert created.status_code == 200, created.text
        databases = client.get("/api/connections/mysql-default/databases", headers=auth)
        assert OPS_DATABASE in {item["name"] for item in databases.json()}
        deleted = client.delete(f"/api/databases/mysql-default/{OPS_DATABASE}", headers=auth)
        assert deleted.status_code == 200, deleted.text
    finally:
        query(auth, cleanup, f"DROP DATABASE IF EXISTS `{OPS_DATABASE}`")
        client.delete(f"/api/sessions/{cleanup}", headers=auth)


def test_background_jobs_report_progress_results_and_cancellation():
    auth = headers()
    session_id = open_session(auth)
    try:
        query(auth, session_id, f"DROP TABLE IF EXISTS `{TABLE}`")
        query(auth, session_id, f"CREATE TABLE `{TABLE}` (id BIGINT AUTO_INCREMENT PRIMARY KEY, label VARCHAR(100), score INT)")
        created = client.post("/api/jobs/generate", headers=auth, json={
            "profile_id": "mysql-default", "database": DATABASE, "table": TABLE, "count": 30,
        })
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        progress_values = []
        for _ in range(80):
            status = client.get(f"/api/jobs/{job_id}", headers=auth).json()
            progress_values.append(status["progress"])
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert status["status"] == "completed", status
        assert status["progress"] == 100
        assert status["result"]["affected_rows"] == 30
        assert max(progress_values) == 100

        csv_content = b"label,score\njob-import,42\n"
        imported = client.post(
            "/api/jobs/import", headers=auth,
            params={"profile_id": "mysql-default", "database": DATABASE, "table": TABLE},
            files={"file": ("job.csv", csv_content, "text/csv")},
        )
        import_id = imported.json()["id"]
        for _ in range(80):
            import_status = client.get(f"/api/jobs/{import_id}", headers=auth).json()
            if import_status["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert import_status["status"] == "completed", import_status
        assert import_status["result"]["affected_rows"] == 1

        cancel_source = client.post("/api/jobs/generate", headers=auth, json={
            "profile_id": "mysql-default", "database": DATABASE, "table": TABLE, "count": 10000,
        }).json()
        cancelled = client.post(f"/api/jobs/{cancel_source['id']}/cancel", headers=auth)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] in {"cancelling", "cancelled", "completed"}
        if cancelled.json()["status"] != "completed":
            for _ in range(80):
                cancel_status = client.get(f"/api/jobs/{cancel_source['id']}", headers=auth).json()
                if cancel_status["status"] in {"cancelled", "completed", "failed"}:
                    break
                time.sleep(0.05)
            assert cancel_status["status"] == "cancelled", cancel_status
    finally:
        query(auth, session_id, f"DROP TABLE IF EXISTS `{TABLE}`")
        client.delete(f"/api/sessions/{session_id}", headers=auth)
