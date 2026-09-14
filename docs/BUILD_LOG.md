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
