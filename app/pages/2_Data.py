"""Data page: edit sample rows, ask an LLM for more (only with a provider), preview generated data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make benchlib importable under streamlit run

import streamlit as st

from benchlib import editor, samples
from benchlib.config import load_config
from benchlib.llm.base import LLMError, get_provider
from benchlib.model import ModelError, load_model
from benchlib.samples import SamplesError

st.set_page_config(page_title="Data", layout="wide")
config = load_config()


def show_rejected(rejected: list[samples.RejectedRow]) -> None:
    lines = [f"Row {item.number} rejected: " + "; ".join(item.problems) for item in rejected]
    st.error("\n\n".join(lines))


st.title("Sample data")
try:
    model = load_model(config.paths.model)
except ModelError as exc:
    st.error("\n".join([str(exc), *exc.errors]))
    st.stop()

table_name = st.selectbox("Table", list(model.tables), key="data_table")
table = model.tables[table_name]
editor_key = f"samples:{table_name}"
st.caption(
    f"model/samples/{table_name}.csv · hand-crafted rows, upserted on the primary key after generated data · "
    "empty cell = NULL"
)
for message in st.session_state.pop("data_flash", []):
    st.success(message)

try:
    provider = get_provider(config.llm, config.paths.llm_logs)
    provider_problem = None
except LLMError as exc:
    provider, provider_problem = None, str(exc)

try:
    edited = st.data_editor(samples.sample_frame(config.paths, table), num_rows="dynamic", key=editor_key)
except SamplesError as exc:
    st.error(str(exc))
    st.stop()

if st.button("Validate and Save", type="primary"):
    try:
        result = samples.save_records(config.paths, model, table_name, editor.frame_records(edited))
    except SamplesError as exc:
        st.error(str(exc))
    else:
        if result.rejected:
            show_rejected(result.rejected)
        else:
            st.session_state.data_flash = [f"Saved {len(result.accepted)} rows to {table_name}.csv"]
            st.session_state.pop(editor_key, None)
            st.rerun()

st.subheader("Ask LLM")
if provider is not None:
    instruction = st.text_area("Instruction", key=f"instruction:{table_name}", placeholder="3 customers, one with a NULL segment")
    count = st.number_input("Rows", min_value=1, max_value=50, value=5, step=1, key=f"count:{table_name}")
    if st.button("Ask LLM"):
        try:
            with st.spinner(f"Asking {config.llm.name}..."):
                result = samples.fill_samples(config.paths, model, table_name, instruction, int(count), provider)
        except (SamplesError, LLMError) as exc:
            st.error(str(exc))
        else:
            if result.rejected:
                show_rejected(result.rejected)
            st.session_state.data_flash = [f"Appended {len(result.appended)} of {result.requested} requested rows"]
            if not result.rejected:
                st.session_state.pop(editor_key, None)
                st.rerun()
            st.session_state.pop(editor_key, None)
elif provider_problem is not None:
    st.warning(f"LLM profile {config.llm.name} is not usable: {provider_problem}")
else:
    st.caption("LLM features are off (profile none). Start the app with BENCH_LLM=<profile> to enable them.")

st.subheader("Generated data preview")
st.caption(f"First 50 rows of data/{table_name}.csv")
st.dataframe(samples.data_preview(config.paths, table), hide_index=True)
