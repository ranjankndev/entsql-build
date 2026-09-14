import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from starter.agent.loop import Agent
from starter.agent.nodes import AgentDeps
from starter.guardrails import build_pipeline
from starter.llm.provider import EchoProvider
from starter.memory import ContextMemory, InMemoryStore
from starter.observability import NoOpTracer
from starter.settings import Settings
from starter.tools import default_registry


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_provider="echo",
        memory_backend="in_memory",
        observability_provider="none",
        guardrails_policy="config/guardrails.yaml",
        agent_max_iterations=3,
        agent_max_tool_calls=4,
    )


@pytest.fixture
def deps(settings: Settings) -> AgentDeps:
    llm = EchoProvider()
    return AgentDeps(
        llm=llm,
        tools=default_registry(),
        memory=ContextMemory(store=InMemoryStore(), max_turns=6, summary_trigger=4),
        guardrails=build_pipeline(settings=settings),
        tracer=NoOpTracer(),
        settings=settings,
        reflect=False,
    )


@pytest.fixture
def agent(deps: AgentDeps) -> Agent:
    return Agent(deps)
