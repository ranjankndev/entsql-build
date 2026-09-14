"""Shared test data."""

from pathlib import Path

from benchlib.config import Paths


def make_paths(root: Path) -> Paths:
    """Paths laid out like bench.toml, under a temporary root."""
    return Paths(
        root=root,
        model=root / "model" / "mybank.yaml",
        extra_sql=root / "model" / "extra.sql",
        samples=root / "model" / "samples",
        build=root / "build",
        versions=root / "versions",
        data=root / "data",
        checks=root / "checks" / "generated",
        llm_logs=root / "logs" / "llm",
    )


SAMPLE_YAML = """\
schema: mybank
version: 3
seed: 20240901
tables:
  SEG_LKP:
    description: Customer segments
    columns:
      - {name: SEG_CD, type: char(2), pk: true}
      - {name: SEG_DESC, type: varchar(40), nullable: false}
    rows: 3
    generation:
      SEG_CD: {expr: "['RE', 'SM', 'CO'][i]"}
      SEG_DESC: {faker: word}
  CUST_MSTR:
    description: Customer master, one row per customer
    columns:
      - {name: CUST_ID,   type: integer,        pk: true}
      - {name: CUST_NM,   type: varchar(80),    nullable: false, description: legal name}
      - {name: SEG_CD,    type: char(2),        description: "segment code, see SEG_LKP"}
      - {name: OPEN_DT,   type: date}
      - {name: STAT_CD,   type: char(1),        default: "'A'", check: "STAT_CD IN ('A','C','S')"}
    rows: 20
    generation:
      CUST_ID: {seq: 1}
      CUST_NM: {faker: company}
      SEG_CD:  {ref: SEG_LKP.SEG_CD, null_rate: 0.1}
      OPEN_DT: {date: ["2015-01-01", "2024-12-31"]}
      STAT_CD: {choice: {A: 0.8, C: 0.15, S: 0.05}}
  ACCT:
    columns:
      - {name: ACCT_ID, type: bigint, pk: true}
      - {name: CUST_ID, type: integer, nullable: false}
      - {name: SEG_CD, type: char(2)}
      - {name: BAL, type: "numeric(12,2)", nullable: false}
    rows: 50
    generation:
      ACCT_ID: {seq: 1000}
      CUST_ID: {ref: CUST_MSTR.CUST_ID, dist: zipf}
      SEG_CD: {copy: {from: CUST_MSTR.SEG_CD, via: CUST_ID}}
      BAL: {decimal: [0, 5000, 2]}
  TXN:
    columns:
      - {name: TXN_ID, type: bigint, pk: true}
      - {name: ACCT_ID, type: bigint, nullable: false}
      - {name: AMT, type: "numeric(12,2)", nullable: false}
      - {name: NOTE, type: text}
    rows: 200
    generation:
      TXN_ID: {seq: 1}
      ACCT_ID: {ref: ACCT.ACCT_ID}
      AMT: {int: [-500, 500]}
      NOTE: {expr: "'big' if abs(row['AMT']) > 400 else None"}
relations:
  - {from: CUST_MSTR.SEG_CD, to: SEG_LKP.SEG_CD, declared: false, kind: lookup}
  - {from: ACCT.CUST_ID,     to: CUST_MSTR.CUST_ID, declared: true, kind: parent}
  - {from: TXN.ACCT_ID,      to: ACCT.ACCT_ID,      declared: false, kind: parent,
     note: "dropped FK on purpose, ERP style"}
"""
