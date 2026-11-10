"""Centralized configuration management for EchoScope AI.

Implements native Pydantic Settings with TomlConfigSettingsSource for
Streamlit secrets (.streamlit/secrets.toml), enforcing a strict precedence:
1. Explicit initialization arguments
2. OS Environment variables
3. .env file
4. .streamlit/secrets.toml
5. Schema defaults
"""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class StreamlitSecretsSettingsSource(TomlConfigSettingsSource):
    """Custom settings source loading configuration from .streamlit/secrets.toml."""

    def __init__(self, settings_cls: type[BaseSettings]):
        candidates = [
            Path(".streamlit/secrets.toml"),
            Path.cwd() / ".streamlit" / "secrets.toml",
            Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml",
        ]
        chosen = next((p for p in candidates if p.exists()), None)
        super().__init__(settings_cls, toml_file=chosen)


class Settings(BaseSettings):
    """Application settings schema and loader."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # API Credentials
    youtube_api_key: str = Field(
        default="",
        alias="YOUTUBE_API_KEY",
        description="YouTube Data API v3 key",
    )
    gemini_api_key: str = Field(
        default="",
        alias="GEMINI_API_KEY",
        description="Google Gemini API key",
    )

    # Model Configuration
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        alias="GEMINI_MODEL",
        description="Google Gemini model identifier",
    )

    # Storage & Environment
    db_path: str = Field(
        default="youtube_tracker.db",
        alias="DB_PATH",
        description="Path to SQLite database file",
    )
    app_env: str = Field(
        default="development",
        alias="APP_ENV",
        description="Execution environment: development, testing, or production",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Defines strict source precedence: Init > Env > Dotenv > Streamlit Secrets."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            StreamlitSecretsSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("youtube_api_key", "gemini_api_key", mode="before")
    @classmethod
    def strip_whitespace_and_placeholders(cls, v: str | None) -> str:
        if not v:
            return ""
        val = str(v).strip()
        # Reject default dummy placeholders
        if any(placeholder in val.upper() for placeholder in ["YOUR_", "REPLACE_WITH"]):
            return ""
        return val

    @property
    def is_youtube_configured(self) -> bool:
        """Returns True if a non-placeholder YouTube API key is present."""
        return bool(self.youtube_api_key)

    @property
    def is_gemini_configured(self) -> bool:
        """Returns True if a non-placeholder Gemini API key is present."""
        return bool(self.gemini_api_key)


def get_settings() -> Settings:
    """Returns singleton/active Settings instance."""
    return Settings()
