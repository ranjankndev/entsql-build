"""SQL page: run a query as the read-only role with the model schema on the search path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make benchlib importable under streamlit run

import pandas as pd
import psycopg
import streamlit as st

from benchlib import sqlrun
from benchlib.config import load_config
from benchlib.model import ModelError

st.set_page_config(page_title="SQL", layout="wide")
config = load_config()

st.title("SQL")
st.caption(f"Runs as {config.db.read_user} on db profile {config.db.name}, read-only, model schema on the search path.")

query = st.text_area("Query", height=200, key="sql_query", placeholder="select * from cust_mstr limit 10")
if st.button("Run", type="primary", disabled=not query.strip()):
    try:
        st.session_state.sql_result = sqlrun.run_sql(config.paths, config.db, query)
        st.session_state.pop("sql_error", None)
    except (psycopg.Error, ModelError) as exc:
        st.session_state.sql_error = str(exc).strip()
        st.session_state.pop("sql_result", None)

if error := st.session_state.get("sql_error"):
    st.error(error)
if (result := st.session_state.get("sql_result")) is not None:
    st.caption(sqlrun.result_summary(result))
    if result.columns:
        st.dataframe(pd.DataFrame(result.rows, columns=result.columns), hide_index=True)
