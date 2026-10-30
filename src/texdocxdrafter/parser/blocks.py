"""Block constructs: paragraphs, sectioning, lists, quotes, verbatim, floats, bibliography, front matter."""
from __future__ import annotations

import re

from ..lexer import (
    LatexCharsNode, LatexEnvironmentNode, LatexGroupNode, LatexMacroNode, LatexMathNode, LatexSpecialsNode,
    arg_nodes, arg_text, macro_args, plain_text,
)
from .core import (
    DECLARATIONS, DROP_MACROS, MATH_ENVS, RUN_IN, SECTIONING, merge_text, norm_text, strip_inlines,
)
from .model import (
    BibEntry, Bibliography, Figure, Heading, Image, Label, ListBlock, ListItem, PageBreak, Para, Quote,
    RawBlock, RevisionBlock, Run, Styled, Table, Text, Verbatim, Footnote,
)

TRANSPARENT_ENVS = {
    "flushleft", "flushright", "raggedright", "minipage", "spacing", "singlespace", "doublespace",
    "onehalfspace", "small", "footnotesize", "large", "samepage", "adjustwidth", "multicols", "refsection_",
    "frame", "landscape", "sloppypar", "nopagebreak",
}
FLOATS_TABLE = {"table", "table*", "sidewaystable", "ltxtable"}
FLOATS_FIGURE = {"figure", "figure*", "sidewaysfigure", "wrapfigure", "wrapfigure*"}
TABULARS = {"tabular", "tabular*", "tabularx", "tabulary", "longtable", "array_", "xtabular", "supertabular"}
PAGE_BREAKS = {"newpage", "clearpage", "cleardoublepage", "pagebreak"}
LIST_ENVS = {"itemize", "enumerate", "description"}
TIKZ_ENVS = {"tikzpicture"}
SIZES = {"tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large", "LARGE", "huge", "Huge"}


class Flow:
    """Accumulates inlines into paragraphs and finished blocks."""

    def __init__(self, style: str | None = None, out: list | None = None):
        self.out: list = [] if out is None else out
        self.cur: list = []
        self.style = style

    def flush(self) -> None:
        items = strip_inlines(merge_text(self.cur))
        self.cur = []
        labels = [x for x in items if isinstance(x, Label)]
        content = [x for x in items if not isinstance(x, Label)]
        if not content:
            if labels:
                self._orphan_labels(labels)
            return
        loc = next((c.loc for c in content if getattr(c, "loc", None)), None)
        self.out.append(Para(children=content, style=self.style, labels=[l.key for l in labels], loc=loc))

    def _orphan_labels(self, labels) -> None:
        last = self.out[-1] if self.out else None
        if isinstance(last, Heading) and not last.label:
            last.label = labels[0].key
        elif isinstance(last, Para) and not last.labels:
            last.labels = [l.key for l in labels]

    def has_content(self) -> bool:
        return any(not (isinstance(x, Text) and not x.text.strip()) for x in self.cur)


class BlockMixin:
    list_depth = 0
    refsection = 0
    appendix = False

    # ---------------------------------------------------------------------------------------
    def blocks(self, nodes, style: str | None = None) -> list:
        flow = Flow(style)
        self.flow(self.prep(nodes), flow)
        flow.flush()
        return flow.out

    def has_block(self, nodes, depth: int = 0) -> bool:
        for n in nodes:
            if isinstance(n, LatexEnvironmentNode):
                return True
            if isinstance(n, LatexChars) and re.search(r"\n[ \t]*\n", n.chars):
                return True
            if isinstance(n, LatexMacroNode) and (n.macroname in SECTIONING or n.macroname in RUN_IN
                                                  or n.macroname == "par"):
                return True
            if isinstance(n, LatexMathNode) and n.displaytype == "display":
                return True
            if isinstance(n, LatexGroupNode) and depth < 100 and self.has_block(n.nodelist, depth + 1):
                return True
        return False

    # ---------------------------------------------------------------------------------------
    def flow(self, nodes: list, ctx: Flow) -> None:
        i = 0
        while i < len(nodes):
            n = nodes[i]
            if isinstance(n, LatexCharsNode):
                pos = 0
                for k, part in enumerate(re.split(r"(\n[ \t]*\n\s*)", n.chars)):
                    if k % 2:
                        ctx.flush()
                    elif part.strip() or (ctx.cur and part):
                        lead = len(part) - len(part.lstrip()) if not ctx.cur else 0
                        ctx.cur.append(Text(text=norm_text(part), loc=self.loc_at(n, pos + lead)))
                    pos += len(part)
            elif isinstance(n, LatexMacroNode):
                i += self.block_macro(n, ctx, nodes, i)
            elif isinstance(n, LatexEnvironmentNode):
                self.block_env(n, ctx)
            elif isinstance(n, LatexMathNode) and n.displaytype == "display":
                ctx.flush()
                ctx.out.append(self.display_from_node(n))
            elif isinstance(n, LatexGroupNode):
                if self.has_block(n.nodelist):
                    self.flow(self.prep(n.nodelist), ctx)
                else:
                    ctx.cur.extend(self.inlines([n]))
            else:
                self.inline_node(n, ctx.cur)
            i += 1

    # ---------------------------------------------------------------------------------------
    def block_macro(self, n, ctx: Flow, nodes: list, i: int) -> int:
        """Handle a macro in block context. Returns the number of extra sibling nodes consumed."""
        name = n.macroname
        args = macro_args(n)
        loc = self.loc(n)
        if name == "_marker":
            ctx.cur.extend(n.inl)
            return 0
        if name in self.revisions and args:
            body = self.prep(arg_nodes(args[-1]))
            if self.has_block(body):
                ctx.flush()
                self.audit.count(f"\\{name} (tracked {self.revisions[name]}, block)")
                ctx.out.append(RevisionBlock(kind=self.revisions[name], blocks=self.blocks(body), macro=name, loc=loc))
                return 0
        if name in SECTIONING or name in RUN_IN:
            return self.sectioning(n, ctx, nodes, i)
        if name == "par":
            ctx.flush()
            return 0
        if name in PAGE_BREAKS:
            ctx.flush()
            ctx.out.append(PageBreak(loc=loc))
            return 0
        if name == "maketitle":
            ctx.flush()
            ctx.out.extend(self.front_blocks())
            self.doc.has_maketitle = True
            return 0
        if name == "appendix":
            ctx.flush()
            self.appendix = True
            self.audit.add("numbering", "\\appendix: Word headings keep numeric numbering; "
                           "appendix letters need a manual fix", loc, "warn")
            return 0
        if name == "printbibliography":
            ctx.flush()
            ctx.out.append(Bibliography(options=parse_keyvals(arg_text(args[0]) if args and args[0] else ""),
                                        section=self.refsection, loc=loc))
            return 0
        if name == "bibliography":
            ctx.flush()
            files = arg_text(args[-1]).strip() if args and args[-1] is not None else ""
            ctx.out.append(Bibliography(bibtex=files, section=self.refsection, loc=loc))
            return 0
        if name in ("title", "author", "date", "keywords", "affil"):
            self.capture_front(n)
            return 0
        if name == "label":
            key = plain_text(arg_nodes(args[0])).strip() if args else ""
            if not ctx.has_content() and ctx.out and isinstance(ctx.out[-1], Heading) and not ctx.out[-1].label:
                ctx.out[-1].label = key
            else:
                ctx.cur.append(Label(key=key, loc=loc))
            return 0
        if name == "includegraphics":
            ctx.flush()
            ctx.out.append(Figure(images=[self.image(n)], floating=False, loc=loc))
            return 0
        if name == "caption":
            self.audit.add("raw", "\\caption outside a float kept as a paragraph", loc)
            ctx.cur.extend(self.inlines(arg_nodes(args[-1])) if args else [])
            return 0
        if name in ("hline", "toprule", "midrule", "bottomrule", "cline", "cmidrule", "addlinespace"):
            self.drop(n)
            return 0
        if name in DECLARATIONS:
            self.audit.add("direct-format", f"\\{name} outside a group ignored", loc, "info")
            return 0
        self.inline_node(n, ctx.cur)
        return 0

    # ---------------------------------------------------------------------------------------
    def block_env(self, n, ctx: Flow) -> None:
        name = n.environmentname
        loc = self.loc(n)
        self.audit.count(f"environment {name}")
        if name in LIST_ENVS:
            ctx.flush()
            ctx.out.append(self.list_block(n))
        elif name in ("quote", "quotation", "displayquote", "verse"):
            ctx.flush()
            ctx.out.append(Quote(blocks=self.blocks(n.nodelist), loc=loc))
        elif name in ("verbatim", "verbatim*", "lstlisting", "Verbatim"):
            ctx.flush()
            ctx.out.append(Verbatim(text=self.verbatim_text(n), loc=loc))
        elif name == "center":
            self.sub_flow(n.nodelist, ctx, "center")
        elif name == "abstract":
            self.sub_flow(n.nodelist, ctx, "abstract")
        elif name in FLOATS_TABLE or name in FLOATS_FIGURE:
            ctx.flush()
            ctx.out.extend(self.float_env(n))
        elif name in TABULARS:
            ctx.flush()
            ctx.out.extend(self.standalone_table(n))
        elif name in MATH_ENVS:
            ctx.flush()
            ctx.out.append(self.display_from_env(n))
        elif name == "thebibliography":
            ctx.flush()
            ctx.out.append(self.manual_bibliography(n))
        elif name == "refsection":
            ctx.flush()
            self.refsection += 1
            self.flow(self.prep(n.nodelist), ctx)
            ctx.flush()
        elif name == "appendices":
            ctx.flush()
            self.appendix = True
            self.flow(self.prep(n.nodelist), ctx)
            ctx.flush()
        elif name in TRANSPARENT_ENVS:
            ctx.flush()
            self.flow(self.prep(n.nodelist), ctx)
            ctx.flush()
        elif name in TIKZ_ENVS:
            ctx.flush()
            ctx.out.append(Figure(images=[self.tikz_image(n)], floating=False, loc=loc))
        elif name in self.revision_envs:
            ctx.flush()
            kind = self.revision_envs[name]
            self.audit.count(f"environment {name} (tracked {kind}, block)")
            ctx.out.append(RevisionBlock(kind=kind, blocks=self.blocks(n.nodelist), macro=name, loc=loc))
        else:
            ctx.flush()
            if not self.leftover_author_construct(name):  # the budget or depth note said it once
                self.audit.add("raw", f"environment {name} kept as visible text", loc)
            self.audit.count(f"environment {name} (unsupported)")
            ctx.out.append(RawBlock(latex=n.latex_verbatim(), reason=f"environment {name}", loc=loc))

    def sub_flow(self, nodes, ctx: Flow, style: str) -> None:
        ctx.flush()
        sub = Flow(style, out=ctx.out)
        self.flow(self.prep(nodes), sub)
        sub.flush()

    @staticmethod
    def verbatim_text(n) -> str:
        d = n.nodeargd
        node = getattr(d, "verbatim_chars_node", None)
        if node is None and d and d.argnlist:
            node = d.argnlist[-1]
        s = getattr(node, "chars", "") or ""
        return s.strip("\n")

    # ---------------------------------------------------------------------------------------
    def heading_level(self, name: str) -> int:
        book = self.doc.docclass in ("report", "book", "memoir", "scrreprt", "scrbook")
        offset = self.opts.get("heading_offset", "auto")
        base = {"part": 1, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4,
                "paragraph": 5, "subparagraph": 6}[name]
        if not book and offset == "auto":
            base -= 1
            if name == "part":
                base = 1
        elif isinstance(offset, int):
            base += offset
        return max(1, min(base, 9))

    def sectioning(self, n, ctx: Flow, nodes: list, i: int) -> int:
        name = n.macroname
        args = macro_args(n)
        starred = bool(args and args[0] is not None)
        title_arg = args[2] if len(args) > 2 else None
        short = args[1] if len(args) > 1 else None
        loc = self.loc(n)
        ctx.flush()
        title = self.inlines(arg_nodes(title_arg)) if title_arg is not None else []
        labels = [x for x in title if isinstance(x, Label)]
        title = strip_inlines([x for x in title if not isinstance(x, Label)])
        if name in RUN_IN and not self.opts.get("paragraph_as_heading"):
            self.audit.count(f"\\{name} (run-in)")
            ctx.cur.append(Styled(kind="strong", children=title, loc=loc))
            ctx.cur.append(Text(text=" ", loc=loc))
            return 0
        if self.opts.get("strip_bold_in_headings", True):
            title = strip_bold(title)
        if name == "part":
            self.audit.add("numbering", "\\part treated as a top-level heading", loc)
        h = Heading(level=self.heading_level(name), children=title, starred=starred,
                    label=labels[0].key if labels else None, appendix=self.appendix, loc=loc)
        if short is not None:
            h.toc_title = plain_text(arg_nodes(short))
        ctx.out.append(h)
        self.audit.count(f"\\{name}{'*' if starred else ''}")
        used = 0
        j = i + 1
        while j < len(nodes):
            x = nodes[j]
            if isinstance(x, LatexCharsNode) and not x.chars.strip():
                j += 1
                continue
            if isinstance(x, LatexMacroNode) and x.macroname == "label" and not h.label:
                a = macro_args(x)
                h.label = plain_text(arg_nodes(a[0])).strip() if a else None
                used = j - i
            break
        return used

    # ---------------------------------------------------------------------------------------
    def list_block(self, n) -> ListBlock:
        kind = n.environmentname
        args = macro_args(n)
        opts = arg_text(args[0]) if args and args[0] is not None else ""
        spec = parse_list_label(opts, kind) if kind == "enumerate" else None
        if kind == "enumerate" and spec is None and re.search(r"label\s*=", opts):
            self.audit.add("raw", f"enumerate label spec [{opts}] not recognised; default numbering used", self.loc(n))
        lb = ListBlock(kind=kind, label_spec=spec, level=self.list_depth, loc=self.loc(n))
        self.list_depth += 1
        try:
            nodes = self.prep(n.nodelist)
            cur_nodes: list | None = None
            cur_term = None
            cur_loc = None
            groups: list[tuple] = []
            for x in nodes:
                if isinstance(x, LatexMacroNode) and x.macroname == "item":
                    if cur_nodes is not None:
                        groups.append((cur_term, cur_nodes, cur_loc))
                    a = macro_args(x)
                    cur_term = self.inlines(arg_nodes(a[0])) if a and a[0] is not None else None
                    cur_nodes, cur_loc = [], self.loc(x)
                elif cur_nodes is not None:
                    cur_nodes.append(x)
                elif isinstance(x, LatexMacroNode) and (x.macroname in DROP_MACROS or x.macroname in DECLARATIONS):
                    self.drop(x)  # list setup such as \\setlength{\\itemsep}{1pt}
                elif not (isinstance(x, LatexCharsNode) and not x.chars.strip()):
                    self.audit.add("raw", "content before the first \\item dropped", self.loc(x))
            if cur_nodes is not None:
                groups.append((cur_term, cur_nodes, cur_loc))
            for term, body, loc in groups:
                blocks = self.blocks(body)
                item = ListItem(blocks=blocks, term=term, loc=loc)
                if term and kind != "description":
                    self.audit.count("custom \\item[...] marker")
                first = blocks[0] if blocks else None
                if isinstance(first, Para) and first.labels:
                    item.label = first.labels[0]
                lb.items.append(item)
        finally:
            self.list_depth -= 1
        return lb

    # ---------------------------------------------------------------------------------------
    def image(self, n) -> Image:
        args = macro_args(n)
        opts = parse_keyvals(arg_text(args[1]) if len(args) > 1 and args[1] is not None else "")
        name = arg_text(args[2]).strip() if len(args) > 2 else ""
        return Image(name=name, options=opts, loc=self.loc(n))

    def tikz_image(self, n, size: str | None = None, in_float: bool = False) -> Image:
        self.tikz_count += 1
        src = n.latex_verbatim()
        if in_float:
            # floats are single spaced even under setspace's \doublespacing
            src = f"\\def\\baselinestretch{{1}}\\{size or 'normalsize'} {src}"
        elif size:
            src = f"\\{size} {src}"
        return Image(name=f"tikz-{self.tikz_count}", tikz=src, loc=self.loc(n))

    def float_env(self, n) -> list:
        """A table or figure environment. Every block it holds is kept: a float with several captions gives one
        captioned block per caption (each caption with the content before it, or after it when nothing precedes),
        and tabulars inside a figure (or pictures inside a table) follow the main block instead of vanishing."""
        name = n.environmentname
        kind = "table" if name in FLOATS_TABLE else "figure"
        acc = FloatAcc()
        self.collect_float(self.prep(n.nodelist), acc)
        loc = self.loc(n)
        notes = [b for b in self.blocks(acc.rest) if isinstance(b, Para)]
        wide = name.endswith("*")
        segments = acc.segments + [acc.segment()]
        if len(segments) > 1:
            self.audit.add(kind, f"{len(segments)} captions in one {name} environment: one captioned "
                                 f"{kind} per caption", loc)
        out: list = []
        for i, seg in enumerate(segments):
            out.extend(self._float_segment(kind, name, seg, notes if i == len(segments) - 1 else [], wide, loc))
        return out

    def _float_segment(self, kind: str, name: str, seg: "FloatSegment", notes: list, wide: bool, loc) -> list:
        caption = seg.caption
        if seg.extra_labels:  # a second \\label in the float: kept as a bookmark inside the caption text
            caption = list(caption or []) + [Label(key=k, loc=loc) for k in seg.extra_labels]
        tables, images = list(seg.tables), list(seg.images)
        out: list = []
        if kind == "table" and not tables and images:
            self.audit.add("table", "table environment without a tabular: treated as a figure", loc)
            kind = "figure"
        elif kind == "figure" and tables and not images:
            self.audit.add("table", "figure environment holds a tabular: kept as a table", loc)
            kind = "table"
        if kind == "table":
            if not tables:
                if caption is None and not seg.label:
                    return []
                self.audit.add("table", "table environment without a tabular", loc)
                return [Para(children=caption or [Text(text="")], style="caption_table", loc=loc)]
            first = tables[0]
            first.caption, first.short_caption, first.label = caption, seg.short, seg.label
            first.notes = notes
            first.wide = wide
            first.unnumbered = seg.unnumbered
            for t in tables[1:]:
                t.floating = False
            if len(tables) > 1:
                self.audit.add("table", f"{len(tables)} tabulars in one {name} environment; "
                               "caption attached to the first", loc)
            out.extend(tables)
            if images:
                self.audit.add("figure", f"{len(images)} picture(s) inside a {name} environment kept after the table", loc)
                out.append(Figure(images=images, floating=False, loc=loc))
            return out
        if not images and not tables:
            if caption is None and not seg.label:
                return []
            return [Figure(images=[], caption=caption, short_caption=seg.short, label=seg.label, notes=notes,
                           wide=wide, unnumbered=seg.unnumbered, loc=loc)]
        out.append(Figure(images=images, caption=caption, short_caption=seg.short, label=seg.label,
                          notes=notes, wide=wide, unnumbered=seg.unnumbered, loc=loc))
        if tables:
            self.audit.add("table", f"{len(tables)} tabular(s) inside a {name} environment kept after the figure", loc)
            for t in tables:
                t.floating = False
            out.extend(tables)
        return out

    def collect_float(self, nodes: list, acc: "FloatAcc") -> None:
        for n in nodes:
            if isinstance(n, LatexMacroNode):
                nm = n.macroname
                a = macro_args(n)
                if nm in ("caption", "captionof") and a:
                    if acc.sub is not None:
                        acc.sub.caption = self.caption_inlines(a[-1])
                    else:
                        if acc.caption is not None:
                            acc.new_caption()  # a second caption: the content so far gets its own block
                        acc.caption = self.caption_inlines(a[-1])
                        acc.unnumbered = nm == "caption" and a[0] is not None and getattr(a[0], "chars", "") == "*"
                        acc.at_caption = (len(acc.tables), len(acc.images))
                        if len(a) > 1 and a[1] is not None and nm == "caption":
                            acc.short = self.caption_inlines(a[1])
                elif nm == "subcaption" and a:
                    acc.pending_subcaption = self.caption_inlines(a[-1])
                    if acc.images:
                        acc.images[-1].caption = acc.pending_subcaption
                elif nm == "label":
                    key = plain_text(arg_nodes(a[0])).strip() if a else ""
                    if acc.sub is not None:
                        acc.sub.label = key
                    elif acc.label is None:
                        acc.label = key
                    elif key:
                        acc.extra_labels.append(key)
                elif nm == "includegraphics":
                    img = self.image(n)
                    acc.images.append(img)
                elif nm in ("resizebox", "scalebox", "adjustbox", "makebox", "parbox", "raisebox", "textnormal"):
                    if a:
                        self.collect_float(self.prep(arg_nodes(a[-1])), acc)
                elif nm in ("centering", "raggedright", "raggedleft", "tiny", "scriptsize", "footnotesize", "small",
                            "normalsize", "large", "noindent", "hfill", "vspace", "medskip", "smallskip", "bigskip",
                            "caption_", "arraybackslash"):
                    if nm in SIZES:
                        acc.size = nm
                    self.drop(n)
                elif nm == "subfloat" and a:
                    sub = FloatAcc()
                    self.collect_float(self.prep(arg_nodes(a[-1])), sub)
                    for im in sub.images:
                        im.caption = self.caption_inlines(a[0]) if a[0] is not None else None
                        acc.images.append(im)
                else:
                    acc.rest.append(n)
            elif isinstance(n, LatexEnvironmentNode):
                nm = n.environmentname
                if nm in TABULARS:
                    acc.tables.extend(self.standalone_table(n))
                elif nm in TIKZ_ENVS:
                    self.audit.count(f"environment {nm}")
                    acc.images.append(self.tikz_image(n, acc.size, in_float=True))
                elif nm in ("threeparttable", "center", "minipage", "adjustbox", "tablenotes_", "table_", "figure_"):
                    self.collect_float(self.prep(n.nodelist), acc)
                elif nm == "tablenotes":
                    acc.rest.extend(self.table_notes(n))
                elif nm in ("subfigure", "subtable", "subfloat"):
                    sub = FloatAcc()
                    saved = acc.sub
                    acc.sub = None
                    self.collect_float(self.prep(n.nodelist), sub)
                    acc.sub = saved
                    for im in sub.images:
                        im.caption = sub.caption
                        im.label = sub.label
                        acc.images.append(im)
                    acc.tables.extend(sub.tables)
                    acc.rest.extend(sub.rest)
                else:
                    acc.rest.append(n)
            elif isinstance(n, LatexGroupNode):
                self.collect_float(self.prep(n.nodelist), acc)
            else:
                acc.rest.append(n)

    def caption_inlines(self, arg) -> list:
        """The caption text; a \\label written inside the caption stays as a Label node (the writer bookmarks it)."""
        return strip_inlines(self.inlines(arg_nodes(arg)))

    def table_notes(self, env) -> list:
        """tablenotes: each \\item becomes a note paragraph; returns synthetic nodes via a flag list."""
        nodes: list = []
        for x in self.prep(env.nodelist):
            if isinstance(x, LatexMacroNode) and x.macroname == "item":
                a = macro_args(x)
                nodes.append(_ParBreak())
                if a and a[0] is not None:
                    nodes.append(_Marker(self.inlines(arg_nodes(a[0]))))
            else:
                nodes.append(x)
        return nodes

    # ---------------------------------------------------------------------------------------
    def manual_bibliography(self, n) -> Bibliography:
        bib = Bibliography(manual=True, section=self.refsection, loc=self.loc(n))
        key = None
        buf: list = []

        def finish():
            if key is not None:
                bib.entries.append(BibEntry(key=key, runs=inlines_to_runs(self.inlines(buf)), loc=bib.loc))

        for x in self.prep(n.nodelist):
            if isinstance(x, LatexMacroNode) and x.macroname == "bibitem":
                finish()
                a = macro_args(x)
                key = plain_text(arg_nodes(a[-1])).strip() if a else ""
                buf = []
            elif key is not None:
                buf.append(x)
        finish()
        self.audit.add("harvest", "thebibliography environment: entries kept, citations rendered as [key]", bib.loc)
        return bib


class _ParBreak(LatexCharsNode):
    """Synthetic paragraph break used when turning table notes into paragraphs."""

    def __init__(self):
        super().__init__(chars="\n\n")


class _Marker(LatexMacroNode):
    """Synthetic node that carries already-parsed inlines (the note marker such as 'a')."""

    def __init__(self, inl):
        super().__init__(macroname="_marker")
        self.inl = inl


class FloatSegment:
    """One captioned block of a float: its caption, labels and content."""

    def __init__(self, caption, short, label, extra_labels, tables, images, unnumbered=False):
        self.caption, self.short, self.label = caption, short, label
        self.extra_labels, self.tables, self.images = extra_labels, tables, images
        self.unnumbered = unnumbered


class FloatAcc:
    def __init__(self):
        self.caption = None
        self.short = None
        self.label = None
        self.extra_labels: list = []
        self.images: list = []
        self.tables: list = []
        self.rest: list = []
        self.sub = None
        self.pending_subcaption = None
        self.size = None  # last font-size declaration, applied to tikzpicture renders
        self.segments: list = []  # finished FloatSegments (a float with several captions)
        self.at_caption = (0, 0)  # how many tables and images had been seen when the current caption arrived
        self.unnumbered = False  # the current caption is \\caption*

    def segment(self) -> FloatSegment:
        return FloatSegment(self.caption, self.short, self.label, self.extra_labels, self.tables, self.images,
                            self.unnumbered)

    def new_caption(self) -> None:
        """A second \\caption: close the segment of the first. A caption that followed content owns that content;
        one that preceded it owns what came after, up to here."""
        nt, ni = self.at_caption
        if nt + ni > 0:  # caption below its content: the content before it is its own, the rest moves on
            self.segments.append(FloatSegment(self.caption, self.short, self.label, self.extra_labels,
                                              self.tables[:nt], self.images[:ni], self.unnumbered))
            self.tables, self.images = self.tables[nt:], self.images[ni:]
        else:  # caption above its content: everything so far is its own
            self.segments.append(self.segment())
            self.tables, self.images = [], []
        self.caption = self.short = self.label = None
        self.extra_labels = []
        self.unnumbered = False


LatexChars = LatexCharsNode


def strip_bold(items: list) -> list:
    """Unwrap strong runs in a heading: the heading style governs."""
    out: list = []
    for x in items:
        if isinstance(x, Styled) and x.kind == "strong":
            out.extend(strip_bold(x.children))
        else:
            out.append(x)
    return out


def parse_keyvals(s: str) -> dict:
    out: dict = {}
    for part in split_top(s, ","):
        if not part.strip():
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        else:
            out[part.strip()] = True
    return out


def split_top(s: str, sep: str) -> list[str]:
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


_COUNTERS = {
    "\\arabic*": ("decimal", "%1"), "\\alph*": ("lowerLetter", "%1"), "\\Alph*": ("upperLetter", "%1"),
    "\\roman*": ("lowerRoman", "%1"), "\\Roman*": ("upperRoman", "%1"),
}


def parse_list_label(opts: str, kind: str = "enumerate") -> dict | None:
    """enumitem ``label=`` (and the legacy enumerate ``[(a)]`` form) → Word numbering description."""
    if not opts.strip():
        return None
    label = None
    for part in split_top(opts, ","):
        if part.strip().startswith("label"):
            label = part.split("=", 1)[1].strip() if "=" in part else None
    if label is None:
        if "=" in opts:
            return None
        legacy = opts.strip()
        m = re.search(r"[1aAiI]", legacy)
        if not m:
            return None
        fmt = {"1": "decimal", "a": "lowerLetter", "A": "upperLetter", "i": "lowerRoman", "I": "upperRoman"}[m.group(0)]
        return {"fmt": fmt, "pattern": legacy[:m.start()] + "%1" + legacy[m.end():], "bold": False}
    bold = False
    m = re.fullmatch(r"\\textbf\{(.*)\}", label, re.S)
    if m:
        bold, label = True, m.group(1)
    if label.startswith("{") and label.endswith("}"):
        label = label[1:-1]
    for tok, (fmt, rep) in _COUNTERS.items():
        if tok in label:
            pat = label.replace(tok, rep)
            pat = re.sub(r"\\[A-Za-z]+\*?", "", pat)
            return {"fmt": fmt, "pattern": pat.replace("{", "").replace("}", ""), "bold": bold}
    return None


def inlines_to_runs(items: list, fmt: frozenset = frozenset()) -> list:
    from .model import InlineMath, Link, Cite, Ref, RawInline

    runs: list = []
    for x in items:
        if isinstance(x, Text):
            runs.append(Run(x.text, fmt))
        elif isinstance(x, Styled):
            add = {"emph": "i", "strong": "b", "superscript": "sup", "subscript": "sub"}.get(x.kind)
            runs.extend(inlines_to_runs(x.children, fmt | {add} if add else fmt))
        elif isinstance(x, Link):
            runs.append(Run("".join(r.text for r in inlines_to_runs(x.children)), fmt, url=x.url))
        elif isinstance(x, (InlineMath, RawInline)):
            runs.append(Run(getattr(x, "latex", ""), fmt))
    return runs
