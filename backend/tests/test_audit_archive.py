from __future__ import annotations

import json
import sqlite3

from deebee.access.audit import preview_bytes, preview_value
from deebee.access.store import AccessStore


def test_audit_preview_redacts_secrets_and_limits_utf8_bytes():
    preview, truncated = preview_bytes(
        json.dumps({"username": "alice", "password": "never-log", "command": "id"}).encode(), "application/json"
    )
    assert "never-log" not in preview
    assert "[REDACTED]" in preview
    assert len(preview.encode("utf-8")) <= 1024
    assert truncated is False

    long_preview, long_truncated = preview_value({"command": "x" * 3000})
    assert len(long_preview.encode("utf-8")) <= 1024
    assert long_truncated is True

    partial_secret, _ = preview_bytes(
        b'{"private_key":"-----BEGIN PRIVATE KEY-----' + b"x" * 4000,
        "application/json",
        total_bytes=8000,
        already_truncated=True,
    )
    assert "BEGIN PRIVATE KEY" not in partial_secret
    assert "[REDACTED]" in partial_secret

    command_preview, _ = preview_value({
        "sql": "CREATE USER demo IDENTIFIED BY 'sql-password'",
        "command": "MYSQL_PWD=cli-password mysql -psecond-password -e 'SELECT 1'",
    })
    assert "sql-password" not in command_preview
    assert "cli-password" not in command_preview
    assert "second-password" not in command_preview

    result, result_truncated = preview_value({"token": "never-log", "rows": [{"name": "张三"}]})
    assert "never-log" not in result
    assert "张三" in result
    assert result_truncated is False


def test_audit_schema_migrates_old_database_and_keeps_archive(tmp_path):
    directory = tmp_path / "access"
    directory.mkdir()
    database = directory / "access.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, "
        "actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL, detail TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO audit(at,actor,action,object_id,detail) VALUES(1,'old-user','old.action','old-object','{}')"
    )
    connection.commit()
    connection.close()

    store = AccessStore(directory)
    try:
        store.audit(
            "principal_1",
            "mcp.tool.ssh.exec",
            "exec_1",
            {"safe": True},
            request_id="req_1",
            surface="mcp",
            actor_name="Agent Alice",
            actor_kind="service",
            auth_method="api_key",
            source_id="local_keys",
            subject="key_1",
            resource_id="ssh_1",
            account_username="root",
            mode="privileged",
            status_code=200,
            request_preview='{"command":"df -h"}',
            response_preview='{"stdout":"ok"}',
        )
        event = store.audits(actor="principal_1", operation="ssh.exec", surface="mcp")[0]
        assert event["actor_name"] == "Agent Alice"
        assert event["resource_id"] == "ssh_1"
        assert event["account_username"] == "root"
        assert event["request_preview"] == '{"command":"df -h"}'
        assert store.audit_event(event["id"])["response_preview"] == '{"stdout":"ok"}'

        store.cleanup()
        assert any(item["actor"] == "old-user" for item in store.audits(limit=500))
    finally:
        store.close()
