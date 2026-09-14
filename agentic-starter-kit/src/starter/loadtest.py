"""Load generator and latency report.

Two modes, and the first is the one people skip:

* **in-process** — drive the agent directly, no HTTP. With
  `LLM_PROVIDER=echo` this measures *your* overhead: guardrails, memory
  assembly, graph bookkeeping. If that is already 40 ms, no amount of model
  tuning gets you under it, and you would never have found out by load-testing
  the endpoint.
* **http** — drive the deployed service, which adds serialisation, the network,
  ingress, and replica scheduling. Run both and the difference is the
  infrastructure's contribution, itemised.

Threads, not asyncio: an agent turn is dominated by waiting on a model, the
kit's own code is sync, and a thread pool is the honest model of a sync service
behind a WSGI/ASGI worker. It is also 60 lines instead of an event-loop
rewrite.

Percentiles use nearest-rank on the observed samples — no interpolation, no
pretending 200 samples describe a p99.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

SAMPLE_QUERIES: tuple[str, ...] = (
    "What is the refund policy?",
    "What is the premium support SLA?",
    "How long is data retained?",
    "What is (18 * 7) + 4?",
    "Rewrite this politely: send it now",
)


@dataclass
class Sample:
    latency_ms: float
    ok: bool
    stop_reason: str = ""
    status: int | None = None
    error: str | None = None


@dataclass
class LoadReport:
    samples: list[Sample] = field(default_factory=list)
    concurrency: int = 1
    wall_seconds: float = 0.0
    target: str = "in-process"

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def errors(self) -> int:
        return sum(1 for s in self.samples if not s.ok)

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0.0

    @property
    def throughput(self) -> float:
        return self.count / self.wall_seconds if self.wall_seconds else 0.0

    def latencies(self, successful_only: bool = True) -> list[float]:
        pool = [s for s in self.samples if s.ok or not successful_only]
        return sorted(s.latency_ms for s in pool)

    def percentile(self, p: float, successful_only: bool = True) -> float:
        """Nearest-rank percentile. `p` in [0, 100]."""
        values = self.latencies(successful_only)
        if not values:
            return 0.0
        rank = max(1, min(len(values), int(-(-p / 100 * len(values) // 1))))
        return values[rank - 1]

    def stop_reasons(self) -> dict[str, int]:
        return dict(Counter(s.stop_reason for s in self.samples if s.stop_reason))

    def to_dict(self) -> dict[str, Any]:
        values = self.latencies()
        return {
            "target": self.target,
            "requests": self.count,
            "concurrency": self.concurrency,
            "wall_seconds": round(self.wall_seconds, 2),
            "throughput_rps": round(self.throughput, 2),
            "error_rate": round(self.error_rate, 4),
            "latency_ms": {
                "mean": round(statistics.fmean(values), 1) if values else 0.0,
                "p50": round(self.percentile(50), 1),
                "p90": round(self.percentile(90), 1),
                "p95": round(self.percentile(95), 1),
                "p99": round(self.percentile(99), 1),
                "max": round(values[-1], 1) if values else 0.0,
            },
            "stop_reasons": self.stop_reasons(),
        }

    def to_markdown(self) -> str:
        blob = self.to_dict()
        latency = blob["latency_ms"]
        lines = [
            f"# Load test — {self.target}",
            "",
            f"- requests: **{blob['requests']}** at concurrency **{blob['concurrency']}**",
            f"- throughput: **{blob['throughput_rps']} req/s** over {blob['wall_seconds']}s",
            f"- error rate: **{blob['error_rate']:.1%}**",
            "",
            "| metric | ms |",
            "| --- | --- |",
            f"| mean | {latency['mean']} |",
            f"| p50 | {latency['p50']} |",
            f"| p90 | {latency['p90']} |",
            f"| p95 | {latency['p95']} |",
            f"| p99 | {latency['p99']} |",
            f"| max | {latency['max']} |",
        ]
        if blob["stop_reasons"]:
            lines += ["", "| stop reason | count |", "| --- | --- |"]
            lines += [f"| {k} | {v} |" for k, v in sorted(blob["stop_reasons"].items())]
        if blob["requests"] < 100:
            lines += [
                "",
                f"> {blob['requests']} samples: p95 and p99 here are indicative, not a "
                "measurement. Percentiles need roughly 100x the tail you are quoting.",
            ]
        return "\n".join(lines)


def run_load(
    call: Callable[[str], Sample],
    requests: int = 50,
    concurrency: int = 5,
    queries: tuple[str, ...] = SAMPLE_QUERIES,
    target: str = "in-process",
) -> LoadReport:
    """Fire `requests` calls through `concurrency` workers."""
    if requests < 1 or concurrency < 1:
        raise ValueError("requests and concurrency must be >= 1")
    report = LoadReport(concurrency=concurrency, target=target)
    lock = threading.Lock()
    started = time.perf_counter()

    def one(index: int) -> None:
        sample = call(queries[index % len(queries)])
        with lock:
            report.samples.append(sample)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(requests)))

    report.wall_seconds = time.perf_counter() - started
    return report


def in_process_caller(agent: Any) -> Callable[[str], Sample]:
    """Drive the agent directly: no HTTP, no serialisation, just your code."""

    def call(query: str) -> Sample:
        start = time.perf_counter()
        try:
            state = agent.run(query, thread_id=f"load:{threading.get_ident()}")
        except Exception as exc:
            return Sample((time.perf_counter() - start) * 1000, False, error=str(exc)[:200])
        return Sample(
            latency_ms=(time.perf_counter() - start) * 1000,
            ok=state.get("error") is None,
            stop_reason=state.get("stop_reason", ""),
        )

    return call


def http_caller(url: str, timeout: float = 60.0) -> Callable[[str], Sample]:
    """Drive a deployed `/chat` endpoint with the standard library only."""

    def call(query: str) -> Sample:
        payload = json.dumps({"query": query}).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                return Sample(
                    latency_ms=(time.perf_counter() - start) * 1000,
                    ok=True,
                    stop_reason=body.get("stop_reason", ""),
                    status=response.status,
                )
        except urllib.error.HTTPError as exc:
            # A 429 is the rate limiter working, not the service failing — it is
            # counted as an error here but broken out by status so you can tell.
            return Sample(
                (time.perf_counter() - start) * 1000, False, status=exc.code, error=f"HTTP {exc.code}"
            )
        except Exception as exc:
            return Sample((time.perf_counter() - start) * 1000, False, error=str(exc)[:200])

    return call
