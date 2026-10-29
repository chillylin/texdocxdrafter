"""Semantic document tree. Every node carries ``loc = (file, line)`` of its source."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

Loc = tuple[str, int]


@dataclass
class Node:
    loc: Loc | None = field(default=None, kw_only=True)


# ---- inlines ------------------------------------------------------------------------------

@dataclass
class Text(Node):
    text: str = ""


@dataclass
class Styled(Node):
    """kind: emph strong code smallcaps underline strike superscript subscript color sans roman quote"""
    kind: str = ""
    children: list = field(default_factory=list)
    arg: str | None = None  # colour name for kind == "color"


@dataclass
class Link(Node):
    url: str = ""
    children: list = field(default_factory=list)


@dataclass
class Footnote(Node):
    blocks: list = field(default_factory=list)


@dataclass
class Cite(Node):
    index: int = -1
    cmd: str = ""
    raw: str = ""
    section: int = 0
    in_footnote: bool = False
    runs: list | None = None  # filled by resolve: list[Run]


@dataclass
class Ref(Node):
    cmd: str = "ref"  # ref eqref pageref autoref cref Cref vref nameref
    keys: list = field(default_factory=list)
    kind: str | None = None  # filled by resolve
    # filled by resolve: one entry per key (kind, number text)
    targets: list = field(default_factory=list)


@dataclass
class InlineMath(Node):
    latex: str = ""


@dataclass
class LineBreak(Node):
    pass


@dataclass
class Label(Node):
    key: str = ""


@dataclass
class RawInline(Node):
    latex: str = ""


# ---- blocks -------------------------------------------------------------------------------

@dataclass
class Heading(Node):
    level: int = 1
    children: list = field(default_factory=list)
    starred: bool = False
    label: str | None = None
    appendix: bool = False
    toc_title: str | None = None


@dataclass
class Para(Node):
    children: list = field(default_factory=list)
    style: str | None = None  # style-map key override: abstract, center, keywords, title, author, date, quote ...
    labels: list = field(default_factory=list)
    first: bool = False  # first paragraph after a heading


@dataclass
class ListItem(Node):
    blocks: list = field(default_factory=list)
    term: list | None = None  # description list term
    label: str | None = None


@dataclass
class ListBlock(Node):
    kind: str = "itemize"  # itemize enumerate description
    items: list = field(default_factory=list)
    label_spec: dict | None = None  # {"fmt": "decimal", "pattern": "O%1.", "bold": True}
    level: int = 0


@dataclass
class Quote(Node):
    blocks: list = field(default_factory=list)


@dataclass
class Verbatim(Node):
    text: str = ""


@dataclass
class Cell(Node):
    blocks: list = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    vmerge: bool = False  # continuation of a rowspan above
    align: str = "l"
    header: bool = False
    border_bottom: bool = False


@dataclass
class Row(Node):
    cells: list = field(default_factory=list)
    rule_above: bool = False
    rule_below: bool = False


@dataclass
class Table(Node):
    rows: list = field(default_factory=list)
    colspec: list = field(default_factory=list)  # one of l c r per column
    colweights: list = field(default_factory=list)  # relative widths (p{} ratios), 0 if unknown
    header_rows: int = 0
    caption: list | None = None
    short_caption: list | None = None
    label: str | None = None
    notes: list = field(default_factory=list)  # list of Para
    floating: bool = True
    wide: bool = False


@dataclass
class Image(Node):
    name: str = ""
    options: dict = field(default_factory=dict)
    caption: list | None = None  # subfigure caption
    label: str | None = None
    path: str | None = None  # filled by resolve
    width_in: float | None = None
    tikz: str | None = None  # tikzpicture source, rendered to PNG by tikz.render_tikz


@dataclass
class Figure(Node):
    images: list = field(default_factory=list)
    caption: list | None = None
    short_caption: list | None = None
    label: str | None = None
    notes: list = field(default_factory=list)
    floating: bool = True
    wide: bool = False  # figure*: spans both columns


@dataclass
class EqLine(Node):
    latex: str = ""
    numbered: bool = False
    label: str | None = None


@dataclass
class DisplayMath(Node):
    env: str = "equation"
    lines: list = field(default_factory=list)  # list[EqLine]
    latex: str = ""  # whole body


@dataclass
class Bibliography(Node):
    options: dict = field(default_factory=dict)
    section: int = 0
    title: list | None = None  # filled by harvest
    entries: list = field(default_factory=list)  # filled by resolve: list[BibEntry]
    manual: bool = False
    bibtex: str | None = None  # \\bibliography{files}: rendered by BibTeX instead of biblatex


@dataclass
class BibEntry(Node):
    key: str = ""
    runs: list = field(default_factory=list)


@dataclass
class Revision(Node):
    """Tracked change from review markup such as \\add{...}/\\del{...}; kind is "ins" or "del"."""
    kind: str = "ins"
    children: list = field(default_factory=list)
    macro: str = ""


@dataclass
class RevisionBlock(Node):
    """A tracked change whose argument holds paragraphs, headings or floats."""
    kind: str = "ins"
    blocks: list = field(default_factory=list)
    macro: str = ""


@dataclass
class RawBlock(Node):
    latex: str = ""
    reason: str = ""


@dataclass
class PageBreak(Node):
    pass


@dataclass
class Run:
    """A harvested run of citation text. fmt is a frozenset of: i b sup sub."""
    text: str
    fmt: frozenset = frozenset()
    url: str | None = None


@dataclass
class Document(Node):
    title: list | None = None
    authors: list = field(default_factory=list)  # list of inline lists
    author_marks: list = field(default_factory=list)  # authblk affiliation marks, parallel to authors ('' if none)
    affils: list = field(default_factory=list)  # authblk affiliations: (mark, inline list)
    date: list | None = None
    abstract: list = field(default_factory=list)  # blocks
    keywords: list | None = None
    blocks: list = field(default_factory=list)
    cites: list = field(default_factory=list)  # all Cite nodes in document order
    labels: dict = field(default_factory=dict)  # key -> (kind, loc)
    docclass: str = "article"
    has_maketitle: bool = False


def to_json(obj: Any) -> Any:
    """Serialisable view of a tree (for ``texdocxdrafter parse`` and golden snapshots)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d: dict[str, Any] = {"type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            v = getattr(obj, f.name)
            if f.name == "loc":
                if v:
                    d["loc"] = f"{v[0]}:{v[1]}"
                continue
            if (v is None or v is False or (isinstance(v, (list, dict, str)) and len(v) == 0)) \
                    and f.name not in ("text", "level", "index"):
                continue
            d[f.name] = to_json(v)
        return d
    if isinstance(obj, (list, tuple)):
        return [to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, frozenset):
        return sorted(obj)
    return obj


def walk_tree(obj, fn, _seen=None):
    """Call fn(node) for every dataclass node in the tree, depth first (iterative: no recursion limit)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if dataclasses.is_dataclass(cur) and not isinstance(cur, type):
            fn(cur)
            stack.extend(getattr(cur, f.name) for f in reversed(dataclasses.fields(cur)))
        elif isinstance(cur, (list, tuple)):
            stack.extend(reversed(cur))
        elif isinstance(cur, dict):
            stack.extend(reversed(list(cur.values())))
