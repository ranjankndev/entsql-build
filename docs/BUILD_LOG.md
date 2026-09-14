# Build log

Exact acceptance commands and their output per step (CLAUDE.md: report the
exact command and its output). Open questions are in `docs/PENDING.md`.

## P1 Model core (2026-09-14)

Starter DDL: `model/import/mybank.sql` (7 tables, 3 declared FKs). After import,
seed 20240901 and three undeclared relations were added to the YAML
(CUST_MSTR.SEG_CD -> SEG_LKP, ACCT.PROD_CD -> PROD_LKP, TXN.ACCT_ID -> ACCT).

```
$ ./bench model import model/import/mybank.sql
wrote model/mybank.yaml: 7 tables, 3 relations
exit=0

$ ./bench model render
wrote build/mybank.sql
wrote build/mybank.svg
exit=0

$ { BEGIN; CREATE SCHEMA p1_scratch; SET LOCAL search_path TO p1_scratch; build/mybank.sql; \dt+; ROLLBACK; } | psql -h 127.0.0.1 -U bench_owner -d benchdata -v ON_ERROR_STOP=1
 p1_scratch | acct         | table | bench_owner | ... | Accounts, one customer owns many
 p1_scratch | acct_bal_mth | table | bench_owner | ... | Month-end balance per account
 p1_scratch | brnch        | table | bench_owner | ... | Bank branches
 p1_scratch | cust_mstr    | table | bench_owner | ... | Customer master, one row per customer
 p1_scratch | prod_lkp     | table | bench_owner | ... | Account product lookup
 p1_scratch | seg_lkp      | table | bench_owner | ... | Customer segment lookup
 p1_scratch | txn          | table | bench_owner | ... | Account transactions; ACCT_ID has no foreign key, ERP style
(7 rows)
 acct         | FOREIGN KEY (brnch_id) REFERENCES brnch(brnch_id)
 acct         | FOREIGN KEY (cust_id) REFERENCES cust_mstr(cust_id)
 acct_bal_mth | FOREIGN KEY (acct_id) REFERENCES acct(acct_id)
exit=0   (scratch schemas left afterwards: 0)

$ SVG check (xml parse of build/mybank.svg)
root {http://www.w3.org/2000/svg}svg nodes 7 edges 3

$ ./bench model commit -m init
- v001 (2026-09-14) init; added tables: SEG_LKP, BRNCH, PROD_LKP, CUST_MSTR, ACCT, TXN, ACCT_BAL_MTH
committed model v001: model/mybank.yaml, build/mybank.sql, build/mybank.svg, versions/mybank_v001.yaml, versions/mybank_v001.sql, versions/CHANGELOG.md
exit=0   (git commit e50e428 "model v001: init")

$ .venv/bin/python -m unittest
Ran 43 tests  OK
```

## P2 Rebuild (2026-09-14)

```
$ ./bench status
no build yet
exit=0

$ ./bench rebuild --no-generate
table        | data rows | sample rows | rows in db
-------------+-----------+-------------+-----------
SEG_LKP      | 0         | 0           | 0
BRNCH        | 0         | 0           | 0
PROD_LKP     | 0         | 0           | 0
CUST_MSTR    | 0         | 0           | 0
ACCT         | 0         | 0           | 0
TXN          | 0         | 0           | 0
ACCT_BAL_MTH | 0         | 0           | 0
rebuilt schema mybank on db profile local: 0 rows, model version 1
exit=0

$ ./bench status
OK: built 2026-09-14T19:13:30+00:00 on db profile local, schema mybank, model version 1, 0 rows in 7 tables
exit=0

$ ./bench sql "select table_name from information_schema.tables where table_schema = 'mybank' order by 1"
acct, acct_bal_mth, brnch, cust_mstr, prod_lkp, seg_lkp, txn   (7 rows, as bench_read)

$ ./bench sql "select conname, pg_get_constraintdef(oid) as def from pg_constraint where conrelid = 'cust_mstr'::regclass and contype = 'c'"
cust_mstr_stat_cd_check | CHECK ((stat_cd = ANY (ARRAY['A'::bpchar, 'C'::bpchar, 'S'::bpchar])))

--- deliberate typo in model/mybank.yaml: check "stat_cdx IN ('A', 'C', 'S')"
$ ./bench status
STALE: built 2026-09-14T19:13:30+00:00 on db profile local, schema mybank, model version 1, 0 rows in 7 tables
changed since that build:
  model/mybank.yaml (changed)
exit=1

$ ./bench model render
exit=0

$ ./bench rebuild --no-generate
bench rebuild: build/mybank.sql: column "stat_cdx" does not exist
LINE 6:     STAT_CD char(1) DEFAULT 'A' CHECK (stat_cdx IN ('A', 'C'...
HINT:  Perhaps you meant to reference the column "cust_mstr.stat_cd".
  - statement: CREATE TABLE CUST_MSTR (...)
exit=1

$ ./bench sql "...same constraint query..."
cust_mstr_stat_cd_check | CHECK ((stat_cd = ANY (ARRAY['A'::bpchar, 'C'::bpchar, 'S'::bpchar])))   <- previous schema intact
$ ./bench sql "select count(*) as tables from information_schema.tables where table_schema = 'mybank'"
7

--- git checkout model/mybank.yaml build/mybank.sql build/mybank.svg
$ ./bench status
OK: built 2026-09-14T19:13:30+00:00 ...
exit=0

$ BENCH_INTEGRATION=1 .venv/bin/python -m unittest tests.test_rebuild_integration
test_bad_csv_rolls_back ... ok
test_rebuild_rollback_and_samples ... ok   (sample upsert on PK, rollback on CHECK typo, bench_read query)
```

## P3 Model UI (2026-09-14)

Driven through the real `app/pages/1_Model.py` with Streamlit AppTest
(`tests/acceptance/p3_model_page.py`). AppTest has no data_editor element, so
the column and relation rows were injected as data_editor widget state in the
same rerun as the Save click, which is what the browser sends.

```
$ PYTHONPATH=. .venv/bin/python tests/acceptance/p3_model_page.py
1 page loaded, tables: ['SEG_LKP', 'BRNCH', 'PROD_LKP', 'CUST_MSTR', 'ACCT', 'TXN', 'ACCT_BAL_MTH'] []
2 after Add table: selected = ATM | caption: model/mybank.yaml · schema mybank · version 1 · **unsaved changes** []
3 edits injected into the editors; the Save click runs in the same rerun, as the browser sends them
4 after Save YAML: ['Saved mybank.yaml'] []
5 after Render: ['Rendered the saved YAML to mybank.sql and mybank.svg'] []

$ git diff model/mybank.yaml
+  ATM:
+    description: Cash machines, one row per ATM
+    columns:
+      - {name: ATM_ID, type: integer, pk: true}
+      - {name: BRNCH_ID, type: integer, nullable: false}
+      - {name: INSTALL_DT, type: date}
+    rows: 0
+  - {from: ATM.BRNCH_ID, to: BRNCH.BRNCH_ID, declared: false, kind: parent, note: 'no FK, ERP style'}

diagram: ['ATM -> BRNCH [label=parent style=dashed]'], SVG edge ATM->BRNCH stroke-dasharray="5,2"

$ ./bench rebuild --no-generate
SEG_LKP 5 sample rows, BRNCH 5, PROD_LKP 7, ATM 0, ... rebuilt schema mybank on db profile local: 17 rows, model version 1
exit=0

$ ./bench sql "select column_name, data_type, is_nullable from information_schema.columns where table_schema = 'mybank' and table_name = 'atm' order by ordinal_position"
atm_id      | integer | NO
brnch_id    | integer | NO
install_dt  | date    | YES
(3 rows)
```

The demo ATM table was then reverted (`git checkout model/mybank.yaml build/`)
so the starter model stays as committed in v001.
First attempt note: injecting editor state in one rerun and clicking Save in a
later rerun saved only the new table, because AppTest does not resend state
for widgets it does not model. A browser resends it; see PENDING item 5.

## P4 Samples and LLM (2026-09-14)

No real LLM is reachable (no .env, no Ollama), see PENDING item 1. The
acceptance commands were run through the real CLI against a local stub
OpenAI-compatible endpoint (scripted replies, `[llm.stub]` profile added to
bench.toml for the run and removed afterwards; the stub rows were deleted).

```
$ ./bench samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3
bench samples fill: LLM profile none has no provider; choose one with --llm or BENCH_LLM
exit=1

$ ./bench --llm stub samples fill CUST_MSTR "3 customers, one with a NULL segment" -n 3
appended 3 of 3 rows to model/samples/CUST_MSTR.csv:
CUST_ID | CUST_NM                 | SEG_CD | OPEN_DT    | STAT_CD
--------+-------------------------+--------+------------+--------
900001  | Nordlicht Logistik GmbH | SM     | 2019-04-12 | A
900002  | Anna Weber              | NULL   | 2021-11-03 | A
900003  | Stadtwerke Kiel         | PS     | 2016-01-20 | C
exit=0

$ ./bench --llm stub samples fill CUST_MSTR "1 customer in segment ZZ" -n 1
appended 0 of 1 rows to model/samples/CUST_MSTR.csv
rejected row 1: {"CUST_ID": 900004, "CUST_NM": "Zeta Holdings", "SEG_CD": "ZZ", "OPEN_DT": "2022-02-02", "STAT_CD": "A"}
  - SEG_CD: 'ZZ' does not exist in SEG_LKP.SEG_CD (existing values include: CO, PB, PS, RE, SM)
exit=1
(model/samples/CUST_MSTR.csv unchanged)

$ ./bench rebuild --no-generate
CUST_MSTR    | 0 data rows | 3 sample rows | 3 rows in db   ... 20 rows, model version 1
$ ./bench sql "select cust_id, cust_nm, seg_cd, stat_cd from cust_mstr order by cust_id"
900001 | Nordlicht Logistik GmbH | SM | A
900002 | Anna Weber              | NULL | A
900003 | Stadtwerke Kiel         | PS | C

logs/llm: 2 files, each with request (url, body incl. response_format) and raw response.
```

## P5 Generator (2026-09-14)

Generation specs for the starter model: CUST_MSTR 20k, ACCT 40k, TXN 400k,
ACCT_BAL_MTH 60k rows (520k); lookups stay sample-only (rows 0).

```
$ ./bench gen
table        | rows   | file
-------------+--------+----------------------
SEG_LKP      | 0      | -
BRNCH        | 0      | -
PROD_LKP     | 0      | -
CUST_MSTR    | 20000  | data/CUST_MSTR.csv
ACCT         | 40000  | data/ACCT.csv
TXN          | 400000 | data/TXN.csv
ACCT_BAL_MTH | 60000  | data/ACCT_BAL_MTH.csv
generated 520000 rows in 7 tables in 3.9 s
exit=0
$ sha256sum data/*.csv
d41c58e240b454965c093f8228809bff2b6c8fe70cec6fbc813f05b10782ae2e  data/ACCT_BAL_MTH.csv
65c1c0d7feaa17fa763e9a77bf56743116e0e687dfc78c9725f61e7735c43b35  data/ACCT.csv
156d5a570b0b2bc45afe40eda38f5476683dbfdbb855e68ad46586973705d541  data/CUST_MSTR.csv
224f70722c24859a0b65b373401fb85f6557bd92f416466b21fdb034ce012039  data/TXN.csv

$ ./bench gen            (second run, 3.7 s)
$ diff <(sha256sum after first gen) <(sha256sum after second gen)
byte-identical: yes

--- every relation: child values exist in the parent (data + samples)
ACCT.CUST_ID         -> CUST_MSTR.CUST_ID declared=True  child values= 40000 distinct parents used= 8275 orphans=0
ACCT.BRNCH_ID        -> BRNCH.BRNCH_ID    declared=True  child values= 40000 distinct parents used=    5 orphans=0
ACCT_BAL_MTH.ACCT_ID -> ACCT.ACCT_ID      declared=True  child values= 60000 distinct parents used= 5000 orphans=0
CUST_MSTR.SEG_CD     -> SEG_LKP.SEG_CD    declared=False child values= 18909 distinct parents used=    5 orphans=0
ACCT.PROD_CD         -> PROD_LKP.PROD_CD  declared=False child values= 40000 distinct parents used=    7 orphans=0
TXN.ACCT_ID          -> ACCT.ACCT_ID      declared=False child values=400000 distinct parents used=33005 orphans=0

$ ./bench rebuild        (first attempt, before the fix below)
bench rebuild: loading data/ACCT.csv: insert or update on table "acct" violates foreign key constraint "acct_brnch_id_fkey"
DETAIL:  Key (brnch_id)=(2) is not present in table "brnch".
exit=1   (rolled back; previous schema intact)
```

Fix: rebuild loaded all data before all samples, so a declared FK to a
sample-only parent (ACCT.BRNCH_ID -> BRNCH) failed. Rebuild now loads table by
table in generation order: data, then that table's samples (PENDING item 7).

```
$ BENCH_INTEGRATION=1 .venv/bin/python -m unittest tests.test_rebuild_integration
test_bad_csv_rolls_back ... ok
test_declared_foreign_key_to_sample_only_parent ... ok
test_rebuild_rollback_and_samples ... ok

$ ./bench rebuild
SEG_LKP      | 0      | 5 | 5
BRNCH        | 0      | 5 | 5
PROD_LKP     | 0      | 7 | 7
CUST_MSTR    | 20000  | 0 | 20000
ACCT         | 40000  | 0 | 40000
TXN          | 400000 | 0 | 400000
ACCT_BAL_MTH | 60000  | 0 | 60000
rebuilt schema mybank on db profile local: 520017 rows, model version 1, 4.9 s
exit=0

$ ./bench sql "select count(*) as txn_orphans from txn t where not exists (select 1 from acct a where a.acct_id = t.acct_id)"
0
$ ./bench sql "select txn_typ, count(*), min(txn_amt), max(txn_amt), count(desc_txt) as with_desc from txn group by txn_typ order by 1"
DP | 140540 | 1.04     | 4999.98 | 137804
FE | 40138  | -3000.00 | -1.00   | 39334
IN | 19593  | 1.16     | 4999.69 | 19238
TR | 60224  | -2999.97 | -1.08   | 59009
WD | 139505 | -2999.98 | -1.00   | 136738
$ ./bench sql "select acct_id, bal_mth, bal_amt from acct_bal_mth order by acct_id, bal_mth limit 3"
100000001 | 2024-01-31 | 64175.47
100000001 | 2024-02-29 | 113626.87
100000001 | 2024-03-31 | -206.36
```

Model change versioned and rebuilt:
```

$ ./bench model commit -m starter generation specs, about 520k rows
- v002 (2026-09-14) starter generation specs, about 520k rows; no table or column changes
committed model v002: model/mybank.yaml, build/mybank.sql, build/mybank.svg, versions/mybank_v002.yaml, versions/mybank_v002.sql, versions/CHANGELOG.md
exit=0

$ ./bench rebuild
table        | data rows | sample rows | rows in db
-------------+-----------+-------------+-----------
SEG_LKP      | 0         | 5           | 5
BRNCH        | 0         | 5           | 5
PROD_LKP     | 0         | 7           | 7
CUST_MSTR    | 20000     | 0           | 20000
ACCT         | 40000     | 0           | 40000
TXN          | 400000    | 0           | 400000
ACCT_BAL_MTH | 60000     | 0           | 60000
rebuilt schema mybank on db profile local: 520017 rows, model version 2, 4.8 s
exit=0

$ ./bench status
OK: built 2026-09-14T19:38:25+00:00 on db profile local, schema mybank, model version 2, 520017 rows in 7 tables
exit=0
```
