from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SqlStatement:
    sql: str
    end_byte: int
    number: int


@dataclass(frozen=True)
class SqlBatch:
    sql: str
    end_byte: int
    first_statement: int
    last_statement: int


_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def iter_sql_statements(path: str | Path, driver: str = "mysql") -> Iterator[SqlStatement]:
    """Stream SQL statements without loading the whole dump into memory.

    The scanner understands quoted strings/identifiers, line and block comments,
    PostgreSQL dollar quotes, MySQL DELIMITER directives and SQL Server GO lines.
    Statement offsets are byte based so callers can report meaningful progress.
    """

    source = Path(path)
    delimiter = ";"
    parts: list[str] = []
    quote = ""
    dollar_quote = ""
    block_comment = False
    escaped = False
    number = 0
    first_line = True

    with source.open("rb") as handle:
        while raw_line := handle.readline():
            line_end = handle.tell()
            try:
                line = raw_line.decode("utf-8-sig" if first_line else "utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("SQL 文件必须使用 UTF-8 编码") from exc
            first_line = False

            stripped = line.strip()
            if not quote and not dollar_quote and not block_comment:
                if driver == "mysql" and stripped.upper().startswith("DELIMITER "):
                    delimiter = stripped.split(None, 1)[1]
                    if not delimiter:
                        raise ValueError("DELIMITER 指令缺少分隔符")
                    continue
                if driver == "mssql" and stripped.upper() == "GO":
                    statement = "".join(parts).strip()
                    parts.clear()
                    if statement:
                        number += 1
                        yield SqlStatement(statement, line_end, number)
                    continue

            index = 0
            line_comment = False
            while index < len(line):
                if line_comment:
                    parts.append(line[index:])
                    index = len(line)
                    continue

                if block_comment:
                    end = line.find("*/", index)
                    if end < 0:
                        parts.append(line[index:])
                        index = len(line)
                    else:
                        parts.append(line[index : end + 2])
                        index = end + 2
                        block_comment = False
                    continue

                if dollar_quote:
                    end = line.find(dollar_quote, index)
                    if end < 0:
                        parts.append(line[index:])
                        index = len(line)
                    else:
                        parts.append(line[index : end + len(dollar_quote)])
                        index = end + len(dollar_quote)
                        dollar_quote = ""
                    continue

                char = line[index]
                if quote:
                    parts.append(char)
                    if escaped:
                        escaped = False
                    elif char == "\\" and driver == "mysql":
                        escaped = True
                    elif quote == "[" and char == "]":
                        if index + 1 < len(line) and line[index + 1] == "]":
                            parts.append("]")
                            index += 1
                        else:
                            quote = ""
                    elif char == quote:
                        if index + 1 < len(line) and line[index + 1] == quote:
                            parts.append(quote)
                            index += 1
                        else:
                            quote = ""
                    index += 1
                    continue

                if line.startswith("/*", index):
                    parts.append("/*")
                    block_comment = True
                    index += 2
                    continue
                if line.startswith("--", index) or (driver == "mysql" and char == "#"):
                    parts.append(line[index:])
                    line_comment = True
                    index = len(line)
                    continue
                if char in {"'", '"', "`"} or (driver == "mssql" and char == "["):
                    quote = char
                    parts.append(char)
                    index += 1
                    continue
                if driver == "postgresql" and char == "$":
                    match = _DOLLAR_QUOTE.match(line, index)
                    if match:
                        dollar_quote = match.group(0)
                        parts.append(dollar_quote)
                        index = match.end()
                        continue
                if delimiter and line.startswith(delimiter, index):
                    statement = "".join(parts).strip()
                    parts.clear()
                    index += len(delimiter)
                    if statement:
                        number += 1
                        yield SqlStatement(statement, line_end, number)
                    continue
                parts.append(char)
                index += 1

    if quote or dollar_quote or block_comment:
        raise ValueError("SQL 文件结尾存在未闭合的字符串、标识符或注释")
    statement = "".join(parts).strip()
    if statement:
        number += 1
        yield SqlStatement(statement, source.stat().st_size, number)


def iter_sql_batches(
    path: str | Path,
    driver: str = "mysql",
    *,
    max_statements: int = 250,
    max_bytes: int = 1024 * 1024,
) -> Iterator[SqlBatch]:
    statements: list[SqlStatement] = []
    size = 0
    for statement in iter_sql_statements(path, driver):
        encoded_size = len(statement.sql.encode("utf-8")) + 2
        if statements and (len(statements) >= max_statements or size + encoded_size > max_bytes):
            yield SqlBatch(
                ";\n".join(item.sql for item in statements),
                statements[-1].end_byte,
                statements[0].number,
                statements[-1].number,
            )
            statements = []
            size = 0
        statements.append(statement)
        size += encoded_size
    if statements:
        yield SqlBatch(
            ";\n".join(item.sql for item in statements),
            statements[-1].end_byte,
            statements[0].number,
            statements[-1].number,
        )


def execute_sql_file(
    path: str | Path,
    driver: str,
    execute_batch: Callable[[str], None],
    *,
    cancelled: threading.Event | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, int | bool]:
    total_bytes = os.path.getsize(path)
    if total_bytes <= 0:
        raise ValueError("SQL 文件没有内容")
    statements = 0
    batches = 0
    for batch in iter_sql_batches(path, driver):
        if cancelled and cancelled.is_set():
            raise RuntimeError("任务已取消")
        try:
            execute_batch(batch.sql)
        except Exception as exc:
            if cancelled and cancelled.is_set():
                raise RuntimeError("任务已取消") from exc
            location = (
                f"第 {batch.first_statement} 条 SQL"
                if batch.first_statement == batch.last_statement
                else f"第 {batch.first_statement}–{batch.last_statement} 条 SQL"
            )
            raise RuntimeError(f"{location}执行失败：{exc}") from exc
        statements = batch.last_statement
        batches += 1
        if progress:
            progress(min(batch.end_byte, total_bytes), total_bytes, statements)
    if not statements:
        raise ValueError("SQL 文件没有可执行语句")
    return {"ok": True, "statements": statements, "batches": batches, "bytes": total_bytes}
