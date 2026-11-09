"""Isolated local browser QA: python backend/tests/sftp_lab.py.

Only generated files beneath /tmp/deebee-sftp-qa are exposed by this SFTP server.
The app has its own empty configuration store and no database connections.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

ROOT = Path("/tmp/deebee-sftp-qa")
ROOT.mkdir(exist_ok=True)
os.environ.update(DEEBEE_CONNECTIONS_FILE=str(ROOT / "app/connections.json"),
                  DEEBEE_ACCESS_DIR=str(ROOT / "app/access-v1"),
                  DEEBEE_RECORDINGS_DIR=str(ROOT / "app/recordings"),
                  DEEBEE_TOKEN_SECRET=secrets.token_urlsafe(48),
                  DEEBEE_BASE_PATH="", DEEBEE_MYSQL_ENABLED="0", DEEBEE_POSTGRES_ENABLED="0",
                  DEEBEE_ADMIN_USER="admin", DEEBEE_ADMIN_PASSWORD="deebee")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncssh
import uvicorn
from deebee.main import app
from deebee.workbench import workbench
from test_sftp import TestSSHServer


async def main():
    files = ROOT / "remote"
    files.mkdir(exist_ok=True)
    for name in ["documents", "uploads", "archive", "documents/reports"]:
        (files / name).mkdir(exist_ok=True)
    (files / "README.txt").write_text("DeeBee SFTP browser acceptance fixture.\n", encoding="utf-8")
    (files / "documents/说明.txt").write_text("文件管理验收：上传、下载、移动、删除。\n", encoding="utf-8")
    (files / ".hidden-test").write_text("hidden QA fixture\n")
    (ROOT / "browser-upload.txt").write_text("Browser upload roundtrip 中文 2026-10-26\n", encoding="utf-8")
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(TestSSHServer, "127.0.0.1", 0, server_host_keys=[key],
        sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(files)))
    for profile in workbench.list_profiles():
        workbench.delete_connection(profile["id"])
    workbench.create_connection({"driver": "ssh", "name": "SFTP 本地验收主机", "host": "127.0.0.1",
        "port": server.get_port(), "user": "sftp-qa", "password": "local-test-only",
        "options": {"strict_host_key": True, "host_key_fingerprint": key.get_fingerprint("sha256")}})
    print(f"SFTP QA ready. Generated files: {files}", flush=True)
    try:
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18000, log_level="warning")).serve()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
