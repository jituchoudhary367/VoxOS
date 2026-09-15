"""
VoxOS Configuration — Pydantic Settings.

All settings are loaded from environment variables (with .env file support).
Secrets are NEVER hard-coded here. Use .env.example as a template.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file_paths() -> tuple[str, ...]:
    """Return dotenv files supported by both the root and nested settings."""
    env_files = [".env"]
    if Path("env").is_file():
        env_files.append("env")
    return tuple(env_files)


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
            url = self.url_override
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

            parsed = urlsplit(url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            sslmode = query.pop("sslmode", None)
            query.pop("channel_binding", None)
            if sslmode and "ssl" not in query:
                query["ssl"] = sslmode
            return urlunsplit(parsed._replace(query=urlencode(query)))
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
    url_override: str | None = Field(default=None, alias="REDIS_URL")
    ca_certs: str | None = Field(default=None, alias="REDIS_CA_CERTS")

    @property
    def url(self) -> str:
        if self.url_override:
            return self.url_override
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class LLMSettings(BaseSettings):
    """LLM provider settings."""

    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    fast_model: str = "openai/gpt-oss-20b"
    strong_model: str = "openai/gpt-oss-120b"
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
    edge_tts_rate: int = Field(default=0, alias="EDGE_TTS_RATE")
    edge_tts_volume: int = Field(default=0, alias="EDGE_TTS_VOLUME")


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
        return DatabaseSettings(_env_file=_env_file_paths())

    @property
    def redis(self) -> RedisSettings:
        return RedisSettings(_env_file=_env_file_paths())

    @property
    def llm(self) -> LLMSettings:
        return LLMSettings(_env_file=_env_file_paths())

    @property
    def stt(self) -> STTSettings:
        return STTSettings(_env_file=_env_file_paths())

    @property
    def tts(self) -> TTSSettings:
        return TTSSettings(_env_file=_env_file_paths())

    @property
    def voice(self) -> VoiceSettings:
        return VoiceSettings(_env_file=_env_file_paths())

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings(_env_file=_env_file_paths())

    @property
    def agent(self) -> AgentSettings:
        return AgentSettings(_env_file=_env_file_paths())

    @property
    def workspace(self) -> WorkspaceSettings:
        return WorkspaceSettings(_env_file=_env_file_paths())

    @property
    def observability(self) -> ObservabilitySettings:
        return ObservabilitySettings(_env_file=_env_file_paths())

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance (loaded once at startup)."""
    return Settings(_env_file=_env_file_paths())
