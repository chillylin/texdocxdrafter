"""Semantic tree: parse a preflighted document into ``model.Document``."""
from __future__ import annotations

from .. import lexer
from ..audit import Audit
from ..expand import Expander
from ..preflight import Preflight
from ..stylemap import validate as validate_stylemap
from .blocks import BlockMixin, Flow
from .core import ParserBase
from .frontmatter import FrontMixin
from .inlines import InlineMixin
from .math import MathMixin
from .model import Document, Heading, Para, Table, Figure, ListBlock, Quote, to_json, walk_tree  # noqa: F401
from .tables import TableMixin


class Parser(InlineMixin, BlockMixin, TableMixin, MathMixin, FrontMixin, ParserBase):
    def __init__(self, pf: Preflight, audit: Audit | None = None, stylemap: dict | None = None):
        ex = Expander()
        super().__init__(pf, ex, audit or Audit(), stylemap or validate_stylemap({}))
        self.doc = Document(docclass=pf.docclass)

    def parse(self) -> Document:
        pf = self.pf
        text = pf.source.text
        text = self.ex.scan(text, pf.locate)
        pre = text[: len(pf.preamble)]
        body = text[pf.body_offset: pf.body_offset + len(pf.body)]
        # preamble: front matter only
        self.base = 0
        try:
            for n in self.ex.walk(pre):
                if isinstance(n, lexer.LatexMacroNode) and n.macroname in ("title", "author", "date", "keywords", "affil"):
                    self.capture_front(n)
        except Exception as exc:  # tolerant parsing should not raise, but the preamble can be exotic
            self.audit.add("other", f"preamble could not be fully parsed: {exc}")
        self.base = pf.body_offset
        nodes = self.ex.walk(body)
        flow = Flow()
        self.flow(self.prep(nodes), flow)
        flow.flush()
        self.doc.blocks = flow.out
        mark_first_paragraphs(self.doc.blocks)
        self.audit.info["citations"] = len(self.doc.cites)
        return self.doc


def mark_first_paragraphs(blocks: list) -> None:
    prev_heading = False
    for b in blocks:
        if isinstance(b, Para) and b.style is None:
            b.first = prev_heading
        elif isinstance(b, ListBlock):
            for it in b.items:
                mark_first_paragraphs(it.blocks)
        elif isinstance(b, Quote):
            mark_first_paragraphs(b.blocks)
        prev_heading = isinstance(b, Heading)


def parse_document(pf: Preflight, audit: Audit | None = None, stylemap: dict | None = None) -> Document:
    return Parser(pf, audit, stylemap).parse()
