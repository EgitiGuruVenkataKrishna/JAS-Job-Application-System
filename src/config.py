"""JAS Configuration — Pydantic Settings with environment variable validation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Google Gemini ---
    gemini_api_key: str

    # --- Telegram ---
    telegram_bot_token: str
    telegram_chat_id: str

    # --- Supabase ---
    supabase_url: str
    supabase_key: str
    supabase_service_key: str

    # --- Thresholds ---
    cosine_threshold: float = 0.77
    cover_letter_score_threshold: int = 90

    # --- Scheduling ---
    ingestion_interval_hours: int = 3
    digest_hour: int = 8

    # --- Output Paths ---
    output_dir: str = "output"

    @property
    def resumes_dir(self) -> Path:
        path = Path(self.output_dir) / "resumes"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cover_letters_dir(self) -> Path:
        path = Path(self.output_dir) / "cover_letters"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def screenshots_dir(self) -> Path:
        path = Path(self.output_dir) / "screenshots"
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """Singleton settings instance — cached after first load."""
    return Settings()
