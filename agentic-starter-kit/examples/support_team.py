"""A two-specialist support team, runnable with no credentials.

    python examples/support_team.py "I was double charged for invoice 42"

Read `docs/multi-agent.md` first: one agent with more tools beats a team in
most projects. This example exists to show the seams — separate tools, separate
prompts, one trace — not to recommend the shape.
"""

from __future__ import annotations

import sys

from starter.agent import Agent, build_agent
from starter.agent.team import Supervisor, Worker
from starter.tools import ToolRegistry, tool


@tool(
    name="lookup_invoice",
    description="Fetch an invoice by id.",
    parameters={
        "type": "object",
        "properties": {"invoice_id": {"type": "string"}},
        "required": ["invoice_id"],
    },
    risk="read",
)
def lookup_invoice(invoice_id: str) -> str:
    return f"Invoice {invoice_id}: 49.00 EUR, paid, refundable until the 30th."


@tool(
    name="check_status_page",
    description="Current status of a service.",
    parameters={
        "type": "object",
        "properties": {"service": {"type": "string"}},
        "required": ["service"],
    },
    risk="read",
)
def check_status_page(service: str) -> str:
    return f"{service}: operational, no incidents in the last 24h."


def specialist(name: str, domain: str, tools: ToolRegistry) -> Agent:
    """A worker is a normal agent — its own tools, prompt and budgets."""
    agent = build_agent(agent_name=name, domain=domain)
    agent.deps.tools = tools
    return agent


def build_team() -> Supervisor:
    supervisor = build_agent(agent_name="Supervisor", domain="customer support")
    return Supervisor(
        deps=supervisor.deps,
        max_workers=2,
        workers=[
            Worker(
                name="billing",
                description="Refunds, invoices, charges and payment questions.",
                agent=specialist(
                    "Billing", "billing and refunds", ToolRegistry().add(lookup_invoice)
                ),
            ),
            Worker(
                name="technical",
                description="Errors, outages, API integration and service status.",
                agent=specialist(
                    "Technical", "technical support", ToolRegistry().add(check_status_page)
                ),
            ),
        ],
    )


def main() -> int:
    query = " ".join(sys.argv[1:]) or "I was double charged for invoice 42"
    state = build_team().run(query, thread_id="example-team")
    print(state.answer)
    print(
        f"\n— routed to {state.routed_to} | trace {state.trace_id} "
        f"| {sum(r.iterations for r in state.results)} iterations "
        f"| {state.latency_ms:.0f} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
