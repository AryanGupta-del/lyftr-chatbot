# Resilient LLM chatbot (key failover + response caching)

React + FastAPI + SQLite, with LiteLLM handling the model calls.

## 1. Live URLs

- Frontend: `https://lyftr-chatbot-beta.vercel.app`  <!-- fill in -->
- Backend: `https://lyftr-chatbot-lsjm.onrender.com`  <!-- fill in -->
- Health check: `<backend>/health`
- Demo video: `<link>`

## 2. Run locally

### With Docker

```bash
cp .env.example .env     # then put at least 2 real keys in LLM_API_KEYS
make up                  # backend :8000, frontend :3000
make down
```

### Without Docker

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-one,key-two ENABLE_FAILURE_SIMULATION=true \
       DATABASE_URL=sqlite:///./app.db ALLOWED_ORIGINS=http://localhost:3000
uvicorn app.main:app --port 8000
pytest -q                # tests run offline (MOCK_LLM=true is set by the tests)

# frontend (new terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

Set `MOCK_LLM=true` to run everything without real API calls.

### Environment variables

| Variable | Meaning |
| --- | --- |
| `LLM_API_KEYS` | Comma-separated key pool (>= 2). Ids are `key_1`, `key_2`, ... |
| `LLM_MODEL` | LiteLLM model name (default `gemini/gemini-2.0-flash`) |
| `DATABASE_URL` | e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables `X-Simulate-Failure` |
| `MOCK_LLM` | `true` skips real provider calls |
| `VITE_API_URL` | Backend URL, baked into the frontend at build time |

### Deploying

- Backend (Render, Docker): root directory `backend`, set the env vars above, `ALLOWED_ORIGINS` = your frontend URL, `ENABLE_FAILURE_SIMULATION=true`. For history to survive restarts, attach a persistent disk mounted at `/data` (Render paid plans or Fly.io volumes). Free tiers without a disk lose the SQLite file on redeploy.
- Frontend (Vercel/Netlify): root directory `frontend`, build `npm run build`, output `dist`, env `VITE_API_URL` = backend URL.

## 3. Design decisions

**Key fallback.** `app/llm.py` loops over the key pool in order. Each attempt is one `litellm.completion` call with `num_retries=0`. Any failure (429, quota, 5xx, timeout, bad key) moves to the next key, and `fallback_attempts` counts the failures. If every key fails the API returns `503 {"detail": "all providers unavailable"}` and nothing is stored. Keys are never logged or returned, only ids like `key_1`.

**Simulated failures.** With `ENABLE_FAILURE_SIMULATION=true`, the `X-Simulate-Failure` header makes chosen keys fail inside the loop for that request only. The remaining keys make real calls.

**Caching.** LiteLLM's built-in in-memory cache (`litellm.cache = Cache(type="local")`) is enabled for every call. LiteLLM builds the cache key from the model and the full message list (system prompt + history + new message), not from the API key, so a cached answer is shared across keys and sessions. A hit is detected with `_hidden_params["cache_hit"]` and reported as `LLM_CACHE` with `key_id: null`. The cache is lost on restart, which is fine because it only saves cost and time.

**SQLite session storage.** `messages` follows the required schema; a small `sessions` table stores the per-session system prompt. The client sends only the new message; the server rebuilds `[system] + history + [new]` from SQLite each time. The user message and the reply are saved together after a successful answer, so failed requests (422/503) leave no trace. The schema is created on startup, and the file lives on the `/data` volume.

**Tokens and latency.** Tokens come from the provider usage of that one request (a cache hit returns the usage stored with the cached response). Latency is measured on the server with `perf_counter`.

## 4. Verification

```bash
B=http://localhost:8000
# live call with per-message tokens
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"a","message":"What is the capital of France?"}'
# cache hit: same first message, NEW session -> LLM_CACHE, key_id null, lower latency
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"b","message":"What is the capital of France?"}'
# failover: key_1 fails, key_2 answers (fallback_attempts 1)
curl -s $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"c","message":"Tell me a joke"}'
# every key fails: 503, nothing stored
curl -s -i $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"d","message":"Hi"}'
# history (404 for "d")
curl -s $B/sessions/a/messages
```

In the UI, open "Session settings" and type `key_1=429` in the simulate field to demo failover. Each reply shows source, model, latency and tokens.

## 5. Setup Used

- Editor: <your editor>
- AI tools: Claude (generated the first version; reviewed and tested by me)

## 6. Bonus features

- Per-session system prompt field in the UI.
- UI field for `X-Simulate-Failure`, so failover can be demoed without curl.
- Key id and failed-attempt count shown next to each reply.
- `MOCK_LLM` mode for offline development and fast tests.
