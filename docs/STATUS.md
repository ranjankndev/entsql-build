# Status

Last update: 2026-11-06. Branch `claude/text-to-sql-benchmark-setup-efzs2y`,
commit `3b13e3d`.

## Where the project is

**Phase 1 is complete.** The workbench is built, tested and pushed. Steps P0 to
P6 of `docs/PLAN.md` are done. Every acceptance test in section 7 of the plan
passed. `docs/BUILD_LOG.md` holds the output of each test.

The next work is not code. The next work is data modelling.

| Item | State |
|---|---|
| P0 Scaffold: config, database connections, CLI | Done |
| P1 Model core: YAML model, DDL output, diagram, DDL import | Done |
| P2 Rebuild: one transaction, build stamp, `status` | Done |
| P3 Model page in the app | Done. One manual test in a browser is open. |
| P4 Sample rows and the LLM providers | Done. One test with a real provider is open. |
| P5 Generator | Done |
| P6 Checks, the Checks page and the SQL page | Done |
| Phase 2 | Not started. This is correct. |

Tests: 101 unit tests pass. 4 database tests pass when the database is
available.

## What is in the database now

| Property | Value |
|---|---|
| Database | `benchdata`, schema `mybank` |
| Model version | 2 |
| Tables | 7 |
| Rows | 520,017 |
| Seed | 20240901 |

The seven tables are `SEG_LKP`, `BRNCH`, `PROD_LKP`, `CUST_MSTR`, `ACCT`,
`TXN` and `ACCT_BAL_MTH`.

The model has six relations. Three have a declared foreign key. Three have no
declared foreign key. The three without a key are the interesting ones for the
benchmark: two lookups and one parent relation.

Three tables have hand-written rows in `model/samples/`: `SEG_LKP`, `BRNCH`
and `PROD_LKP`.

## What is open

Two items only. `docs/PENDING.md` holds the detail.

1. **Test the Model page in a browser.** The automatic test ran with no
   browser. Start the app, open the Model page, add a table and a column, save,
   and render. This test is yours.
2. **Test a sample fill with a real LLM.** No key exists yet. Put a key in
   `.env`. Then run the command in `docs/PENDING.md`.

Neither item stops the work.

## What to do next

The workbench is ready. The next work is to grow the schema.

1. Do the browser test.
2. Add tables to `mybank` until the schema is complete. The target is 20 to 40
   tables for one domain.
3. Add the enterprise patterns: history tables, snapshot tables, a fiscal
   calendar and more lookup tables. Phase 2 adds the generators for these
   patterns. Write the tables first. Then decide what the generator needs.
4. Write the first questions and their correct SQL answers.
5. Build the phase 2 parts when the schema is stable.

## Where to read more

| Document | Content |
|---|---|
| `docs/ARCHITECTURE.md` | The parts of the system and the daily loop. |
| `docs/PLAN.md` | The phase 1 plan and the phase 2 list. |
| `docs/BUILD_LOG.md` | The output of each acceptance test. |
| `docs/PENDING.md` | The open items. |
| `CLAUDE.md` | The rules for Claude Code. |
| `README.md` | The setup steps. |
