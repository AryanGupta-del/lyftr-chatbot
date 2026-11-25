import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_SYSTEM_PROMPT, get_settings
from .db import Database
from .llm import AllProvidersUnavailable, complete, enable_cache, parse_simulation

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chatbot")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str
    system_prompt: str | None = None

    @field_validator("message")
    @classmethod
    def check_message(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 4000:
            raise ValueError("message must be 1-4000 characters after trimming")
        return v

    @field_validator("system_prompt")
    @classmethod
    def check_system_prompt(cls, v):
        if v is None:
            return None
        v = v.strip()
        if len(v) > 4000:
            raise ValueError("system_prompt must be at most 4000 characters")
        return v or None


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(get_settings().database_url)  # creates schema
        enable_cache()
        log.info("started: %d keys, model=%s", len(get_settings().keys), get_settings().model)
        yield

    app = FastAPI(title="LLM chatbot with key failover and caching", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health")
    def health():
        s = get_settings()
        try:
            app.state.db.ping()
        except Exception:
            return JSONResponse({"status": "error", "detail": "database unreachable"}, 503)
        if not s.keys:
            return JSONResponse({"status": "error", "detail": "no keys configured"}, 503)
        return {"status": "ok", "keys_configured": len(s.keys), "cache_enabled": True}

    @app.post("/chat")
    def chat(req: ChatRequest, x_simulate_failure: str | None = Header(default=None)):
        started = time.perf_counter()
        s = get_settings()
        db: Database = app.state.db

        system_prompt = req.system_prompt or db.get_system_prompt(req.session_id) or DEFAULT_SYSTEM_PROMPT
        messages = (
            [{"role": "system", "content": system_prompt}]
            + db.get_history(req.session_id)
            + [{"role": "user", "content": req.message}]
        )
        simulate = parse_simulation(x_simulate_failure) if s.simulation_enabled else {}

        try:
            result = complete(messages, s, simulate)
        except AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = {
            "resolution_source": "LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
            "model": result.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        # Only successful exchanges are stored.
        db.save_exchange(req.session_id, req.system_prompt, req.message, result.reply, metadata)
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def get_messages(session_id: str):
        db: Database = app.state.db
        if not db.session_exists(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": db.get_messages(session_id)}

    return app


app = create_app()
