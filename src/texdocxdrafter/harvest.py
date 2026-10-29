"""Stage 5: citations and bibliography rendered by the real biblatex.

A side document reuses the original preamble, typesets every citation command in document order and
``\\printbibliography`` on a very large page with text markers for italics and entry boundaries, and
``pdftotext -raw`` turns the PDF back into text. Markers are parsed into runs with formats.
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox
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


def build_harvest_tex(pf: Preflight, doc: Document) -> tuple[str, list[tuple[int, int]]]:
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
            out.append("{\\ttfamily @@CITE %d@@} %s{\\ttfamily @@END@@}\\par" % (c.index, raw))
        for k, b in enumerate([b for b in bibs if b.section == s]):
            out.append("{\\ttfamily @@BIB %d %d@@}\\par" % (s, k))
            out.append("\\bibliography{%s}" % b.bibtex if b.bibtex is not None
                       else "\\printbibliography" + option_string(b.options))
            out.append("{\\ttfamily @@ENDBIB@@}\\par")
            bib_keys.append((s, k))
        if s > 0:
            out.append("\\end{refsection}")
    out.append("\\end{document}")
    return "\n".join(out) + "\n", bib_keys


def compile_harvest(pf: Preflight, tex: str, audit: Audit, timeout: int = sandbox.COMPILE_TIMEOUT) -> Path | None:
    """Compile the harvest document in the run's private work directory.

    The engine runs with shell escape off and ``openout_any=p``, reads inputs from the source directory
    (plus ``--allow-dir`` directories) and can write only into ``work``."""
    work = pf.workdir
    work.mkdir(parents=True, exist_ok=True)
    (work / "harvest.tex").write_text(tex, encoding="utf-8")
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
    log = work / "harvest.log"
    if log.exists():
        text = log.read_text(errors="replace")
        missing = sorted(set(re.findall(r"didn't find a database entry for '([^']*)'", text)
                             + re.findall(r"Citation `([^']*)' (?:on page \S+ )?undefined", text)))
        if missing:
            audit.add("harvest", f"{len(missing)} cited key(s) not found in the .bib files "
                      f"(shown as keys): {', '.join(missing[:12])}")
        for bf in sorted(set(re.findall(r"Cannot find '([^']*\.bib)'|Cannot find (\S+\.bib)", text)))[:3]:
            audit.add("harvest", f"bibliography database not found: {bf[0] or bf[1]}")
    pdf = work / "harvest.pdf"
    if not pdf.exists() or pdf.stat().st_size == 0:
        log = work / "harvest.log"
        errs = []
        if log.exists():
            errs = [l for l in log.read_text(errors="replace").splitlines() if l.startswith("!")][:5]
        audit.add("harvest", f"harvest compile failed ({engine}): {'; '.join(errs) or r.stderr[:200]}")
        return None
    return pdf


_LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_TOKEN = re.compile(r"@@(/?)(I|B|SUP|SUB)@@")


def normalise(text: str) -> str:
    text = "".join(_LIG.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFC", text)
    return text.replace("­", "").replace("\x0c", "\n")


def to_runs(s: str) -> list[Run]:
    """Marked text → runs with formats. Italic markers toggle (an \\emph inside \\emph is upright)."""
    s = re.sub(r"\s+", " ", s)
    runs: list[Run] = []
    depth = {"I": 0, "B": 0, "SUP": 0, "SUB": 0}
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
        pos = 0
        for m in url_re.finditer(r.text):
            if m.start() > pos:
                out.append(Run(r.text[pos:m.start()], r.fmt))
            out.append(Run(m.group(1), r.fmt, url=m.group(1)))
            pos = m.end()
        if pos < len(r.text):
            out.append(Run(r.text[pos:], r.fmt))
    return out


def parse_text(text: str, expected_cites: int, bib_keys: list[tuple[int, int]], audit: Audit) -> HarvestResult:
    res = HarvestResult()
    text = normalise(text)
    for m in re.finditer(r"@@CITE (\d+)@@(.*?)@@END@@", text, re.S):
        res.cites[int(m.group(1))] = linkify(to_runs(m.group(2)))
    for m in re.finditer(r"@@BIB (\d+) (\d+)@@(.*?)@@ENDBIB@@", text, re.S):
        key = (int(m.group(1)), int(m.group(2)))
        body = m.group(3)
        tm = re.search(r"@@TITLE@@(.*?)@@/TITLE@@", body, re.S)
        if tm:
            res.titles[key] = to_runs(tm.group(1))
            body = body[:tm.start()] + body[tm.end():]
        entries = []
        parts = re.split(r"@@ENTRY (\S+?)@@", body)
        lead = parts[0].strip()
        for i in range(1, len(parts), 2):
            ekey, etxt = parts[i], parts[i + 1]
            runs = linkify(to_runs(etxt))
            # numeric styles print a label before the marker: it ends up in the previous chunk
            entries.append((ekey, runs))
        # labels (e.g. "[1]") that precede the next marker are at the tail of the previous chunk; peel them off
        fixed = []
        for i, (ekey, runs) in enumerate(entries):
            fixed.append((ekey, runs))
        if lead and re.fullmatch(r"\[?\(?\d+[\]\).]?", lead) and fixed:
            k, rs = fixed[0]
            fixed[0] = (k, [Run(lead + " ", frozenset())] + rs)
        res.bibs[key] = _attach_labels(fixed)
    missing = [i for i in range(expected_cites) if i not in res.cites]
    if missing:
        audit.add("harvest", f"{len(missing)} citation(s) missing from the harvest output: {missing[:10]}")
    for key in bib_keys:
        if key not in res.bibs:
            audit.add("harvest", f"bibliography block {key} missing from the harvest output")
    res.ok = not missing
    return res


def _attach_labels(entries: list[tuple[str, list[Run]]]) -> list[tuple[str, list[Run]]]:
    """Numeric labels print before the entry marker, so they trail the previous entry: move them back."""
    out = []
    label_re = re.compile(r"\s*(\[\d+\]|\(\d+\)|\d+\.)\s*$")
    pending = None
    for k, runs in entries:
        if pending:
            runs = [Run(pending + " ", frozenset())] + runs
            pending = None
        if runs:
            last = runs[-1]
            m = label_re.search(last.text) if re.search(r"[\.\)\]]\s*(\[\d+\]|\(\d+\))\s*$", last.text) else None
            if m and len(entries) > 1:
                pending = m.group(1)
                runs = runs[:-1] + ([Run(last.text[:m.start()], last.fmt)] if last.text[:m.start()].strip() else [])
        out.append((k, runs))
    return out


def run_harvest(pf: Preflight, doc: Document, audit: Audit, timeout: int = sandbox.COMPILE_TIMEOUT,
                side_dir: Path | None = None) -> HarvestResult:
    """Citations and bibliography entries as runs.

    BibTeX documents are read from the existing ``.bbl`` with no compile. biblatex documents need the
    harvest side compile: inside the OS sandbox when ``side_dir`` is None, otherwise the side document is
    written to ``side_dir`` for the user to compile and the PDF they produced is used (see sidecompile)."""
    if not pf.uses_biblatex and uses_bibtex(doc):
        from .bbl import harvest_from_bbl

        return harvest_from_bbl(pf, doc, audit)
    tex, bib_keys = build_harvest_tex(pf, doc)
    if side_dir is None:
        pdf = compile_harvest(pf, tex, audit, timeout=timeout)
        res = HarvestResult(tex=pf.workdir / "harvest.tex")
    else:
        from .sidecompile import user_compiled_pdf

        pdf = user_compiled_pdf(pf, side_dir, "harvest", tex, audit, "harvest",
                                bib="biber" if pf.uses_biblatex else "bibtex")
        res = HarvestResult(tex=side_dir / "harvest.tex")
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
    (pf.workdir / "harvest.txt").write_text(txt, encoding="utf-8")
    parsed = parse_text(txt, len(doc.cites), bib_keys, audit)
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
