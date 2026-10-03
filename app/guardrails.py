import re
from sqlglot import exp, parse_one

DESTRUCTIVE = {"DELETE", "UPDATE", "INSERT", "DROP", "ALTER", "TRUNCATE", "REPLACE", "MERGE", "VACUUM", "ATTACH", "DETACH", "PRAGMA", "CREATE"}

class SQLValidationError(Exception):
    pass

def _strip_trailing_semicolon(sql: str) -> str:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if ";" in s:
        raise SQLValidationError("Only one SQL statement is allowed.")
    return s

def validate_read_only_sql(sql: str, dialect: str = "sqlite") -> str:
    if not sql or not sql.strip():
        raise SQLValidationError("No SQL query was generated.")
    cleaned = _strip_trailing_semicolon(sql)
    # Reject SQL comments in model output; they add little value and complicate safety checks.
    if "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
        raise SQLValidationError("SQL comments are not allowed in generated queries.")
    try:
        tree = parse_one(cleaned, read=dialect)
    except Exception as exc:
        raise SQLValidationError(f"Invalid SQL syntax: {exc}") from exc

    # SELECT, UNION/INTERSECT/EXCEPT and CTEs rooted in a query are read-only.
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)) and not tree.find(exp.Select):
        raise SQLValidationError("Only read-only SELECT queries are allowed.")

    for node in tree.walk():
        if node.key.upper() in {name.lower() for name in DESTRUCTIVE}:
            raise SQLValidationError(f"Forbidden SQL operation: {node.key.upper()}.")
    return tree.sql(dialect=dialect)

def validate_schema_references(sql: str, schema_tables: set[str], schema_columns: dict[str, set[str]]) -> list[str]:
    errors: list[str] = []
    try:
        tree = parse_one(sql, read="sqlite")
    except Exception:
        return ["SQL could not be parsed for schema validation."]

    table_lookup = {t.lower(): t for t in schema_tables}
    all_columns = {c.lower() for cols in schema_columns.values() for c in cols}
    aliases: dict[str, str] = {}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}

    for table in tree.find_all(exp.Table):
        name = table.name
        if name.lower() not in table_lookup and name.lower() not in cte_names:
            errors.append(f"Unknown table: {name}")
        if table.alias:
            aliases[table.alias.lower()] = name.lower()

    for col in tree.find_all(exp.Column):
        name = col.name
        if name == "*":
            continue
        if col.table:
            qualifier = col.table.lower()
            actual = aliases.get(qualifier, qualifier)
            if actual in cte_names:
                continue
            if actual not in table_lookup:
                errors.append(f"Unknown table/alias: {col.table}")
            else:
                real_table = table_lookup[actual]
                if name.lower() not in {c.lower() for c in schema_columns[real_table]}:
                    errors.append(f"Unknown column: {col.table}.{name}")
        elif name.lower() not in all_columns and name.lower() not in select_aliases:
            errors.append(f"Unknown column: {name}")
    return sorted(set(errors))

def validate_relationships(sql: str, foreign_keys: set[tuple[str, str, str, str]]) -> list[str]:
    """Flag joins whose equality does not match a known FK relationship.

    This is intentionally conservative: non-equality filters and derived-table joins are
    allowed, while obvious base-table joins are checked against known relationships.
    """
    try:
        tree = parse_one(sql, read="sqlite")
    except Exception:
        return ["SQL could not be parsed for relationship validation."]
    table_lookup = {t.lower(): t for t in {x[0] for x in foreign_keys} | {x[2] for x in foreign_keys}}
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if table.name.lower() in table_lookup:
            aliases[table.alias_or_name.lower()] = table_lookup[table.name.lower()]
    allowed = {(a.lower(), b.lower(), c.lower(), d.lower()) for a,b,c,d in foreign_keys} | {(c.lower(), d.lower(), a.lower(), b.lower()) for a,b,c,d in foreign_keys}
    errors=[]
    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column) or not left.table or not right.table:
            continue
        lt, rt = aliases.get(left.table.lower(), left.table), aliases.get(right.table.lower(), right.table)
        pair=(lt.lower(), left.name.lower(), rt.lower(), right.name.lower())
        if pair not in allowed and lt.lower() != rt.lower():
            # Do not reject arbitrary valid analytical joins; surface it as a warning.
            errors.append(f"Join relationship not present in declared foreign keys: {left.sql()} = {right.sql()}")
    return sorted(set(errors))

def is_safe_user_sql(sql: str) -> tuple[bool, str]:
    try:
        normalized = validate_read_only_sql(sql)
        return True, normalized
    except SQLValidationError as exc:
        return False, str(exc)
