"""Title, author, date, keywords."""
from __future__ import annotations

from ..lexer import LatexMacroNode, arg_nodes, macro_args, plain_text
from .core import strip_inlines
from .blocks import strip_bold
from .model import Footnote, LineBreak, Para, Styled, Text


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
            opt = args[0] if len(args) > 1 else None
            mark = plain_text(arg_nodes(opt)).strip() if opt is not None else ""
            new = self.split_authors(args[-1])
            if self.doc.authors and (mark or self.authblk()):
                # authblk: one \author per person, several in a row
                self.doc.authors.extend(new)
                self.doc.author_marks.extend([mark] * len(new))
            else:
                self.doc.authors = new
                self.doc.author_marks = [mark] * len(new)
        elif name == "date":
            self.doc.date = self.title_inlines(args[-1])
        elif name == "keywords":
            self.doc.keywords = strip_inlines(self.inlines(arg_nodes(args[-1])))
        elif name == "affil":
            opt = args[0] if len(args) > 1 else None
            mark = plain_text(arg_nodes(opt)).strip() if opt is not None else ""
            self.doc.affils.append((mark, strip_inlines(self.inlines(arg_nodes(args[-1])))))

    def authblk(self) -> bool:
        pf = getattr(self, "pf", None)
        return bool(pf) and any(n == "authblk" for n, _ in getattr(pf, "packages", []))

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
        marks = list(doc.author_marks) + [""] * (len(doc.authors) - len(doc.author_marks))
        if doc.affils or any(marks):
            # authblk: all authors on one line with affiliation marks, then one line per affiliation
            line: list = []
            for i, (a, mk) in enumerate(zip(doc.authors, marks)):
                if i:
                    line.append(Text(text=", " if i < len(doc.authors) - 1 else " and "))
                line.extend(a)
                if mk:
                    line.append(Styled(kind="superscript", children=[Text(text=mk)]))
            if line:
                out.append(Para(children=line, style="author"))
            for mk, inl in doc.affils:
                head = [Styled(kind="superscript", children=[Text(text=mk)])] if mk else []
                out.append(Para(children=head + list(inl), style="author"))
        else:
            for a in doc.authors:
                out.append(Para(children=a, style="author"))
        if doc.date:
            out.append(Para(children=doc.date, style="date"))
        if doc.keywords:
            out.append(Para(children=[Text(text="Keywords: ")] + doc.keywords, style="keywords"))
        return out
