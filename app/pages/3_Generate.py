"""Generate page: per-table spec as YAML, Save, Generate (all or selected), row counts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make benchlib importable under streamlit run

import time

import pandas as pd
import streamlit as st

from benchlib import editor, gen
from benchlib.config import load_config
from benchlib.gen import GenError
from benchlib.model import ModelError, load_model

st.set_page_config(page_title="Generate", layout="wide")
config = load_config()

st.title("Generate")
try:
    model = load_model(config.paths.model)
except ModelError as exc:
    st.error("\n".join([str(exc), *exc.errors]))
    st.stop()

st.caption(f"seed {model.seed} · the same YAML always produces byte-identical CSVs in data/")
for message in st.session_state.pop("gen_flash", []):
    st.success(message)

left, right = st.columns([3, 2], gap="medium")

with left:
    table_name = st.selectbox("Table", list(model.tables), key="gen_table")
    spec_key = f"spec:{table_name}"
    text = st.text_area("rows and generation spec (YAML)", editor.spec_yaml(model.tables[table_name]), height=360, key=spec_key)
    if st.button("Save spec"):
        updated, errors = editor.apply_spec_yaml(model, table_name, text)
        errors = errors or editor.save_checked(updated, config.paths)
        if errors:
            st.error("\n".join(["Not saved", *(f"- {error}" for error in errors)]))
        else:
            st.session_state.gen_flash = [f"Saved the spec of {table_name}"]
            st.session_state.pop(spec_key, None)
            st.rerun()

with right:
    selected = st.multiselect("Tables to generate (none selected = all)", list(model.tables), key="gen_selected")
    if st.button("Generate", type="primary"):
        started = time.perf_counter()
        try:
            with st.spinner("Generating..."):
                result = gen.generate(config.paths, model, selected or None)
        except ModelError as exc:
            st.error("\n".join([str(exc), *(f"- {error}" for error in exc.errors)]))
        except GenError as exc:
            st.error(str(exc))
        else:
            elapsed = time.perf_counter() - started
            st.session_state.gen_flash = [f"Generated {sum(result.rows.values())} rows in {len(result.rows)} tables in {elapsed:.1f} s"]
            st.rerun()
    counts = gen.data_row_counts(config.paths, model)
    summary = pd.DataFrame(
        [{"table": name, "rows in spec": table.rows, "rows in data CSV": counts[name]} for name, table in model.tables.items()]
    )
    st.dataframe(summary, hide_index=True)
