# Text-to-SQL benchmark workbench

Design benchmark schemas in YAML, rebuild a PostgreSQL database from them,
generate synthetic data and check it. See `docs/PLAN.md` for the plan,
`CLAUDE.md` for the project rules, `docs/BUILD_LOG.md` for the acceptance runs
of each step and `docs/PENDING.md` for open questions.

## VPS setup (user `deploy`)

Everything (Claude Code, the CLI, the Streamlit app) runs on the VPS as user
`deploy` from `~/entsql-build`.

1. System packages (Debian 13 ships Python 3.13; 3.12 or newer is required):

   ```sh
   sudo apt install git python3-venv graphviz postgresql-client
   ```

2. Code and Python environment:

   ```sh
   git clone <repo-url> ~/entsql-build
   cd ~/entsql-build
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   # optional, only for the [llm.anthropic] profile:
   .venv/bin/pip install anthropic
   ```

3. Database, done once by the human: PostgreSQL runs in the external container
   `app_postgres`. Apply `docker/init/01-roles.sql` as described in
   `docker/README.md`.

4. Passwords in `~/.pgpass` (mode 600):

   ```sh
   cat >> ~/.pgpass <<'EOF'
   127.0.0.1:5432:benchdata:bench_owner:<owner password>
   127.0.0.1:5432:benchdata:bench_read:<read password>
   EOF
   chmod 600 ~/.pgpass
   ```

5. LLM keys (optional) in `.env` at the repo root, never in `~/.bashrc`
   (Claude Code reads `ANTHROPIC_API_KEY` from the shell):

   ```sh
   touch .env && chmod 600 .env
   echo 'OPENROUTER_API_KEY=...' >> .env
   ```

6. Verify:

   ```sh
   ./bench status
   psql -h 127.0.0.1 -U bench_owner benchdata -c 'select 1'
   ```

## Everyday commands

```sh
./bench model render                 # validate model/mybank.yaml, write build/mybank.sql and .svg
./bench rebuild                      # gen, then one transaction: schema, data, samples, checks
./bench rebuild --no-generate        # same, with the CSVs already in data/
./bench status                       # OK or STALE with the changed files (exit 1 when STALE)
./bench gen [TABLE ...]              # deterministic data/<TABLE>.csv from the generation specs
./bench check                        # structural checks as bench_read, failures with first rows
./bench sql "select count(*) from txn"
./bench model commit -m "message"    # bump version, snapshot into versions/, git commit
./bench --llm openrouter samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3
./bench model import some.sql        # one-time bootstrap of the YAML from DDL (--force to overwrite)
```

`--verbose` prints every SQL statement. After editing the YAML: `./bench model
render`, then `./bench rebuild`.

## The app

```sh
.venv/bin/streamlit run app/Home.py --server.address 127.0.0.1 --server.port 8501
BENCH_LLM=openrouter .venv/bin/streamlit run app/Home.py ...   # with the LLM box on the Data page
```

Pages: Home (status, Render, Rebuild), Model (tables, columns, generation
specs, relations, diagram, Save, Render, Commit), Data (sample rows, Ask LLM,
generated preview), Generate (spec YAML, Generate), Checks, SQL.

## Profiles

Database and LLM targets are profiles in `bench.toml`. Select them with
`BENCH_DB` / `BENCH_LLM` or `--db` / `--llm`; defaults are `local` and `none`.
Both can also be set in `.env`.

## From the laptop

```sh
ssh -L 8501:127.0.0.1:8501 -L 5432:127.0.0.1:5432 deploy@<vps>
```

Open the Streamlit app at `http://localhost:8501` and point DBeaver at
`localhost:5432`.

## Tests

```sh
.venv/bin/python -m unittest                                              # unit tests and page smoke tests
BENCH_INTEGRATION=1 .venv/bin/python -m unittest tests.test_rebuild_integration   # against the database, throwaway schema
PYTHONPATH=. .venv/bin/python tests/acceptance/p3_model_page.py           # P3 UI run; writes the YAML, git checkout afterwards
```
