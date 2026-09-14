import pytest

from starter.tools import ToolError, default_registry


def test_registry_exposes_schemas():
    registry = default_registry()
    names = {s["function"]["name"] for s in registry.schemas()}
    assert {"search_kb", "calculator", "now"} <= names


def test_calculator_evaluates():
    assert default_registry().invoke("calculator", {"expression": "(18 * 7) + 4"}).content == "130.0"


def test_calculator_rejects_code():
    result = default_registry().invoke("calculator", {"expression": "__import__('os').system('id')"})
    assert not result.ok


def test_unknown_tool_is_reported_not_raised():
    result = default_registry().invoke("nope", {})
    assert not result.ok and "unknown tool" in result.content


def test_unexpected_argument_rejected():
    result = default_registry().invoke("now", {"surprise": 1})
    assert not result.ok


def test_tool_error_type():
    with pytest.raises(ToolError):
        default_registry().get("missing")
