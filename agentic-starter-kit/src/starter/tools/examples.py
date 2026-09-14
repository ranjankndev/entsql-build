"""Placeholder tools.

These exist so a fresh project runs end to end on day one. Delete or replace
them; keep the shape (typed args, small return, `risk` set honestly).
"""

from __future__ import annotations

import ast
import datetime as dt
import operator
from typing import Any

from starter.tools.base import Tool, ToolError, tool

_KB: dict[str, str] = {
    "refund policy": "Refunds are issued within 14 days of purchase, to the original method.",
    "sla": "Standard support responds in 1 business day; premium in 2 hours.",
    "data retention": "Conversation logs are retained for 30 days, then deleted.",
}

_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


@tool(
    name="search_kb",
    description="Search the internal knowledge base and return the best matching snippet.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    },
    risk="read",
)
def search_kb(query: str) -> str:
    lowered = query.lower()
    hits = [v for k, v in _KB.items() if any(w in lowered for w in k.split())]
    return hits[0] if hits else "No knowledge-base entry matched that query."


@tool(
    name="calculator",
    description="Evaluate a arithmetic expression, e.g. '(3 + 4) * 2'.",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
    risk="read",
)
def calculator(expression: str) -> str:
    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ToolError(f"unsupported expression element: {ast.dump(node)[:60]}")

    try:
        parsed = ast.parse(expression, mode="eval").body
    except SyntaxError as exc:
        raise ToolError(f"cannot parse expression: {exc}") from exc
    return str(_eval(parsed))


@tool(
    name="now",
    description="Current UTC timestamp in ISO-8601.",
    parameters={"type": "object", "properties": {}},
    risk="read",
)
def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@tool(
    name="escalate_to_human",
    description="Hand the conversation to a human agent with a short reason.",
    parameters={
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
    risk="write",
    requires_approval=True,
)
def escalate_to_human(reason: str) -> str:
    return f"Escalation queued: {reason}"


TOOLS: tuple[Tool, ...] = (search_kb, calculator, now, escalate_to_human)
