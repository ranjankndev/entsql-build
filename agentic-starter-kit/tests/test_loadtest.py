"""Load generator: percentile maths, aggregation, concurrency, failure counting."""

import threading

import pytest

from starter.loadtest import LoadReport, Sample, in_process_caller, run_load


def report_of(latencies, ok=True) -> LoadReport:
    report = LoadReport(concurrency=1, wall_seconds=1.0)
    report.samples = [Sample(latency_ms=v, ok=ok) for v in latencies]
    return report


# --------------------------------------------------------------- percentiles


def test_nearest_rank_percentiles():
    report = report_of(range(1, 101))  # 1..100 ms
    assert report.percentile(50) == 50
    assert report.percentile(90) == 90
    assert report.percentile(95) == 95
    assert report.percentile(100) == 100


def test_percentile_of_a_single_sample():
    assert report_of([42]).percentile(99) == 42


def test_percentile_of_nothing_is_zero_not_an_error():
    assert LoadReport().percentile(95) == 0.0


def test_failures_are_excluded_from_latency_by_default():
    report = LoadReport(wall_seconds=1.0)
    report.samples = [Sample(10, True), Sample(9999, False)]
    assert report.percentile(100) == 10
    assert report.percentile(100, successful_only=False) == 9999


# --------------------------------------------------------------- aggregation


def test_error_rate_and_throughput():
    report = LoadReport(wall_seconds=2.0)
    report.samples = [Sample(10, True), Sample(10, True), Sample(10, False), Sample(10, True)]
    assert report.error_rate == 0.25
    assert report.throughput == 2.0


def test_stop_reasons_are_counted():
    report = LoadReport(wall_seconds=1.0)
    report.samples = [
        Sample(1, True, "answered"),
        Sample(1, True, "answered"),
        Sample(1, True, "max_iterations"),
    ]
    assert report.stop_reasons() == {"answered": 2, "max_iterations": 1}


def test_small_samples_are_labelled_as_indicative():
    markdown = report_of([1, 2, 3]).to_markdown()
    assert "indicative, not a measurement" in markdown


def test_large_samples_drop_the_caveat():
    assert "indicative" not in report_of(range(1, 201)).to_markdown()


# ---------------------------------------------------------------- the driver


def test_runs_every_request_and_honours_concurrency():
    seen_threads: set[int] = set()
    lock = threading.Lock()

    def call(query: str) -> Sample:
        with lock:
            seen_threads.add(threading.get_ident())
        return Sample(1.0, True, "answered")

    report = run_load(call, requests=20, concurrency=4)
    assert report.count == 20
    assert 1 < len(seen_threads) <= 4


def test_rejects_nonsense_parameters():
    with pytest.raises(ValueError):
        run_load(lambda q: Sample(1, True), requests=0)
    with pytest.raises(ValueError):
        run_load(lambda q: Sample(1, True), concurrency=0)


def test_a_thrown_error_is_recorded_not_raised(agent):
    class Boom:
        name = "boom"

        def complete(self, messages, tools=None):
            raise RuntimeError("provider down")

    agent.deps.llm = Boom()
    report = run_load(in_process_caller(agent), requests=4, concurrency=2)
    assert report.count == 4
    assert report.error_rate == 1.0  # the agent contains the error; the caller sees stop_reason=error


def test_in_process_run_against_the_real_agent(agent):
    report = run_load(in_process_caller(agent), requests=12, concurrency=3)
    assert report.count == 12
    assert report.error_rate == 0.0
    assert report.stop_reasons()["answered"] == 12
    assert report.percentile(95) > 0
    assert "Load test" in report.to_markdown()


def test_report_dict_shape(agent):
    blob = run_load(in_process_caller(agent), requests=5, concurrency=2).to_dict()
    assert blob["requests"] == 5 and blob["concurrency"] == 2
    assert set(blob["latency_ms"]) == {"mean", "p50", "p90", "p95", "p99", "max"}
