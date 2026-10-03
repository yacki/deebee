from __future__ import annotations

import asyncio
import datetime
import decimal
import os
import shlex
import ssl
import threading
from typing import Any

import asyncssh
import pymysql
import psycopg

from .models import AccessError
from .sql import prepare_sql
from .store import encode


class VerifiedSSHClient(asyncssh.SSHClient):
    def __init__(self, fingerprint: str):
        self.fingerprint = fingerprint

    def validate_host_public_key(self, host, addr, port, key):
        # Called during key exchange, before user authentication.
        return bool(self.fingerprint) and key.get_fingerprint("sha256") == self.fingerprint


async def ssh_connect(resource: dict, account: dict, secret: dict):
    keys = None
    if account["auth_method"] == "private_key":
        keys = [asyncssh.import_private_key(secret["private_key"], passphrase=secret.get("passphrase") or None)]
    return await asyncssh.connect(resource["host"], port=resource["port"], username=account["username"],
                                  password=secret.get("password") if keys is None else None, client_keys=keys,
                                  known_hosts=(), client_factory=lambda: VerifiedSSHClient(resource["host_key"]),
                                  server_host_key_algs=[resource.get("host_key_algorithm", "ssh-ed25519")],
                                  agent_path=None, connect_timeout=10, login_timeout=10,
                                  keepalive_interval=15, keepalive_count_max=2)


def db_connect(resource: dict, account: dict, secret: dict):
    if not resource["tls"] and os.getenv("DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL", "") != "1":
        raise AccessError("TLS_REQUIRED", "数据库必须启用 TLS（仅隔离测试可显式关闭）")
    if resource["type"] == "mysql":
        context = ssl.create_default_context(cafile=resource.get("ca_file") or None) if resource["tls"] else None
        return pymysql.connect(host=resource["host"], port=resource["port"], user=account["username"],
                               password=secret.get("password", ""), database=resource["database"],
                               ssl=context, charset="utf8mb4", connect_timeout=10, read_timeout=310, write_timeout=30,
                               autocommit=False, local_infile=False)
    return psycopg.connect(host=resource["host"], port=resource["port"], user=account["username"],
                           password=secret.get("password", ""), dbname=resource["database"],
                           connect_timeout=10, autocommit=False,
                           sslmode="verify-full" if resource["tls"] else "disable",
                           **({"sslrootcert": resource["ca_file"]} if resource.get("ca_file") else {}))


def serial_value(value: Any):
    if isinstance(value, (decimal.Decimal, datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, int) and abs(value) > 9007199254740991:
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"binary_bytes": len(value), "omitted": True}
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


class RunningHandle:
    def __init__(self):
        self.cancelled = threading.Event()
        self.stop = None

    async def cancel(self):
        self.cancelled.set()
        if self.stop:
            result = self.stop()
            if asyncio.iscoroutine(result):
                await result


def elevated_ssh_command(account: dict, secret: dict, command: str, elevation: str) -> tuple[str, str]:
    if elevation == "none":
        return command, ""
    if elevation != "sudo" or account.get("tier") != "privileged":
        raise AccessError("PRIVILEGE_DENIED", "sudo 提权需要已授权的特权账号", 403)
    password = secret.get("sudo_password", "")
    if "\n" in password or "\r" in password:
        raise AccessError("INVALID_CREDENTIAL", "sudo 凭据格式无效")
    # stdin is reserved for authentication. Even with NOPASSWD, the payload
    # must never receive an unread password. The shell replaces its fd 0 first.
    wrapped = shlex.quote("exec </dev/null\n" + command)
    return ("sudo -S -p '' -- sh -c " if password else "sudo -n -- sh -c ") + wrapped, password


async def ssh_execute(resource: dict, account: dict, secret: dict, command: str, limit: int, handle: RunningHandle, *, elevation: str = "none"):
    command, sudo_password = elevated_ssh_command(account, secret, command, elevation)
    connection = await ssh_connect(resource, account, secret)
    try:
        process = await connection.create_process(command, encoding="utf-8", errors="replace", request_pty=False)
        if sudo_password:
            process.stdin.write(sudo_password + "\n")
        process.stdin.write_eof()
        async def stop():
            process.terminate()
            connection.close()
        handle.stop = stop
        if handle.cancelled.is_set():
            await stop()
        output = {"stdout": "", "stderr": "", "truncated": False}
        remaining = limit
        async def pump(reader, name):
            nonlocal remaining
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                raw = chunk.encode()
                kept = raw[:remaining]
                output[name] += kept.decode("utf-8", errors="ignore")
                remaining -= len(kept)
                if len(kept) < len(raw):
                    output["truncated"] = True
        await asyncio.gather(pump(process.stdout, "stdout"), pump(process.stderr, "stderr"))
        await process.wait_closed()
        if handle.cancelled.is_set():
            raise AccessError("CANCELLATION_UNCONFIRMED", "连接已关闭，无法确认远端所有子进程已终止", 409)
        if sudo_password:
            for field in ("stdout", "stderr"):
                output[field] = output[field].replace(sudo_password, "[REDACTED]")
        return output | {"exit_code": process.exit_status}
    finally:
        handle.stop = None
        connection.close()
        await connection.wait_closed()


def database_execute(resource: dict, account: dict, secret: dict, sql: str, parameters: dict,
                     write: bool, timeout: int, max_rows: int, max_bytes: int, handle: RunningHandle) -> dict:
    statement, binds = prepare_sql(sql, parameters, resource, write)
    conn = db_connect(resource, account, secret)
    is_mysql = resource["type"] == "mysql"
    try:
        if is_mysql:
            thread_id = conn.thread_id()
            async def stop_mysql():
                def kill():
                    killer = db_connect(resource, account, secret)
                    try:
                        with killer.cursor() as cur:
                            cur.execute("KILL QUERY %s", (thread_id,))
                    finally:
                        killer.close()
                await asyncio.to_thread(kill)
            handle.stop = stop_mysql
        else:
            async def stop_pg():
                await asyncio.to_thread(conn.cancel)
            handle.stop = stop_pg
        if handle.cancelled.is_set():
            raise AccessError("CANCELLED", "执行已取消", 409)
        with conn.cursor() as cursor:
            if is_mysql:
                cursor.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout * 1000,))
                cursor.execute("SET SESSION innodb_lock_wait_timeout=%s", (min(timeout, 50),))
                cursor.execute("START TRANSACTION" if write else "START TRANSACTION READ ONLY")
            else:
                cursor.execute("SET TRANSACTION READ WRITE" if write else "SET TRANSACTION READ ONLY")
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout * 1000),))
                from psycopg import sql as pgsql
                cursor.execute(pgsql.SQL("SET LOCAL search_path TO {}").format(pgsql.SQL(",").join(pgsql.Identifier(s) for s in [*resource["schemas"], "pg_catalog"])))
        # MySQL's default cursor buffers the entire result. Read queries use a
        # wire-streaming cursor; PostgreSQL uses a server-side cursor.
        cursor = (conn.cursor(pymysql.cursors.SSCursor) if is_mysql else conn.cursor(name="deebee_read")) if not write else conn.cursor()
        try:
            cursor.execute(statement, binds or None)
            result = {"columns": [], "rows": [], "affected_rows": max(cursor.rowcount, 0) if write else 0, "truncated": False}
            if cursor.description:
                result["columns"] = [{"name": c[0], "type": str(c[1])} for c in cursor.description]
                size = len(encode(result["columns"]).encode())
                # Bound retained results; a single server cell may still exceed
                # the output limit transiently in the database driver's decoder.
                for _ in range(max_rows + 1):
                    row = cursor.fetchone()
                    if row is None:
                        break
                    values = [serial_value(v) for v in row]
                    size += len(encode(values).encode())
                    if len(result["rows"]) == max_rows or size > max_bytes:
                        result["truncated"] = True
                        break
                    result["rows"].append(values)
            if handle.cancelled.is_set():
                conn.rollback()
                raise AccessError("CANCELLED", "执行被取消；请核验非事务操作结果", 409)
            if is_mysql and not write and result["truncated"]:
                # Closing an SSCursor drains unread rows. Close the connection
                # instead so oversized queries cannot force that drain.
                cursor._result.unbuffered_active = False
                conn.close()
                cursor._result = None
                return result
        finally:
            cursor.close()
        if write:
            conn.commit()
        else:
            conn.rollback()
        return result
    finally:
        handle.stop = None
        if not is_mysql or conn.open:
            conn.close()


def inspect_database(resource: dict, account: dict, secret: dict) -> dict:
    conn = db_connect(resource, account, secret)
    try:
        with conn.cursor() as cursor:
            if resource["type"] == "mysql":
                cursor.execute("SELECT CURRENT_USER()")
                actual = cursor.fetchone()[0]
                cursor.execute("SHOW GRANTS")
                grants = [row[0] for row in cursor.fetchall()]
                # A conservative normal account has only SELECT on its own database
                # (plus USAGE), no inherited roles/global grants/routines/GRANT OPTION.
                import re
                safe = True
                for grant in grants:
                    if re.fullmatch(r"GRANT USAGE ON \*\.\* TO .+", grant):
                        continue
                    allowed = re.fullmatch(r"GRANT SELECT ON `([^`]+)`\.(`[^`]+`|\*) TO .+", grant)
                    if not allowed or allowed.group(1) != resource["database"] or "WITH GRANT OPTION" in grant:
                        safe = False
                return {"connected": True, "actual_user": actual, "normal_safe": safe, "permission_summary": grants}
            cursor.execute("SELECT current_user, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user")
            role = cursor.fetchone()
            cursor.execute("SELECT rolname FROM pg_roles WHERE oid IN (SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))")
            inherited = [r[0] for r in cursor.fetchall()]
            cursor.execute("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT IN ('pg_catalog','information_schema')
                AND (has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
                     OR (n.nspname != ALL(%s) AND has_table_privilege(c.oid,'SELECT'))) LIMIT 50""", (resource["schemas"],))
            unsafe_objects = [list(r) for r in cursor.fetchall()]
            cursor.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname != 'information_schema' AND has_schema_privilege(oid,'CREATE')")
            writable_schemas = [r[0] for r in cursor.fetchall()]
            safe = not any(role[1:]) and not inherited and not unsafe_objects and not writable_schemas
            return {"connected": True, "actual_user": role[0], "normal_safe": safe,
                    "permission_summary": {"elevated_role": any(role[1:]), "inherited_roles": inherited, "unsafe_objects": unsafe_objects, "writable_schemas": writable_schemas}}
    finally:
        conn.close()


async def inspect_account(resource: dict, account: dict, secret: dict) -> dict:
    if resource["type"] == "k8s":
        from .operations import inspect_kubernetes
        return await inspect_kubernetes(resource, account, secret)
    if resource["type"] != "ssh":
        return await asyncio.to_thread(inspect_database, resource, account, secret)
    connection = await ssh_connect(resource, account, secret)
    try:
        result = await connection.run("id -u; id -un; sudo -n -l 2>&1", check=False, timeout=10)
        lines = result.stdout.splitlines()
        uid = lines[0] if lines else ""
        # No noninteractive sudo grants is necessary but not sufficient: admin must
        # separately confirm filesystem/groups/capabilities in permission_note.
        sudo_output = "\n".join(lines[2:])
        return {"connected": True, "actual_user": lines[1] if len(lines) > 1 else "", "normal_safe": uid.isdigit() and uid != "0" and result.exit_status != 0,
                "permission_summary": {"uid": uid, "sudo_check": sudo_output[:2000], "manual_review_required": True}}
    finally:
        connection.close()
        await connection.wait_closed()


def database_schema(resource: dict, account: dict, secret: dict) -> list[dict]:
    conn = db_connect(resource, account, secret)
    try:
        with conn.cursor() as cursor:
            if resource["type"] == "mysql":
                cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 10001", (resource["database"],))
            else:
                cursor.execute("SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns WHERE table_schema=ANY(%s) ORDER BY table_schema,table_name,ordinal_position LIMIT 10001", (resource["schemas"],))
            return [{"schema": r[0], "table": r[1], "column": r[2], "type": r[3]} for r in cursor.fetchall()]
    finally:
        conn.close()
