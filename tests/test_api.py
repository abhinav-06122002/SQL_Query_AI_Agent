"""Integration tests for the FastAPI endpoints.

These tests hit the actual endpoints via FastAPI's test client without
requiring a real OpenAI key.  LLM calls are mocked so tests are
deterministic and fast, but the real guardrails and SQLite DB are used.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


# ── /health ─────────────────────────────────────────────────────────────────

def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "service" in data


# ── /api/chat ────────────────────────────────────────────────────────────────

def _make_llm_mock(intent_json: str, sql_text: str = "", explain_text: str = "Test explanation."):
    call_count = 0
    responses = [intent_json, sql_text, explain_text]

    def fake_invoke(messages, **_):
        nonlocal call_count
        resp = responses[min(call_count, len(responses) - 1)]
        call_count += 1
        m = MagicMock()
        m.content = resp
        return m

    mock_llm = MagicMock()
    mock_llm.invoke = fake_invoke
    return mock_llm


def test_chat_out_of_scope_returns_error():
    with patch("app.agent.get_llm") as mock_get_llm:
        mock_get_llm.return_value = _make_llm_mock(
            '{"scope": "out_of_scope", "reason": "Not SQL related", "task": "none"}'
        )
        r = client.post("/api/chat", json={"message": "Who won the FIFA World Cup?", "history": []})
    assert r.status_code == 200
    data = r.json()
    assert data["error"] is not None
    assert data["sql"] is None


def test_chat_generates_sql_for_customers():
    mock_sql = "SELECT CustomerID, CustomerName, State FROM Customers"
    with patch("app.agent.get_llm") as mock_get_llm:
        mock_get_llm.return_value = _make_llm_mock(
            '{"scope": "sql", "reason": "Asks for customer data", "task": "generate"}',
            sql_text=mock_sql,
            explain_text="Retrieves all customer records."
        )
        r = client.post("/api/chat", json={"message": "Show all customers", "history": []})
    assert r.status_code == 200
    data = r.json()
    assert data["error"] is None
    assert data["sql"] is not None
    assert "SELECT" in data["sql"].upper()
    assert isinstance(data["result"], list)
    assert len(data["result"]) >= 4


def test_chat_blocks_destructive_sql():
    with patch("app.agent.get_llm") as mock_get_llm:
        mock_get_llm.return_value = _make_llm_mock(
            '{"scope": "sql", "reason": "Database query", "task": "generate"}',
            sql_text="DELETE FROM Employees",
        )
        r = client.post("/api/chat", json={"message": "Delete all employees", "history": []})
    assert r.status_code == 200
    data = r.json()
    assert data["sql"] is None or (data.get("validation_errors") and len(data["validation_errors"]) > 0)


def test_chat_conversation_history_accepted():
    history = [
        {"role": "user", "content": "Show all customers"},
        {"role": "assistant", "content": "SELECT * FROM Customers"}
    ]
    with patch("app.agent.get_llm") as mock_get_llm:
        mock_get_llm.return_value = _make_llm_mock(
            '{"scope": "sql", "reason": "Follow-up filter", "task": "generate"}',
            sql_text="SELECT CustomerID, CustomerName FROM Customers WHERE State = 'California'",
            explain_text="Filters customers by state."
        )
        r = client.post("/api/chat", json={"message": "Only from California", "history": history})
    assert r.status_code == 200


def test_chat_empty_message_rejected():
    r = client.post("/api/chat", json={"message": "", "history": []})
    assert r.status_code == 422


# ── /api/optimize ────────────────────────────────────────────────────────────

def test_optimize_valid_sql():
    sql = "SELECT * FROM Employees WHERE Salary > 80000"
    with patch("app.agent.get_llm") as mock_get_llm:
        mock = MagicMock()
        mock.invoke.return_value.content = "Consider an index on Salary."
        mock_get_llm.return_value = mock
        r = client.post("/api/optimize", json={"sql": sql})
    assert r.status_code == 200
    assert "analysis" in r.json()


def test_optimize_destructive_sql_rejected():
    r = client.post("/api/optimize", json={"sql": "DELETE FROM Employees"})
    assert r.status_code == 400


# ── /api/debug ───────────────────────────────────────────────────────────────

def test_debug_invalid_sql():
    sql = "SELECT FirstNam FROM Employes"
    with patch("app.agent.get_llm") as mock_get_llm:
        mock = MagicMock()
        mock.invoke.return_value.content = "Table 'Employes' should be 'Employees'."
        mock_get_llm.return_value = mock
        r = client.post("/api/debug", json={"sql": sql})
    assert r.status_code == 200
    data = r.json()
    assert "analysis" in data
    assert "validation_error" in data
