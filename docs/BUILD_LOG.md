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
