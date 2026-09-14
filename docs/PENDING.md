# Pending issues that need the human

Kept up to date while building P1-P6 unattended. Newest step last. Each item
says what is blocked and what to do.

## Open

### 1. P4 acceptance needs a real LLM provider
No `.env` exists and nothing listens on `127.0.0.1:11434` (Ollama), so
`./bench samples fill ...` cannot be run against a real model. Everything else
in P4 is tested with fake providers and local HTTP stubs.
To do: put a key in `.env` (e.g. `OPENROUTER_API_KEY=...`, mode 600) and run
`./bench --llm openrouter samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3`.

### 2. Identifier case policy (decision to confirm)
The model keeps names as written (`CUST_MSTR`). DDL emits them unquoted, so
PostgreSQL stores them lower case (`cust_mstr`) and queries work with any case
without quotes. Raw SQL in `check`/`default` relies on this. Names that are SQL
keywords (e.g. `ORDER`) are quoted in lower case (`"order"`). The importer keeps
the source spelling of table and column names, but CHECK/DEFAULT expressions
come back lower case (pglast deparse). Say if you want quoted, case-preserving
identifiers instead; it is one function (`PostgresDialect.quote`).

### 3. PLAN section 3 example YAML is invalid as written
`note: dropped FK on purpose, ERP style` sits inside a `{...}` flow mapping,
where the comma splits it into a second key. Quote it:
`note: "dropped FK on purpose, ERP style"`. The real model files are written by
the tool and are quoted correctly; only the doc example is affected.

### 4. Nothing pushed
Each step is committed locally on `claude/text-to-sql-benchmark-setup-efzs2y`;
nothing was pushed. Review, then `git push`.

### 5. P3: click through the Model page once in a browser
The UI acceptance ran headless (AppTest, editor edits injected as widget
state). Please do one manual pass: `.venv/bin/streamlit run app/Home.py
--server.address 127.0.0.1`, tunnel port 8501, open Model, add a table, add a
column row and a relation row in the editors, Save YAML, Render, then
`./bench rebuild --no-generate`. Revert with `git checkout model/mybank.yaml build/`.

### 6. P4 details to confirm with a real provider
- The `[llm.anthropic]` profile has `fallbacks = "default"` (server-side refusal
  fallback, beta header `server-side-fallback-2026-07-01`). Remove the line if
  you do not want declined requests re-run on another model.
- `anthropic` is not installed (optional extra): `.venv/bin/pip install anthropic`.
- Nullable columns are sent as JSON schema `"type": ["string", "null"]`.
  OpenAI strict mode, Ollama and Anthropic structured outputs should accept
  this; if one rejects the schema, the openai_compat provider falls back to a
  prompt-only request on HTTP 400/422, the others report the error.
- Sample validation checks columns, types, lengths, NOT NULL, primary key
  uniqueness and parent keys. CHECK constraints are only enforced by the
  database, so a row violating a CHECK is saved and then fails the rebuild.
