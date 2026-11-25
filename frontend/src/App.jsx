import { useEffect, useRef, useState } from "react";

const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

// A new session starts on every page load.
const newSessionId = () =>
  "s-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

function readableError(status, data) {
  if (status === 503) return "All AI providers are unavailable right now. Try again in a moment.";
  if (status === 422) {
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((d) => d.msg).join("; ")
      : data?.detail;
    return `Your message could not be sent: ${detail || "invalid input"}.`;
  }
  return data?.detail ? String(data.detail) : `Something went wrong (HTTP ${status}).`;
}

function Meta({ m }) {
  const cached = m.resolution_source === "LLM_CACHE";
  return (
    <dl className="meta">
      <div>
        <dt>Source</dt>
        <dd data-testid="meta-source" className={cached ? "pill cache" : "pill live"}>
          {m.resolution_source}
        </dd>
      </div>
      <div>
        <dt>Model</dt>
        <dd data-testid="meta-model">{m.model}</dd>
      </div>
      <div>
        <dt>Latency</dt>
        <dd data-testid="meta-latency">{m.latency_ms} ms</dd>
      </div>
      <div>
        <dt>Input tokens</dt>
        <dd data-testid="meta-input-tokens">{m.input_tokens}</dd>
      </div>
      <div>
        <dt>Output tokens</dt>
        <dd data-testid="meta-output-tokens">{m.output_tokens}</dd>
      </div>
      <div>
        <dt>Key</dt>
        <dd>
          {m.key_id ?? "none"}
          {m.fallback_attempts > 0 ? ` (after ${m.fallback_attempts} failed)` : ""}
        </dd>
      </div>
    </dl>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setLoading(true);
    try {
      const headers = { "Content-Type": "application/json" };
      if (simulate.trim()) headers["X-Simulate-Failure"] = simulate.trim();
      const body = { session_id: sessionId, message: text };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();

      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        /* non-JSON body */
      }
      if (!res.ok) throw new Error(readableError(res.status, data));
      setMessages((m) => [
        ...m,
        { role: "assistant", content: data.reply, metadata: data.metadata },
      ]);
    } catch (err) {
      const msg =
        err instanceof TypeError
          ? "Could not reach the server. Check your connection and try again."
          : err.message;
      setMessages((m) => [...m, { role: "error", content: msg }]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header className="top">
        <h1>Resilient chat</h1>
        <p className="session">Session {sessionId}</p>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt
          <textarea
            data-testid="system-prompt-input"
            rows={2}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Default: a helpful, concise assistant"
          />
        </label>
        <label>
          Simulate key failures (demo)
          <input
            value={simulate}
            onChange={(e) => setSimulate(e.target.value)}
            placeholder="key_1=429  or  *=429"
          />
        </label>
      </details>

      <main className="thread" aria-live="polite">
        {messages.length === 0 && (
          <p className="empty">Ask something. Ask it again in a new tab to see a cache hit.</p>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg user" data-testid="user-message">
              {m.content}
            </div>
          ) : m.role === "error" ? (
            <div key={i} className="msg error" role="alert" data-testid="error-message">
              {m.content}
            </div>
          ) : (
            <div key={i} className="msg assistant" data-testid="assistant-message">
              <div className="reply">{m.content}</div>
              <Meta m={m.metadata} />
            </div>
          )
        )}
        {loading && <div className="msg assistant typing">Thinking…</div>}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <textarea
          data-testid="chat-input"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message. Enter sends, Shift+Enter adds a line."
          maxLength={4000}
          aria-label="Message"
        />
        <button data-testid="send-button" onClick={send} disabled={loading || !input.trim()}>
          Send
        </button>
      </footer>
    </div>
  );
}
