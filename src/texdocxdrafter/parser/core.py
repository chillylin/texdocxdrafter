"""Shared machinery of the parser: locations, macro expansion, conditionals, text normalisation."""
from __future__ import annotations

import datetime
import re

from pylatexenc.latex2text import LatexNodes2Text

from .. import lexer
from ..audit import Audit
from ..expand import MAX_DEPTH, Expander, resolve_conditionals
from ..lexer import (
    LatexCharsNode, LatexCommentNode, LatexEnvironmentNode, LatexGroupNode, LatexMacroNode, LatexMathNode,
    LatexSpecialsNode,
)
from ..preflight import Preflight
from .model import Loc

SPECIALS = {
    "~": " ", "--": "–", "---": "—", "``": "“", "''": "”", "&": "&",
    "!`": "¡", "?`": "¿", "`": "‘", "'": "’",
}

MACRO_TEXT = {
    "&": "&", "%": "%", "$": "$", "#": "#", "_": "_", "{": "{", "}": "}", "S": "§", "P": "¶",
    "dots": "…", "ldots": "…", "textellipsis": "…", "textendash": "–", "textemdash": "—",
    "textbackslash": "\\", "textasciitilde": "~", "textasciicircum": "^", "LaTeX": "LaTeX", "TeX": "TeX",
    "textregistered": "®", "texttrademark": "™", "copyright": "©", "pounds": "£",
    "textsterling": "£", "euro": "€", "textbullet": "•", "ss": "ß", "ae": "æ",
    "AE": "Æ", "oe": "œ", "OE": "Œ", "o": "ø", "O": "Ø", "aa": "å", "AA": "Å",
    "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ", "degree": "°", "textdegree": "°",
    "textless": "<", "textgreater": ">", "textbar": "|", "quad": " ", "qquad": "  ", "enspace": " ",
    " ": " ", ",": " ", ";": " ", ":": " ", "!": "", "-": "", "/": "", "@": "", "textquotedblleft": "“",
    "textquotedblright": "”", "textquoteleft": "‘", "textquoteright": "’", "guillemotleft": "«",
    "guillemotright": "»", "textperiodcentered": "·", "dag": "†", "ddag": "‡",
    "textdagger": "†", "texttimes": "×", "textpm": "±", "textmu": "µ", "nobreakspace": " ",
    "thinspace": " ", "textunderscore": "_", "slash": "/", "textpercent": "%", "textdollar": "$",
    "textampersand": "&", "textsection": "§", "textparagraph": "¶", "ellipsis": "…",
}
ACCENT_MACROS = set("'`^\"~=.cvuHkrbdt")

DROP_MACROS = {
    # sizes and font families (the paragraph or character style governs)
    "tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large", "LARGE", "huge", "Huge",
    "normalfont", "rmfamily", "sffamily", "mdseries", "upshape", "rm", "sf", "selectfont", "boldmath",
    # spacing and layout
    "noindent", "indent", "medskip", "bigskip", "smallskip", "vfill", "hfill", "hspace", "vspace", "vskip",
    "hskip", "hrulefill", "dotfill", "centering", "raggedright", "raggedleft", "flushleft", "flushright",
    "clearpage", "cleardoublepage", "pagebreak", "nopagebreak", "linebreak", "nolinebreak", "newpage",
    "thispagestyle", "pagestyle", "setcounter", "addtocounter", "setlength", "addtolength", "linespread",
    "protect", "relax", "strut", "hrule", "vrule", "rule", "phantom", "vphantom", "hphantom", "color",
    "frenchspacing", "nonfrenchspacing", "allowbreak", "nobreak", "sloppy", "fussy", "leavevmode", "null",
    "makeatletter", "makeatother", "FloatBarrier", "tableofcontents", "listoffigures", "listoftables",
    "addcontentsline", "bibliographystyle", "usepackage", "hypersetup", "geometry", "titlespacing", "titleformat",
    "setlist", "captionsetup", "newline_", "fontsize", "vss", "pagenumbering", "stepcounter", "refstepcounter",
    "textwidth", "columnwidth", "linewidth", "textheight", "arraybackslash", "tabularnewline_", "par_",
    "hyphenation", "bibliography_", "printindex", "index", "glossary", "markboth", "markright", "paperwidth",
}
DECLARATIONS = {
    "bfseries": "strong", "bf": "strong", "itshape": "emph", "it": "emph", "em": "emph", "slshape": "emph",
    "sl": "emph", "scshape": "smallcaps", "sc": "smallcaps", "ttfamily": "code", "tt": "code",
}
STYLED = {
    "textbf": "strong", "textit": "emph", "textsl": "emph", "emph": "emph", "mkbibemph": "emph",
    "mkbibitalic": "emph", "mkbibbold": "strong", "textsc": "smallcaps", "texttt": "code",
    "underline": "underline", "uline": "underline", "sout": "strike", "textsuperscript": "superscript",
    "textsubscript": "subscript", "enquote": "quote", "mkbibquote": "quote", "textcolor": "color",
    "tnote": "superscript",
}
TRANSPARENT = {
    "textrm", "textmd", "textup", "textnormal", "textsf", "mbox", "fbox", "hbox", "makebox", "texorpdfstring",
    "hyperlink", "hypertarget", "mathrm_", "textcircled_", "ensuremath_", "colorbox_",
}
SECTIONING = {"part": 0, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}  # depth in the hierarchy
RUN_IN = {"paragraph", "subparagraph"}
CITE_CMDS = {
    "cite", "Cite", "parencite", "Parencite", "textcite", "Textcite", "autocite", "Autocite", "smartcite",
    "Smartcite", "footcite", "footcitetext", "fullcite", "footfullcite", "citeauthor", "Citeauthor", "citetitle",
    "citeyear", "citedate", "citeurl", "nocite", "supercite", "citet", "citep", "citealt", "citealp", "Citet",
    "Citep", "citeyearpar", "cites", "parencites", "textcites", "autocites", "smartcites", "footcites",
    "Parencites", "Textcites", "Autocites", "supercites",
}
REF_CMDS = {"ref", "eqref", "pageref", "autoref", "cref", "Cref", "vref", "nameref"}
MATH_ENVS = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*",
             "eqnarray", "eqnarray*", "flalign", "flalign*", "alignat", "alignat*", "displaymath", "math"}

_l2t = LatexNodes2Text()


def norm_text(s: str) -> str:
    s = re.sub(r"[ \t\r\n]+", " ", s)
    s = s.replace("---", "—").replace("--", "–")
    s = s.replace("``", "“").replace("''", "”")
    s = re.sub(r"(?<=\w)'(?=\w)", "’", s)
    s = s.replace("`", "‘").replace("'", "’")
    s = s.replace("~", " ")
    s = s.replace("!‘", "¡").replace("?‘", "¿")
    return s


class ParserBase:
    def __init__(self, pf: Preflight, expander: Expander, audit: Audit, stylemap: dict):
        self.pf = pf
        self.ex = expander
        self.audit = audit
        self.sm = stylemap
        self.opts = stylemap["options"]
        self.base = 0
        self._locmap: dict[int, tuple[object, Loc]] = {}
        self.flags = dict(pf.bool_flags)
        self.site_stack: list[Loc] = []
        self.tikz_count = 0
        self._revisions: dict[str, str] | None = None

    # -- locations ---------------------------------------------------------------------------
    def loc_at(self, node, offset: int) -> Loc | None:
        """Location of a character inside a node; falls back to the node's own location for expanded text."""
        pos = getattr(node, "pos", None)
        if pos is None or id(node) in self._locmap or self.site_stack:
            return self.loc(node)
        return self.pf.locate(pos + offset + self.base)

    def loc(self, node) -> Loc | None:
        if node is None:
            return None
        hit = self._locmap.get(id(node))
        if hit:
            return hit[1]
        if self.site_stack:
            return self.site_stack[-1]
        pos = getattr(node, "pos", None)
        if pos is None:
            return None
        return self.pf.locate(pos + self.base)

    def _cond_report(self, node, msg: str) -> None:
        self.audit.add("conditional", msg, self.loc(node))

    # -- review markup -----------------------------------------------------------------------
    @property
    def revisions(self) -> dict[str, str]:
        """Author macros that mark tracked changes: name -> "ins" | "del".

        ``options.revision_macros`` is "auto" (detect from the definition: \\sout/\\xout means a deletion,
        \\uline/\\uuline/\\uwave an insertion), a mapping such as {add: ins, del: del}, or false to expand
        the macros as ordinary text."""
        if self._revisions is None:
            opt = self.opts.get("revision_macros", "auto")
            rev: dict[str, str] = {}
            if isinstance(opt, dict):
                kinds = {"ins": "ins", "insert": "ins", "add": "ins", "del": "del", "delete": "del"}
                rev = {k.lstrip("\\"): kinds[str(v).lower()] for k, v in opt.items()}
            elif opt == "auto":
                for name, d in self.ex.macros.items():
                    if d.nargs < 1 or "#" not in d.body:
                        continue
                    if re.search(r"\\(sout|xout)\b", d.body):
                        rev[name] = "del"
                    elif re.search(r"\\(uline|uuline|uwave)\b", d.body):
                        rev[name] = "ins"
            self._revisions = rev
        return self._revisions

    @property
    def revision_envs(self) -> dict[str, str]:
        """Environments that mark tracked changes (``options.revision_environments``): name -> "ins" | "del"."""
        kinds = {"ins": "ins", "insert": "ins", "add": "ins", "del": "del", "delete": "del"}
        opt = self.opts.get("revision_environments") or {}
        return {k: kinds[str(v).lower()] for k, v in opt.items()} if isinstance(opt, dict) else {}

    # -- preparation: conditionals and author macros -----------------------------------------
    def prep(self, nodes, depth: int = 0) -> list:
        """Resolve conditionals, drop comments, expand author macros. Returns a flat node list."""
        nodes = [n for n in (nodes or []) if n is not None and not isinstance(n, LatexCommentNode)]
        nodes = resolve_conditionals(nodes, self.flags, self._cond_report)
        out: list = []
        for n in nodes:
            if isinstance(n, LatexMacroNode) and n.macroname in self.ex.macros and n.macroname not in self.revisions:
                out.extend(self._expand_macro(n, depth))
            elif (isinstance(n, LatexEnvironmentNode) and n.environmentname in self.ex.envs
                  and n.environmentname not in self.revision_envs):
                out.extend(self._expand_env(n, depth))
            else:
                out.append(n)
        return out

    def _site(self, node) -> Loc | None:
        return self.loc(node)

    def _expand_macro(self, node, depth: int) -> list:
        if depth > MAX_DEPTH:
            self.audit.add("macro", f"\\{node.macroname}: expansion depth limit reached", self.loc(node))
            return [node]
        text = self.ex.expand_macro_node(node)
        if text is None:
            return [node]
        site = self.loc(node)
        self.audit.count(f"\\{node.macroname} (author macro)")
        new = self.ex.walk(text)
        new = self._tag(new, site)
        return self.prep(new, depth + 1)

    def _expand_env(self, node, depth: int) -> list:
        if depth > MAX_DEPTH:
            self.audit.add("macro", f"environment {node.environmentname}: expansion depth limit", self.loc(node))
            return [node]
        pair = self.ex.expand_env_node(node)
        if pair is None:
            return [node]
        site = self.loc(node)
        self.audit.count(f"{node.environmentname} (author environment)")
        begin = self._tag(self.ex.walk(pair[0]), site)
        end = self._tag(self.ex.walk(pair[1]), site)
        return self.prep(begin + list(node.nodelist) + end, depth + 1)

    def _tag(self, nodes, site: Loc | None) -> list:
        def tag(n):
            if n is None:
                return
            self._locmap[id(n)] = (n, site)
            for attr in ("nodelist",):
                for c in getattr(n, attr, None) or []:
                    tag(c)
            if getattr(n, "nodeargd", None) and n.nodeargd.argnlist:
                for a in n.nodeargd.argnlist:
                    tag(a)

        for n in nodes:
            tag(n)
        return nodes

    # -- audit helpers -----------------------------------------------------------------------
    def drop(self, node, name: str | None = None) -> None:
        nm = name or getattr(node, "macroname", None) or getattr(node, "environmentname", "?")
        self.audit.add("dropped", f"\\{nm}", self.loc(node), "info")

    def raw_inline(self, node, why: str = "") -> object:
        from .model import RawInline

        text = node.latex_verbatim()
        nm = getattr(node, "macroname", None) or getattr(node, "environmentname", None) or type(node).__name__
        self.audit.add("raw", f"{'\\' + nm if nm else ''} kept as text{(': ' + why) if why else ''}", self.loc(node))
        self.audit.count(f"\\{nm} (unsupported)")
        return RawInline(latex=text, loc=self.loc(node))

    # -- text --------------------------------------------------------------------------------
    @staticmethod
    def accent_text(node) -> str | None:
        try:
            t = _l2t.nodelist_to_text([node])
        except Exception:
            return None
        return t if t and "\\" not in t else None

    @staticmethod
    def today() -> str:
        return datetime.date.today().strftime("%B %-d, %Y")


def strip_inlines(items: list) -> list:
    """Trim leading and trailing whitespace Text and line breaks."""
    from .model import LineBreak, Text

    items = list(items)
    while items and (isinstance(items[0], LineBreak) or (isinstance(items[0], Text) and not items[0].text.strip())):
        items.pop(0)
    while items and (isinstance(items[-1], LineBreak) or (isinstance(items[-1], Text) and not items[-1].text.strip())):
        items.pop()
    if items and isinstance(items[0], Text):
        items[0].text = items[0].text.lstrip(" ")
    if items and isinstance(items[-1], Text):
        items[-1].text = items[-1].text.rstrip(" ")
    return items


def merge_text(items: list) -> list:
    """Merge adjacent Text nodes and collapse doubled spaces."""
    from .model import Text

    out: list = []
    for it in items:
        if isinstance(it, Text):
            if not it.text:
                continue
            if out and isinstance(out[-1], Text):
                out[-1].text += it.text
                continue
            out.append(Text(text=it.text, loc=it.loc))
        else:
            out.append(it)
    for it in out:
        if isinstance(it, Text):
            it.text = re.sub(r" {2,}", " ", it.text)
    return out
