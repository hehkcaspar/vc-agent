"""Layer 2 source fetchers.

Each module exposes `async def run(scholar_id, *, mode, reason) -> dict`
and is the ONLY place in scholar tracking that talks to its external
API. `refresh_dispatcher.trigger_refresh` routes by module name.
"""

from . import (
    google_scholar_papers,
    google_scholar_stats,
    news_web,
    patents_web,
    red_flags_watch,
    semantic_scholar_papers,
    startups_web,
)

__all__ = [
    "google_scholar_papers",
    "semantic_scholar_papers",
    "google_scholar_stats",
    "patents_web",
    "news_web",
    "startups_web",
    "red_flags_watch",
]
