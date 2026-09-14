from starter.evals.harness import EvalCase, load_cases, run_suite


def test_load_jsonl():
    cases = load_cases("evals/datasets/safety.jsonl")
    assert cases and all(isinstance(c, EvalCase) for c in cases)


def test_safety_suite_passes_with_echo_provider(agent):
    report = run_suite("evals/datasets/safety.jsonl", agent=agent, push_scores=False)
    assert report.pass_rate == 1.0, report.to_markdown()


def test_smoke_suite_passes_with_echo_provider(agent):
    report = run_suite("evals/datasets/smoke.jsonl", agent=agent, push_scores=False)
    assert report.pass_rate == 1.0, report.to_markdown()


def test_report_shapes(agent):
    report = run_suite("evals/datasets/smoke.jsonl", agent=agent, push_scores=False)
    blob = report.to_dict()
    assert blob["cases"] == 3
    assert "pass_rate" in blob and "by_scorer" in blob
    assert report.to_markdown().startswith("# Eval report")


def test_unknown_scorer_is_loud(agent):
    import pytest

    with pytest.raises(KeyError):
        run_suite([EvalCase(id="x", query="hi", scorers=["nope"])], agent=agent, push_scores=False)
