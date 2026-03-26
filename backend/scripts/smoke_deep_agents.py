"""Smoke-test LangChain Gemini, Moonshot Kimi, and Deep Agent invokes using real API keys from `.env`.

Run from the `backend` directory (so `app.config` loads `backend/.env`):

  cd backend
  ..\\venv\\Scripts\\python.exe scripts\\smoke_deep_agents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _message_content_to_str(message) -> str:
    """Gemini sometimes returns list-shaped message content."""
    c = getattr(message, "content", message)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(c)


def main() -> int:
    if Path.cwd().resolve() != BACKEND_ROOT:
        print(
            "WARNING: cwd is not `backend/`. Change directory to backend so `.env` loads correctly.\n"
            f"  Expected: {BACKEND_ROOT}\n"
            f"  Current:  {Path.cwd().resolve()}"
        )

    sys.path.insert(0, str(BACKEND_ROOT))

    from langchain_core.messages import HumanMessage

    from app.config import settings
    from app.services.model_profiles import _kimi_openai_credentials, build_chat_model
    from app.services.portfolio_deep_agent import (
        create_portfolio_agent,
        history_to_lc_messages,
        invoke_portfolio_agent,
    )
    from app.services.prompt_assembly import EntityBrief

    failures: list[tuple[str, str]] = []
    print("CHAT_USE_DEEP_AGENT =", settings.CHAT_USE_DEEP_AGENT)
    try:
        _, kimi_base = _kimi_openai_credentials()
        print("Kimi OpenAI base_url (resolved) =", kimi_base)
    except ValueError:
        print("Kimi OpenAI: no API key (MOONSHOT_API_KEY / KIMI_CODE_API_KEY)")
    print("------")

    # 1) Gemini via LangChain only (no google_search binding for simpler smoke)
    try:
        m = build_chat_model("gemini_google", enable_google_search=False)
        r = m.invoke([HumanMessage(content='Reply with exactly: gemini-ok')])
        c = _message_content_to_str(r)
        print("[OK] LangChain Gemini:", (c[:180] + "…") if len(c) > 180 else c)
    except Exception as e:
        failures.append(("LangChain Gemini", str(e)))
        print("[FAIL] LangChain Gemini:", e)

    # 2) Kimi via LangChain
    try:
        m2 = build_chat_model("kimi_moonshot")
        r2 = m2.invoke([HumanMessage(content='Reply with exactly: kimi-ok')])
        c2 = _message_content_to_str(r2)
        print("[OK] LangChain Kimi:", (c2[:180] + "…") if len(c2) > 180 else c2)
    except Exception as e:
        failures.append(("LangChain Kimi", str(e)))
        print("[FAIL] LangChain Kimi:", e)

    brief = EntityBrief(
        entity_id="smoke-entity",
        name="SmokeCo",
        website="https://example.test",
    )
    extras = (
        "No need to call portfolio tools for this smoke test. "
        "Answer the user in one short phrase."
    )

    # 3) Deep Agent + Gemini profile
    try:
        agent = create_portfolio_agent(
            entity=brief,
            system_prompt_extras=extras,
            session_id="smoke-sess",
            session_artifact_ids=[],
            model_profile_id="gemini_google",
            run_id="smoke-run-gemini",
        )
        text, _ = invoke_portfolio_agent(
            agent, history_to_lc_messages([], 'Reply with exactly: deep-gemini-ok')
        )
        print("[OK] Deep Agent (gemini_google):", (text[:220] + "…") if len(text) > 220 else text)
    except Exception as e:
        failures.append(("Deep Agent gemini_google", str(e)))
        print("[FAIL] Deep Agent (gemini_google):", e)

    # 4) Deep Agent + Kimi profile
    try:
        agent_k = create_portfolio_agent(
            entity=brief,
            system_prompt_extras=extras,
            session_id="smoke-sess",
            session_artifact_ids=[],
            model_profile_id="kimi_moonshot",
            run_id="smoke-run-kimi",
        )
        text_k, _ = invoke_portfolio_agent(
            agent_k, history_to_lc_messages([], 'Reply with exactly: deep-kimi-ok')
        )
        print("[OK] Deep Agent (kimi_moonshot):", (text_k[:220] + "…") if len(text_k) > 220 else text_k)
    except Exception as e:
        failures.append(("Deep Agent kimi_moonshot", str(e)))
        print("[FAIL] Deep Agent (kimi_moonshot):", e)

    print("------")
    if failures:
        print(f"Completed with {len(failures)} failure(s).")
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
