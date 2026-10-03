"""Comprehensive end-to-end verification of all system functionality."""
import sys
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

# 1. HTML & JS Static Analysis
print("=" * 60)
print("1. VERIFYING FRONTEND (index.html)")
print("=" * 60)

with open("static/index.html", encoding="utf-8") as f:
    html_content = f.read()

dom_ids = set(re.findall(r'id=["\']([\w\-]+)["\']', html_content))
js_ids = set(re.findall(r"\$\(['\"]([\w\-]+)['\"]\)", html_content))

print(f"  DOM IDs found in HTML: {len(dom_ids)}")
print(f"  IDs queried by JS: {len(js_ids)}")
missing_ids = js_ids - dom_ids
assert not missing_ids, f"ERROR: JS accesses non-existent DOM IDs: {missing_ids}"
print("  [PASS] All JS DOM references match valid HTML elements.")

onclick_handlers = re.findall(r'onclick=["\'](\w+)\(', html_content)
defined_functions = set(re.findall(r"function\s+(\w+)\s*\(", html_content))
missing_funcs = set(onclick_handlers) - defined_functions
assert not missing_funcs, f"ERROR: Onclick handlers not defined: {missing_funcs}"
print(f"  [PASS] All {len(set(onclick_handlers))} onclick handlers are properly defined in JavaScript.")


# 2. Database & Schema Verification
print("\n" + "=" * 60)
print("2. VERIFYING DATABASE (app/db.py)")
print("=" * 60)

from app.db import init_db, get_schema, execute_read_only, get_schema_metadata, get_foreign_keys

init_db()
schema_sql = get_schema()
metadata = get_schema_metadata()
fks = get_foreign_keys()

expected_tables = {"Employees", "Departments", "Customers", "Orders"}
assert set(metadata.keys()) == expected_tables, f"Expected tables {expected_tables}, got {set(metadata.keys())}"
print(f"  [PASS] All {len(expected_tables)} expected tables exist in SQLite schema.")

# Verify row counts and queries
test_queries = [
    ("SELECT COUNT(*) as c FROM Employees", 5),
    ("SELECT COUNT(*) as c FROM Departments", 4),
    ("SELECT COUNT(*) as c FROM Customers", 4),
    ("SELECT COUNT(*) as c FROM Orders", 5),
]
for q, min_count in test_queries:
    rows = execute_read_only(q)
    assert rows and rows[0]["c"] >= min_count, f"Query '{q}' returned insufficient rows: {rows}"
print("  [PASS] Seed data verified across all 4 tables.")

# Verify foreign keys
assert len(fks) == 2, f"Expected 2 foreign keys, got {fks}"
print(f"  [PASS] Foreign key relationships verified ({len(fks)} relations mapped).")

# Verify read-only enforcement in db.py
try:
    execute_read_only("DELETE FROM Employees WHERE EmployeeID = 1")
    assert False, "Should have blocked DELETE"
except sqlite3.OperationalError as e:
    print(f"  [PASS] DB-level guardrail blocked direct destructive SQL: {e}")


# 3. Guardrails AST & Schema Validation
print("\n" + "=" * 60)
print("3. VERIFYING GUARDRAILS (app/guardrails.py)")
print("=" * 60)

from app.guardrails import (
    validate_read_only_sql,
    validate_schema_references,
    validate_relationships,
    SQLValidationError,
)

# Allowed queries
allowed = [
    "SELECT * FROM Employees",
    "SELECT e.FirstName, d.DepartmentName FROM Employees e JOIN Departments d ON e.DepartmentID = d.DepartmentID",
    "WITH HighEarners AS (SELECT * FROM Employees WHERE Salary > 80000) SELECT * FROM HighEarners",
    "SELECT CustomerID FROM Customers UNION SELECT CustomerID FROM Orders",
    "SELECT DepartmentID, AVG(Salary) as avg_sal FROM Employees GROUP BY DepartmentID HAVING avg_sal > 70000 ORDER BY avg_sal DESC LIMIT 5",
]
for q in allowed:
    normalized = validate_read_only_sql(q)
    schema_errs = validate_schema_references(normalized, set(metadata.keys()), metadata)
    assert not schema_errs, f"Unexpected schema errors for valid query '{q}': {schema_errs}"
    fk_errs = validate_relationships(normalized, fks)
    assert not fk_errs, f"Unexpected FK errors for valid query '{q}': {fk_errs}"
print(f"  [PASS] All {len(allowed)} complex SELECT/CTE/JOIN/UNION queries passed guardrails.")

# Blocked destructive queries
blocked = [
    "DELETE FROM Employees",
    "UPDATE Employees SET Salary = 100000",
    "DROP TABLE Customers",
    "ALTER TABLE Orders ADD COLUMN notes TEXT",
    "TRUNCATE Departments",
    "INSERT INTO Customers VALUES (99, 'Acme', 'CA')",
    "PRAGMA table_info(Employees)",
    "SELECT * FROM Employees; DROP TABLE Employees",
    "ATTACH DATABASE 'evil.db' AS evil",
]
for q in blocked:
    try:
        validate_read_only_sql(q)
        assert False, f"Guardrail failed to block: {q}"
    except SQLValidationError as e:
        pass
print(f"  [PASS] All {len(blocked)} destructive / multi-statement attacks blocked.")

# Schema hallucination detection
bad_table = "SELECT * FROM NonExistentTable"
errs = validate_schema_references(validate_read_only_sql(bad_table), set(metadata.keys()), metadata)
assert any("Unknown table" in e for e in errs), f"Failed to catch unknown table: {errs}"

bad_col = "SELECT NonExistentColumn FROM Employees"
errs = validate_schema_references(validate_read_only_sql(bad_col), set(metadata.keys()), metadata)
assert any("Unknown column" in e for e in errs), f"Failed to catch unknown column: {errs}"

bad_join = "SELECT * FROM Employees e JOIN Departments d ON e.EmployeeID = d.DepartmentID"
fk_errs = validate_relationships(validate_read_only_sql(bad_join), fks)
assert any("not present in declared foreign keys" in e for e in fk_errs), f"Failed to catch invalid FK join: {fk_errs}"
print("  [PASS] Schema validation caught hallucinated tables, hallucinated columns, and invalid FK joins.")


# 4. LangGraph Agent Workflow
print("\n" + "=" * 60)
print("4. VERIFYING LANGGRAPH WORKFLOW (app/agent.py)")
print("=" * 60)

from app.agent import build_graph, _heuristic_scope

graph = build_graph()
assert graph is not None
print("  [PASS] LangGraph StateGraph compiles successfully.")

# Test heuristic scope classifier
assert _heuristic_scope("Show me the employees") == "sql"
assert _heuristic_scope("What is the capital of France?") == "out_of_scope"
assert _heuristic_scope("Who won the match?") == "out_of_scope"
# Follow-up with history
history = [{"role": "user", "content": "Show all customers"}, {"role": "assistant", "content": "SELECT * FROM Customers"}]
assert _heuristic_scope("Only those from California", history=history) == "sql"
print("  [PASS] Scope classification & follow-up heuristics verified.")


# 5. FastAPI Integration Endpoints
print("\n" + "=" * 60)
print("5. VERIFYING FASTAPI ENDPOINTS (app/main.py)")
print("=" * 60)

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Health endpoint
r = client.get("/health")
assert r.status_code == 200
assert r.json() == {"status": "ok", "service": "sql-query-ai-agent"}
print("  [PASS] GET /health returned 200 OK.")

# Root index endpoint
r = client.get("/")
assert r.status_code == 200
assert "<!doctype html>" in r.text.lower()
assert "sql query ai agent" in r.text.lower()
print("  [PASS] GET / returned full UI HTML (200 OK).")

# API Chat: Out of scope
with patch("app.agent.get_llm") as mock_get_llm:
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"scope": "out_of_scope", "reason": "Not SQL", "task": "none"}')
    mock_get_llm.return_value = mock_llm
    r = client.post("/api/chat", json={"message": "What is the meaning of life?", "history": []})
    assert r.status_code == 200
    assert r.json()["error"] is not None
print("  [PASS] POST /api/chat correctly rejected out-of-scope question.")

# API Chat: Valid SQL flow
with patch("app.agent.get_llm") as mock_get_llm:
    call_idx = 0
    responses = [
        '{"scope": "sql", "reason": "SQL request", "task": "generate"}',
        'SELECT FirstName, LastName, Salary FROM Employees WHERE Salary > 85000',
        'Returns employees earning above $85,000.',
    ]
    def fake_invoke(msgs, **_):
        global call_idx
        res = responses[min(call_idx, len(responses) - 1)]
        call_idx += 1
        return MagicMock(content=res)
    mock_llm = MagicMock()
    mock_llm.invoke = fake_invoke
    mock_get_llm.return_value = mock_llm
    
    r = client.post("/api/chat", json={"message": "Show high earning employees", "history": []})
    assert r.status_code == 200
    data = r.json()
    assert data["sql"] == "SELECT FirstName, LastName, Salary FROM Employees WHERE Salary > 85000"
    assert data["error"] is None
    assert isinstance(data["result"], list)
    assert len(data["result"]) > 0
    assert "Salary" in data["result"][0]
print(f"  [PASS] POST /api/chat end-to-end execution returned {len(data['result'])} real database rows.")

# API Optimize
with patch("app.agent.get_llm") as mock_get_llm:
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="Use an index on DepartmentID.")
    mock_get_llm.return_value = mock_llm
    r = client.post("/api/optimize", json={"sql": "SELECT * FROM Employees WHERE DepartmentID = 1"})
    assert r.status_code == 200
    assert "analysis" in r.json()
print("  [PASS] POST /api/optimize returned optimization analysis.")

# API Debug
with patch("app.agent.get_llm") as mock_get_llm:
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="Corrected: SELECT FirstName FROM Employees")
    mock_get_llm.return_value = mock_llm
    r = client.post("/api/debug", json={"sql": "SELECT FirstNam FROM Employes"})
    assert r.status_code == 200
    data = r.json()
    assert "validation_error" in data
    assert "Employes" in data["validation_error"]
print("  [PASS] POST /api/debug identified typo and returned debug fix.")

print("\n" + "=" * 60)
print("ALL FUNCTIONALITY CHECKS PASSED SUCCESSFULLY (100% GREEN)!")
print("=" * 60)
