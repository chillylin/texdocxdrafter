"""BibTeX bibliographies without a compile: read the document's own ``.bbl``.

A ``.bst`` style has already typeset every entry into the ``.bbl`` as plain TeX, and natbib styles put the
author and year labels into the optional argument of ``\\bibitem``. So for ``\\bibliography`` documents the
citations and the reference list are rendered from that file here, with no TeX engine. (biblatex is
different: its ``.bbl`` holds field data and the formatting lives in TeX macros, see ``harvest``.)

Supported: natbib author-year and numeric citations (``\\citet``, ``\\citep``, ``\\citealt``, ``\\citealp``,
``\\citeauthor``, ``\\citeyear``, ``\\citeyearpar``, ``\\cite``, starred and capitalised forms, pre- and
post-notes, the ``round``/``square``/``curly``/``angle``, ``comma``/``semicolon``, ``numbers``/``super`` and
``sort&compress`` options, ``\\bibpunct`` and ``\\setcitestyle``) and plain LaTeX ``\\cite`` with
``thebibliography`` labels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexwalker import (LatexCharsNode, LatexCommentNode, LatexGroupNode, LatexMacroNode,
                                    LatexMathNode, LatexSpecialsNode, LatexWalker, get_default_latex_context_db)
from pylatexenc.macrospec import MacroSpec

from .audit import Audit
from .parser.model import Bibliography, Document, Run, walk_tree
from .preflight import Preflight


@dataclass
class Entry:
    key: str
    number: int  # 1-based position in the .bbl
    label: str | None  # the \bibitem optional argument, raw TeX
    body: str  # raw TeX of the entry
    short: str = ""  # natbib: "Smith et al."
    year: str = ""  # natbib: "2020a"
    long: str = ""  # natbib: "Smith, Jones, and Lee"


@dataclass
class CiteStyle:
    mode: str = "authoryear"  # authoryear | numbers | super
    open: str = "("
    close: str = ")"
    citesep: str = ";"
    aysep: str = ","
    yysep: str = ","
    notesep: str = ", "
    compress: bool = False
    natbib: bool = True


# ---- the .bbl ----------------------------------------------------------------------------------

_BIBITEM = re.compile(r"\\bibitem\s*")


def _balanced(text: str, pos: int, open_: str, close: str) -> tuple[str, int]:
    """Content of the bracket/brace group starting at ``pos`` (which holds ``open_``) and the index after it."""
    depth = 0
    i = pos
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if open_ == "[" and ch == "{":  # a brace group inside an optional argument may contain ] : skip it
            braces = 0
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                braces += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
                if braces == 0:
                    break
            continue
        if ch == open_:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return text[pos + 1:i], i + 1
        i += 1
    return text[pos + 1:], len(text)


def parse_bbl(text: str) -> list[Entry]:
    text = re.sub(r"%.*", "", text)  # comments
    m = re.search(r"\\begin\{thebibliography\}\s*\{[^}]*\}", text)
    start = m.end() if m else 0
    end = text.find("\\end{thebibliography}", start)
    body = text[start:end if end >= 0 else len(text)]
    entries: list[Entry] = []
    items = list(_BIBITEM.finditer(body))
    for n, im in enumerate(items):
        pos = im.end()
        label = None
        if pos < len(body) and body[pos] == "[":
            label, pos = _balanced(body, pos, "[", "]")
            while pos < len(body) and body[pos].isspace():
                pos += 1
        if pos >= len(body) or body[pos] != "{":
            continue
        key, pos = _balanced(body, pos, "{", "}")
        stop = items[n + 1].start() if n + 1 < len(items) else len(body)
        e = Entry(key=key.strip(), number=n + 1, label=label, body=body[pos:stop])
        if label is not None:
            _split_label(e)
        entries.append(e)
    return entries


def _split_label(e: Entry) -> None:
    """natbib label ``{Smith et~al.(2020{\\natexlab{a}})Smith, Jones, and Lee}`` → short, year, long."""
    lab = e.label.strip()
    if lab.startswith("{") and lab.endswith("}"):
        lab = lab[1:-1]
    depth = 0
    for i, ch in enumerate(lab):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "(" and depth == 0:
            j, d2 = i + 1, 1
            while j < len(lab) and d2:
                d2 += {"(": 1, ")": -1}.get(lab[j], 0)
                j += 1
            e.short = plain(lab[:i])
            e.year = plain(lab[i + 1:j - 1])
            e.long = plain(lab[j:]) or e.short
            return
    e.short = plain(lab)


# ---- TeX fragments → runs ---------------------------------------------------------------------

_L2T = LatexNodes2Text(math_mode="text", keep_comments=False)
# macros .bst styles emit that pylatexenc does not know, with their argument shapes
_CONTEXT = get_default_latex_context_db()
_CONTEXT.add_context_category("bbl", prepend=True, macros=[
    MacroSpec("doi", "{"), MacroSpec("bibinfo", "{{"), MacroSpec("natexlab", "{"), MacroSpec("noopsort", "{"),
    MacroSpec("mkbibquote", "{"), MacroSpec("enquote", "{"), MacroSpec("urlprefix", ""), MacroSpec("BIBand", ""),
    MacroSpec("newblock", ""), MacroSpec("textsuperscript", "{"), MacroSpec("textsubscript", "{"),
    MacroSpec("textendash", ""), MacroSpec("textemdash", ""), MacroSpec("bibAnnoteFile", "{"),
    MacroSpec("textsl", "{"), MacroSpec("textmd", "{"), MacroSpec("textup", "{"), MacroSpec("textnormal", "{"),
])
_ITALIC_SWITCH = {"em", "it", "itshape", "sl", "slshape"}
_BOLD_SWITCH = {"bf", "bfseries"}
_PLAIN_SWITCH = {"rm", "rmfamily", "normalfont", "upshape", "mdseries", "sc", "scshape", "sf", "sffamily", "tt", "ttfamily"}
_ITALIC_MACROS = {"emph", "textit", "textsl"}
_BOLD_MACROS = {"textbf"}
_TRANSPARENT = {"mbox", "hbox", "textsc", "textrm", "textup", "textnormal", "texttt", "textsf", "natexlab", "mkbibquote",
                "bibinfo", "enquote", "protect", "noopsort", "textmd"}
_DROP = {"newblock", "relax", "penalty", "break", "nobreak", "sloppy", "fussy", "hskip", "vskip", "hfill", "hfil",
         "allowbreak", "ignorespaces", "unskip", "frenchspacing", "nonfrenchspacing", "bibAnnoteFile", "providecommand",
         "urlstyle", "bibitem", "newline", "linebreak", "nolinebreak"}
_WORDS = {"BIBand": "and", "urlprefix": "URL ", "ldots": "…", "dots": "…", "textellipsis": "…", "&": "&", "%": "%",
          "_": "_", "$": "$", "#": "#", "{": "{", "}": "}", "textendash": "–", "textemdash": "—", " ": " ", ",": "",
          "/": "", "-": "", "@": ""}


def _arg_nodes(node: LatexMacroNode, k: int = -1) -> list:
    """Nodes of the k-th (default last) non-empty argument of a macro node, or []."""
    if node.nodeargd is None or not node.nodeargd.argnlist:
        return []
    args = [a for a in node.nodeargd.argnlist if a is not None]
    if not args:
        return []
    a = args[k]
    if isinstance(a, LatexGroupNode):
        return a.nodelist
    return [a]


def _text(nodes: list) -> str:
    return "".join(r.text for r in to_runs(nodes))


def to_runs(nodes: list, fmt: frozenset = frozenset()) -> list[Run]:
    out: list[Run] = []
    cur = set(fmt)

    def emit(text: str, f: set, url: str | None = None) -> None:
        if not text:
            return
        if url is None and out and out[-1].fmt == frozenset(f) and out[-1].url is None:
            out[-1] = Run(out[-1].text + text, out[-1].fmt)
        else:
            out.append(Run(text, frozenset(f), url))

    for n in nodes:
        if n is None or isinstance(n, LatexCommentNode):
            continue
        if isinstance(n, LatexCharsNode):
            emit(n.chars, cur)
        elif isinstance(n, LatexGroupNode):
            for r in to_runs(n.nodelist, frozenset(cur)):
                emit(r.text, set(r.fmt), r.url)
        elif isinstance(n, LatexSpecialsNode):
            emit(_L2T.node_to_text(n), cur)
        elif isinstance(n, LatexMathNode):
            emit(_L2T.node_to_text(n), cur)
        elif isinstance(n, LatexMacroNode):
            name = n.macroname
            if name in _ITALIC_SWITCH:
                cur = (cur - {"i"}) if "i" in cur else (cur | {"i"})
            elif name in _BOLD_SWITCH:
                cur = cur | {"b"}
            elif name in _PLAIN_SWITCH:
                cur = cur - {"i", "b"}
            elif name in _ITALIC_MACROS:
                for r in to_runs(_arg_nodes(n), frozenset(cur ^ {"i"})):
                    emit(r.text, set(r.fmt), r.url)
            elif name in _BOLD_MACROS:
                for r in to_runs(_arg_nodes(n), frozenset(cur | {"b"})):
                    emit(r.text, set(r.fmt), r.url)
            elif name == "textsuperscript":
                for r in to_runs(_arg_nodes(n), frozenset(cur | {"sup"})):
                    emit(r.text, set(r.fmt), r.url)
            elif name == "textsubscript":
                for r in to_runs(_arg_nodes(n), frozenset(cur | {"sub"})):
                    emit(r.text, set(r.fmt), r.url)
            elif name in _TRANSPARENT:
                for r in to_runs(_arg_nodes(n), frozenset(cur)):
                    emit(r.text, set(r.fmt), r.url)
            elif name in ("url", "path"):
                u = n.latex_verbatim()
                u = re.sub(r"^\\\w+\s*", "", u).strip()
                u = u[1:-1] if u[:1] == "{" else u
                emit(u, cur, u if u.startswith(("http", "ftp")) else None)
            elif name == "href":
                u = _text(_arg_nodes(n, 0)).strip()
                emit(_text(_arg_nodes(n, -1)), cur, u)
            elif name == "doi":
                d = _text(_arg_nodes(n)).strip()
                emit("doi:" + d, cur, "https://doi.org/" + d)
            elif name in _WORDS:
                emit(_WORDS[name], cur)
            elif name == "\\":
                emit(" ", cur)
            elif name in _DROP:
                continue
            else:
                emit(_L2T.node_to_text(n), cur)
    return out


def render(tex: str, fmt: frozenset = frozenset()) -> list[Run]:
    """A TeX fragment from a .bbl → runs with i/b/sup/sub formats and URLs; whitespace normalised."""
    tex = re.sub(r"\\penalty\s*-?\d+", " ", tex)
    tex = re.sub(r"\\hskip\s*-?[\d.]+\s*\w+(?:\s*plus\s*[\d.]+\s*\w+)?(?:\s*minus\s*[\d.]+\s*\w+)?", " ", tex)
    nodes, _, _ = LatexWalker(tex, latex_context=_CONTEXT, tolerant_parsing=True).get_latex_nodes()
    runs = to_runs(nodes, fmt)
    out: list[Run] = []
    for r in runs:
        t = re.sub(r"\s+", " ", r.text.replace("\u00a0", " "))
        if out and out[-1].text.endswith(" ") and t.startswith(" "):
            t = t[1:]
        if t:
            out.append(Run(t, r.fmt, r.url))
    if out:
        out[0] = Run(out[0].text.lstrip(), out[0].fmt, out[0].url)
        out[-1] = Run(out[-1].text.rstrip(), out[-1].fmt, out[-1].url)
    return [r for r in out if r.text]


def plain(tex: str) -> str:
    return "".join(r.text for r in render(tex))


# ---- citation style from the preamble ---------------------------------------------------------

_BRACKETS = {"round": ("(", ")"), "square": ("[", "]"), "curly": ("{", "}"), "angle": ("<", ">")}


def cite_style(pf: Preflight, entries: list[Entry]) -> CiteStyle:
    st = CiteStyle()
    opts = next((o for n, o in pf.packages if n == "natbib"), None)
    st.natbib = opts is not None
    if not st.natbib:
        st.mode, st.open, st.close, st.citesep = "numbers", "[", "]", ","
        return st
    labelled = any(e.year for e in entries)
    st.mode = "authoryear" if labelled else "numbers"
    for o in (x.strip() for x in opts.split(",")):
        if o in _BRACKETS:
            st.open, st.close = _BRACKETS[o]
        elif o == "comma":
            st.citesep = ","
        elif o in ("colon", "semicolon"):
            st.citesep = ";"
        elif o == "numbers":
            st.mode = "numbers"
        elif o == "super":
            st.mode = "super"
        elif o == "authoryear":
            st.mode = "authoryear" if labelled else "numbers"
        elif o == "sort&compress":
            st.compress = True
    if st.mode in ("numbers", "super") and not any(o.strip() in _BRACKETS for o in opts.split(",")):
        st.open, st.close = "[", "]"
    if st.mode in ("numbers", "super") and "comma" not in opts and "semicolon" not in opts and "colon" not in opts:
        st.citesep = ","
    pre = pf.orig_preamble
    m = re.search(r"\\bibpunct\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}", pre)
    if m:
        if m.group(1) is not None:
            st.notesep = m.group(1)
        st.open, st.close, st.citesep = m.group(2), m.group(3), m.group(4)
        st.mode = {"n": "numbers", "s": "super"}.get(m.group(5).strip(), "authoryear" if labelled else "numbers")
        st.aysep, st.yysep = m.group(6), m.group(7)
    for m in re.finditer(r"\\setcitestyle\s*\{([^}]*)\}", pre):
        for o in (x.strip() for x in m.group(1).split(",")):
            k, _, v = o.partition("=")
            v = v.strip().strip("{}")
            if k in _BRACKETS:
                st.open, st.close = _BRACKETS[k]
            elif k == "open":
                st.open = v
            elif k == "close":
                st.close = v
            elif k == "comma":
                st.citesep = ","
            elif k in ("semicolon", "colon"):
                st.citesep = ";"
            elif k == "citesep":
                st.citesep = v
            elif k == "aysep":
                st.aysep = v
            elif k == "yysep":
                st.yysep = v
            elif k == "notesep":
                st.notesep = v
            elif k in ("numbers", "super"):
                st.mode = k
            elif k == "authoryear":
                st.mode = "authoryear" if labelled else "numbers"
            elif k == "sort&compress":
                st.compress = True
    return st


# ---- rendering citation commands --------------------------------------------------------------

_CITE = re.compile(r"^\\(\w+?)(\*?)\s*((?:\[(?:[^\[\]]|\{[^{}]*\})*\]\s*){0,2})\{([^}]*)\}", re.S)


def parse_cite(raw: str) -> tuple[str, bool, str, str, list[str]]:
    """\\citep*[pre][post]{k1,k2} → (cmd, star, pre, post, keys). One optional argument is the post-note."""
    m = _CITE.match(raw.strip())
    if not m:
        return "", False, "", "", []
    opts = re.findall(r"\[((?:[^\[\]]|\{[^{}]*\})*)\]", m.group(3))
    pre, post = ("", "") if not opts else (("", opts[0]) if len(opts) == 1 else (opts[0], opts[1]))
    keys = [k.strip() for k in m.group(4).split(",") if k.strip()]
    return m.group(1), bool(m.group(2)), pre.strip(), post.strip(), keys


def _cap(runs: list[Run]) -> list[Run]:
    if runs and runs[0].text:
        runs[0] = Run(runs[0].text[0].upper() + runs[0].text[1:], runs[0].fmt, runs[0].url)
    return runs


def _compress(nums: list[int]) -> str:
    nums = sorted(set(nums))
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if j - i < 2 else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def render_cite(raw: str, by_key: dict[str, Entry], st: CiteStyle) -> list[Run] | None:
    """Runs for one citation command, or None when a key is unknown or the command is not a citation."""
    cmd, star, pre, post, keys = parse_cite(raw)
    if not cmd or cmd == "nocite":
        return None
    if cmd == "citetext":
        inner = re.search(r"\{(.*)\}", raw, re.S)
        return render(f"{st.open}{inner.group(1) if inner else ''}{st.close}")
    if any(k not in by_key for k in keys) or not keys:
        return None
    es = [by_key[k] for k in keys]
    base = cmd.lower()
    capital = cmd[:1].isupper()
    pre_s = (plain(pre) + " ") if pre else ""
    post_s = (st.notesep + plain(post)) if post else ""
    sep = st.citesep + " "

    if st.mode in ("numbers", "super") or not st.natbib or base in ("cite",) and st.mode != "authoryear":
        if not st.natbib:
            labels = [plain(e.label) if e.label is not None else str(e.number) for e in es]
            inner = ", ".join(labels)
        elif st.compress:
            inner = _compress([e.number for e in es])
        else:
            inner = sep.join(str(e.number) for e in es)
        if base in ("citeauthor", "citefullauthor"):
            txt = sep.join((e.long if (star or base == "citefullauthor") else e.short) or e.key for e in es)
            return _cap(render(txt)) if capital else render(txt)
        if base == "citeyear":
            return render(sep.join(e.year or str(e.number) for e in es))
        if st.mode == "super" and base in ("cite", "citep"):
            return [Run(inner.replace(" ", ""), frozenset({"sup"}))]
        if base in ("citet", "citealt"):
            author = sep.join((e.long if star else e.short) or e.key for e in es)
            runs = render(f"{author} {st.open}{pre_s}{inner}{post_s}{st.close}")
            return _cap(runs) if capital else runs
        if base == "citealp":
            return render(f"{pre_s}{inner}{post_s}")
        return render(f"{st.open}{pre_s}{inner}{post_s}{st.close}")

    # author-year: consecutive entries with the same author share one author mention
    groups: list[tuple[str, list[str]]] = []
    for e in es:
        author = (e.long if star else e.short) or e.key
        if groups and groups[-1][0] == author:
            groups[-1][1].append(e.year)
        else:
            groups.append((author, [e.year]))

    def years(ys: list[str]) -> str:
        return (st.yysep + " ").join(ys)

    if base in ("citet", "cite"):
        parts = [f"{a} {st.open}{years(ys)}{st.close}" for a, ys in groups]
        if pre_s or post_s:
            a, ys = groups[-1]
            parts[-1] = f"{a} {st.open}{pre_s}{years(ys)}{post_s}{st.close}"
        txt = sep.join(parts)
    elif base == "citealt":
        txt = sep.join(f"{a} {years(ys)}" for a, ys in groups)
        txt = f"{pre_s}{txt}{post_s}"
    elif base == "citep":
        txt = f"{st.open}{pre_s}{sep.join(f'{a}{st.aysep} {years(ys)}' for a, ys in groups)}{post_s}{st.close}"
    elif base == "citealp":
        txt = f"{pre_s}{sep.join(f'{a}{st.aysep} {years(ys)}' for a, ys in groups)}{post_s}"
    elif base in ("citeauthor", "citefullauthor"):
        txt = sep.join((e.long if (star or base == "citefullauthor") else e.short) or e.key for e in es)
    elif base == "citeyear":
        txt = sep.join(e.year for e in es)
    elif base == "citeyearpar":
        txt = f"{st.open}{pre_s}{sep.join(e.year for e in es)}{post_s}{st.close}"
    else:
        return None
    runs = render(txt)
    return _cap(runs) if capital else runs


# ---- putting it together ----------------------------------------------------------------------

def harvest_from_bbl(pf: Preflight, doc: Document, audit: Audit):
    """A HarvestResult for a \\bibliography document, built from ``main.bbl`` with no compile."""
    from .harvest import HarvestResult, linkify

    res = HarvestResult()
    if not pf.bbl or not pf.bbl.is_file():
        audit.add("harvest", f"{pf.bbl.name if pf.bbl else 'the .bbl'} is missing: run bibtex on {pf.main.name} first; "
                             "citations rendered as [key]")
        return res
    from .preflight import read_source_text

    issues: list[str] = []
    entries = parse_bbl(read_source_text(pf.bbl, issues))
    for issue in issues:
        audit.add("harvest", issue)
    if not entries:
        audit.add("harvest", f"no \\bibitem entries found in {pf.bbl.name}; citations rendered as [key]")
        return res
    by_key = {e.key: e for e in entries}
    st = cite_style(pf, entries)
    missing: set[str] = set()
    for c in doc.cites:
        runs = render_cite(c.raw, by_key, st)
        if runs is None:
            _, _, _, _, keys = parse_cite(c.raw)
            missing.update(k for k in keys if k not in by_key)
            continue
        res.cites[c.index] = linkify(runs)
    if missing:
        audit.add("harvest", f"{len(missing)} cited key(s) not found in {pf.bbl.name} (shown as keys): "
                             f"{', '.join(sorted(missing)[:12])}")
    numbered = st.mode in ("numbers", "super") or not st.natbib
    rendered = []
    for e in entries:
        runs = linkify(render(e.body))
        if numbered:
            label = plain(e.label) if (e.label is not None and not st.natbib) else str(e.number)
            runs = [Run(f"[{label}] ", frozenset())] + runs
        rendered.append((e.key, runs))
    counts: dict[int, int] = {}

    def collect(n):
        if isinstance(n, Bibliography) and not n.manual and n.bibtex is not None:
            k = counts.get(n.section, 0)
            counts[n.section] = k + 1
            res.bibs[(n.section, k)] = list(rendered)

    walk_tree(doc, collect)
    res.ok = all(c.index in res.cites for c in doc.cites if c.cmd != "nocite")
    audit.info["harvest source"] = f"{pf.bbl.name} (no compile)"
    return res
