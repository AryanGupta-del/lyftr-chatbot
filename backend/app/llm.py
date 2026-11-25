"""LiteLLM gateway: key pool, automatic failover and response caching."""
import logging
from dataclasses import dataclass

import litellm
from litellm.caching.caching import Cache

from .config import Settings

log = logging.getLogger("chatbot.llm")
litellm.suppress_debug_info = True


def enable_cache() -> None:
    """LiteLLM's built-in in-memory cache. Key = model + full message list."""
    litellm.cache = Cache(type="local")


class AllProvidersUnavailable(Exception):
    pass


class SimulatedProviderError(Exception):
    def __init__(self, status: int):
        super().__init__(f"simulated provider status {status}")
        self.status_code = status


@dataclass
class LLMResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int


def parse_simulation(header: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, ...}. Bad pairs are ignored."""
    out: dict[str, int] = {}
    for pair in (header or "").split(","):
        name, _, status = pair.strip().partition("=")
        if name and status.strip() in {"429", "500", "502", "503"}:
            out[name.strip()] = int(status)
    return out


def complete(messages: list[dict], settings: Settings, simulate: dict[str, int]) -> LLMResult:
    attempts = 0
    for index, api_key in enumerate(settings.keys, start=1):
        key_id = f"key_{index}"
        status = simulate.get(key_id, simulate.get("*"))
        try:
            if status:
                raise SimulatedProviderError(status)
            kwargs = dict(
                model=settings.model,
                messages=messages,
                api_key=api_key,
                timeout=settings.timeout,
                num_retries=0,  # we do our own failover, one attempt per key
                caching=True,
            )
            if settings.mock_llm:  # offline demo/tests: no network call
                kwargs["mock_response"] = "Mock reply to: " + messages[-1]["content"][:200]
            resp = litellm.completion(**kwargs)

            text = resp.choices[0].message.content
            if not text or not text.strip():
                raise ValueError("empty reply")
            hit = bool(getattr(resp, "_hidden_params", {}).get("cache_hit"))
            usage = resp.usage
            return LLMResult(
                reply=text,
                model=settings.model,
                input_tokens=int(usage.prompt_tokens),
                output_tokens=int(usage.completion_tokens),
                cache_hit=hit,
                key_id=None if hit else key_id,
                fallback_attempts=0 if hit else attempts,
            )
        except Exception as exc:  # 429, quota, 5xx, timeouts, bad key... -> next key
            attempts += 1
            # Log only the error type/status, never the key or provider message.
            log.warning("%s failed: %s (status=%s)", key_id, type(exc).__name__,
                        getattr(exc, "status_code", None))
    raise AllProvidersUnavailable()
