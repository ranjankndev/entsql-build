# Pending issues that need the human

Only still-open items. Each item says what is blocked and what to do.

Build state (2026-09-14): P0-P6 are built, committed and pushed. Every
acceptance check in PLAN section 7 passed (output in docs/BUILD_LOG.md) except
that P4 ran against a local stub endpoint instead of a real LLM (item 1) and P3
ran headless (item 2). The database holds the starter model, version 2,
520,017 rows, status OK.

Resolved on 2026-09-14 and removed from this list: identifiers stay unquoted
(PLAN section 3), PLAN YAML example quoting fixed, branch pushed,
`fallbacks = "default"` kept, CHECK validation left to the transactional
rebuild, per-table load order adopted in PLAN section 4, data/*.csv stays out
of git with the Faker pin explained in requirements.txt.

## Open

### 1. P4 acceptance with a real LLM provider (deferred, no key yet)
No `.env` exists and nothing listens on `127.0.0.1:11434` (Ollama), so
`./bench samples fill ...` has not been run against a real model. Everything
else in P4 is tested with fake providers and local HTTP stubs.
To do when a key exists: put it in `.env` (e.g. `OPENROUTER_API_KEY=...`,
mode 600) and run
`./bench --llm openrouter samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3`.
While doing that, also confirm:
- `anthropic` is not installed (optional extra): `.venv/bin/pip install anthropic`
  before using the `[llm.anthropic]` profile.
- Nullable columns are sent as JSON schema `"type": ["string", "null"]`.
  OpenAI strict mode, Ollama and Anthropic structured outputs should accept
  this; if one rejects the schema, the openai_compat provider falls back to a
  prompt-only request on HTTP 400/422, the others report the error.

### 2. P3: click through the Model page once in a browser (human doing it)
The UI acceptance ran headless (AppTest, editor edits injected as widget
state). Manual pass: `.venv/bin/streamlit run app/Home.py
--server.address 127.0.0.1`, tunnel port 8501, open Model, add a table, add a
column row and a relation row in the editors, Save YAML, Render, then
`./bench rebuild --no-generate`. Revert with `git checkout model/mybank.yaml build/`.
