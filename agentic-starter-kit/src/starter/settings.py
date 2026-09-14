"""Central configuration.

Everything tunable lives here and comes from the environment (or `.env`).
No host, model name, key or URL is hard-coded anywhere else in the package.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: Literal["echo", "openai", "azure_openai", "anthropic"] = "echo"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024

    openai_api_key: str | None = None
    openai_base_url: str | None = None

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str | None = None

    anthropic_api_key: str | None = None

    # Resilience: retries with backoff, plus a circuit breaker that stops
    # hammering an endpoint that is persistently failing.
    llm_retry_enabled: bool = True
    llm_max_attempts: int = 3
    llm_retry_base_delay: float = 0.5
    llm_retry_max_delay: float = 20.0
    llm_breaker_threshold: int = 5
    llm_breaker_reset_seconds: float = 30.0

    # Observability
    observability_provider: Literal["none", "langfuse"] = "none"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # Memory
    memory_backend: Literal["in_memory", "file", "cosmos"] = "in_memory"
    memory_dir: str = ".memory"
    cosmos_endpoint: str | None = None
    cosmos_database: str = "agentmem"
    cosmos_container: str = "threads"
    short_term_max_turns: int = 20
    summary_trigger_turns: int = 12

    # Guardrails
    guardrails_policy: str = "config/guardrails.yaml"
    guardrails_fail_mode: Literal["block", "flag"] = "block"

    # Agent loop budget
    agent_max_iterations: int = 8
    agent_wall_clock_seconds: float = 120.0
    agent_max_tool_calls: int = 16
    # Stream draft tokens to the client. Off by default: tokens sent before
    # `guard_output` runs cannot be unsent. See `agent/events.py`.
    agent_stream_tokens: bool = False

    # Service
    app_env: str = "local"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call `get_settings.cache_clear()` in tests."""
    return Settings()
