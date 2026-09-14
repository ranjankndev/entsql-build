"""Graphviz diagram of the model: a record node per table, solid edges for declared relations, dashed for undeclared."""

from __future__ import annotations

import graphviz

from benchlib.model import Model, Table

RECORD_SPECIAL_CHARACTERS = '\\{}|<>"'


def record_escape(text: str) -> str:
    for character in RECORD_SPECIAL_CHARACTERS:
        text = text.replace(character, "\\" + character)
    return text


def table_label(table: Table) -> str:
    """Record label: table name on top, one left-aligned line per column (rankdir LR stacks fields vertically)."""
    lines = "".join(
        record_escape(f"{column.name} : {column.type}{' PK' if column.pk else ''}") + "\\l" for column in table.columns
    )
    return f"{record_escape(table.name)}|{lines}"


def build_graph(model: Model) -> graphviz.Digraph:
    graph = graphviz.Digraph(
        name=model.schema,
        graph_attr={"rankdir": "LR", "fontname": "Helvetica"},
        node_attr={"shape": "record", "fontname": "Helvetica", "fontsize": "10"},
        edge_attr={"fontname": "Helvetica", "fontsize": "9"},
    )
    for table in model.tables.values():
        graph.node(table.name, label=table_label(table))
    for relation in model.relations:
        style = "solid" if relation.declared else "dashed"
        graph.edge(relation.from_table, relation.to_table, label=relation.kind, style=style)
    return graph


def dot_source(model: Model) -> str:
    """DOT text, also used by the Streamlit model page."""
    return build_graph(model).source


def render_svg(model: Model) -> bytes:
    """SVG bytes; needs the Graphviz dot binary (apt install graphviz)."""
    return build_graph(model).pipe(format="svg")
