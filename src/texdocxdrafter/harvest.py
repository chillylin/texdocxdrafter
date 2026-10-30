"""Stage 5: citations and bibliography rendered by the real biblatex.

A side document reuses the original preamble, typesets every citation command in document order and
``\\printbibliography`` on a very large page with text markers for italics and entry boundaries, and
``pdftotext -raw`` turns the PDF back into text. Markers are parsed into runs with formats.
"""
from __future__ import annotations

import re
import secrets
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox
from .writer.xmlutil import xml_safe
from .audit import Audit
from .parser.model import Bibliography, Document, Run, walk_tree
from .preflight import Preflight, side_preamble

OVERRIDES = r"""
% ---- texdocxdrafter harvest overrides -------------------------------------------------------------
\makeatletter
\@ifpackageloaded{geometry}{\geometry{paperwidth=500cm,paperheight=500cm,margin=1cm}}{%
  \usepackage[paperwidth=500cm,paperheight=500cm,margin=1cm]{geometry}}
\makeatother
\makeatletter
\AtBeginDocument{%
  \hyphenpenalty=10000 \exhyphenpenalty=10000 \sloppy \raggedright \pagestyle{empty}%
  \renewcommand{\TeX}{TeX}\renewcommand{\LaTeX}{LaTeX}\renewcommand{\LaTeXe}{LaTeX2e}%
  \renewcommand{\footnote}[2][]{{\ttfamily @@FN@@}#2{\ttfamily @@/FN@@}}%  footnote citations (verbose styles,
  \renewcommand{\footnotetext}[2][]{{\ttfamily @@FN@@}#2{\ttfamily @@/FN@@}}% \footcite): their text, inline
  \renewcommand{\footnotemark}[1][]{}%
  \providecommand{\BibTeX}{}\renewcommand{\BibTeX}{BibTeX}%
  \providecommand{\bibhang}{}\setlength{\bibhang}{0pt}%
  \def\wfI{{\ttfamily @@I@@}}\def\wfEI{{\ttfamily @@/I@@}}%
  \def\wfB{{\ttfamily @@B@@}}\def\wfEB{{\ttfamily @@/B@@}}%
  \def\mkbibemph#1{\wfI#1\wfEI}%
  \def\mkbibitalic#1{\wfI#1\wfEI}%
  \def\mkbibbold#1{\wfB#1\wfEB}%
  \def\mkbibsuperscript#1{{\ttfamily @@SUP@@}#1{\ttfamily @@/SUP@@}}%
  \renewcommand{\emph}[1]{\wfI#1\wfEI}%
  \renewcommand{\textit}[1]{\wfI#1\wfEI}%
  \renewcommand{\textbf}[1]{\wfB#1\wfEB}%
  \@ifundefined{AtEveryBibitem}{}{%
    \AtEveryBibitem{{\ttfamily @@ENTRY \edef\wfk{\thefield{entrykey}}\expandafter\detokenize\expandafter{\wfk}@@ }}}%
  \@ifundefined{defbibheading}{}{%
    \defbibheading{bibliography}[\bibname]{{\ttfamily @@TITLE@@ }#1{\ttfamily @@/TITLE@@}\par}%
    \defbibheading{bibintoc}[\bibname]{{\ttfamily @@TITLE@@ }#1{\ttfamily @@/TITLE@@}\par}%
    \defbibheading{subbibliography}[\refname]{{\ttfamily @@TITLE@@ }#1{\ttfamily @@/TITLE@@}\par}%
    \defbibheading{subbibintoc}[\refname]{{\ttfamily @@TITLE@@ }#1{\ttfamily @@/TITLE@@}\par}%
  }%
}
\makeatother
"""


@dataclass
class HarvestResult:
    ok: bool = False
    cites: dict[int, list[Run]] = field(default_factory=dict)
    bibs: dict[tuple[int, int], list[tuple[str, list[Run]]]] = field(default_factory=dict)
    titles: dict[tuple[int, int], list[Run]] = field(default_factory=dict)
    log_errors: list[str] = field(default_factory=list)
    tex: Path | None = None


def foot_to_plain(raw: str) -> str:
    """\\footcite becomes \\cite for the harvest (the tree records the footnote position)."""
    return re.sub(r"^\\foot(cites?|citetext)\b", lambda m: "\\" + ("cites" if m.group(1) == "cites" else "cite"), raw) \
        .replace("\\footfullcite", "\\fullcite")


def option_string(opts: dict) -> str:
    parts = []
    for k, v in opts.items():
        parts.append(k if v is True else f"{k}={v}")
    return "[" + ",".join(parts) + "]" if parts else ""


# BibTeX (natbib or plain thebibliography): entry markers after each \\bibitem, the declarations that .bst
# styles emit ({\\em ...}, {\\it ...}, {\\bf ...}) as format markers, and the \\refname heading as the title.
BIBTEX_OVERRIDES = r"""
\makeatletter
\AtBeginDocument{%
  \let\wf@lbibitem\@lbibitem \let\wf@bibitem\@bibitem
  \def\@lbibitem[#1]#2{\wf@lbibitem[#1]{#2}{\ttfamily @@ENTRY \detokenize{#2}@@ }}%
  \def\@bibitem#1{\wf@bibitem{#1}{\ttfamily @@ENTRY \detokenize{#1}@@ }}%
  \def\em{\wfI\aftergroup\wfEI}\def\it{\wfI\aftergroup\wfEI}\def\bf{\wfB\aftergroup\wfEB}%
  \@ifundefined{refname}{}{\edef\wf@refname{\refname}%
    \renewcommand{\refname}{{\ttfamily @@TITLE@@ }\wf@refname{\ttfamily @@/TITLE@@}}}%
  \@ifundefined{bibname}{}{\edef\wf@bibname{\bibname}%
    \renewcommand{\bibname}{{\ttfamily @@TITLE@@ }\wf@bibname{\ttfamily @@/TITLE@@}}}%
}
\makeatother
"""


def uses_bibtex(doc: Document) -> bool:
    found = []
    walk_tree(doc, lambda n: found.append(n) if isinstance(n, Bibliography) and n.bibtex is not None else None)
    return bool(found)


def build_harvest_tex(pf: Preflight, doc: Document, nonce: str = "") -> tuple[str, list[tuple[int, int]]]:
    """The harvest document. ``nonce`` is spliced into every ``@@…@@`` marker (``@@<nonce>CITE 1@@<nonce>``), so
    that text from a .bib file cannot forge or close a marker: it does not know the nonce."""
    cites = list(doc.cites)
    bibs: list[Bibliography] = []

    def find(n):
        if isinstance(n, Bibliography) and not n.manual:
            bibs.append(n)

    walk_tree(doc, find)
    sections = sorted({c.section for c in cites} | {b.section for b in bibs} | {0})
    bibtex = any(b.bibtex is not None for b in bibs)
    out = [side_preamble(pf).rstrip(), OVERRIDES] + ([BIBTEX_OVERRIDES] if bibtex else []) + \
          ["\\begin{document}", "\\raggedright"]
    bib_keys: list[tuple[int, int]] = []
    for s in sections:
        if s > 0:
            out.append("\\begin{refsection}")
        for c in (c for c in cites if c.section == s):
            raw = foot_to_plain(c.raw)
            # \\relax ends the argument list of biblatex's \\cites, \\parencites, ...: without it the marker group
            # that follows would be read as one more citation
            out.append("{\\ttfamily @@CITE %d@@} %s\\relax{\\ttfamily @@END@@}\\par" % (c.index, raw))
        for k, b in enumerate([b for b in bibs if b.section == s]):
            out.append("{\\ttfamily @@BIB %d %d@@}\\par" % (s, k))
            out.append("\\bibliography{%s}" % b.bibtex if b.bibtex is not None
                       else "\\printbibliography" + option_string(b.options))
            out.append("{\\ttfamily @@ENDBIB@@}\\par")
            bib_keys.append((s, k))
        if s > 0:
            out.append("\\end{refsection}")
    out.append("\\end{document}")
    return ("\n".join(out) + "\n").replace("@@", "@@" + nonce), bib_keys


def compile_harvest(pf: Preflight, tex: str, audit: Audit, timeout: int = sandbox.COMPILE_TIMEOUT) -> Path | None:
    """Compile the harvest document in the run's private work directory.

    The engine runs with shell escape off and ``openout_any=p``, reads inputs from the source directory
    (plus ``--allow-dir`` directories) and can write only into ``work``."""
    work = pf.workdir
    work.mkdir(parents=True, exist_ok=True)
    sandbox.write_fresh(work / "harvest.tex", tex)
    dirs = [pf.srcdir] + list(pf.allow_dirs)
    env = sandbox.tex_env(dirs)
    engine = pf.engine

    def tex_run():
        return sandbox.compile_tex(engine, work, "harvest", work / "harvest.tex", dirs, timeout)

    r = tex_run()
    if r.returncode == -1:
        audit.add("harvest", f"harvest compile aborted ({engine}): {r.stderr}")
        return None
    if pf.uses_biblatex and shutil.which("biber"):
        sandbox.run(["biber", "--output-directory", str(work), "harvest"], cwd=work, env=env, timeout=timeout, rw=[work], ro=dirs)
        tex_run()
        tex_run()
    elif not pf.uses_biblatex and shutil.which("bibtex"):
        # bibtex looks for the .aux in its working directory and for .bib/.bst files on BIBINPUTS/BSTINPUTS
        b = sandbox.run(["bibtex", "harvest"], cwd=work, env=env, timeout=timeout, rw=[work], ro=dirs)
        for m in re.findall(r"I couldn't open (?:database|style) file (\S+)", b.stdout):
            audit.add("harvest", f"bibtex could not open {m}")
        tex_run()
        tex_run()
    # whatever the document left in work is untrusted: regular files only, never through a symbolic link
    text = sandbox.read_untrusted_text(work / "harvest.log")
    blg = sandbox.read_untrusted_text(work / "harvest.blg") if (work / "harvest.blg").exists() else ""
    if text or blg:
        missing = missing_keys(text, blg)
        if missing:
            audit.add("harvest", f"{len(missing)} cited key(s) not found in the .bib files "
                      f"(shown as keys): {', '.join(missing[:12])}")
        for bf in sorted(set(re.findall(r"Cannot find '([^']*\.bib)'|Cannot find (\S+\.bib)", text)))[:3]:
            audit.add("harvest", f"bibliography database not found: {bf[0] or bf[1]}")
    pdf = work / "harvest.pdf"
    if not sandbox.is_plain_file(pdf) or pdf.stat().st_size == 0:
        errs = [l for l in text.splitlines() if l.startswith("!")][:5]
        audit.add("harvest", f"harvest compile failed ({engine}): {'; '.join(errs) or r.stderr[:200]}")
        return None
    return pdf


def missing_keys(log: str, blg: str = "") -> list[str]:
    """Cited keys with no entry, from the engine log (LaTeX writes ``Citation 'key'`` with a straight quote since
    2021, older runs with a backtick) and from biber's own log (``harvest.blg``)."""
    found = set(re.findall(r"didn't find a database entry for '([^']*)'", log + "\n" + blg))
    found.update(re.findall(r"Citation [`']([^']*)' (?:on (?:input line|page) \S+ )?undefined", log))
    return sorted(found)


_LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}


def _marker(pattern: str, nonce: str) -> re.Pattern:
    """``pattern`` with every ``@@`` standing for the run's marker ``@@<nonce>``."""
    return re.compile(pattern.replace("@@", re.escape("@@" + nonce)), re.S)


def normalise(text: str) -> str:
    text = "".join(_LIG.get(ch, ch) for ch in text)
    # pdftotext gives some accented i's as a dotless ı plus a combining mark: that is an i with the mark
    text = re.sub(r"\u0131(?=[\u0300-\u036f])", "i", text)
    text = unicodedata.normalize("NFC", text)
    return xml_safe(text.replace("­", "").replace("\x0c", "\n"))


def to_runs(s: str, nonce: str = "") -> list[Run]:
    """Marked text → runs with formats. Italic markers toggle (an \\emph inside \\emph is upright)."""
    _TOKEN = _marker(r"@@(/?)(I|B|SUP|SUB|FN)@@", nonce)
    s = re.sub(r"\s+", " ", s)
    runs: list[Run] = []
    depth = {"I": 0, "B": 0, "SUP": 0, "SUB": 0, "FN": 0}
    pos = 0

    def emit(txt: str) -> None:
        if not txt:
            return
        fmt = set()
        if depth["I"] % 2 == 1:
            fmt.add("i")
        if depth["B"] > 0:
            fmt.add("b")
        if depth["SUP"] > 0:
            fmt.add("sup")
        if depth["SUB"] > 0:
            fmt.add("sub")
        if depth["FN"] > 0:
            fmt.add("fn")  # text LaTeX put in a footnote: the writer makes a Word footnote of it
        if runs and runs[-1].fmt == frozenset(fmt):
            runs[-1] = Run(runs[-1].text + txt, runs[-1].fmt)
        else:
            runs.append(Run(txt, frozenset(fmt)))

    for m in _TOKEN.finditer(s):
        emit(s[pos:m.start()])
        depth[m.group(2)] += -1 if m.group(1) else 1
        depth[m.group(2)] = max(depth[m.group(2)], 0)
        pos = m.end()
    emit(s[pos:])
    # tidy: marker boundaries can leave stray spaces next to punctuation or doubled spaces
    for i, r in enumerate(runs):
        t = r.text
        if i == 0:
            t = t.lstrip()
        if i == len(runs) - 1:
            t = t.rstrip()
        runs[i] = Run(re.sub(r" ([,.;:)\]])", r"\1", t), r.fmt)
    return [r for r in runs if r.text]


def linkify(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    url_re = re.compile(r"(https?://[^\s<>]+[^\s<>.,;:)\]])")
    for r in runs:
        if r.url:  # a target from \\href, \\doi or \\url: keep it
            out.append(r)
            continue
        pos = 0
        for m in url_re.finditer(r.text):
            if m.start() > pos:
                out.append(Run(r.text[pos:m.start()], r.fmt))
            out.append(Run(m.group(1), r.fmt, url=m.group(1)))
            pos = m.end()
        if pos < len(r.text):
            out.append(Run(r.text[pos:], r.fmt))
    return out


def parse_text(text: str, expected_cites: int, bib_keys: list[tuple[int, int]], audit: Audit,
               nonce: str = "") -> HarvestResult:
    res = HarvestResult()
    text = normalise(text)
    for m in _marker(r"@@CITE (\d+)@@(.*?)@@END@@", nonce).finditer(text):
        res.cites[int(m.group(1))] = linkify(to_runs(m.group(2), nonce))
    for m in _marker(r"@@BIB (\d+) (\d+)@@(.*?)@@ENDBIB@@", nonce).finditer(text):
        key = (int(m.group(1)), int(m.group(2)))
        body = m.group(3)
        tm = _marker(r"@@TITLE@@(.*?)@@/TITLE@@", nonce).search(body)
        if tm:
            res.titles[key] = to_runs(tm.group(1), nonce)
            body = body[:tm.start()] + body[tm.end():]
        entries = []
        parts = _marker(r"@@ENTRY (\S+?)@@", nonce).split(body)
        lead = parts[0].strip()
        for i in range(1, len(parts), 2):
            ekey, etxt = parts[i], parts[i + 1]
            runs = linkify(to_runs(etxt, nonce))
            # numeric styles print a label before the marker: it ends up in the previous chunk
            entries.append((ekey, runs))
        # labels (e.g. "[1]", "[Knu84]", "12.") print before the entry marker, so the first one is in the lead
        # chunk and every other one trails the previous entry: move them back
        lm = _LABEL_AT_END.search(lead) if lead else None
        if lm and entries:
            k, rs = entries[0]
            entries[0] = (k, [Run(lm.group(1) + " ", frozenset())] + rs)
        res.bibs[key] = _attach_labels(entries)
    missing = [i for i in range(expected_cites) if i not in res.cites]
    if missing:
        audit.add("harvest", f"{len(missing)} citation(s) missing from the harvest output: {missing[:10]}")
    for key in bib_keys:
        if key not in res.bibs:
            audit.add("harvest", f"bibliography block {key} missing from the harvest output")
    res.ok = not missing
    return res


# a bibliography label as numeric and alphabetic styles print it: [1], [Knu84], (12), 12.  (at the end of a chunk;
# at most three digits for the bare form, so that a year closing an entry, "2020.", is never taken for one)
_LABEL_AT_END = re.compile(r"(?:^|\s)(\[[^\[\]\s]{1,24}\]|\([^()\s]{1,24}\)|\d{1,3}\.)\s*$")


def _attach_labels(entries: list[tuple[str, list[Run]]]) -> list[tuple[str, list[Run]]]:
    """Labels print before the entry marker, so they trail the previous entry: move them back."""
    out = []
    pending = None
    for i, (k, runs) in enumerate(entries):
        if pending:
            runs = [Run(pending + " ", frozenset())] + runs
            pending = None
        if runs and i < len(entries) - 1:  # only when an entry follows: its label is what trails this one
            last = runs[-1]
            m = _LABEL_AT_END.search(last.text)
            if m and not last.url:
                pending = m.group(1)
                head = last.text[:m.start()].rstrip()
                runs = runs[:-1] + ([Run(head, last.fmt)] if head else [])
        out.append((k, runs))
    return out


def run_harvest(pf: Preflight, doc: Document, audit: Audit, timeout: int = sandbox.COMPILE_TIMEOUT,
                side=None) -> HarvestResult:
    """Citations and bibliography entries as runs.

    BibTeX documents are read from the existing ``.bbl`` with no compile. biblatex documents need the
    harvest side compile: inside the OS sandbox when ``side`` is None, otherwise the side document is
    written to ``side.dir`` for the user to compile and the PDF they produced is used (see sidecompile)."""
    if not pf.uses_biblatex and uses_bibtex(doc):
        from .bbl import harvest_from_bbl

        return harvest_from_bbl(pf, doc, audit)
    if side is None:
        nonce = secrets.token_hex(4)  # fresh for this run
    else:
        from .sidecompile import harvest_salt

        nonce = harvest_salt(pf, side)  # stable across runs, so the side document does not change every time
    tex, bib_keys = build_harvest_tex(pf, doc, nonce)
    if side is None:
        pdf = compile_harvest(pf, tex, audit, timeout=timeout)
        res = HarvestResult(tex=pf.workdir / "harvest.tex")
    else:
        from .sidecompile import user_compiled_pdf

        pdf = user_compiled_pdf(pf, side, "harvest", tex, audit, "harvest",
                                bib="biber" if pf.uses_biblatex else "bibtex")
        res = HarvestResult(tex=side.dir / "harvest.tex")
    if pdf is None:
        res.log_errors.append("compile failed")
        return res
    if not shutil.which("pdftotext"):
        audit.add("harvest", "pdftotext not found; citations rendered as [key]")
        return res
    r = sandbox.run(["pdftotext", "-raw", str(pdf), "-"], cwd=pf.workdir, timeout=60, rw=[], ro=[pdf.parent])
    if r.returncode != 0:
        audit.add("harvest", f"pdftotext failed: {r.stderr[:200]}; citations rendered as [key]")
        return res
    txt = r.stdout
    sandbox.write_fresh(pf.workdir / "harvest.txt", txt)
    parsed = parse_text(txt, len(doc.cites), bib_keys, audit, nonce)
    parsed.tex = res.tex
    return parsed


def apply_harvest(doc: Document, res: HarvestResult, audit: Audit) -> None:
    """Attach harvested runs to the tree."""
    if not res.cites and not res.bibs:
        return
    for c in doc.cites:
        c.runs = res.cites.get(c.index)

    def visit(n):
        if isinstance(n, Bibliography) and not n.manual:
            pass

    keyed: dict[tuple[int, int], Bibliography] = {}
    counts: dict[int, int] = {}

    def collect(n):
        if isinstance(n, Bibliography) and not n.manual:
            k = counts.get(n.section, 0)
            counts[n.section] = k + 1
            keyed[(n.section, k)] = n

    walk_tree(doc, collect)
    from .parser.model import BibEntry, Text

    for key, bib in keyed.items():
        entries = res.bibs.get(key)
        if entries is not None:
            bib.entries = [BibEntry(key=k, runs=r, loc=bib.loc) for k, r in entries]
        t = res.titles.get(key)
        if t:
            bib.title = [Text(text="".join(r.text for r in t))]
