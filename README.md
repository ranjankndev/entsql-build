# Text-to-SQL benchmark workbench

Design benchmark schemas in YAML, rebuild a PostgreSQL database from them,
generate synthetic data and check it. See `docs/PLAN.md` for the plan and
`CLAUDE.md` for the project rules.

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
   ./bench status                                          # prints "no build yet"
   psql -h 127.0.0.1 -U bench_owner benchdata -c 'select 1'
   ```

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
.venv/bin/python -m unittest
```
