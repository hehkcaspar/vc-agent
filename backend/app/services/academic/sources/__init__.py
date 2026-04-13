"""Layer 2 source fetchers.

Each module exposes `async def run(scholar_id, *, mode, reason) -> dict`
and is the ONLY place in scholar tracking that talks to its external
API. `refresh_dispatcher.trigger_refresh` routes by module name.
"""

from . import (
    crunchbase_startups,
    google_scholar_stats,
    news_web,
    patents_lens,
    red_flags_watch,
    semantic_scholar_papers,
)

__all__ = [
    "semantic_scholar_papers",
    "google_scholar_stats",
    "patents_lens",
    "news_web",
    "crunchbase_startups",
    "red_flags_watch",
]
