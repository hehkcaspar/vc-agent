"""Entity names and heuristics used by backend API tests and local cleanup.

Keep in sync with ``client.post("/entities", json={"name": ...})`` in ``backend/tests/``.
The delete helper ``scripts/delete_entities_and_chats.py --test-entities`` uses this module.
"""

from __future__ import annotations

# Exact ``name`` values from integration tests (see grep in tests/).
TEST_ENTITY_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "Meta Co",
        "Arti Meta Co",
        "Reject Co",
        "Preprocess Co",
        "Override Co",
        "Override Harness Co",
        "Deep Co",
        "Acme Corp",
        "Beta Inc",
        "Gamma LLC",
        "Epsilon Co",
        "Delta LLC",
    }
)

# ``test_chat_e2e_llm`` uses f"E2E LLM {uuid.hex[:8]}"
TEST_ENTITY_NAME_PREFIXES: tuple[str, ...] = ("E2E LLM ",)


def is_test_entity(name: str, website: str | None) -> bool:
    """Return True if this row matches known test-harness patterns."""
    if name in TEST_ENTITY_EXACT_NAMES:
        return True
    for prefix in TEST_ENTITY_NAME_PREFIXES:
        if name.startswith(prefix):
            return True
    site = (website or "").lower()
    if site and ".test" in site:
        return True
    return False
