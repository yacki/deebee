from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from deebee.main import api_app as app


pytestmark = pytest.mark.skipif(
    os.getenv("DEEBEE_POSTGRES_ENABLED", "").lower() not in {"1", "true", "yes", "on"},
    reason="PostgreSQL integration profile is disabled",
)

SCHEMA_A = "deebee_pg_schema_a"
SCHEMA_B = "deebee_pg_schema_b"
TABLE = "same_name_items"
client = TestClient(app)


def auth_headers() -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={
            "username": os.getenv("DEEBEE_ADMIN_USER", "admin"),
            "password": os.getenv("DEEBEE_ADMIN_PASSWORD", ""),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def query(headers: dict[str, str], session_id: str, sql: str) -> dict:
    response = client.post(
        f"/api/sessions/{session_id}/query",
        headers=headers,
        json={"sql": sql, "limit": 1000},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_session(
    headers: dict[str, str], profile_id: str, database: str, schema: str
) -> str:
    response = client.post(
        "/api/sessions",
        headers=headers,
        json={
            "profile_id": profile_id,
            "database": database,
            "schema": schema,
            "autocommit": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["schema"] == schema
    return response.json()["id"]


def test_postgres_database_schema_object_hierarchy_and_isolation():
    headers = auth_headers()
    profiles = client.get("/api/connections", headers=headers).json()
    profile = next(item for item in profiles if item["driver"] == "postgresql")
    database = profile["default_database"]
    admin_session = create_session(headers, profile["id"], database, "public")
    sessions = [admin_session]
    try:
        query(
            headers,
            admin_session,
            f'DROP SCHEMA IF EXISTS "{SCHEMA_A}" CASCADE; '
            f'DROP SCHEMA IF EXISTS "{SCHEMA_B}" CASCADE; '
            f'CREATE SCHEMA "{SCHEMA_A}"; CREATE SCHEMA "{SCHEMA_B}";',
        )
        for schema, label in ((SCHEMA_A, "alpha"), (SCHEMA_B, "beta")):
            session_id = create_session(headers, profile["id"], database, schema)
            sessions.append(session_id)
            result = query(
                headers,
                session_id,
                f'CREATE TABLE "{TABLE}" ('
                "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label text NOT NULL); "
                f'INSERT INTO "{TABLE}" (label) VALUES (\'{label}\'); '
                f'SELECT * FROM "{TABLE}";',
            )
            assert result["results"][-1]["rows"] == [{"id": 1, "label": label}]

        schemas = client.get(
            f"/api/connections/{profile['id']}/databases/{database}/schemas",
            headers=headers,
        )
        assert schemas.status_code == 200, schemas.text
        assert {SCHEMA_A, SCHEMA_B}.issubset({item["name"] for item in schemas.json()})

        for schema, label in ((SCHEMA_A, "alpha"), (SCHEMA_B, "beta")):
            objects = client.get(
                f"/api/connections/{profile['id']}/databases/{database}/objects",
                headers=headers,
                params={"schema": schema},
            )
            assert objects.status_code == 200, objects.text
            assert objects.json()["schema"] == schema
            assert TABLE in {item["name"] for item in objects.json()["tables"]}

            catalog = client.get(
                f"/api/connections/{profile['id']}/databases/{database}/catalog",
                headers=headers,
                params={"schema": schema},
            )
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()["schema"] == schema

            table_schema = client.get(
                f"/api/schema/{profile['id']}/{database}/{TABLE}",
                headers=headers,
                params={"schema": schema},
            )
            assert table_schema.status_code == 200, table_schema.text
            assert table_schema.json()["schema"] == schema
            assert table_schema.json()["primary_key"] == ["id"]

            data = client.post(
                "/api/data/read",
                headers=headers,
                json={
                    "profile_id": profile["id"], "database": database,
                    "schema": schema, "table": TABLE, "page": 1, "page_size": 100,
                },
            )
            assert data.status_code == 200, data.text
            assert data.json()["rows"] == [{"id": 1, "label": label}]

        spec = {
            "database": database,
            "schema": SCHEMA_A,
            "table": "designed_table",
            "columns": [
                {
                    "name": "id", "data_type": "BIGINT", "nullable": False,
                    "default": None, "extra": "AUTO_INCREMENT", "comment": "",
                },
                {
                    "name": "code", "data_type": "VARCHAR(40)", "nullable": False,
                    "default": None, "extra": "", "comment": "",
                },
            ],
            "primary_key": ["id"],
            "indexes": [{"name": "uq_designed_code", "unique": True, "columns": ["code"]}],
            "foreign_keys": [],
            "checks": [{"name": "chk_designed_code", "clause": "char_length(code) > 0"}],
            "engine": "PostgreSQL", "charset": "UTF8", "collation": "", "comment": "",
        }
        preview = client.post(
            "/api/ddl/preview", headers=headers,
            json={"profile_id": profile["id"], "spec": spec, "current_table": None},
        )
        assert preview.status_code == 200, preview.text
        statements = preview.json()["statements"]
        assert statements[0].startswith(f'CREATE TABLE "{SCHEMA_A}"."designed_table"')
        applied = client.post(
            "/api/ddl/apply", headers=headers,
            json={
                "profile_id": profile["id"], "spec": spec, "current_table": None,
                "expected_statements": statements,
            },
        )
        assert applied.status_code == 200, applied.text
    finally:
        try:
            query(
                headers,
                admin_session,
                f'DROP SCHEMA IF EXISTS "{SCHEMA_A}" CASCADE; '
                f'DROP SCHEMA IF EXISTS "{SCHEMA_B}" CASCADE;',
            )
        finally:
            for session_id in sessions:
                client.delete(f"/api/sessions/{session_id}", headers=headers)
