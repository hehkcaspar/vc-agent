# Tracing (LangSmith)

This document is the single source of truth for tracing setup in this project.

## Scope

Tracing is configured for the LangChain-based chat path (Deep Agent harness and LangChain model/tool calls).

- Traced: Deep Agent flows (`use_deep_agent=true` or server default `CHAT_USE_DEEP_AGENT=true`)
- Not automatically traced: non-LangChain direct SDK calls unless explicitly instrumented

## Required Environment Variables

Configure these in `backend/.env`:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-langsmith-api-key>
LANGSMITH_PROJECT=vc-portfolio-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Notes:

- `LANGSMITH_PROJECT` controls which project receives runs.
- `LANGSMITH_ENDPOINT` should remain the default cloud endpoint unless you use a custom deployment.

## Runtime Wiring

At backend startup, tracing settings are normalized into process environment variables in `backend/app/main.py` (`_guard_langsmith_tracing()`), so LangSmith/LangChain SDKs consistently read them.

## Verify Setup

1. Start backend:

```powershell
cd backend
..\venv\Scripts\python.exe run.py
```

2. Trigger a Deep Agent message from UI (Agent toggle on), or run:

```powershell
cd backend
..\venv\Scripts\python.exe scripts\smoke_deep_agents.py
```

3. Open LangSmith and check project `vc-portfolio-agent` for new runs.

## Troubleshooting

- No traces visible:
  - Confirm backend was restarted after `.env` changes.
  - Confirm `LANGSMITH_TRACING=true`.
  - Confirm `LANGSMITH_API_KEY` is valid.
  - Confirm you are viewing `LANGSMITH_PROJECT`.
- Traces appear in a different project:
  - Check `LANGSMITH_PROJECT` value for typos.
- Intermittent missing traces during development:
  - Uvicorn reload/restart interruptions can cancel in-flight requests.

## Security

- Never commit real API keys.
- Rotate keys immediately if exposed.
