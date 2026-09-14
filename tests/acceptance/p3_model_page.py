"""P3 acceptance through the real Model page (Streamlit AppTest). WRITES model/mybank.yaml and build/: run, check, then git checkout those files.

Run: PYTHONPATH=. .venv/bin/python tests/acceptance/p3_model_page.py
"""
from streamlit.testing.v1 import AppTest
from benchlib.config import REPO_ROOT

def button(at, label):
    return next(b for b in at.button if b.label == label)

def problems(at):
    return [e.value for e in at.exception] + [e.value for e in at.error]

at = AppTest.from_file(str(REPO_ROOT / "app/pages/1_Model.py"), default_timeout=60)
at.run()
print("1 page loaded, tables:", at.radio(key="selected_table").options, problems(at))

at.text_input(key="new_table_name").input("ATM")
button(at, "Add table").click()
at.run()
print("2 after Add table: selected =", at.radio(key="selected_table").value, "| caption:", at.caption[0].value, problems(at))

at.text_input(key="form:description:ATM").input("Cash machines, one row per ATM")
at.session_state["columns:ATM"] = {"edited_rows": {}, "deleted_rows": [], "added_rows": [
    {"name": "BRNCH_ID", "type": "integer", "pk": False, "nullable": False},
    {"name": "INSTALL_DT", "type": "date", "pk": False, "nullable": True},
]}
at.session_state["relations"] = {"edited_rows": {}, "deleted_rows": [], "added_rows": [
    {"from": "ATM.BRNCH_ID", "to": "BRNCH.BRNCH_ID", "declared": False, "kind": "parent", "note": "no FK, ERP style"},
]}
print("3 edits injected into the editors; the Save click runs in the same rerun, as the browser sends them")

button(at, "Save YAML").click()
at.run()
print("4 after Save YAML:", [s.value for s in at.success], problems(at))

button(at, "Render").click()
at.run()
print("5 after Render:", [s.value for s in at.success], problems(at))
