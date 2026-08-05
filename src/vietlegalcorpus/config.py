"""Runtime configuration, overridable via VLC_* environment variables or .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Resolved settings for a single vlc invocation."""

    model_config = SettingsConfigDict(env_prefix="VLC_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("data"))
    out_dir: Path = Field(default=Path("out"))
    log_level: str = Field(default="INFO")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"


def load_settings() -> Settings:
    """Load settings from the environment (single source of truth for the CLI)."""
    return Settings()
