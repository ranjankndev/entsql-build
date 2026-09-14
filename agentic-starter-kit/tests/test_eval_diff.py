"""Diffing two eval runs.

The property that matters: an unchanged aggregate must not hide a regression.
"""

import json

import pytest

from starter.evals.diff import compare, load_report, save_baseline
from starter.evals.harness import run_suite


def report(suite: str, cases: list[tuple[str, bool]], p95: float = 10.0, by_scorer=None) -> dict:
    return {
        "suite": suite,
        "pass_rate": sum(p for _, p in cases) / len(cases) if cases else 1.0,
        "p95_latency_ms": p95,
        "by_scorer": by_scorer or {},
        "results": [
            {
                "case_id": case_id,
                "passed": passed,
                "scores": [
                    {
                        "name": "contains_all",
                        "value": 1.0 if passed else 0.0,
                        "passed": passed,
                        "reason": "" if passed else "missing: ['14 days']",
                    }
                ],
            }
            for case_id, passed in cases
        ],
    }


def test_detects_a_regression():
    diff = compare(report("s", [("a", True)]), report("s", [("a", False)]))
    assert not diff.clean
    assert [d.case_id for d in diff.regressions] == ["a"]
    assert "missing" in diff.regressions[0].reasons[0]


def test_detects_a_fix():
    diff = compare(report("s", [("a", False)]), report("s", [("a", True)]))
    assert diff.clean
    assert [d.case_id for d in diff.fixes] == ["a"]


def test_unchanged_pass_rate_still_surfaces_a_regression():
    # One case regressed, another was fixed: the aggregate is identical.
    before = report("s", [("a", True), ("b", False)])
    after = report("s", [("a", False), ("b", True)])
    diff = compare(before, after)
    assert diff.pass_rate_before == diff.pass_rate_after
    assert not diff.clean
    assert [d.case_id for d in diff.regressions] == ["a"]


def test_tracks_added_and_removed_cases():
    diff = compare(report("s", [("a", True)]), report("s", [("b", True)]))
    assert diff.added == ["b"] and diff.removed == ["a"]
    assert diff.clean  # a removed case is not a regression


def test_score_deltas_and_kinds():
    diff = compare(report("s", [("a", True)]), report("s", [("a", False)]))
    delta = diff.deltas[0]
    assert delta.kind == "regression"
    assert delta.score_delta == -1.0


def test_scorer_mean_deltas():
    diff = compare(
        report("s", [("a", True)], by_scorer={"tool_use": 0.9, "budget": 1.0}),
        report("s", [("a", True)], by_scorer={"tool_use": 0.6, "budget": 1.0}),
    )
    assert diff.scorer_deltas()["tool_use"] == pytest.approx(-0.3)
    assert diff.scorer_deltas()["budget"] == 0.0


def test_markdown_names_the_verdict_and_the_cases():
    markdown = compare(report("s", [("a", True)]), report("s", [("a", False)])).to_markdown()
    assert "REGRESSED" in markdown
    assert "`a`" in markdown


def test_markdown_is_clean_when_nothing_flipped():
    markdown = compare(report("s", [("a", True)]), report("s", [("a", True)])).to_markdown()
    assert "clean" in markdown and "REGRESSED" not in markdown


def test_latency_delta_is_reported():
    diff = compare(report("s", [("a", True)], p95=10), report("s", [("a", True)], p95=25))
    assert diff.to_dict()["p95_latency_delta_ms"] == 15.0


def test_baseline_round_trip(agent, tmp_path):
    current = run_suite("evals/datasets/smoke.jsonl", agent=agent, push_scores=False)
    path = save_baseline(current, tmp_path / "baselines" / "smoke.json")
    assert json.loads(path.read_text())["suite"]
    diff = compare(load_report(path), current.to_dict())
    assert diff.clean and not diff.added and not diff.removed


def test_regression_suite_passes_with_echo_provider(agent):
    suite = run_suite("evals/datasets/regression.jsonl", agent=agent, push_scores=False)
    assert suite.pass_rate == 1.0, suite.to_markdown()
