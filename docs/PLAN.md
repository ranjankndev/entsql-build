# Text-to-SQL Benchmark Workbench - Phase 1 Plan

Single-user MVP for designing benchmark schemas, generating synthetic data and
checking it. One Python package, one CLI, one Streamlit UI. Local PostgreSQL in
Docker. Everything is plain files in git.

Phase 1 goal: edit a model of 20+ tables in a form-based UI or in YAML, rebuild
the database from it in one command, craft sample rows with LLM help, generate
a full dataset from a per-table spec, and run structural checks. Nothing else.

Deferred to phase 2 (do not build now): history/snapshot/fiscal-calendar
pattern generators, business-rule and answerability checks, gold-query
regression runner, SQLite export, VPS deployment, LLM plausibility review.

---

## 1. Environment

| Piece | Choice |
|---|---|
| OS | Windows 11. Toolchain runs inside WSL2 (Ubuntu). Same scripts later run unchanged on the Linux VPS. |
| Database | PostgreSQL 16 in Docker Desktop (WSL2 backend). `docker/compose.yaml`, port bound to `127.0.0.1:5432`. Roles from `docker/init/01-roles.sql`: `postgres`, `bench_owner`, `bench_read`. |
| Python | 3.12 in a venv inside WSL2. Packages: `psycopg[binary]`, `pyyaml`, `pglast`, `graphviz`, `faker`, `streamlit`, `anthropic`, `pandas`. System package: `graphviz` (`apt install graphviz`). |
| Editors | VS Code with Remote-WSL, Claude Code in the integrated terminal. DBeaver on Windows connects to `localhost:5432`. |
| LLM | Pluggable provider (section 8). Default: Anthropic Python SDK, model `claude-opus-5`, key in `ANTHROPIC_API_KEY`. Alternative: local Ollama over its HTTP API. |

Connection comes from a named profile in `bench.toml` (section 8). Local and
VPS differ only by profile. Password via `~/.pgpass`. No `docker exec`
anywhere in the code.

---

## 2. Repository layout

```
text2sql-benchmark/
├── CLAUDE.md
├── docs/PLAN.md                 # this file
├── bench.toml                   # DSNs, paths, seed
├── docker/compose.yaml, init/01-roles.sql
├── model/
│   ├── mybank.yaml              # SOURCE OF TRUTH for the schema
│   ├── extra.sql                # optional views/functions, appended verbatim
│   └── samples/<TABLE>.csv      # hand/LLM crafted rows, loaded after generated data
├── build/
│   ├── mybank.sql               # generated DDL (never hand-edited)
│   ├── mybank.svg               # generated diagram
│   └── .build.json              # build stamp
├── versions/                    # mybank_v001.yaml, mybank_v001.sql, CHANGELOG.md
├── data/<TABLE>.csv             # generated dataset
├── checks/generated/structural.sql
├── tools/bench.py               # CLI entry point
├── benchlib/                    # library used by CLI and UI
│   ├── config.py, db.py, model.py, importer.py, diagram.py,
│   ├── build.py, samples.py, gen.py, checks.py
│   ├── dialects/base.py, postgres.py        # DDL emit + load per engine (sqlite.py, mysql.py later)
│   └── llm/base.py, anthropic_provider.py, ollama_provider.py
└── app/
    ├── Home.py
    └── pages/1_Model.py 2_Data.py 3_Generate.py 4_Checks.py 5_SQL.py
```

---

## 3. Model file (`model/mybank.yaml`)

```yaml
schema: mybank
version: 3
seed: 20240901
tables:
  CUST_MSTR:
    description: Customer master, one row per customer
    columns:
      - {name: CUST_ID,   type: integer,        pk: true}
      - {name: CUST_NM,   type: varchar(80),    nullable: false, description: legal name}
      - {name: SEG_CD,    type: char(2),        description: segment code, see SEG_LKP}
      - {name: OPEN_DT,   type: date}
      - {name: STAT_CD,   type: char(1),        default: "'A'", check: "STAT_CD IN ('A','C','S')"}
    rows: 2000
    generation:
      CUST_ID: {seq: 1}
      CUST_NM: {faker: company}
      SEG_CD:  {ref: SEG_LKP.SEG_CD}
      OPEN_DT: {date: ["2015-01-01", "2024-12-31"]}
      STAT_CD: {choice: {A: 0.8, C: 0.15, S: 0.05}}
relations:
  - {from: CUST_MSTR.SEG_CD, to: SEG_LKP.SEG_CD, declared: false, kind: lookup}
  - {from: ACCT.CUST_ID,     to: CUST_MSTR.CUST_ID, declared: true, kind: parent}
  - {from: TXN.ACCT_ID,      to: ACCT.ACCT_ID,      declared: false, kind: parent,
     note: dropped FK on purpose, ERP style}
```

Rules:
- `type` is a raw PostgreSQL type string. `check` and `default` are raw SQL.
- Composite PK: list `pk: true` on each column; order is column order.
- `relations` is the only place joins are defined. `declared: true` emits a
  FOREIGN KEY; `declared: false` emits nothing but is used by the diagram,
  the generator (parent-before-child ordering, value sampling) and checks.
- `generation` per column, one of:
  `seq`, `faker: <provider>`, `choice: {value: weight}`, `int: [lo, hi]`,
  `decimal: [lo, hi, scale]`, `date: [from, to]`, `ref: TABLE.COL`
  (sample an existing parent value; `dist: uniform|zipf`),
  `copy: {from: TABLE.COL, via: LOCAL_COL}` (denormalised copy from parent),
  `expr: "<python expression over row dict>"`, `const: value`, `null_rate: 0.1`
  may be combined with any of the above.
- `rows` per table. Generation order = topological order over `relations`.

---

## 4. CLI (`./bench <cmd>`)

| Command | Does |
|---|---|
| `model import <file.sql>` | Parse DDL with `pglast` into `model/mybank.yaml` (tables, columns, PK, declared FKs as `declared: true` relations). One-time bootstrap. |
| `model render` | Validate YAML; write `build/mybank.sql` and `build/mybank.svg`. Exit non-zero on validation errors (unknown ref, duplicate column, relation column missing). |
| `model commit -m "msg"` | `render`, bump `version`, copy YAML and SQL to `versions/`, append CHANGELOG line with added/removed tables and columns vs previous version, `git commit`. |
| `rebuild [--no-generate]` | Run `gen` unless disabled, then ONE transaction: `DROP SCHEMA mybank CASCADE; CREATE SCHEMA; run build/mybank.sql; run model/extra.sql; COPY data/*.csv in generation order; upsert model/samples/*.csv on PK; run checks/generated/structural.sql (each statement must return 0 rows); COMMIT`. On any error roll back. Write `build/.build.json` (hashes of yaml, extra.sql, samples, data, timestamp, row counts). `lock_timeout` 10s; terminate other `bench_owner` sessions first. |
| `status` | Compare current file hashes with `.build.json`; print OK or STALE with changed files. |
| `samples fill <TABLE> "<instruction>" [-n 5]` | Call LLM with table definition, relations, existing sample rows, up to 20 parent key values per ref, and the instruction; receive rows as structured JSON; validate columns, types, PK uniqueness, ref existence; append to `model/samples/<TABLE>.csv`. |
| `gen [TABLE ...]` | Deterministic generation from `generation` specs, seed from YAML, parents first, writes `data/<TABLE>.csv`. Prints row counts and time. |
| `check` | Regenerate `checks/generated/structural.sql` from the model (PK uniqueness, NOT NULL, orphan checks for every relation declared or not) and run them against the DB. Print failures with first 10 rows. |
| `sql "<query>"` | Run as `bench_read`, print result. |

Acceptance for each command is in section 7.

---

## 5. UI (Streamlit, `streamlit run app/Home.py`)

All pages call `benchlib` functions. No logic lives in the pages.

| Page | Widgets |
|---|---|
| Home | Build status banner (OK / STALE), version, table count, row counts. Buttons: Render, Rebuild. |
| 1 Model | Left: table list with add/delete. Middle: selected table form (name, description, `st.data_editor` for columns: name, type, pk, nullable, default, check, description), rows, and a `st.data_editor` for that table's generation spec. Right: relation editor (`st.data_editor` over `relations`, columns from/to/declared/kind/note) and the diagram (`st.graphviz_chart`, dashed edges for undeclared). Buttons: Save YAML, Render, Commit version (with message). |
| 2 Data | Table picker. `st.data_editor` over `model/samples/<TABLE>.csv`. Text box + "Ask LLM" (calls `samples fill`). Validate and Save. Read-only preview of first 50 generated rows for the same table. |
| 3 Generate | Per-table spec shown as YAML in a text area, Save. "Generate" button (all or selected). Row counts table. |
| 4 Checks | "Run checks" button, results table, expandable failing rows. |
| 5 SQL | Text area, Run (as `bench_read`), result grid, row count, elapsed. |

---

## 6. Build order and Claude Code prompts

Run one prompt per session step. After each, run the acceptance command
yourself before moving on.

**P0 Scaffold**
> Read docs/PLAN.md and CLAUDE.md. Create the repository layout from section 2, `bench.toml` with the `[db.local]`, `[db.vps]` and `[llm.*]` profiles from section 8, `benchlib/config.py` (loads bench.toml, selects profiles from `BENCH_DB` and `BENCH_LLM` env vars with defaults `local` and `anthropic`), `benchlib/db.py` (connections for the owner and read roles of the selected profile using ~/.pgpass), `tools/bench.py` with argparse subcommands from section 4 as stubs, a `./bench` wrapper, `requirements.txt`, and `docker/compose.yaml` plus `docker/init/01-roles.sql` for PostgreSQL 16 bound to 127.0.0.1:5432 with roles postgres, bench_owner (owner of database benchdata) and bench_read (read-only). Add a README with the WSL2 setup steps.

**P1 Model core**
> Implement `benchlib/model.py` (load, validate, save the YAML from PLAN section 3, dataclasses for Table, Column, Relation), `benchlib/dialects/base.py` (the `Dialect` interface from section 8) and `benchlib/dialects/postgres.py` (YAML to PostgreSQL DDL: CREATE TABLE with PK, NOT NULL, DEFAULT, CHECK, then FOREIGN KEY constraints only for declared relations, then COMMENT ON for descriptions; plus `load_csv` using COPY), `benchlib/diagram.py` (Graphviz SVG: record node per table, solid edges declared, dashed undeclared, label = kind), and `benchlib/importer.py` (pglast-based DDL import to YAML). Wire `bench model import|render|commit`. Include unit tests that round-trip a small DDL through import and emit.

**P2 Rebuild**
> Implement `benchlib/build.py` and `bench rebuild`, `bench status` exactly as PLAN section 4 describes: one transaction, generation-order COPY, samples upsert on PK, structural checks inside the transaction, rollback on any failure, build stamp. Add `bench sql`.

**P3 Model UI**
> Implement `app/Home.py` and `app/pages/1_Model.py` per PLAN section 5, calling only benchlib functions. Saving writes the YAML and re-renders the diagram.

**P4 Samples and LLM**
> Implement `benchlib/llm/base.py` (the `LLMProvider` interface from section 8), `benchlib/llm/anthropic_provider.py` (Anthropic Python SDK, model claude-opus-5, structured output as a JSON schema for a list of rows matching the table's columns) and `benchlib/llm/ollama_provider.py` (Ollama native `/api/chat` with `format` set to the same JSON schema, `stream: false`, using `urllib.request`, no new dependency). Then `benchlib/samples.py` with validation as in PLAN section 4; it takes a provider and never imports a provider module directly. Wire `bench samples fill` and `app/pages/2_Data.py`.

**P5 Generator**
> Implement `benchlib/gen.py`: the column generators from PLAN section 3, seeded `random.Random` and `faker.Faker` with the YAML seed, parents-before-children ordering over all relations, `ref` sampling from already generated parent values, `copy` via a local ref column, `expr` evaluated over the row dict, `null_rate`. Write `data/<TABLE>.csv`. Wire `bench gen` and `app/pages/3_Generate.py`. Make it fast enough for 20 tables and 500k total rows in under a minute.

**P6 Checks**
> Implement `benchlib/checks.py`: generate `checks/generated/structural.sql` from the model (PK uniqueness, NOT NULL, orphans for every relation whether declared or not, each as a SELECT that returns offending rows), run them, report. Wire `bench check`, `app/pages/4_Checks.py` and `app/pages/5_SQL.py`. Rebuild must use the same generated checks.

---

## 7. Acceptance per step

| Step | Check |
|---|---|
| P0 | `docker compose up -d` then `./bench status` prints "no build yet" without error. `psql -h 127.0.0.1 -U bench_owner benchdata -c 'select 1'` works. |
| P1 | `./bench model import mybank.sql` then `./bench model render` produces DDL that `psql` accepts into an empty scratch schema, and the SVG opens. `./bench model commit -m init` creates `versions/mybank_v001.*`. |
| P2 | `./bench rebuild --no-generate` succeeds with empty data; a deliberate typo in a CHECK leaves the previous schema intact. `./bench status` reports STALE after editing the YAML. |
| P3 | Add a table and an undeclared relation in the UI, Save, Render; the diagram shows a dashed edge; `./bench rebuild --no-generate` creates the table. |
| P4 | `./bench samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3` appends 3 valid rows; a row with a non-existent SEG_CD is rejected with a clear message. |
| P5 | `./bench gen` twice produces byte-identical CSVs. Child tables reference only existing parents. |
| P6 | `./bench rebuild` runs checks and fails if a sample row references a missing parent; passes otherwise. |

---

## 8. Pluggability (design once, do not revisit)

Three seams exist from P0 so that later changes are configuration or one new
file, never a redesign.

**Database target: profiles in `bench.toml`.**
```toml
[db.local]
dialect = "postgres"
host = "127.0.0.1"
port = 5432
dbname = "benchdata"
owner_user = "bench_owner"
read_user = "bench_read"

[db.vps]                      # reached through an SSH tunnel on the laptop, or directly on the VPS
dialect = "postgres"
host = "127.0.0.1"
port = 5433
dbname = "benchdata"
owner_user = "bench_owner"
read_user = "bench_read"

[llm.anthropic]
provider = "anthropic"
model = "claude-opus-5"

[llm.ollama]
provider = "ollama"
model = "llama3.1:8b"
base_url = "http://127.0.0.1:11434"
```
`BENCH_DB=vps ./bench rebuild` targets the VPS. Nothing else changes. The
CLI also accepts `--db vps` and `--llm ollama`.

**SQL dialect: `benchlib/dialects/base.py`.** All engine-specific SQL and
loading goes through one interface; the rest of the library only calls it.
```python
class Dialect(Protocol):
    name: str
    def create_schema_sql(self, schema: str) -> str: ...
    def drop_schema_sql(self, schema: str) -> str: ...
    def table_ddl(self, table: Table, relations: list[Relation]) -> str: ...
    def fk_ddl(self, relation: Relation) -> str: ...
    def comment_sql(self, table: Table) -> str: ...
    def type_map(self, pg_type: str) -> str: ...      # identity for postgres
    def load_csv(self, conn, schema: str, table: Table, path: Path) -> int: ...
    def upsert_csv(self, conn, schema: str, table: Table, path: Path) -> int: ...
    def quote(self, ident: str) -> str: ...
```
Phase 1 implements `postgres.py` only. SQLite and MySQL are one file each
later. Column types in the YAML stay PostgreSQL types; other dialects map them
in `type_map`. Known limits to accept up front: SQLite has no schemas (the
dialect prefixes table names or uses one file per schema), no CHECK on
`char(n)` length, and different date functions; MySQL has no transactional
DDL, so a MySQL rebuild cannot be atomic and must drop and recreate into a
scratch database, then rename. These are dialect-file concerns, not design
changes.

**LLM provider: `benchlib/llm/base.py`.**
```python
class LLMProvider(Protocol):
    name: str
    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...
```
One method. Callers (`samples.py`, later spec proposals) build the prompt and
the JSON schema, call `complete_json`, and validate the result with code.
`anthropic_provider.py` uses structured outputs; `ollama_provider.py` uses
Ollama's `format` field with the same schema. Provider selection is in
`config.py` from the `[llm.*]` profile. No provider import outside `benchlib/llm/`.

**Debuggability rules.** Every command prints the SQL it is about to run when
`--verbose` is set. Every failing check prints the query and the first rows.
Every LLM call writes the request and raw response to `logs/llm/<timestamp>.json`.
Modules have no hidden state; functions take a `Model` and a connection and
return values.

## 9. Phase 2 (later)

History, snapshot and fiscal-calendar pattern generators; business-rule checks
(`rules:` section); gold-query files, EXPLAIN check, result fingerprints and
regression; `benchlib/dialects/sqlite.py` and SQLite export with PG-vs-SQLite comparison; `mysql.py` if ever needed; LLM plausibility
review; multi-domain layout; VPS deployment behind SSH.
