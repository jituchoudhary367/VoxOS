"""
VoxOS Configuration — Pydantic Settings.

All settings are loaded from environment variables (with .env file support).
Secrets are NEVER hard-coded here. Use .env.example as a template.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    user: str = "voxos"
    password: str = "jarvis_dev_secret"
    db: str = "voxos"
    host: str = "localhost"
    port: int = 5432
    
    # Allow passing the entire URL (e.g. for NeonDB)
    url_override: str | None = Field(default=None, alias="POSTGRES_URL")

    @property
    def url(self) -> str:
        if self.url_override:
            # SQLAlchemy asyncpg needs postgresql+asyncpg scheme
            if self.url_override.startswith("postgresql://"):
                return self.url_override.replace("postgresql://", "postgresql+asyncpg://", 1)
            return self.url_override
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    @property
    def sync_url(self) -> str:
        if self.url_override:
            # Sync URL doesn't need +asyncpg
            return self.url_override.replace("postgresql+asyncpg://", "postgresql://", 1)
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class RedisSettings(BaseSettings):
    """Redis connection settings."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class LLMSettings(BaseSettings):
    """LLM provider settings."""

    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    fast_model: str = "llama-3.1-8b-instant"
    strong_model: str = "llama-3.3-70b-versatile"
    max_tokens: int = 4096
    temperature: float = 0.1

    @field_validator("groq_api_key", mode="before")
    @classmethod
    def check_api_key(cls, v: str) -> str:
        if not v:
            # Allow empty for dev/testing with mocks
            return v
        return v


class STTSettings(BaseSettings):
    """Speech-to-Text settings."""

    model_config = SettingsConfigDict(extra="ignore")

    stt_provider: str = Field(default="deepgram", alias="STT_PROVIDER")
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")
    deepgram_model: str = Field(default="nova-3", alias="DEEPGRAM_MODEL")
    deepgram_language: str = Field(default="multi", alias="DEEPGRAM_LANGUAGE")


class TTSSettings(BaseSettings):
    """Text-to-Speech settings."""

    model_config = SettingsConfigDict(extra="ignore")

    tts_provider: str = Field(default="edge", alias="TTS_PROVIDER")
    edge_tts_voice: str = Field(default="en-US-AriaNeural", alias="EDGE_TTS_VOICE")


class VoiceSettings(BaseSettings):
    """Voice pipeline settings."""

    model_config = SettingsConfigDict(env_prefix="VOICE_", extra="ignore")

    sample_rate: int = 16000
    vad_enabled: bool = True
    interruption_enabled: bool = True


class SecuritySettings(BaseSettings):
    """Security and authentication settings."""

    model_config = SettingsConfigDict(extra="ignore")

    secret_key: str = Field(
        default="INSECURE-CHANGE-ME-IN-PRODUCTION",
        alias="JARVIS_SECRET_KEY",
    )
    jwt_secret_key: str = Field(
        default="INSECURE-JWT-CHANGE-ME",
        alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=1440, alias="JWT_EXPIRE_MINUTES")


class AgentSettings(BaseSettings):
    """Agent execution settings."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    max_retries: int = 3
    task_timeout_seconds: int = 300
    step_timeout_seconds: int = 60

    approval_timeout_seconds: int = Field(
        default=120,
        alias="APPROVAL_TIMEOUT_SECONDS",
    )


class WorkspaceSettings(BaseSettings):
    """Filesystem workspace safety settings."""

    model_config = SettingsConfigDict(extra="ignore")

    workspace_root: str = Field(
        default="~/JarvisWorkspace",
        alias="JARVIS_WORKSPACE_ROOT",
    )
    filesystem_strict_mode: bool = Field(default=True, alias="FILESYSTEM_STRICT_MODE")

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_root).expanduser().resolve()


class ObservabilitySettings(BaseSettings):
    """Logging and metrics settings."""

    model_config = SettingsConfigDict(extra="ignore")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(default="json", alias="LOG_FORMAT")
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")


class Settings(BaseSettings):
    """Master settings class — aggregates all subsystem settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    env: Literal["development", "production", "test"] = Field(
        default="development",
        alias="JARVIS_ENV",
    )
    debug: bool = Field(default=True, alias="JARVIS_DEBUG")
    host: str = Field(default="0.0.0.0", alias="JARVIS_HOST")
    port: int = Field(default=8000, alias="JARVIS_PORT")

    # Sub-settings (lazily instantiated)
    @property
    def db(self) -> DatabaseSettings:
        return DatabaseSettings()

    @property
    def redis(self) -> RedisSettings:
        return RedisSettings()

    @property
    def llm(self) -> LLMSettings:
        return LLMSettings()

    @property
    def stt(self) -> STTSettings:
        return STTSettings()

    @property
    def tts(self) -> TTSSettings:
        return TTSSettings()

    @property
    def voice(self) -> VoiceSettings:
        return VoiceSettings()

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings()

    @property
    def agent(self) -> AgentSettings:
        return AgentSettings()

    @property
    def workspace(self) -> WorkspaceSettings:
        return WorkspaceSettings()

    @property
    def observability(self) -> ObservabilitySettings:
        return ObservabilitySettings()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance (loaded once at startup)."""
    return Settings()
