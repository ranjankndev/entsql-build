"""Command line entry point.

    starter chat  "your question" [--thread t1] [--graph]
    starter eval  --suite evals/datasets/safety.jsonl --threshold 1.0
    starter eval  --suite evals/datasets/core.jsonl --baseline evals/baselines/core.json \
                  --fail-on-regression
    starter eval-diff evals/baselines/core.json .eval-runs/<run>.json
    starter guard "text to test" --stage input
    starter serve --port 8000
    starter init ../supportbot --package supportbot
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from starter.settings import get_settings


def _configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def cmd_chat(args: argparse.Namespace) -> int:
    from starter.agent import build_agent

    agent = build_agent(reflect_enabled=args.reflect)
    if args.stream:
        state = None
        for event in agent.stream(args.query, thread_id=args.thread):
            if event.type == "token":
                print(event.data["text"], end="", flush=True)
            elif event.type in {"status", "tool", "guardrail"}:
                print(f"[{event.type}] {event.data}", file=sys.stderr)
            elif event.type == "answer":
                print(("\n" if args.stream else "") + event.data["text"])
            elif event.type == "done":
                state = event.data
        print(f"\n— {state}")
        return 0
    if args.graph:
        from starter.agent.graph import run_graph

        state = run_graph(agent.deps, args.query, thread_id=args.thread)
    else:
        state = agent.run(args.query, thread_id=args.thread)

    if args.json:
        print(json.dumps(dict(state), indent=2, default=str))
    else:
        print(state.get("answer", ""))
        print(
            f"\n— stop_reason={state.get('stop_reason')} "
            f"iterations={state.get('iteration')} "
            f"tools={state.get('tool_calls_made')} "
            f"trace={state.get('trace_id')}"
        )
    return 1 if state.get("error") else 0


def cmd_eval(args: argparse.Namespace) -> int:
    from starter.evals.diff import compare, load_report, save_baseline
    from starter.evals.harness import run_suite, write_report

    report = run_suite(args.suite, suite_name=args.suite)
    print(report.to_markdown())
    if args.report:
        path = write_report(report, args.report)
        print(f"\nreport: {path}")

    failed = False
    if args.baseline and Path(args.baseline).exists():
        diff = compare(load_report(args.baseline), report.to_dict())
        print("\n" + diff.to_markdown())
        if args.fail_on_regression and not diff.clean:
            print(
                f"\nFAIL: {len(diff.regressions)} case(s) regressed vs {args.baseline}",
                file=sys.stderr,
            )
            failed = True
    elif args.baseline:
        print(f"\nno baseline at {args.baseline} yet — run with --save-baseline to create one")

    if args.save_baseline:
        print(f"baseline written: {save_baseline(report, args.save_baseline)}")

    if report.pass_rate < args.threshold:
        print(
            f"\nFAIL: pass rate {report.pass_rate:.1%} below threshold {args.threshold:.1%}",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


def cmd_eval_diff(args: argparse.Namespace) -> int:
    from starter.evals.diff import compare, load_report

    diff = compare(load_report(args.baseline), load_report(args.current))
    print(diff.to_markdown() if not args.json else json.dumps(diff.to_dict(), indent=2))
    return 0 if diff.clean else 1


def cmd_guard(args: argparse.Namespace) -> int:
    from starter.guardrails import GuardContext, Stage, build_pipeline

    pipeline = build_pipeline()
    outcome = pipeline.run_safe(args.text, GuardContext(stage=Stage(args.stage)))
    print(
        json.dumps(
            {
                "blocked": outcome.blocked,
                "text": outcome.text,
                "results": [
                    {
                        "guard": r.guard,
                        "passed": r.passed,
                        "severity": r.severity.value,
                        "message": r.message,
                    }
                    for r in outcome.results
                ],
            },
            indent=2,
        )
    )
    return 1 if outcome.blocked else 0


def cmd_init(args: argparse.Namespace) -> int:
    from starter.scaffold import ScaffoldError, init_project

    try:
        result = init_project(
            target=args.target,
            package=args.package,
            project_name=args.name,
            force=args.force,
            dry_run=args.dry_run,
        )
    except ScaffoldError as exc:
        print(f"cannot scaffold: {exc}", file=sys.stderr)
        return 1
    print(result.summary())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("starter.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="starter", description="Agentic starter kit")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="run one agent turn")
    chat.add_argument("query")
    chat.add_argument("--thread", default=None)
    chat.add_argument("--graph", action="store_true", help="run through LangGraph")
    chat.add_argument("--stream", action="store_true", help="stream progress events")
    chat.add_argument("--reflect", action="store_true", help="enable the self-check node")
    chat.add_argument("--json", action="store_true")
    chat.set_defaults(func=cmd_chat)

    ev = sub.add_parser("eval", help="run an eval suite")
    ev.add_argument("--suite", required=True)
    ev.add_argument("--threshold", type=float, default=1.0)
    ev.add_argument("--report", default=".eval-runs")
    ev.add_argument("--baseline", help="compare this run against a saved baseline report")
    ev.add_argument("--save-baseline", help="write this run as the new baseline")
    ev.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit non-zero if any case flipped pass -> fail, even if the pass rate holds",
    )
    ev.set_defaults(func=cmd_eval)

    diff = sub.add_parser("eval-diff", help="compare two saved eval reports")
    diff.add_argument("baseline")
    diff.add_argument("current")
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=cmd_eval_diff)

    guard = sub.add_parser("guard", help="test text against the guardrail policy")
    guard.add_argument("text")
    guard.add_argument("--stage", default="input", choices=["input", "tool", "output"])
    guard.set_defaults(func=cmd_guard)

    init = sub.add_parser("init", help="scaffold a new project from this kit")
    init.add_argument("target", help="directory to create")
    init.add_argument("--package", required=True, help="python package name, e.g. supportbot")
    init.add_argument("--name", help="project name for pyproject (defaults to the package)")
    init.add_argument("--force", action="store_true", help="write into a non-empty directory")
    init.add_argument("--dry-run", action="store_true")
    init.set_defaults(func=cmd_init)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
