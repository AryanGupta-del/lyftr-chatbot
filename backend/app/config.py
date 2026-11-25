"""All configuration comes from environment variables."""
import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass
class Settings:
    keys: list[str]
    database_url: str
    allowed_origins: list[str]
    simulation_enabled: bool
    model: str
    mock_llm: bool
    timeout: float


def get_settings() -> Settings:
    # Read on every call so tests (and restarts) always see the current env.
    return Settings(
        keys=_csv(os.getenv("LLM_API_KEYS", "")),
        database_url=os.getenv("DATABASE_URL", "sqlite:////data/app.db"),
        allowed_origins=_csv(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
        simulation_enabled=_flag("ENABLE_FAILURE_SIMULATION"),
        model=os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash"),
        mock_llm=_flag("MOCK_LLM"),
        timeout=float(os.getenv("LLM_TIMEOUT", "30")),
    )
