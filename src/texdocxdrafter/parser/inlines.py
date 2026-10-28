"""Inline constructs: text, emphasis, links, footnotes, citations, references, inline math."""
from __future__ import annotations

from ..lexer import (
    LatexCharsNode, LatexEnvironmentNode, LatexGroupNode, LatexMacroNode, LatexMathNode, LatexSpecialsNode,
    arg_nodes, arg_text, macro_args, plain_text,
)
from .core import (
    ACCENT_MACROS, CITE_CMDS, DECLARATIONS, DROP_MACROS, MACRO_TEXT, REF_CMDS, SPECIALS, STYLED, TRANSPARENT,
    merge_text, norm_text,
)
from .model import (
    Cite, Footnote, InlineMath, Label, LineBreak, Link, Ref, Revision, Styled, Text,
)


class InlineMixin:
    in_footnote: int = 0

    # ---------------------------------------------------------------------------------------
    def inlines(self, nodes) -> list:
        """Convert a node list into inline objects."""
        nodes = self.prep(nodes)
        out: list = []
        i = 0
        while i < len(nodes):
            n = nodes[i]
            if isinstance(n, LatexMacroNode) and n.macroname in DECLARATIONS:
                kind = DECLARATIONS[n.macroname]
                rest = self.inlines(nodes[i + 1:])
                out.append(Styled(kind=kind, children=rest, loc=self.loc(n)))
                break
            self.inline_node(n, out)
            i += 1
        return merge_text(out)

    def text(self, s: str, node=None) -> Text:
        return Text(text=norm_text(s), loc=self.loc(node))

    # ---------------------------------------------------------------------------------------
    def inline_node(self, n, out: list) -> None:
        loc = self.loc(n)
        if isinstance(n, LatexCharsNode):
            out.append(self.text(n.chars, n))
        elif isinstance(n, LatexSpecialsNode):
            ch = n.specials_chars
            if ch == "&":
                out.append(Text(text="&", loc=loc))
            elif ch in SPECIALS:
                out.append(Text(text=SPECIALS[ch], loc=loc))
            else:
                t = self.accent_text(n)
                out.append(Text(text=t if t else ch, loc=loc))
        elif isinstance(n, LatexGroupNode):
            out.extend(self.inlines(n.nodelist))
        elif isinstance(n, LatexMathNode):
            out.append(InlineMath(latex=self.math_source(n), loc=loc))
        elif isinstance(n, LatexMacroNode):
            self.inline_macro(n, out)
        elif isinstance(n, LatexEnvironmentNode):
            out.append(self.raw_inline(n, "environment inside a paragraph"))
        else:
            out.append(self.raw_inline(n))

    def math_source(self, n) -> str:
        s = n.latex_verbatim()
        d = getattr(n, "delimiters", None)
        if d and s.startswith(d[0]) and s.endswith(d[1]) and len(s) >= len(d[0]) + len(d[1]):
            s = s[len(d[0]):len(s) - len(d[1])]
        return self.ex.expand_text(s.strip())

    # ---------------------------------------------------------------------------------------
    def inline_macro(self, n, out: list) -> None:
        name = n.macroname
        loc = self.loc(n)
        args = macro_args(n)
        if name in self.revisions:
            kind = self.revisions[name]
            self.audit.count(f"\\{name} (tracked {kind})")
            kids = self.inlines(arg_nodes(args[-1])) if args else []
            out.append(Revision(kind=kind, children=kids, macro=name, loc=loc))
            return
        if name in STYLED:
            if name == "textcolor":
                color = plain_text(arg_nodes(args[0])).strip() if args else ""
                body = arg_nodes(args[1]) if len(args) > 1 else []
                out.append(Styled(kind="color", arg=color, children=self.inlines(body), loc=loc))
                self.audit.add("direct-format", f"\\textcolor{{{color}}} becomes direct run formatting", loc, "info")
                return
            idx = -1
            kids = self.inlines(arg_nodes(args[idx])) if args else []
            kind = STYLED[name]
            if kind == "quote":
                out.append(Text(text="“", loc=loc))
                out.extend(kids)
                out.append(Text(text="”", loc=loc))
                return
            if kind in ("smallcaps", "underline", "strike", "superscript", "subscript"):
                self.audit.add("direct-format", f"\\{name} becomes direct run formatting", loc, "info")
            out.append(Styled(kind=kind, children=kids, loc=loc))
            return
        if name in TRANSPARENT:
            if name == "texorpdfstring":
                out.extend(self.inlines(arg_nodes(args[0])) if args else [])
            elif name in ("hyperlink",):
                out.extend(self.inlines(arg_nodes(args[1])) if len(args) > 1 else [])
            elif name == "hypertarget":
                out.extend(self.inlines(arg_nodes(args[1])) if len(args) > 1 else [])
            else:
                if args:
                    out.extend(self.inlines(arg_nodes(args[-1])))
            return
        if name in CITE_CMDS:
            out.append(self.make_cite(n))
            return
        if name in REF_CMDS:
            keys = [k.strip() for k in plain_text(arg_nodes(args[-1])).split(",") if k.strip()] if args else []
            out.append(Ref(cmd=name, keys=keys, loc=loc))
            return
        if name == "label":
            out.append(Label(key=plain_text(arg_nodes(args[0])).strip() if args else "", loc=loc))
            return
        if name in ("footnote", "thanks", "footnotetext"):
            body = arg_nodes(args[-1]) if args else []
            self.in_footnote += 1
            try:
                blocks = self.blocks(body)
            finally:
                self.in_footnote -= 1
            out.append(Footnote(blocks=blocks, loc=loc))
            return
        if name in ("url",):
            u = arg_text(args[0]).strip() if args else ""
            out.append(Link(url=u, children=[Text(text=u, loc=loc)], loc=loc))
            return
        if name == "href":
            u = arg_text(args[0]).strip() if args else ""
            out.append(Link(url=u, children=self.inlines(arg_nodes(args[1])) if len(args) > 1 else [], loc=loc))
            return
        if name == "hyperref":
            out.extend(self.inlines(arg_nodes(args[-1])) if args else [])
            return
        if name in ("\\", "newline"):
            out.append(LineBreak(loc=loc))
            return
        if name == "verb":
            chars = args[0].chars if args and hasattr(args[0], "chars") else ""
            out.append(Styled(kind="code", children=[Text(text=chars, loc=loc)], loc=loc))
            return
        if name == "ensuremath":
            out.append(InlineMath(latex=self.ex.expand_text(arg_text(args[0])) if args else "", loc=loc))
            return
        if name in ("parbox", "raisebox", "resizebox", "scalebox", "adjustbox"):
            if args:
                out.extend(self.inlines(arg_nodes(args[-1])))
            return
        if name == "today":
            out.append(Text(text=self.today(), loc=loc))
            return
        if name in ("footnotemark", "item"):
            if name == "item":
                out.append(self.raw_inline(n, "\\item outside a list"))
            else:
                self.drop(n)
            return
        if name in MACRO_TEXT:
            out.append(Text(text=MACRO_TEXT[name], loc=loc))
            return
        if name in ACCENT_MACROS:
            t = self.accent_text(n)
            out.append(Text(text=t if t else "", loc=loc))
            return
        if name in DROP_MACROS or name in self.ex.mathops:
            self.drop(n)
            return
        if name == "par":
            return  # handled by the block flow; harmless here
        t = self.accent_text(n)
        if t and len(t) <= 4:
            out.append(Text(text=t, loc=loc))
            return
        out.append(self.raw_inline(n))

    # ---------------------------------------------------------------------------------------
    def make_cite(self, n) -> Cite:
        c = Cite(index=len(self.doc.cites), cmd=n.macroname, raw=n.latex_verbatim(), section=self.refsection,
                 in_footnote=bool(self.in_footnote), loc=self.loc(n))
        self.doc.cites.append(c)
        self.audit.count(f"\\{n.macroname}")
        return c
