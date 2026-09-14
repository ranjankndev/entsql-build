"""Eval harness.

Runs a dataset of cases through the agent, scores each one, aggregates, and
writes a JSON report plus a markdown summary. Scores are pushed to the tracer
so a failing case in CI links straight to its trace.

    python -m starter.cli eval --suite evals/datasets/core.jsonl --threshold 0.9
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from starter.agent import Agent, build_agent
from starter.evals.scorers import SCORERS, Score
from starter.observability import get_tracer
from starter.settings import Settings, get_settings


@dataclass
class EvalCase:
    id: str
    query: str
    expected: str | None = None
    expect_contains: list[str] = field(default_factory=list)
    expect_absent: list[str] = field(default_factory=list)
    expect_tools: list[str] = field(default_factory=list)
    expect_refusal: bool = False
    max_iterations: int | None = None
    similarity_threshold: float = 0.3
    tags: list[str] = field(default_factory=list)
    scorers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EvalCase:
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class EvalResult:
    case_id: str
    query: str
    answer: str
    trace_id: str
    stop_reason: str
    latency_ms: float
    scores: list[Score]
    tags: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scores)

    @property
    def mean_score(self) -> float:
        return statistics.fmean([s.value for s in self.scores]) if self.scores else 1.0


@dataclass
class EvalReport:
    suite: str
    results: list[EvalResult]
    started_at: float
    finished_at: float

    @property
    def pass_rate(self) -> float:
        return (sum(r.passed for r in self.results) / len(self.results)) if self.results else 1.0

    @property
    def mean_score(self) -> float:
        return statistics.fmean([r.mean_score for r in self.results]) if self.results else 1.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        ordered = sorted(r.latency_ms for r in self.results)
        return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]

    def by_scorer(self) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for result in self.results:
            for score in result.scores:
                totals.setdefault(score.name, []).append(score.value)
        return {name: statistics.fmean(values) for name, values in totals.items()}

    def failures(self) -> list[EvalResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "cases": len(self.results),
            "by_scorer": {k: round(v, 4) for k, v in self.by_scorer().items()},
            "duration_s": round(self.finished_at - self.started_at, 2),
            "results": [
                {
                    **{k: v for k, v in asdict(r).items() if k != "scores"},
                    "passed": r.passed,
                    "scores": [asdict(s) for s in r.scores],
                }
                for r in self.results
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Eval report — {self.suite}",
            "",
            f"- cases: **{len(self.results)}**",
            f"- pass rate: **{self.pass_rate:.1%}**",
            f"- mean score: **{self.mean_score:.3f}**",
            f"- p95 latency: **{self.p95_latency_ms:.0f} ms**",
            "",
            "| scorer | mean |",
            "| --- | --- |",
        ]
        lines += [f"| {name} | {value:.3f} |" for name, value in sorted(self.by_scorer().items())]
        failures = self.failures()
        if failures:
            lines += ["", "## Failures", "", "| case | stop reason | why |", "| --- | --- | --- |"]
            for result in failures:
                why = "; ".join(f"{s.name}: {s.reason}" for s in result.scores if not s.passed)
                lines.append(f"| `{result.case_id}` | {result.stop_reason} | {why} |")
        return "\n".join(lines)


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load a `.jsonl` (one case per line) or `.json` (list) dataset."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        raw = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raw = json.loads(text)
    return [EvalCase.from_dict(item) for item in raw]


DEFAULT_SCORERS = ("contains_all", "excludes_all", "refusal", "tool_use", "budget", "guardrails")


def _scorers_for(case: EvalCase, extra: dict[str, Callable[..., Score]]) -> list[Callable[..., Score]]:
    names = case.scorers or list(DEFAULT_SCORERS)
    pool = {**SCORERS, **extra}
    missing = [n for n in names if n not in pool]
    if missing:
        raise KeyError(f"unknown scorer(s) {missing}; available: {sorted(pool)}")
    return [pool[n] for n in names]


def run_suite(
    cases: Sequence[EvalCase] | str | Path,
    agent: Agent | None = None,
    extra_scorers: dict[str, Callable[..., Score]] | None = None,
    settings: Settings | None = None,
    suite_name: str | None = None,
    push_scores: bool = True,
) -> EvalReport:
    s = settings or get_settings()
    if isinstance(cases, (str, Path)):
        suite_name = suite_name or str(cases)
        cases = load_cases(cases)
    agent = agent or build_agent(s)
    tracer = get_tracer(s)
    started = time.time()
    results: list[EvalResult] = []

    for case in cases:
        t0 = time.perf_counter()
        state = agent.run(case.query, thread_id=f"eval:{case.id}")
        latency = (time.perf_counter() - t0) * 1000
        scores = [scorer(case, dict(state)) for scorer in _scorers_for(case, extra_scorers or {})]
        result = EvalResult(
            case_id=case.id,
            query=case.query,
            answer=state.get("answer", ""),
            trace_id=state.get("trace_id", ""),
            stop_reason=state.get("stop_reason", ""),
            latency_ms=latency,
            scores=scores,
            tags=case.tags,
        )
        results.append(result)
        if push_scores and result.trace_id:
            for score in scores:
                tracer.score(result.trace_id, score.name, score.value, score.reason)
    tracer.flush()
    return EvalReport(
        suite=suite_name or "suite", results=results, started_at=started, finished_at=time.time()
    )


def write_report(report: EvalReport, out_dir: str | Path = ".eval-runs") -> Path:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(report.started_at))
    json_path = directory / f"{stamp}.json"
    json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    (directory / f"{stamp}.md").write_text(report.to_markdown(), encoding="utf-8")
    return json_path


def iter_tags(cases: Iterable[EvalCase]) -> set[str]:
    return {tag for case in cases for tag in case.tags}
