"""Conservative SQL admission, independent of the target's mandatory DB grants."""
from __future__ import annotations

import sqlglot
import math
import re
from sqlglot import exp
from sqlglot.errors import ParseError, UnsupportedError

from .models import AccessError


SAFE_FUNCTIONS = {
    "ABS", "AVG", "CEIL", "CEILING", "COALESCE", "CONCAT", "CONCAT_WS", "COUNT", "CURRENT_DATE",
    "CURRENT_TIMESTAMP", "CURRENT_USER", "DATABASE", "DATE", "DAY", "EXTRACT", "FLOOR", "GREATEST",
    "IF", "IFNULL", "LENGTH", "CHAR_LENGTH", "LEAST", "LOWER", "MAX", "MIN", "MONTH", "NOW", "NULLIF",
    "ROUND", "ROW_NUMBER", "RANK", "DENSE_RANK", "SUM", "SUBSTRING", "TRIM", "UPPER", "YEAR", "CAST",
    "TRY_CAST", "CASE", "ARRAY_AGG", "STRING_AGG", "GROUP_CONCAT", "CURRENT_SCHEMA", "CURRENT_DATABASE",
}


def prepare_sql(sql: str, parameters: dict, resource: dict, write: bool = False) -> tuple[str, dict]:
    dialect = "mysql" if resource["type"] == "mysql" else "postgres"
    if not sql.strip() or any(marker in sql for marker in ("/*!", "/*+", "\x00")):
        raise AccessError("UNSUPPORTED_STATEMENT", "SQL 为空或包含不支持的指令注释")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        raise AccessError("UNSUPPORTED_STATEMENT", "SQL 无法安全解析") from exc
    if len(statements) != 1:
        raise AccessError("UNSUPPORTED_STATEMENT", "每次只支持一条 SQL")
    tree = statements[0]
    if write:
        if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Alter, exp.Drop)):
            raise AccessError("UNSUPPORTED_STATEMENT", "只允许业务表数据和结构变更")
        if isinstance(tree, (exp.Create, exp.Drop)) and str(tree.args.get("kind", "")).upper() not in {"TABLE", "INDEX"}:
            raise AccessError("UNSUPPORTED_STATEMENT", "不允许账号、数据库、过程或扩展管理")
        if isinstance(tree, exp.Alter) and str(tree.args.get("kind", "TABLE")).upper() != "TABLE":
            raise AccessError("UNSUPPORTED_STATEMENT", "仅允许 ALTER TABLE")
    elif not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise AccessError("UNSUPPORTED_STATEMENT", "查询接口仅允许只读 SELECT")
    forbidden = {"Command", "Transaction", "Commit", "Rollback", "Set", "Use", "Into", "Lock", "Copy", "Grant", "Revoke", "Execute", "Pragma", "LoadData", "SessionParameter", "Parameter", "Returning"}
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise AccessError("UNSUPPORTED_STATEMENT", "SQL 包含不允许的会话或副作用操作")
        if not write and isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Merge)):
            raise AccessError("UNSUPPORTED_STATEMENT", "查询不能包含数据变更")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in SAFE_FUNCTIONS or isinstance(node.parent, exp.Dot):
                raise AccessError("UNSUPPORTED_STATEMENT", f"函数 {name[:80]} 不在 V1 允许清单")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.catalog or (table.db and table.db not in ([resource["database"]] if dialect == "mysql" else resource["schemas"])):
            raise AccessError("RESOURCE_SCOPE_VIOLATION", "SQL 引用了资源范围之外的对象", 403)
        if not table.db and dialect == "postgres" and table.name not in ctes:
            # Qualify unqualified tables rather than trusting caller-controlled search_path.
            table.set("db", exp.to_identifier(resource["schemas"][0], quoted=True))
    placeholders = list(tree.find_all(exp.Placeholder))
    names = {str(p.this) for p in placeholders}
    if any(not p.this for p in placeholders) or names != set(parameters):
        raise AccessError("INVALID_ARGUMENT", "SQL 命名参数与 parameters 不一致")
    if any(not isinstance(value, (str, int, float, bool, type(None))) for value in parameters.values()):
        raise AccessError("INVALID_ARGUMENT", "参数只支持标量值")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) for name in parameters) or any(isinstance(v, float) and not math.isfinite(v) for v in parameters.values()):
        raise AccessError("INVALID_ARGUMENT", "参数名称或数值无效")
    # Render with unique marker identifiers, then replace only those markers with
    # DB-API bind placeholders. User text is never interpolated as SQL identifiers.
    import secrets
    markers = {}
    for p in placeholders:
        marker = "__db_bind_" + secrets.token_hex(12)
        markers[marker] = str(p.this)
        p.replace(exp.Var(this=marker))
    try:
        compiled = tree.sql(dialect=dialect, unsupported_level=sqlglot.ErrorLevel.RAISE)
    except UnsupportedError as exc:
        raise AccessError("UNSUPPORTED_STATEMENT", "SQL 方言转换不受支持") from exc
    if markers:
        compiled = compiled.replace("%", "%%")
        for marker, name in markers.items():
            compiled = compiled.replace(marker, f"%({name})s")
    return compiled, parameters
