PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install lint typecheck test eval-gate eval-core baselines loadtest run docker clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## create the virtualenv
	$(PY) -m venv $(VENV)

install: venv ## install the package with dev extras
	$(BIN)/pip install -e ".[dev]"

lint: ## ruff
	$(BIN)/ruff check src tests

typecheck: ## mypy
	$(BIN)/mypy src

test: ## unit tests
	$(BIN)/pytest -q

eval-gate: ## suites that must pass on every PR (no credentials needed)
	$(BIN)/starter eval --suite evals/datasets/smoke.jsonl --threshold 1.0
	$(BIN)/starter eval --suite evals/datasets/safety.jsonl --threshold 1.0 \
		--baseline evals/baselines/safety.json --fail-on-regression
	$(BIN)/starter eval --suite evals/datasets/regression.jsonl --threshold 1.0 \
		--baseline evals/baselines/regression.json --fail-on-regression

baselines: ## re-record the committed baselines (review the diff before committing)
	$(BIN)/starter eval --suite evals/datasets/smoke.jsonl --report "" --save-baseline evals/baselines/smoke.json
	$(BIN)/starter eval --suite evals/datasets/safety.jsonl --report "" --save-baseline evals/baselines/safety.json
	$(BIN)/starter eval --suite evals/datasets/regression.jsonl --report "" --save-baseline evals/baselines/regression.json

eval-core: ## quality suite against the configured model
	$(BIN)/starter eval --suite evals/datasets/core.jsonl --threshold 0.85

loadtest: ## measure in-process latency (your overhead, no model)
	$(BIN)/starter loadtest --requests 200 --concurrency 10

run: ## serve the API locally
	$(BIN)/starter serve --port 8000 --reload

docker: ## build the container image
	docker build -t agentic-starter-kit:local .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache .eval-runs .memory
