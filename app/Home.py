"""Home: build status, model summary, Render and Rebuild."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make benchlib importable under streamlit run

import datetime as dt

import pandas as pd
import psycopg
import streamlit as st

from benchlib import build
from benchlib.build import BuildError
from benchlib.config import load_config
from benchlib.dialects import get_dialect
from benchlib.gen import GenError
from benchlib.model import ModelError, load_model

st.set_page_config(page_title="Benchmark workbench", layout="wide")
config = load_config()
dialect = get_dialect(config.db.dialect)


def show_problem(message: str, lines: list[str]) -> None:
    st.error("\n\n".join([message, *(f"`{line}`" for line in lines)]))


st.title("Text-to-SQL benchmark workbench")
st.caption(
    f"db profile **{config.db.name}** ({config.db.host}:{config.db.port}/{config.db.dbname}) · "
    f"llm profile **{config.llm.name}**"
)

status = build.build_status(config.paths)
banner = {"ok": st.success, "stale": st.warning, "none": st.info}[status.state]
banner(build.status_text(status).replace("\n", "  \n"))

try:
    model = load_model(config.paths.model)
except ModelError as exc:
    show_problem(str(exc), exc.errors)
    st.stop()

version_metric, tables_metric, rows_metric = st.columns(3)
version_metric.metric("Model version", model.version)
tables_metric.metric("Tables", len(model.tables))
rows_metric.metric("Rows in last build", sum(status.stamp.row_counts.values()) if status.stamp else "-")

if status.stamp:
    counts = pd.DataFrame(list(status.stamp.row_counts.items()), columns=["table", "rows"])
    st.dataframe(counts, hide_index=True)

generate = st.checkbox("Generate data before rebuild", value=True)
render_button, rebuild_button = st.columns(2)

if render_button.button("Render"):
    try:
        result = build.render(model, config.paths, dialect)
        st.success(f"wrote {result.ddl.name} and {result.diagram.name}")
    except ModelError as exc:
        show_problem(str(exc), exc.errors)

if rebuild_button.button("Rebuild", type="primary"):
    try:
        with st.spinner("Rebuilding in one transaction..."):
            result = build.rebuild(config.paths, config.db, dialect, dt.datetime.now(dt.UTC), generate=generate)
        for warning in result.warnings:
            st.warning(warning)
        st.success(f"rebuilt schema {result.stamp.schema}: {sum(result.stamp.row_counts.values())} rows")
        st.rerun()
    except ModelError as exc:
        show_problem(str(exc), exc.errors)
    except BuildError as exc:
        show_problem(f"Rebuild rolled back: {exc}", exc.details)
    except GenError as exc:
        show_problem("Data generation failed, nothing was changed in the database", [str(exc)])
    except psycopg.Error as exc:
        show_problem("Database error", [str(exc).strip()])
