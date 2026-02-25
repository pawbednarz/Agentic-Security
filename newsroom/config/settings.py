"""
Application configuration loaded from environment variables / .env file.

All secrets live here — never in agent/tool code.
Uses pydantic-settings so every value is validated at startup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


# ---------------------------------------------------------------------------
# Custom env-var sources that fix two pydantic-settings issues:
#   1. Empty strings for complex fields cause json.loads("") → JSONDecodeError
#   2. Comma-separated list values (e.g. RSS_FEEDS=url1,url2) are not valid JSON
# ---------------------------------------------------------------------------

class _SafeEnvSource(EnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            return None
        if field_name == "rss_feeds" and isinstance(value, str) and not value.startswith("["):
            return [url.strip() for url in value.split(",") if url.strip()]
        return super().decode_complex_value(field_name, field, value)


class _SafeDotEnvSource(DotEnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            return None
        if field_name == "rss_feeds" and isinstance(value, str) and not value.startswith("["):
            return [url.strip() for url in value.split(",") if url.strip()]
        return super().decode_complex_value(field_name, field, value)


class Settings(BaseSettings):
    """
    Single source of truth for all configuration.

    SecretStr is used for API keys — it prevents accidental logging
    (the value is masked as '**********' in repr/str).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------
    anthropic_api_key: SecretStr = Field(
        ...,
        description="Anthropic API key — required",
    )
    llm_model: str = Field(
        default="claude-sonnet-4-5",
        description="Claude model identifier",
    )
    llm_max_tokens: int = Field(
        default=2048,
        ge=256,
        le=8192,
        description="Max tokens per LLM call",
    )
    llm_timeout_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
    )
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    tavily_api_key: Optional[SecretStr] = Field(
        default=None,
        description="Tavily API key — optional, falls back to DuckDuckGo",
    )
    search_max_results: int = Field(default=5, ge=1, le=10)
    search_timeout_seconds: int = Field(default=15, ge=5, le=60)

    # ------------------------------------------------------------------
    # RSS
    # ------------------------------------------------------------------
    rss_feeds: list[str] = Field(
        default=[
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
        description="Comma-separated list of RSS feed URLs",
    )
    rss_max_age_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Max age of RSS items to consider (hours)",
    )

    # ------------------------------------------------------------------
    # Newsroom pipeline
    # ------------------------------------------------------------------
    newsroom_language: str = Field(
        default="pl",
        description="Language for generated articles: 'pl' or 'en'",
    )
    newsroom_min_words: int = Field(default=500, ge=200, le=2000)
    newsroom_max_words: int = Field(default=900, ge=300, le=3000)
    newsroom_max_revisions: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Max editor revision loops before rejection",
    )
    newsroom_min_fact_score: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Minimum fact-check score to allow publishing",
    )
    newsroom_output_dir: str = Field(default="output")
    newsroom_checkpoints_dir: str = Field(default="checkpoints")

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    api_secret_key: SecretStr = Field(
        default=SecretStr("change_me_before_deploy"),
        description="Bearer token for FastAPI authentication",
    )
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1024, le=65535)
    api_debug: bool = Field(default=False)

    # ------------------------------------------------------------------
    # LangSmith — observability (optional)
    # ------------------------------------------------------------------
    langsmith_api_key: Optional[SecretStr] = Field(
        default=None,
        description="LangSmith API key — leave empty to disable tracing",
    )
    langsmith_project: str = Field(
        default="newsroom",
        description="Project name visible in LangSmith UI",
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_format: str = Field(
        default="console",
        description="'console' for development, 'json' for production",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Replace default env sources with safe variants that handle
        empty strings and comma-separated list values in .env files."""
        return (
            init_settings,
            _SafeEnvSource(settings_cls),
            _SafeDotEnvSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("tavily_api_key", "langsmith_api_key", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        """Convert empty env-var strings to None for Optional fields."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("newsroom_language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        allowed = {"pl", "en"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"Language must be one of {allowed}, got '{v}'")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper().strip()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        allowed = {"console", "json"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"log_format must be one of {allowed}")
        return v

    # ------------------------------------------------------------------
    # Convenience helpers — never expose raw secret values in logs
    # ------------------------------------------------------------------

    def has_tavily(self) -> bool:
        return self.tavily_api_key is not None

    def has_langsmith(self) -> bool:
        return (
            self.langsmith_api_key is not None
            and bool(self.langsmith_api_key.get_secret_value())
        )

    def language_name(self) -> str:
        return {"pl": "Polish", "en": "English"}.get(self.newsroom_language, "English")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    The cache ensures .env is read only once per process.
    Call `get_settings.cache_clear()` in tests to reload config.
    """
    return Settings()
