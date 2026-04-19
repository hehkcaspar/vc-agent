"""Portfolio-side tracking services — news_web and future per-entity sources.

Mirrors the architecture of ``services/academic`` but keyed by
``entity_id``. Fact-store lives at ``data/entities/{entity_id}/``
alongside the workspace directory, not inside it — tracking ledgers
are agent-managed, not user-editable artifacts.
"""
