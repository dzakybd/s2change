"""SQL guard for the agent's free-form ``readonly_sql`` tool: one SELECT, allow-listed relations, capped rows.

This is defence in depth. The real boundary is the ``s2cd_reader`` role (SELECT only, read-only transactions, 5 s timeout).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"change_runs", "change_features", "change_context", "timeline_months", "change_features_wgs84"}
MAX_ROWS = 200
BANNED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "set_config", "current_setting", "query_to_xml", "txid_", "ts_debug")


class SqlRejected(ValueError):
    """The query is not a single safe SELECT over the allow-listed relations."""


def guard_sql(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return the query to execute (with a LIMIT of at most ``max_rows``) or raise :class:`SqlRejected`."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"Could not parse the SQL: {str(e)[:120]}") from e
    if len(statements) != 1:
        raise SqlRejected("Send exactly one SELECT statement.")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SqlRejected("Only SELECT queries are allowed.")
    forbidden = (exp.Into, exp.Lock, exp.Command, exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)
    if tree.find(*forbidden):
        raise SqlRejected("Only plain SELECT queries are allowed (no INTO, locking or DDL/DML).")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.db or t.catalog or (name not in ALLOWED_TABLES and name not in ctes):
            raise SqlRejected(f"Relation '{t.sql()}' is not allowed. Use only: {', '.join(sorted(ALLOWED_TABLES))}.")
    for f in tree.find_all(exp.Func):
        name = (f.sql_name() if not isinstance(f, exp.Anonymous) else str(f.name)).lower()
        if name.startswith(BANNED_FUNCTION_PREFIXES):
            raise SqlRejected(f"Function '{name}' is not allowed.")
    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        current = int(limit.expression.name)
    if current is None or current > max_rows:
        tree = tree.limit(max_rows)
    return tree.sql(dialect="postgres")
