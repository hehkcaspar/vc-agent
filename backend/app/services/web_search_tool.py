"""Web search tool — Gemini Google Search grounding wrapped as a function tool.

Usable in any LangChain ``create_agent`` alongside the workspace tools.
Implementation leverages ``direct_llm.generate_one_shot(enable_google_search=True)``
so the agent gets Gemini's native grounding — including citations rendered
inline in the returned text — without leaving the function-tool model.

This is the tool used by the Initial Screening preset to fill research gaps
in section JSONs (market structure, competitor backgrounds, co-investor
track records, founder priors, etc.). Not bound by default — presets opt in
via the ``include_web_search=True`` flag on ``build_agent_core``.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


_WEB_SEARCH_SYSTEM = """\
You are a research subagent embedded in a venture-capital diligence workflow.

For the user's query, perform one Google Search pass and return a tight
factual summary. Your output must:

- Lead with the 2-5 most important *verifiable* facts as bullet points.
- Attach a source to every fact: a URL when possible, otherwise a named
  publication / company / person.
- Flag uncertainty explicitly — write "source conflict" or "source unclear"
  rather than speculating.
- Keep total length under 800 words. No headers, no preamble.
- If the query is underspecified, answer what you can and note the gap.

Do NOT editorialize. Do NOT write recommendations. You are collecting
evidence for a downstream analyst, not writing the memo.
"""


def build_web_search_tool(
    on_status: Optional[Callable[[str], None]] = None,
):
    """Return a LangChain ``@tool`` that wraps Gemini's Google Search grounding.

    Meant to be appended to the agent's tool list. Runs synchronously in the
    agent's thread — the Interactions / one-shot call is blocking, but the
    agent_harness already invokes from a background task.
    """
    from langchain_core.tools import tool

    def _notify(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    @tool
    def web_search(query: str) -> str:
        """Search the web for factual evidence (Google-grounded).

        Use this when the workspace doesn't contain the answer — typical
        cases: verifying a founder's prior company, checking a competitor's
        round size, looking up a co-investor's track record.

        Budget: prefer 4-6 targeted queries per run, not 10+. Narrow
        queries ('Michael Schrader Vaxess CEO exit') beat broad ones
        ('brain-computer interface market'). Batch related questions into
        one query when possible.

        Args:
            query: A specific, factual query. One query per call.

        Returns:
            A JSON blob: {"results": "<grounded summary with citations>",
            "query": "<echoed query>"} — or an {"error": "..."} on failure.
        """
        _notify(f"Web search: {query[:80]}...")
        try:
            from app.config import settings
            from app.services.direct_llm import generate_one_shot

            # Use the faster flash model for research-assistant calls —
            # the agent itself runs on Pro for reasoning, but each web
            # search is a bounded one-shot summarisation task, and flash
            # is ~3x quicker (<20s vs ~60s).
            flash_model = (
                settings.GEMINI_METADATA_EXTRACTION_MODEL
                or "gemini-3-flash-preview"
            )
            result_text = generate_one_shot(
                system_instruction=_WEB_SEARCH_SYSTEM,
                history=[],
                user_message_text=query,
                enable_google_search=True,
                model=flash_model,
            )
            return json.dumps(
                {"query": query, "results": result_text or ""},
                ensure_ascii=False,
            )
        except Exception as exc:  # broad catch — agent tool must never raise
            logger.warning("web_search failed: %s", exc, exc_info=True)
            return json.dumps({"error": f"web_search failed: {exc}"})

    return web_search
