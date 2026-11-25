import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.update(
    LLM_API_KEYS="test-key-1,test-key-2",
    MOCK_LLM="true",
    ENABLE_FAILURE_SIMULATION="true",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def uid() -> str:
    return uuid.uuid4().hex[:8]


def chat(client, sid, msg, headers=None, **extra):
    return client.post("/chat", json={"session_id": sid, "message": msg, **extra}, headers=headers or {})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_live_then_cache_in_new_session(client):
    msg = f"capital of France {uid()}?"
    first = chat(client, f"s-{uid()}", msg).json()
    second = chat(client, f"s-{uid()}", msg).json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert first["metadata"]["key_id"] == "key_1"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["reply"] == first["reply"]
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]


def test_different_message_is_not_cached(client):
    a = chat(client, f"s-{uid()}", f"hello {uid()}").json()
    b = chat(client, f"s-{uid()}", f"goodbye {uid()}").json()
    assert b["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert a["reply"] != b["reply"]


@pytest.mark.parametrize("header,key", [("key_1=429", "key_2"), ("key_1=500", "key_2")])
def test_failover(client, header, key):
    r = chat(client, f"s-{uid()}", f"fail {uid()}", {"X-Simulate-Failure": header})
    assert r.status_code == 200
    meta = r.json()["metadata"]
    assert meta["key_id"] == key and meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"


def test_all_keys_fail_returns_503_and_stores_nothing(client):
    sid = f"s-{uid()}"
    r = chat(client, sid, f"x {uid()}", {"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_simulation_header_ignored_when_disabled(client, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    r = chat(client, f"s-{uid()}", f"x {uid()}", {"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200 and r.json()["metadata"]["fallback_attempts"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s", "message": ""},
        {"session_id": "s", "message": "   "},
        {"session_id": "", "message": "hi"},
        {"message": "hi"},
        {"session_id": "s"},
        {"session_id": "s", "message": "a" * 4001},
        {"session_id": "s" * 129, "message": "hi"},
    ],
)
def test_validation_errors(client, body):
    r = client.post("/chat", json=body)
    assert r.status_code == 422 and "detail" in r.json()


def test_malformed_json(client):
    r = client.post("/chat", content="{nope", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


def test_multi_turn_history_and_isolation(client, monkeypatch):
    import app.llm as llm

    seen = []
    original = llm.litellm.completion

    def spy(**kwargs):
        seen.append(kwargs["messages"])
        return original(**kwargs)

    monkeypatch.setattr(llm.litellm, "completion", spy)
    s1, s2 = f"s-{uid()}", f"s-{uid()}"
    chat(client, s1, f"my name is Arya {uid()}", system_prompt="Be a pirate.")
    chat(client, s1, f"what is my name? {uid()}")
    chat(client, s2, f"other session {uid()}")

    second_call = seen[1]
    assert second_call[0] == {"role": "system", "content": "Be a pirate."}
    assert [m["role"] for m in second_call] == ["system", "user", "assistant", "user"]
    assert len(seen[2]) == 2  # new session: only the default system prompt + message

    h1 = client.get(f"/sessions/{s1}/messages").json()
    assert [m["role"] for m in h1["messages"]] == ["user", "assistant", "user", "assistant"]
    assert "metadata" in h1["messages"][1] and "metadata" not in h1["messages"][0]
    assert len(client.get(f"/sessions/{s2}/messages").json()["messages"]) == 2


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_tokens_are_per_request(client):
    a = chat(client, f"s-{uid()}", f"aaaa {uid()}").json()["metadata"]
    b = chat(client, f"s-{uid()}", f"bbbb {uid()}").json()["metadata"]
    assert a["input_tokens"] > 0 and a["output_tokens"] > 0
    assert a["input_tokens"] == b["input_tokens"]  # equal-length first messages
