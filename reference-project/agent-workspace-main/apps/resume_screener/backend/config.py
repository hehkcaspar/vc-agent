"""Configuration management for Resume Screener."""

from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# Suppress LangChain Pydantic V1 warning
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.")

# Load .env from project root before importing agent_workspace
project_root = Path(__file__).parent.parent.parent.parent
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

# Import agent_workspace's LLM settings
sys.path.insert(0, str(project_root))
from agent_workspace.config import llm_settings as core_llm_settings


@dataclass
class ScreenerConfig:
    """Configuration for the resume screener."""
    
    # Paths
    incoming_dir: str = "sample_data/incoming_candidate"
    processed_dir: str = "sample_data/processed"
    evaluations_dir: str = "sample_data/evaluations"
    jds_file: str = "sample_data/jds/positions.json"
    
    # Polling interval in seconds (minimum 3.0)
    poll_interval: float = 5.0
    
    # Processing
    max_file_size_mb: int = 50
    supported_extensions: tuple = (".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tiff", ".bmp")
    
    def __post_init__(self):
        """Ensure paths are resolved relative to project root."""
        app_root = Path(__file__).parent.parent
        self.incoming_dir = str(app_root / self.incoming_dir)
        self.processed_dir = str(app_root / self.processed_dir)
        self.evaluations_dir = str(app_root / self.evaluations_dir)
        self.jds_file = str(app_root / self.jds_file)
    
    @classmethod
    def from_file(cls, path: str) -> ScreenerConfig:
        """Load configuration from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)
    
    def save_to_file(self, path: str) -> None:
        """Save configuration to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)


# Global config instances
_config: Optional[ScreenerConfig] = None


def get_config() -> ScreenerConfig:
    """Get or create global configuration."""
    global _config
    if _config is None:
        _config = ScreenerConfig()
    return _config


def set_config(config: ScreenerConfig) -> None:
    """Set global configuration."""
    global _config
    _config = config


def get_llm_settings():
    """Get LLM settings from agent_workspace core (already loaded from root .env)."""
    return core_llm_settings
