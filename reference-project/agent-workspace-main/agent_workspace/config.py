"""Configuration: env vars (.env) + workspace YAML config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


def _load_env() -> None:
    """Load .env from local dir, then parent dir, then default."""
    for candidate in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=True)
            return
    load_dotenv(override=False)


_load_env()


# ---------------------------------------------------------------------------
# LLM settings (from environment)
# ---------------------------------------------------------------------------

@dataclass
class LLMSettings:
    api_key: str = os.getenv("LLM_API_KEY") or os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("QWEN_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = os.getenv("LLM_MODEL") or "qwen3.5-flash"
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError(
                "No API key found. Set LLM_API_KEY in your .env or environment."
            )


# ---------------------------------------------------------------------------
# Workspace settings (from config.yaml)
# ---------------------------------------------------------------------------

@dataclass
class ExtractionConfig:
    max_text_chars: int = 15_000
    max_images: int = 10
    max_excel_rows: int = 100
    max_excel_sheets: int = 5


@dataclass
class AgentConfig:
    max_iterations: int = 20
    memory_turns: int = 20
    trace_enabled: bool = True


@dataclass
class WorkspaceConfig:
    resources_dir: str = "resources"
    instructions_dir: str = "instructions"
    artifacts_dir: str = "artifacts"
    snapshots_dir: str = ".snapshots"
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


def load_workspace_config(workspace_root: Path) -> WorkspaceConfig:
    """Load config.yaml from workspace root, falling back to defaults."""
    config_path = workspace_root / "config.yaml"
    if not config_path.exists():
        return WorkspaceConfig()

    raw: Dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    ws = raw.get("workspace", {})
    ext = raw.get("extraction", {})
    ag = raw.get("agent", {})

    return WorkspaceConfig(
        resources_dir=ws.get("resources_dir", "resources"),
        instructions_dir=ws.get("instructions_dir", "instructions"),
        artifacts_dir=ws.get("artifacts_dir", "artifacts"),
        snapshots_dir=ws.get("snapshots_dir", ".snapshots"),
        extraction=ExtractionConfig(
            max_text_chars=ext.get("max_text_chars", 15_000),
            max_images=ext.get("max_images", 10),
            max_excel_rows=ext.get("max_excel_rows", 100),
            max_excel_sheets=ext.get("max_excel_sheets", 5),
        ),
        agent=AgentConfig(
            max_iterations=ag.get("max_iterations", 20),
            memory_turns=ag.get("memory_turns", 20),
            trace_enabled=ag.get("trace_enabled", True),
        ),
    )


# Singleton LLM settings (loaded once at import)
llm_settings = LLMSettings()
