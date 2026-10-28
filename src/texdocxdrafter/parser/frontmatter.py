"""Title, author, date, keywords."""
from __future__ import annotations

from ..lexer import LatexMacroNode, arg_nodes, macro_args
from .core import strip_inlines
from .blocks import strip_bold
from .model import Footnote, LineBreak, Para, Text


class FrontMixin:
    def capture_front(self, n) -> None:
        name = n.macroname
        args = macro_args(n)
        if not args:
            return
        self.audit.count(f"\\{name}")
        if name == "title":
            self.doc.title = self.title_inlines(args[-1])
        elif name == "author":
            self.doc.authors = self.split_authors(args[-1])
        elif name == "date":
            self.doc.date = self.title_inlines(args[-1])
        elif name == "keywords":
            self.doc.keywords = strip_inlines(self.inlines(arg_nodes(args[-1])))
        elif name == "affil":
            self.doc.authors.append(strip_inlines(self.inlines(arg_nodes(args[-1]))))

    def title_inlines(self, arg) -> list:
        items = strip_inlines(self.inlines(arg_nodes(arg)))
        return strip_inlines(strip_bold(items))

    def split_authors(self, arg) -> list:
        nodes = self.prep(arg_nodes(arg))
        groups: list[list] = [[]]
        for x in nodes:
            if isinstance(x, LatexMacroNode) and x.macroname in ("and", "AND"):
                groups.append([])
            else:
                groups[-1].append(x)
        out = []
        for g in groups:
            inl = strip_inlines(self.inlines(g))
            if inl:
                out.append(inl)
        return out

    def front_blocks(self) -> list:
        doc = self.doc
        out: list = []
        if doc.title:
            out.append(Para(children=doc.title, style="title"))
        for a in doc.authors:
            out.append(Para(children=a, style="author"))
        if doc.date:
            out.append(Para(children=doc.date, style="date"))
        if doc.keywords:
            out.append(Para(children=[Text(text="Keywords: ")] + doc.keywords, style="keywords"))
        return out
