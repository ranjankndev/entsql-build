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

    # Service
    app_env: str = "local"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call `get_settings.cache_clear()` in tests."""
    return Settings()
