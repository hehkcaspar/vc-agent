"""Portfolio Layer 2 sources.

Import-register each source module so ``refresh_dispatcher`` can resolve
it via ``getattr(sources, source_id)``.
"""

from . import news_web  # noqa: F401
