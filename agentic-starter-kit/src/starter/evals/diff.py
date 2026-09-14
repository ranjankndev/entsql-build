"""Compare two eval runs.

A pass rate is a summary; a diff is a decision. "87% → 86%" tells you nothing
about whether to ship. "`core-kb-refund` regressed, `core-math` fixed" does.

The rule this encodes: **an aggregate that holds can still hide a regression.**
Two cases flipping in opposite directions leave the pass rate unchanged, so
`--fail-on-regression` gates on individual cases flipping to fail, not on the
mean moving.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from starter.evals.harness import EvalReport


@dataclass
class CaseDelta:
    case_id: str
    was: bool
    now: bool
    score_before: float
    score_after: float
    reasons: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.was and not self.now:
            return "regression"
        if not self.was and self.now:
            return "fix"
        return "unchanged"

    @property
    def score_delta(self) -> float:
        return self.score_after - self.score_before


@dataclass
class EvalDiff:
    baseline_suite: str
    current_suite: str
    deltas: list[CaseDelta] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    pass_rate_before: float = 0.0
    pass_rate_after: float = 0.0
    p95_before: float = 0.0
    p95_after: float = 0.0
    scorer_means_before: dict[str, float] = field(default_factory=dict)
    scorer_means_after: dict[str, float] = field(default_factory=dict)

    @property
    def regressions(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "regression"]

    @property
    def fixes(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "fix"]

    @property
    def clean(self) -> bool:
        """No case flipped from passing to failing."""
        return not self.regressions

    def scorer_deltas(self) -> dict[str, float]:
        names = set(self.scorer_means_before) | set(self.scorer_means_after)
        return {
            name: self.scorer_means_after.get(name, 0.0) - self.scorer_means_before.get(name, 0.0)
            for name in sorted(names)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "pass_rate_before": round(self.pass_rate_before, 4),
            "pass_rate_after": round(self.pass_rate_after, 4),
            "regressions": [asdict(d) for d in self.regressions],
            "fixes": [asdict(d) for d in self.fixes],
            "added": self.added,
            "removed": self.removed,
            "scorer_deltas": {k: round(v, 4) for k, v in self.scorer_deltas().items()},
            "p95_latency_delta_ms": round(self.p95_after - self.p95_before, 1),
        }

    def to_markdown(self) -> str:
        arrow = "▲" if self.pass_rate_after > self.pass_rate_before else (
            "▼" if self.pass_rate_after < self.pass_rate_before else "="
        )
        lines = [
            f"# Eval diff — {self.current_suite}",
            "",
            f"- verdict: **{'clean' if self.clean else 'REGRESSED'}**",
            f"- pass rate: {self.pass_rate_before:.1%} → {self.pass_rate_after:.1%} {arrow}",
            f"- p95 latency: {self.p95_before:.0f} ms → {self.p95_after:.0f} ms",
            f"- regressions: **{len(self.regressions)}**, fixes: {len(self.fixes)}",
        ]
        if self.added or self.removed:
            lines.append(f"- cases added: {len(self.added)}, removed: {len(self.removed)}")

        if self.regressions:
            lines += ["", "## Regressions (pass → fail)", "", "| case | why |", "| --- | --- |"]
            lines += [
                f"| `{d.case_id}` | {'; '.join(d.reasons) or 'no reason recorded'} |"
                for d in self.regressions
            ]
        if self.fixes:
            lines += ["", "## Fixed (fail → pass)", ""]
            lines += [f"- `{d.case_id}`" for d in self.fixes]

        moved = {k: v for k, v in self.scorer_deltas().items() if abs(v) >= 0.005}
        if moved:
            lines += ["", "## Scorer means that moved", "", "| scorer | delta |", "| --- | --- |"]
            lines += [f"| {name} | {value:+.3f} |" for name, value in moved.items()]
        return "\n".join(lines)


def _index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: case for case in report.get("results", [])}


def _mean_score(case: dict[str, Any]) -> float:
    scores = case.get("scores", [])
    return sum(s["value"] for s in scores) / len(scores) if scores else 1.0


def _failure_reasons(case: dict[str, Any]) -> list[str]:
    return [
        f"{s['name']}: {s['reason'] or 'failed'}" for s in case.get("scores", []) if not s["passed"]
    ]


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> EvalDiff:
    """Diff two `EvalReport.to_dict()` payloads."""
    before, after = _index(baseline), _index(current)
    diff = EvalDiff(
        baseline_suite=baseline.get("suite", "baseline"),
        current_suite=current.get("suite", "current"),
        added=sorted(set(after) - set(before)),
        removed=sorted(set(before) - set(after)),
        pass_rate_before=baseline.get("pass_rate", 0.0),
        pass_rate_after=current.get("pass_rate", 0.0),
        p95_before=baseline.get("p95_latency_ms", 0.0),
        p95_after=current.get("p95_latency_ms", 0.0),
        scorer_means_before=baseline.get("by_scorer", {}),
        scorer_means_after=current.get("by_scorer", {}),
    )
    for case_id in sorted(set(before) & set(after)):
        old, new = before[case_id], after[case_id]
        diff.deltas.append(
            CaseDelta(
                case_id=case_id,
                was=bool(old.get("passed")),
                now=bool(new.get("passed")),
                score_before=_mean_score(old),
                score_after=_mean_score(new),
                reasons=_failure_reasons(new),
            )
        )
    return diff


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_baseline(report: EvalReport, path: str | Path) -> Path:
    """Write a report as the baseline future runs are compared against.

    Commit this file. A baseline that only lives on someone's laptop answers
    "did it get worse?" with "worse than what?".
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return target
