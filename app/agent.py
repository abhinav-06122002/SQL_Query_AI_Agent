from typing import TypedDict, Any
import json
import re
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from .config import settings
from .db import get_schema, execute_read_only, get_schema_metadata, get_foreign_keys
from .guardrails import validate_read_only_sql, validate_schema_references, validate_relationships, SQLValidationError
from .prompts import SYSTEM_PROMPT, INTENT_PROMPT, SQL_PROMPT, EXPLAIN_PROMPT, OPTIMIZE_PROMPT, DEBUG_PROMPT

class AgentState(TypedDict, total=False):
    user_message: str
    history: list[dict[str, str]]
    schema: str
    intent: str
    scope_reason: str
    sql: str
    explanation: str
    validation_errors: list[str]
    result: list[dict[str, Any]]
    error: str
    attempts: int
    mode: str
    optimization: str
    task: str


def get_llm():
    if not settings.openai_api_key or settings.openai_api_key.strip() in {"your_api_key_here", "sk-...", ""}:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add your valid API key to .env before using the AI agent.")
    
    key = settings.openai_api_key.strip()
    base_url = settings.openai_base_url
    model = settings.openai_model

    # Auto-detect provider if base_url is not explicitly configured
    if not base_url:
        if key.startswith("AQ.") or key.startswith("AIzaSy"):
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            if model in {"gpt-4o-mini", "gpt-5.6", "gpt-4o"}:
                model = "gemini-3.8-flash"
        elif key.startswith("gsk_"):
            base_url = "https://api.groq.com/openai/v1"
            if model in {"gpt-4o-mini", "gpt-5.6", "gpt-4o", "llama-3.3-70b-versatile"}:
                model = "qwen/qwen3.8-27b"

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "api_key": key,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _invoke(prompt: str) -> str:
    msg = get_llm().invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
    return msg.content.strip()


def _heuristic_scope(message: str, history: list[dict[str, str]] | None = None) -> str:
    text = message.lower().strip()
    sql_terms = ("sql", "query", "database", "table", "column", "customer", "employee", "order", "department", "join", "select", "rows", "records")
    if any(term in text for term in sql_terms):
        return "sql"
    if history and len(history) > 0:
        refinement_terms = ("only", "where", "filter", "sort", "order", "with", "from", "and", "or", "desc", "asc", "limit", "top", "more", "now", "also", "exclude", "include")
        if any(term in text.split() for term in refinement_terms):
            return "sql"
    return "out_of_scope"


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def intent_node(state: AgentState):
    history_text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in state.get("history", [])[-6:])
    try:
        raw = _invoke(INTENT_PROMPT.format(
            schema=state["schema"],
            history=history_text or "(none)",
            user_message=state["user_message"]
        ))
        data = _extract_json(raw)
        scope = data.get("scope", "clarification")
        if scope not in {"sql", "out_of_scope", "clarification"}:
            scope = "clarification"
        return {"intent": scope, "scope_reason": data.get("reason", ""), "task": data.get("task", "generate")}
    except Exception:
        return {"intent": _heuristic_scope(state["user_message"], state.get("history")), "scope_reason": "Fallback scope classifier", "task": "generate"}



def reject_node(state: AgentState):
    return {"error": "I'm designed to assist only with SQL and database-related tasks using the provided schema."}


def clarify_node(state: AgentState):
    return {"error": "I need a little more information to generate a safe and accurate SQL query. Please clarify your request."}


def sql_generation_node(state: AgentState):
    history_text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in state.get("history", [])[-8:])
    try:
        raw = _invoke(SQL_PROMPT.format(schema=state["schema"], history=history_text or "(none)", user_message=state["user_message"]))
    except Exception as exc:
        return {"error": str(exc), "attempts": state.get("attempts", 0) + 1}
    if raw.strip().upper() == "CLARIFY":
        return {"error": "I need a little more information to generate a safe and accurate SQL query. Please clarify your request.", "attempts": state.get("attempts", 0) + 1}
    return {"sql": raw.strip(), "attempts": state.get("attempts", 0) + 1, "error": None}


def validation_node(state: AgentState):
    if not state.get("sql"):
        return {"validation_errors": ["No SQL query was generated."]}
    errors = []
    normalized = state["sql"]
    try:
        normalized = validate_read_only_sql(state["sql"])
        metadata = get_schema_metadata()
        errors.extend(validate_schema_references(normalized, set(metadata.keys()), metadata))
        # Also validate that JOIN conditions match known FK relationships
        fk_errors = validate_relationships(normalized, get_foreign_keys())
        errors.extend(fk_errors)
    except SQLValidationError as exc:
        errors.append(str(exc))
    return {"sql": normalized, "validation_errors": errors}


def validation_router(state: AgentState):
    if not state.get("validation_errors"):
        return "valid"
    if state.get("attempts", 0) < 3:
        return "retry"
    return "failed"


def explanation_node(state: AgentState):
    try:
        explanation = _invoke(EXPLAIN_PROMPT.format(sql=state["sql"]))
    except Exception:
        explanation = "This query retrieves the requested information from the provided database."
    return {"explanation": explanation}


def execute_node(state: AgentState):
    try:
        return {"result": execute_read_only(state["sql"]), "error": None}
    except Exception as exc:
        return {"result": [], "error": f"The query passed validation but could not be executed: {exc}"}


def _extract_sql_from_text(text: str) -> str:
    fenced = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.I | re.S)
    if fenced:
        return fenced[0].strip()
    match = re.search(r"\b(WITH|SELECT)\b[\s\S]*", text, re.I)
    return match.group(0).strip() if match else text.strip()

def task_router(state: AgentState):
    if state.get("intent") != "sql":
        return state.get("intent", "clarification")
    task = state.get("task", "generate")
    # "none" can come back from INTENT_PROMPT when scope is clarification/out_of_scope
    if task not in {"generate", "optimize", "debug"}:
        return "generate"
    return task

def optimize_node(state: AgentState):
    raw = state.get("user_message", "")
    sql = _extract_sql_from_text(raw)
    try:
        normalized = validate_read_only_sql(sql)
        metadata = get_schema_metadata()
        errors = validate_schema_references(normalized, set(metadata), metadata)
        if errors:
            return {"error": "; ".join(errors)}
        answer = _invoke(OPTIMIZE_PROMPT.format(schema=state["schema"], sql=normalized))
        return {"explanation": answer, "sql": normalized}
    except Exception as exc:
        return {"error": f"I could not safely optimize that SQL: {exc}"}

def debug_node(state: AgentState):
    raw = state.get("user_message", "")
    try:
        answer = _invoke(DEBUG_PROMPT.format(schema=state["schema"], sql=raw))
        return {"explanation": answer}
    except Exception as exc:
        return {"error": f"I could not debug that SQL: {exc}"}

def build_graph():
    g = StateGraph(AgentState)
    for name, node in [("detect_intent", intent_node), ("reject", reject_node), ("clarify", clarify_node), ("generate_sql", sql_generation_node), ("validate", validation_node), ("explain", explanation_node), ("execute", execute_node)]:
        g.add_node(name, node)
    g.set_entry_point("detect_intent")
    g.add_node("optimize", optimize_node)
    g.add_node("debug", debug_node)
    g.add_conditional_edges("detect_intent", task_router, {"out_of_scope": "reject", "clarification": "clarify", "generate": "generate_sql", "optimize": "optimize", "debug": "debug"})
    g.add_edge("generate_sql", "validate")
    g.add_edge("optimize", END)
    g.add_edge("debug", END)
    g.add_conditional_edges("validate", validation_router, {"valid": "explain", "retry": "generate_sql", "failed": "reject"})
    g.add_edge("explain", "execute")
    g.add_edge("execute", END)
    g.add_edge("reject", END)
    g.add_edge("clarify", END)
    return g.compile()


graph = build_graph()


def run_agent(user_message: str, history: list[dict[str, str]] | None = None):
    state: AgentState = {"user_message": user_message, "history": history or [], "schema": get_schema(), "attempts": 0}
    result = graph.invoke(state)
    return {k: result.get(k) for k in ["sql", "explanation", "result", "error", "validation_errors"]}


def optimize_sql(sql: str) -> dict[str, Any]:
    normalized = validate_read_only_sql(sql)
    metadata = get_schema_metadata()
    errors = validate_schema_references(normalized, set(metadata), metadata)
    if errors:
        raise SQLValidationError("; ".join(errors))
    return {"analysis": _invoke(OPTIMIZE_PROMPT.format(schema=get_schema(), sql=normalized))}


def debug_sql(sql: str) -> dict[str, Any]:
    # First validate syntax/read-only status. A schema error is useful context for the model.
    try:
        normalized = validate_read_only_sql(sql)
    except SQLValidationError as exc:
        normalized = sql
        validation_error = str(exc)
    else:
        metadata = get_schema_metadata()
        errs = validate_schema_references(normalized, set(metadata), metadata)
        validation_error = "; ".join(errs) if errs else ""
    prompt = DEBUG_PROMPT.format(schema=get_schema(), sql=normalized)
    answer = _invoke(prompt)
    return {"validation_error": validation_error, "analysis": answer}
