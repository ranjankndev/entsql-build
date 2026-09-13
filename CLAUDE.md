# Project rules for Claude Code

Read docs/PLAN.md first. Build the step you are asked for, nothing beyond it.

## Sources of truth
- `model/mybank.yaml` is the schema. `build/mybank.sql`, `build/mybank.svg`,
  `checks/generated/*` and `data/*.csv` are generated; never hand-edit them.
- `model/samples/*.csv` are hand-crafted rows and survive rebuilds. Rows edited
  directly in the database are lost on rebuild by design.
- The database is disposable. Never alter the schema in the database directly;
  change the YAML and run `./bench rebuild`.

## Database access
- Connect only with psycopg or `psql -h 127.0.0.1` as `bench_owner`
  (read-only checks and the SQL page use `bench_read`). Password from `~/.pgpass`.
- Never run `docker exec`, `docker compose exec` or any docker command from
  code or scripts. Docker is started by the human.
- Rebuild is one transaction: it either completes or leaves the previous
  schema untouched.

## Code
- Python 3.12, standard library plus: psycopg, pyyaml, pglast, graphviz, faker,
  streamlit, anthropic, pandas. Ask before adding anything else.
- All logic in `benchlib/`. `tools/bench.py` and `app/pages/*` only call it.
- Generation is deterministic: seed from the YAML, no wall-clock or unseeded
  randomness anywhere in `benchlib/gen.py`.
- Engine-specific SQL and loading live only in `benchlib/dialects/`. Nothing
  outside it writes DDL or COPY statements. Phase 1 has `postgres.py` only.
- LLM calls live only in `benchlib/llm/`, behind the `LLMProvider` interface
  (Anthropic `claude-opus-5` by default, Ollama as the alternative). Every LLM
  output is validated by code before it is written to disk.
- Database and LLM targets are profiles in `bench.toml`, selected with
  `BENCH_DB` / `BENCH_LLM` or `--db` / `--llm`. Never hard-code a host, port,
  model name or API URL.
- Small functions, type hints, no clever metaprogramming, no hidden state.
  `--verbose` prints every SQL statement; LLM requests and responses are
  logged to `logs/llm/`. A unit test for every
  parser, emitter and generator.

## Workflow
- After changing `model/mybank.yaml`, run `./bench model render` and then
  `./bench rebuild`, and fix everything the output reports before finishing.
- Run `./bench status` before assuming the database matches the files.
- Keep each change minimal and runnable. Report the exact command you ran and
  its output when you finish a step.
