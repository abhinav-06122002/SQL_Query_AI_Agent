from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .db import init_db
from .agent import run_agent, optimize_sql, debug_sql

BASE = Path(__file__).resolve().parent.parent

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="SQL Query AI Agent",
    version="2.0.0",
    description="Task-oriented, guarded SQL assistant",
    lifespan=lifespan,
)

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)

class SQLRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=10000)

@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "ok", "service": "sql-query-ai-agent"}

@app.post("/api/chat")
def chat(request: ChatRequest):
    try:
        return run_agent(request.message, request.history)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.post("/api/optimize")
def optimize(request: SQLRequest):
    try:
        return optimize_sql(request.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/debug")
def debug(request: SQLRequest):
    try:
        return debug_sql(request.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
