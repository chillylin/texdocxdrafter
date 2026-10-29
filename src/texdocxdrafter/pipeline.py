"""End-to-end orchestration: preflight → parse → harvest → resolve → write → validate → audit."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from pylatexenc.latex2text import LatexNodes2Text

from . import harvest as harvest_mod
from . import sandbox
from . import stylemap as stylemap_mod
from . import tikz as tikz_mod
from .audit import Audit
from .parser import parse_document
from .parser.model import to_json
from .preflight import Preflight, PreflightError, preflight
from .resolve import resolve
from .validate import CheckResult, check_docx
from .writer import DocxWriter

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_STRIP_CMDS = re.compile(
    r"\\(?:label|ref|eqref|pageref|autoref|cref|Cref|vref|nameref|nocite|includegraphics|vspace|hspace|setcounter|"
    r"thispagestyle|rule|color|url|input|bibliography\w*|printbibliography|cite\w*|parencite\w*|textcite\w*|"
    r"footcite\w*|autocite\w*|smartcite\w*|fullcite|supercite\w*|hypersetup)\*?(?:\s*\[[^\]]*\])*(?:\s*\{[^{}]*\})?")
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\[.*?\\\]|\\\(.*?\\\)|"
                   r"\\begin\{(equation|align|gather|multline|eqnarray|flalign|displaymath)\*?\}.*?\\end\{\1\*?\}", re.S)


@dataclass
class BuildResult:
    out: Path
    audit: Audit
    check: CheckResult
    report: Path


_LAYOUT = [
    re.compile(r"\\(?:multicolumn|multirow)\*?(?:\[[^\]]*\])?\{[^{}]*\}\{[^{}]*\}"),
    re.compile(r"\\cmidrule(?:\([^)]*\))?(?:\[[^\]]*\])?\{[^}]*\}"),
    re.compile(r"\\begin\{(?:tabular\*?|tabularx|tabulary|longtable)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})+"),
    re.compile(r"\\begin\{(?:enumerate|itemize|description)\}\[[^\]]*\]"),
]


def _resolve_flags(body: str, flags: dict) -> str:
    for name, val in flags.items():
        pat = re.compile(r"\\if%s\b(.*?)(?:\\else(.*?))?\\fi" % re.escape(name), re.S)
        body = pat.sub(lambda m: (m.group(1) if val else (m.group(2) or "")), body)
    body = re.sub(r"\\iffalse.*?\\fi", " ", body, flags=re.S)
    return body


def source_words(pf: Preflight) -> Counter:
    body = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", " ", pf.body, flags=re.S)
    body = _resolve_flags(body, pf.bool_flags)
    body = re.sub(r"\\maketitle\b", " ", body)
    for pat in _LAYOUT:
        body = pat.sub(" ", body)
    body = _MATH.sub(" ", body)
    body = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", " ", body, flags=re.S)
    body = _STRIP_CMDS.sub(" ", body)
    try:
        text = LatexNodes2Text(math_mode="text").latex_to_text(body)
    except Exception:
        text = body
    return Counter(w.lower() for w in re.findall(r"[^\W\d_]{2,}", text))


def docx_words(path: Path) -> Counter:
    z = zipfile.ZipFile(path)
    c: Counter = Counter()
    for part in ("word/document.xml", "word/footnotes.xml"):
        if part not in z.namelist():
            continue
        root = etree.fromstring(z.read(part))
        for p in root.iter("{%s}p" % W):
            ps = p.find("{%s}pPr/{%s}pStyle" % (W, W))
            if ps is not None and ps.get("{%s}val" % W) == "Bibliography":
                continue
            for r in p.iter("{%s}r" % W):
                rs = r.find("{%s}rPr/{%s}rStyle" % (W, W))
                if rs is not None and rs.get("{%s}val" % W) == "Citation":
                    continue
                for t in r.iter("{%s}t" % W, "{%s}delText" % W):
                    c.update(w.lower() for w in re.findall(r"[^\W\d_]{2,}", t.text or ""))
    return c


def word_loss(pf: Preflight, out: Path, audit: Audit) -> float:
    src, dst = source_words(pf), docx_words(out)
    total = sum(src.values()) or 1
    lost = src - dst
    n = sum(lost.values())
    pct = 100.0 * n / total
    audit.info["source words"] = total
    audit.info["word loss"] = f"{n} ({pct:.2f}%)"
    if pct > 2.0:
        top = ", ".join(f"{w}x{k}" for w, k in lost.most_common(12))
        audit.add("words", f"word loss {pct:.1f}% exceeds the 2% limit; most frequent missing words: {top}")
    return pct


def prepare_workdir(pf: Preflight) -> Path:
    """The per-document configuration directory (``<main>.texdocxdrafter/``): holds stylemap.yaml and kept work."""
    pf.workdir.mkdir(parents=True, exist_ok=True)
    return pf.workdir


def default_stylemap_path(pf: Preflight) -> Path:
    return pf.workdir / "stylemap.yaml"


WORK_MARKER = ".texdocxdrafter-work"  # written into a work directory this tool created, so only those are ever removed
REGISTRY = "outputs.json"  # sha256 of every file this tool wrote, keyed by absolute path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _registry_load(config_dir: Path) -> dict:
    try:
        return json.loads((config_dir / REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _registry_save(config_dir: Path, reg: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(config_dir / REGISTRY, json.dumps(reg, indent=1).encode("utf-8"))


def _is_own_output(path: Path, reg: dict) -> bool:
    """True when ``path`` is byte-for-byte a file this tool wrote (recorded hash), so nobody has edited it since."""
    digest = reg.get(str(path))
    return bool(digest) and path.is_file() and _sha256(path) == digest


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through an exclusively created temporary file in the same directory."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def vet_target(path: Path, what: str, pf: Preflight, reg: dict, overwrite: bool) -> Path:
    """Refuse a target that is a source file, a symlink, a directory, or an existing file this tool did not write."""
    sources = {pf.main.resolve()} | {(pf.srcdir / f).resolve() for f, _ in pf.source.origins if (pf.srcdir / f).exists()}
    if path.resolve() in sources:
        raise PreflightError(f"{what} {path.name} is one of the source files")
    if path.is_symlink():
        raise PreflightError(f"{what} {path} is a symbolic link; refusing to write through it")
    if path.exists():
        if path.is_dir():
            raise PreflightError(f"{what} {path} is a directory")
        if not overwrite and not _is_own_output(path, reg):
            raise PreflightError(f"{path.name} exists and was not written by texdocxdrafter (or has been edited since); "
                                 "pass --overwrite to replace it")
    return path


def output_path(pf: Preflight, out, overwrite: bool = False, reg: dict | None = None) -> Path:
    """Resolve and vet the output path: a .docx that is not a source file and not someone else's document."""
    out = Path(out).absolute() if out else pf.main.with_suffix(".docx")
    if out.suffix.lower() != ".docx":
        raise PreflightError(f"output must be a .docx file, not {out.name}")
    return vet_target(out, "output", pf, reg if reg is not None else _registry_load(pf.workdir), overwrite)


def report_path(out: Path) -> Path:
    return out.with_name(out.stem + ".texdocxdrafter-report.md")


def _keep_work(run_dir: Path, config_dir: Path, log) -> Path:
    """Move the run's work directory under the configuration directory, replacing only a directory this tool made."""
    dest = config_dir / "work"
    if dest.exists():
        if dest.is_dir() and (dest / WORK_MARKER).exists():
            shutil.rmtree(dest)
        else:
            dest = config_dir / f"work-{time.strftime('%Y%m%d-%H%M%S')}"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(dest))
    log(f"work directory kept at {dest}")
    return dest


def build(main, out=None, style_map=None, reference_doc=None, link_citations=False, keep_work=False,
          no_harvest=False, no_tikz=False, validate_lo=False, comments=False, overwrite=False, allow_dirs=(),
          timeout=sandbox.COMPILE_TIMEOUT, trust=False, log=print) -> BuildResult:
    t0 = time.time()
    audit = Audit()
    pf = preflight(main, need_build_files=not no_harvest, allow_dirs=allow_dirs)
    config_dir = prepare_workdir(pf)
    sm_path = Path(style_map) if style_map else default_stylemap_path(pf)
    sm = stylemap_mod.load(sm_path)
    if reference_doc:
        sm["options"]["reference_doc"] = str(reference_doc)
    reference_doc = reference_doc or sm["options"].get("reference_doc")
    reg = _registry_load(config_dir)
    out = output_path(pf, out, overwrite, reg)
    report = vet_target(report_path(out), "report", pf, reg, overwrite)
    for issue in pf.issues:
        audit.add("preflight", issue)
    audit.info["engine"] = pf.engine
    audit.info["document class"] = pf.docclass
    audit.info["sandbox"] = ("bubblewrap (only system, TeX and source directories visible; no network) plus TeX restrictions"
                             if sandbox.have_os_sandbox() else
                             "TeX restrictions and LuaTeX write guard only; install bubblewrap for an OS sandbox")
    skip_reason = sandbox.side_compiles_allowed(pf.engine, trust)
    if skip_reason:
        audit.add("harvest", skip_reason)
        no_harvest = no_tikz = True
    if pf.numberwithin:
        audit.add("numbering", "numbering is continuous in Word; LaTeX used: " + "; ".join(pf.numberwithin))

    # all intermediate files go to a private temporary directory, never next to the user's files
    run_dir = Path(tempfile.mkdtemp(prefix="texdocxdrafter-"))
    (run_dir / WORK_MARKER).write_text("created by texdocxdrafter; safe to delete\n")
    pf.workdir = run_dir
    try:
        log(f"parsing {pf.main.name}")
        doc = parse_document(pf, audit, sm)
        (run_dir / "tree.json").write_text(json.dumps(to_json(doc), ensure_ascii=False, indent=1), encoding="utf-8")

        hv = None
        if no_harvest or not doc.cites and not _has_bib(doc):
            if doc.cites and no_harvest:
                audit.add("harvest", "harvest skipped (--no-harvest): citations rendered as [key]")
        elif not pf.uses_biblatex and not harvest_mod.uses_bibtex(doc):
            audit.add("harvest", "document uses neither biblatex nor \\bibliography: citations rendered as [key]")
        else:
            log("harvesting citations with %s (side compile)" % ("biblatex" if pf.uses_biblatex else "bibtex"))
            hv = harvest_mod.run_harvest(pf, doc, audit, timeout=timeout)
            audit.info["harvest"] = f"{len(hv.cites)}/{len(doc.cites)} citations, " \
                                    f"{sum(len(v) for v in hv.bibs.values())} bibliography entries"
        pictures = tikz_mod.tikz_images(doc)
        if pictures and no_tikz:
            audit.add("figure", f"{len(pictures)} tikzpicture(s) kept as visible text (--no-tikz)", pictures[0].loc)
        elif pictures:
            log("rendering tikzpicture environments")
            tikz_mod.render_tikz(pf, doc, audit, timeout=timeout)
        res = resolve(doc, pf, audit, hv)

        log(f"writing {out.name}")
        writer = DocxWriter(res, pf, sm, audit, template=reference_doc, link_citations=link_citations)
        buf = io.BytesIO()
        writer.write(buf, comments=comments)
        write_atomic(out, buf.getvalue())  # exclusive temporary file, then rename: never half written, never via a symlink
        reg[str(out)] = _sha256(out)
        chk = check_docx(out)
        for e in chk.errors:
            audit.add("other", f"validation: {e}")
        word_loss(pf, out, audit)
        if validate_lo:
            _libreoffice(out, audit)
        audit.info["elapsed"] = f"{time.time() - t0:.1f} s"
        write_atomic(report, audit.render(pf.main.name, stylemap=sm, style_usage=dict(chk.paragraph_styles)).encode("utf-8"))
        reg[str(report)] = _sha256(report)
        _registry_save(config_dir, reg)
    finally:
        pf.workdir = config_dir
        if keep_work:
            _keep_work(run_dir, config_dir, log)
        else:
            shutil.rmtree(run_dir, ignore_errors=True)
    return BuildResult(out, audit, chk, report)


def _has_bib(doc) -> bool:
    from .parser.model import Bibliography, walk_tree

    found = []
    walk_tree(doc, lambda n: found.append(1) if isinstance(n, Bibliography) else None)
    return bool(found)


def _libreoffice(out: Path, audit: Audit) -> None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        audit.add("other", "--validate-libreoffice requested but LibreOffice is not installed", severity="info")
        return
    tmp = Path(tempfile.mkdtemp(prefix="texdocxdrafter-lo-"))
    try:
        r = sandbox.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(out)], cwd=tmp, timeout=300)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = r.returncode == 0
    audit.add("other", "LibreOffice opened and converted the file" if ok else f"LibreOffice failed: {r.stderr[:200]}",
              severity="info" if ok else "warn")
