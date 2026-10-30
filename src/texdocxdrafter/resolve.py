"""Stage 6: attach harvested citations, label kinds and numbers, figure files, converted math."""
from __future__ import annotations

from dataclasses import dataclass, field

from .audit import Audit
from .auxreader import AuxLabel
from .parser.model import (
    Bibliography, DisplayMath, Document, Figure, Heading, Image, ListItem, Para, Ref, Table, walk_tree,
)


@dataclass
class LabelInfo:
    key: str
    kind: str  # section table figure subfigure equation item para
    level: int = 0
    number: str = ""  # from .aux; may be empty
    page: str = ""
    loc: tuple | None = None
    bookmark: str = ""
    heading: str = ""  # para labels: the label of the enclosing heading, whose number LaTeX prints


def collect_labels(doc: Document, aux: dict[str, AuxLabel], audit: Audit) -> dict[str, LabelInfo]:
    labels: dict[str, LabelInfo] = {}
    last_heading = [""]

    def add(key, kind, level=0, loc=None):
        if not key:
            return
        if key in labels:
            audit.add("label", f"label '{key}' defined twice", loc)
            return
        a = aux.get(key)
        labels[key] = LabelInfo(key, kind, level, a.number if a else "", a.page if a else "", loc)
        if kind == "para":
            labels[key].heading = last_heading[0]

    def visit(n):
        if isinstance(n, Heading):
            add(n.label, "section", n.level, n.loc)
            if not n.starred:
                last_heading[0] = n.label or ""
        elif isinstance(n, Table):
            add(n.label, "table", 0, n.loc)
        elif isinstance(n, Figure):
            add(n.label, "figure", 0, n.loc)
        elif isinstance(n, Image):
            add(n.label, "subfigure", 0, n.loc)
        elif isinstance(n, DisplayMath):
            for ln in n.lines:
                add(ln.label, "equation", 0, ln.loc)
        elif isinstance(n, ListItem):
            add(n.label, "item", 0, n.loc)
        elif isinstance(n, Para):
            for k in n.labels:
                if k not in labels:
                    add(k, "para", 0, n.loc)

    walk_tree(doc, visit)
    # list items own their label; a Para inside registers 'para' only if not already item
    return labels


def check_refs(doc: Document, labels: dict[str, LabelInfo], audit: Audit) -> None:
    def visit(n):
        if isinstance(n, Ref):
            for k in n.keys:
                if k not in labels:
                    audit.add("label", f"reference to undefined label '{k}' (shown as ??)", n.loc)

    walk_tree(doc, visit)


@dataclass
class Resolved:
    doc: Document
    labels: dict[str, LabelInfo]
    aux: dict[str, AuxLabel] = field(default_factory=dict)
    harvest_ok: bool = False


def resolve(doc: Document, pf, audit: Audit, harvest=None) -> Resolved:
    from .auxreader import read_aux
    from .harvest import apply_harvest

    issues: list[str] = []
    aux = read_aux(pf.aux, [pf.srcdir] + list(pf.allow_dirs), issues=issues)
    for issue in issues:
        audit.add("label", issue)
    labels = collect_labels(doc, aux, audit)
    check_refs(doc, labels, audit)
    ok = False
    if harvest is not None and (harvest.cites or harvest.bibs):
        apply_harvest(doc, harvest, audit)
        ok = harvest.ok
    return Resolved(doc, labels, aux, ok)
