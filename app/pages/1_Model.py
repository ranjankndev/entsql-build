"""Model page: tables, columns, generation specs and relations; Save YAML, Render, Commit version."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make benchlib importable under streamlit run

import datetime as dt
import subprocess

import streamlit as st

from benchlib import build, diagram, editor, versioning
from benchlib.config import load_config
from benchlib.dialects import get_dialect
from benchlib.model import ModelError, load_model

EDITOR_KEY_PREFIXES = ("form:", "columns:", "generation:", "relations")

st.set_page_config(page_title="Model", layout="wide")
config = load_config()
dialect = get_dialect(config.db.dialect)


def show_errors(message: str, errors: list[str]) -> None:
    st.error("\n".join([message, *(f"- {error}" for error in errors)]))


def reset_editors() -> None:
    """Drop widget state so the editors show the model again instead of stale edits."""
    for key in [key for key in st.session_state if str(key).startswith(EDITOR_KEY_PREFIXES)]:
        del st.session_state[key]


def reload_model() -> None:
    st.session_state.model = load_model(config.paths.model)
    reset_editors()


if "model" not in st.session_state:
    try:
        reload_model()
    except ModelError as exc:
        show_errors(str(exc), exc.errors)
        st.stop()

model = st.session_state.model
st.title("Model")
unsaved = model != load_model(config.paths.model) if config.paths.model.exists() else True
st.caption(
    f"{config.paths.model.relative_to(config.paths.root)} · schema {model.schema} · version {model.version}"
    + (" · **unsaved changes**" if unsaved else "")
)
if message := st.session_state.pop("flash", None):
    st.success(message)

names = list(model.tables)
if pending := st.session_state.pop("pending_selection", None):
    st.session_state.selected_table = pending
if st.session_state.get("selected_table") not in names:
    st.session_state.pop("selected_table", None)

left, middle, right = st.columns([1, 3, 2], gap="medium")

with left:
    st.subheader("Tables")
    selected = st.radio("Table", names, key="selected_table", label_visibility="collapsed") if names else None
    new_name = st.text_input("New table name", key="new_table_name")
    if st.button("Add table"):
        updated, errors = editor.add_table(model, new_name)
        if errors:
            show_errors("Cannot add table", errors)
        else:
            st.session_state.model = updated
            st.session_state.pending_selection = new_name.strip()
            reset_editors()
            st.rerun()
    if selected is not None and st.button(f"Delete {selected}"):
        st.session_state.model = editor.delete_table(model, selected)
        reset_editors()
        st.rerun()
    if st.button("Reload from disk"):
        reload_model()
        st.rerun()

with middle:
    if selected is None:
        st.info("Add a table to start.")
    else:
        table = model.tables[selected]
        st.subheader(selected)
        name = st.text_input("Name", table.name, key=f"form:name:{selected}")
        description = st.text_input("Description", table.description or "", key=f"form:description:{selected}")
        rows = st.number_input("Rows to generate", min_value=0, value=table.rows, step=1, key=f"form:rows:{selected}")
        st.markdown("**Columns**")
        columns_frame = st.data_editor(
            editor.column_frame(table),
            num_rows="dynamic",
            key=f"columns:{selected}",
            column_config={
                "pk": st.column_config.CheckboxColumn("pk"),
                "nullable": st.column_config.CheckboxColumn("nullable"),
            },
        )
        st.markdown("**Generation spec**, one row per column, YAML flow style such as `{seq: 1}`")
        generation_frame = st.data_editor(editor.generation_frame(table), num_rows="dynamic", key=f"generation:{selected}")

with right:
    st.subheader("Relations")
    relations_frame = st.data_editor(
        editor.relation_frame(model),
        num_rows="dynamic",
        key="relations",
        column_config={"declared": st.column_config.CheckboxColumn("declared")},
    )
    st.subheader("Diagram")
    st.caption("solid edge: declared foreign key · dashed edge: undeclared relation")
    st.graphviz_chart(diagram.dot_source(model))

st.divider()
save_area, render_area, commit_area = st.columns(3)

with save_area:
    if st.button("Save YAML", type="primary", disabled=selected is None):
        edit = editor.TableEdit(
            name=name,
            description=description,
            rows=int(rows),
            columns=editor.frame_records(columns_frame),
            generation=editor.frame_records(generation_frame),
        )
        updated, errors = editor.apply_edits(model, selected, edit, editor.frame_records(relations_frame))
        errors = errors or editor.save_checked(updated, config.paths)
        if errors:
            show_errors("Not saved", errors)
        else:
            st.session_state.model = updated
            st.session_state.pending_selection = name.strip()
            st.session_state.flash = f"Saved {config.paths.model.name}"
            reset_editors()
            st.rerun()

with render_area:
    if st.button("Render"):
        try:
            result = build.render(load_model(config.paths.model), config.paths, dialect)
            st.success(f"Rendered the saved YAML to {result.ddl.name} and {result.diagram.name}")
        except ModelError as exc:
            show_errors(str(exc), exc.errors)

with commit_area:
    commit_message = st.text_input("Commit message", key="commit_message")
    if st.button("Commit version"):
        try:
            if not commit_message.strip():
                raise ModelError("enter a commit message first")
            result = versioning.commit_version(config.paths, dialect, commit_message.strip(), dt.date.today())
            reload_model()
            st.session_state.flash = result.changelog_line
            st.rerun()
        except ModelError as exc:
            show_errors(str(exc), exc.errors)
        except subprocess.CalledProcessError as exc:
            show_errors("git failed", [" ".join(exc.cmd)])
