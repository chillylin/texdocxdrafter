"""End-to-end orchestration: preflight → parse → harvest → resolve → write → validate → audit."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from pylatexenc.latex2text import LatexNodes2Text

from . import fidelity
from . import harvest as harvest_mod
from . import sandbox
from . import stylemap as stylemap_mod
from . import tikz as tikz_mod
from .audit import Audit
from .parser import parse_document
from .parser.model import to_json
from .preflight import Preflight, PreflightError, changed_inputs, preflight
from .resolve import resolve
from .sidecompile import SideDir
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


class OutputValidationError(RuntimeError):
    """The docx the writer produced failed validation; nothing was published."""


class SourceChangedError(PreflightError):
    """An input changed while the document was being converted; nothing was published."""


class PublishError(RuntimeError):
    """Publishing failed and the files already written could not all be put back: the message says which."""


_LAYOUT = [
    re.compile(r"\\(?:multicolumn|multirow)\*?(?:\[[^\]]*\])?\{[^{}]*\}\{[^{}]*\}"),
    re.compile(r"\\cmidrule(?:\([^)]*\))?(?:\[[^\]]*\])?\{[^}]*\}"),
    re.compile(r"\\begin\{(?:tabular\*?|tabularx|tabulary|longtable)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})+"),
    re.compile(r"\\begin\{(?:enumerate|itemize|description)\}\[[^\]]*\]"),
]


def _resolve_flags(body: str, flags: dict) -> str:
    """Keep the taken branch of every ``\\if<flag> … \\else … \\fi`` for the flags of ``\\newif`` (and
    ``\\iffalse``), in one linear pass: a regex per flag with a lazy body rescans to the end of the text at
    every occurrence it cannot close."""
    names = {name: bool(val) for name, val in flags.items()}
    names["false"] = False
    tok = re.compile(r"\\(?:if(%s)\b|(else)\b|(fi)\b)" % "|".join(re.escape(n) for n in names))
    out: list[str] = []
    stack: list[list] = []  # [flag value, in the else branch] of the open conditionals
    off = 0  # how many of them are in the branch not taken (kept as a count: the stack can be deep)
    pos = 0
    for m in tok.finditer(body):
        active = off == 0
        if active:
            out.append(body[pos:m.start()])
        pos = m.end()
        if m.group(1):
            val = names[m.group(1)]
            stack.append([val, False])
            off += not val
        elif not stack:  # \else or \fi of some other conditional: ordinary text here
            pos = m.start()
            continue
        elif m.group(2):
            val, in_else = stack[-1]
            if not in_else:
                stack[-1][1] = True
                off += 1 if val else -1
        else:
            val, in_else = stack.pop()
            off -= val == in_else
        if active:
            out.append(" ")  # the token itself separated words
    if off == 0:
        out.append(body[pos:])
    return "".join(out)


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


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _registry_path(config_dir: Path) -> Path:
    return config_dir / REGISTRY


def _registry_load(config_dir: Path) -> dict:
    """The hash registry, or {} when there is none. A file that is not a registry this tool wrote is an error:
    the file is never silently replaced."""
    path = _registry_path(config_dir)
    if path.is_symlink() or path.is_dir():
        raise PreflightError(f"{path} is a symbolic link or a directory; refusing to use it as the hash registry")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise PreflightError(f"cannot read {path}: {exc}")
    try:
        reg = json.loads(text)
    except ValueError:
        reg = None
    if not isinstance(reg, dict) or not all(isinstance(k, str) and isinstance(v, str) and _HEX64.match(v)
                                            for k, v in reg.items()):
        raise PreflightError(f"{path} is not a texdocxdrafter hash registry (a JSON object of path: sha256); "
                             "move it away to continue")
    return reg


def _registry_vet(config_dir: Path, pf: Preflight) -> Path:
    """The registry path, checked not to collide with a source file (``\\input{main.texdocxdrafter/outputs.json}``)."""
    path = _registry_path(config_dir)
    if path.resolve() in _source_files(pf):
        raise PreflightError(f"{path} is one of the document's source files; the hash registry cannot live there")
    return path


class Registry:
    """The hash registry of ``<main>.texdocxdrafter/outputs.json``: every file the tool wrote, by sha256.

    ``save()`` may be called several times in a run (after the side documents, at the end); each save
    refuses if something else changed the file meanwhile."""

    def __init__(self, config_dir: Path, pf: Preflight):
        self.path = _registry_vet(config_dir, pf)
        self.data = _registry_load(config_dir)
        self.snapshot = _snapshot(self.path)

    def owns(self, path) -> bool:
        return _is_own_output(Path(path), self.data)

    def record(self, path) -> None:
        self.data[str(path)] = _sha256(Path(path))

    def staged(self) -> tuple:
        """The registry as a ``publish`` item: (path, bytes, snapshot, what)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return (self.path, json.dumps(self.data, indent=1).encode("utf-8"), self.snapshot, "hash registry")

    def save(self) -> None:
        publish([self.staged()])
        self.snapshot = _sha256(self.path)


def _is_own_output(path: Path, reg: dict) -> bool:
    """True when ``path`` is byte-for-byte a file this tool wrote (recorded hash), so nobody has edited it since."""
    digest = reg.get(str(path))
    return bool(digest) and path.is_file() and _sha256(path) == digest


def _snapshot(path: Path) -> str | None:
    """sha256 of the vetted target as it is now, or None when it does not exist (so the commit must create it)."""
    return _sha256(path) if path.is_file() else None


def _fsync_dir(path: Path) -> None:
    """Make the directory entry durable after a rename, where the platform allows it (best effort)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _write_tmp(path: Path, data: bytes, mode: int | None = None) -> str:
    """``data`` in an exclusively created temporary file beside ``path``, with ``mode`` (the mode of the file it
    will replace) or, for a new file, the usual 0666 minus the umask: mkstemp's 0600 would hide the output from
    coauthors in a shared folder."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, mode if mode is not None else 0o666 & ~_umask())
        except OSError:
            pass  # FAT/exFAT cannot store modes (EPERM): the content matters, the mode is best effort
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _mode_of(path: Path) -> int | None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return stat.S_IMODE(st.st_mode) if stat.S_ISREG(st.st_mode) else None


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through an exclusively created temporary file in the same directory; an existing
    file keeps its mode."""
    tmp = _write_tmp(path, data, _mode_of(path))
    try:
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def commit_file(path: Path, data: bytes, snapshot: str | None, what: str = "file") -> None:
    """Write ``data`` to a target vetted earlier, refusing if the target changed meanwhile.

    ``snapshot`` is what ``_snapshot`` returned at vetting time. None: the target did not exist, so it is
    created exclusively and anything that appeared since is left alone. Otherwise the file at the target is
    opened (never through a symbolic link), its content must still hash to ``snapshot``, and its identity
    (device, inode, size, times) is compared once more right before the rename. What remains is the gap
    between that comparison and the rename itself; a path-based replace cannot close it entirely."""
    tmp = _write_tmp(path, data, _mode_of(path) if snapshot is not None else None)
    try:
        if snapshot is None:
            appeared = PreflightError(f"{what} {path} appeared while the document was being converted; "
                                      "not touching it (rerun to replace it if it is yours)")
            try:
                os.link(tmp, path)  # fails with EEXIST if anything, even a symlink, appeared at the target
            except FileExistsError:
                raise appeared
            except OSError:  # hard links unsupported (FAT, some shares): reserve the name exclusively, then rename
                try:
                    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | sandbox.O_BINARY, 0o644))
                except FileExistsError:
                    raise appeared
                try:
                    os.replace(tmp, path)  # the data was written to tmp already: never a half-written target
                    tmp = None
                except BaseException:
                    try:
                        os.unlink(path)  # our empty reservation
                    except OSError:
                        pass
                    raise
            _fsync_dir(path.parent)
            return  # tmp is now a second name of the new target (or None): the finally removes it
        changed = PreflightError(f"{what} {path} was changed by something else while the document was being "
                                 "converted; not touching it (rerun to replace it if it is yours)")
        try:
            fd = sandbox.open_nofollow(path, os.O_RDONLY)
        except OSError:
            raise changed
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise changed
            h = hashlib.sha256()
            for chunk in iter(lambda: os.read(fd, 1 << 20), b""):
                h.update(chunk)
        finally:
            os.close(fd)
        if h.hexdigest() != snapshot:
            raise changed
        try:  # fstat again, not stat: on Windows the two disagree on st_dev and st_ino for the same file
            fd = sandbox.open_nofollow(path, os.O_RDONLY)
            try:
                now = os.fstat(fd)
            finally:
                os.close(fd)
        except OSError:
            raise changed
        if _identity(now) != _identity(st):
            raise changed
        os.replace(tmp, path)
        tmp = None
        _fsync_dir(path.parent)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def publish(items: list[tuple]) -> None:
    """Commit several files as one step: ``items`` are (path, data, snapshot, what) as for ``commit_file``.

    If any commit fails, the files already written are put back: a replaced file gets its previous content,
    a created file is removed. The three files of a build (report, docx, registry) therefore change together
    or not at all, as far as a sequence of renames allows; a crash between two of them is the residual."""
    done: list[tuple[Path, bytes | None]] = []
    try:
        for path, data, snapshot, what in items:
            previous = path.read_bytes() if snapshot is not None else None
            commit_file(path, data, snapshot, what)
            done.append((path, previous))
    except BaseException as exc:
        failed: list[str] = []
        restored: list[str] = []
        for path, previous in reversed(done):
            try:
                if previous is None:
                    os.unlink(path)
                else:
                    write_atomic(path, previous)
                restored.append(path.name)
            except OSError as e2:
                failed.append(f"{path.name} ({e2.strerror or e2})")
        if failed:
            raise PublishError(f"publishing failed ({exc}); {', '.join(failed)} could not be put back and still "
                               f"hold the new build" + (f", {', '.join(restored)} were restored" if restored else "")
                               + "; when the problem is fixed, rerun with --overwrite to bring them in line") from exc
        raise


def _source_files(pf: Preflight) -> set[Path]:
    return {pf.main.resolve()} | {(pf.srcdir / f).resolve() for f, _ in pf.source.origins if (pf.srcdir / f).exists()}


def _samefile(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def vet_target(path: Path, what: str, pf: Preflight, reg: dict, overwrite: bool) -> Path:
    """Refuse a target that is a source file, a symlink, a directory, or an existing file this tool did not write."""
    sources = _source_files(pf)
    if path.resolve() in sources or (path.exists() and any(_samefile(path, src) for src in sources)):
        raise PreflightError(f"{what} {path.name} is one of the source files")  # also a hard link to one
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
    if dest.is_symlink() or dest.exists():
        if not dest.is_symlink() and dest.is_dir() and (dest / WORK_MARKER).is_file():
            shutil.rmtree(dest)
        else:  # a symlink, a file, or a directory this tool did not make: leave it alone
            dest = config_dir / f"work-{time.strftime('%Y%m%d-%H%M%S')}"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(dest))
    log(f"work directory kept at {dest}")
    return dest


def build(main, out=None, style_map=None, reference_doc=None, link_citations=False, keep_work=False,
          no_harvest=False, no_tikz=False, validate_lo=False, comments=False, overwrite=False, allow_dirs=(),
          timeout=sandbox.COMPILE_TIMEOUT, strict=False, log=print) -> BuildResult:
    """Convert ``main`` and publish the docx, its report and the hash registry.

    Nothing is published before the docx passed structural validation and the policy check (an error there
    raises OutputValidationError), and before every input is confirmed unchanged since it was read
    (SourceChangedError). The word-count and content checks are reported; with ``strict`` they are fatal too."""
    t0 = time.time()
    audit = Audit()
    for what in sandbox.limit_self():  # also when build() is called as a library, not through the CLI
        audit.add("other", f"could not limit the tool's own process: {what}", severity="info")
    pf = preflight(main, need_build_files=not no_harvest, allow_dirs=allow_dirs)
    config_dir = prepare_workdir(pf)
    sm_path = Path(style_map) if style_map else default_stylemap_path(pf)
    sm = stylemap_mod.load(sm_path)
    if reference_doc:
        sm["options"]["reference_doc"] = str(reference_doc)
    reference_doc = reference_doc or sm["options"].get("reference_doc")
    registry = Registry(config_dir, pf)
    reg = registry.data
    out = output_path(pf, out, overwrite, reg)
    report = vet_target(report_path(out), "report", pf, reg, overwrite)
    if reference_doc and (Path(reference_doc).resolve() in (out.resolve(), report.resolve())
                          or _samefile(Path(reference_doc), out)):
        raise PreflightError(f"the output {out.name} is the reference document itself; choose another output path")
    # what the vetted targets look like now; the commit at the end refuses if anything changed them meanwhile
    snaps = {out: _snapshot(out), report: _snapshot(report)}
    for issue in pf.issues:
        audit.add("preflight", issue)
    audit.info["engine"] = pf.engine
    audit.info["document class"] = pf.docclass
    # without an OS sandbox the tool runs no TeX engine: side documents go to the config dir for the user to compile
    side = None if sandbox.have_os_sandbox() else SideDir(config_dir, registry)
    audit.info["sandbox"] = ("bubblewrap (only system, TeX and source directories visible; no network) plus TeX restrictions"
                             if side is None else
                             f"none (no bubblewrap): no TeX engine was run; side documents are written to {config_dir.name}/ "
                             "for you to compile")
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
            log("harvesting citations with biblatex (side compile)" if pf.uses_biblatex else
                f"reading citations from {pf.bbl.name}")
            hv = harvest_mod.run_harvest(pf, doc, audit, timeout=timeout, side=side)
            audit.info["harvest"] = f"{len(hv.cites)}/{len(doc.cites)} citations, " \
                                    f"{sum(len(v) for v in hv.bibs.values())} bibliography entries"
        pictures = tikz_mod.tikz_images(doc)
        if pictures and no_tikz:
            audit.add("figure", f"{len(pictures)} tikzpicture(s) kept as visible text (--no-tikz)", pictures[0].loc)
        elif pictures:
            log("rendering tikzpicture environments")
            tikz_mod.render_tikz(pf, doc, audit, timeout=timeout, side=side)
        if side is not None:
            registry.save()  # the side documents are recorded even if the rest of the run fails
        res = resolve(doc, pf, audit, hv)

        log(f"writing {out.name}")
        writer = DocxWriter(res, pf, sm, audit, template=reference_doc, link_citations=link_citations)
        buf = io.BytesIO()
        writer.write(buf, comments=comments)
        data = buf.getvalue()
        # everything is checked on a private copy in the work directory; the target is not touched until all passed
        candidate = run_dir / out.name
        sandbox.write_fresh(candidate, data)
        try:
            chk = check_docx(candidate)
        except Exception as exc:  # not even a readable package
            raise OutputValidationError(f"{out.name} could not be validated ({exc}); nothing was written")
        for e in chk.errors:
            audit.add("other", f"validation: {e}")
        pct = word_loss(pf, candidate, audit)
        mismatches = fidelity.check(doc, candidate, sm, audit)
        if validate_lo:
            _libreoffice(candidate, audit)
        if chk.errors:
            raise OutputValidationError(f"{out.name} failed validation ({len(chk.errors)} error(s): "
                                        f"{'; '.join(chk.errors[:3])}); nothing was written")
        if strict and (pct > 2.0 or mismatches):
            raise OutputValidationError("--strict: " + "; ".join(([f"word loss {pct:.1f}%"] if pct > 2.0 else []) + mismatches)
                                        + "; nothing was written")
        changed = changed_inputs(pf)
        if changed:
            raise SourceChangedError(f"{', '.join(Path(c).name for c in changed[:3])} changed while the document was "
                                     "being converted; nothing was written (rerun)")
        audit.info["elapsed"] = f"{time.time() - t0:.1f} s"
        report_text = audit.render(pf.main.name, stylemap=sm, style_usage=dict(chk.paragraph_styles)).encode("utf-8")
        reg[str(report)] = hashlib.sha256(report_text).hexdigest()
        reg[str(out)] = hashlib.sha256(data).hexdigest()
        # report, docx, registry as one step: on a failure the ones already written are put back
        publish([(report, report_text, snaps[report], "report"), (out, data, snaps[out], "output"), registry.staged()])
        registry.snapshot = _sha256(registry.path)
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
        # sandboxed like every other tool: it sees the docx (read-only) and a private profile and output directory
        env = dict(os.environ, HOME=str(tmp))
        r = sandbox.run([exe, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--convert-to", "pdf",
                         "--outdir", str(tmp), str(out)], cwd=tmp, env=env, timeout=300, rw=[tmp], ro=[out.parent])
        ok = r.returncode == 0 and (tmp / (out.stem + ".pdf")).is_file()  # ``out`` may be the private candidate
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    audit.add("other", "LibreOffice opened and converted the file" if ok else
              f"LibreOffice could not convert the file inside the sandbox: {(r.stderr or r.stdout)[:200]}",
              severity="info" if ok else "warn")
