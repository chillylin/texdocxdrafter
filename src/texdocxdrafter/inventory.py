"""Stage 3: construct census → style map skeleton."""
from __future__ import annotations

from collections import Counter

from .parser.model import (
    Bibliography, DisplayMath, Document, Figure, Footnote, Heading, ListBlock, Para, Quote, Table, Verbatim, Cite,
    InlineMath, Styled, Link, walk_tree,
)


def census(doc: Document, sm: dict) -> list[tuple[str, int, str]]:
    pm, cm = sm["paragraph"], sm["character"]
    c: Counter = Counter()
    style_of: dict[str, str] = {}

    def add(name: str, style: str, n: int = 1):
        c[name] += n
        style_of[name] = style

    def visit(n):
        if isinstance(n, Heading):
            add(f"heading level {n.level}" + (" (unnumbered)" if n.starred else ""), f"{pm['heading']} {n.level}")
        elif isinstance(n, Para):
            key = n.style or ("first_paragraph" if n.first else "body")
            add(f"paragraph ({key})", pm.get(key, key))
        elif isinstance(n, ListBlock):
            key = {"itemize": "bullet_list", "enumerate": "number_list", "description": "description_list"}[n.kind]
            add(f"list ({n.kind})", pm[key])
        elif isinstance(n, Quote):
            add("quote", pm["quote"])
        elif isinstance(n, Verbatim):
            add("verbatim", pm["code"])
        elif isinstance(n, Table):
            add("table", f"{sm['options']['table_style']} + {pm['caption_table']}")
        elif isinstance(n, Figure):
            add("figure", f"{pm['figure']} + {pm['caption_figure']}")
        elif isinstance(n, DisplayMath):
            add("display math", pm["equation"])
        elif isinstance(n, Bibliography):
            add("bibliography", pm["bibliography"])
        elif isinstance(n, Footnote):
            add("footnote", pm["footnote"])
        elif isinstance(n, Cite):
            add("citation", cm["citation"])
        elif isinstance(n, InlineMath):
            add("inline math", "OMML")
        elif isinstance(n, Styled) and n.kind in ("emph", "strong", "code"):
            add(f"character: {n.kind}", cm[n.kind])
        elif isinstance(n, Link):
            add("link", cm["link"])

    walk_tree(doc, visit)
    return sorted(((k, v, style_of[k]) for k, v in c.items()), key=lambda t: (-t[1], t[0]))


def render(rows: list[tuple[str, int, str]]) -> str:
    out = [f"{'construct':38} {'count':>6}  Word style", "-" * 72]
    for name, n, style in rows:
        out.append(f"{name:38} {n:6d}  {style}")
    return "\n".join(out)
