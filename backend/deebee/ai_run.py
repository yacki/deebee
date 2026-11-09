"""Deterministic admission for automatically running model-generated queries."""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .access.sql import SAFE_FUNCTIONS


def can_auto_run(code: str, language: str) -> bool:
    # Arbitrary shell commands always get a visible confirmation, even if the
    # model describes them as read-only. SQL is inspected independently of it.
    if language == "shell" or not code.strip() or any(s in code for s in ("/*!", "/*+", "\x00", "@")):
        return False
    try:
        trees = [t for t in sqlglot.parse(code, read={"mysql": "mysql", "postgresql": "postgres", "mssql": "tsql"}[language]) if t is not None]
    except (SqlglotError, KeyError):
        return False
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return False
    forbidden = {"Command", "Transaction", "Commit", "Rollback", "Set", "Use", "Into", "Lock", "Copy", "Grant", "Revoke", "Execute", "Pragma", "LoadData", "SessionParameter", "Parameter", "Placeholder", "Returning", "Insert", "Update", "Delete", "Create", "Drop", "Alter", "Merge"}
    for node in trees[0].walk():
        if type(node).__name__ in forbidden:
            return False
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            return False
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in SAFE_FUNCTIONS or isinstance(node.parent, exp.Dot):
                return False
    return True
