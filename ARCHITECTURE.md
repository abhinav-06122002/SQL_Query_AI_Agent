# Architecture Decision Record

## Goal
Build a task-oriented SQL agent that safely converts natural language into SQL for a known relational schema.

## Components
- **FastAPI**: thin API layer.
- **LangGraph**: stateful orchestration and conditional retry flow.
- **OpenAI chat model**: intent classification, SQL generation and explanation.
- **SQLGlot**: deterministic parsing and safety checks.
- **SQLite**: self-contained relational database.
- **Vanilla HTML/CSS/JS**: low-dependency responsive frontend.

## State
`user_message`, `history`, `schema`, `intent`, `sql`, `validation_errors`, `attempts`, `explanation`, `result`, and `error` are carried through the graph.

## Failure handling
- LLM unavailable → explicit API error.
- malformed classifier output → conservative fallback classifier.
- invalid SQL → validation error and regeneration.
- repeated invalid SQL → refusal/error instead of execution.
- execution failure → surfaced without exposing a write path.

## Security model
The application assumes LLM output is untrusted. SQLGlot validation and live schema checks happen before execution. Database credentials are not stored in source code.
