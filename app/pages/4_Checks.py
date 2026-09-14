"""Checks page: run the structural checks as the read-only role and show failing rows."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make benchlib importable under streamlit run

import pandas as pd
import psycopg
import streamlit as st

from benchlib import checks
from benchlib.config import load_config
from benchlib.model import ModelError

st.set_page_config(page_title="Checks", layout="wide")
config = load_config()

st.title("Checks")
st.caption("Primary key uniqueness, NOT NULL and orphans for every relation, declared or not. Regenerated from the model on every run.")

if st.button("Run checks", type="primary"):
    try:
        with st.spinner("Running checks..."):
            st.session_state.check_report = checks.check_database(config.paths, config.db)
    except ModelError as exc:
        st.error("\n".join([str(exc), *(f"- {error}" for error in exc.errors)]))
    except psycopg.Error as exc:
        st.error(f"Database error: {str(exc).strip()}")

report = st.session_state.get("check_report")
if report is not None:
    failed = report.failed
    if failed:
        st.error(f"{len(failed)} of {len(report.results)} checks failed")
    else:
        st.success(f"All {len(report.results)} checks passed")
    summary = pd.DataFrame(
        [
            {
                "check": result.check.name,
                "status": "ok" if result.passed else ("error" if result.error else "FAIL"),
                "failing rows": result.failing_rows,
            }
            for result in report.results
        ]
    )
    st.dataframe(summary, hide_index=True)
    for result in failed:
        with st.expander(result.check.name, expanded=True):
            st.code(result.check.sql, language="sql")
            if result.error:
                st.error(result.error)
            else:
                st.caption(f"{result.failing_rows} failing rows, first {len(result.rows)} shown")
                st.dataframe(pd.DataFrame(result.rows, columns=result.columns), hide_index=True)
