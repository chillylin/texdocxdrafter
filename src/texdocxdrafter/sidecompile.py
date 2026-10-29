"""Side documents compiled by the user instead of the tool.

Without an OS sandbox the tool never starts a TeX engine. It writes the side document (the citation
harvest or the TikZ pictures) into ``main.texdocxdrafter/``, tells the user how to compile it with their
own TeX setup, and on the next run reads the PDF they produced. The document is rewritten only when its
content changes and only if the tool wrote the previous one (hash registry), and a PDF counts only when it
is newer than a document the tool is recorded as having written.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from .audit import Audit
from .preflight import Preflight, PreflightError


@dataclass
class SideDir:
    """Where the user-compiled side documents live, and the registry that records what the tool wrote."""
    dir: Path
    registry: "Registry"  # pipeline.Registry


def compile_command(pf: Preflight, side_dir: Path, stem: str, bib: str | None) -> str:
    """The shell line that compiles ``side_dir/stem.tex`` the way the tool would have. Every path is quoted."""
    dirs = [pf.srcdir] + list(pf.allow_dirs)
    inputs = shlex.quote("".join(f"{d}:" for d in dirs))
    env = f"TEXINPUTS={inputs} BIBINPUTS={inputs} BSTINPUTS={inputs} "
    engine = shlex.quote(Path(pf.engine).name)
    steps = [f"{engine} {stem}"]
    if bib:
        steps += [f"{shlex.quote(bib)} {stem}", f"{engine} {stem}", f"{engine} {stem}"]
    return f"cd {shlex.quote(str(side_dir))} && {env}" + " && ".join(steps)


def _vet(path: Path, pf: Preflight) -> None:
    if path.is_symlink() or path.is_dir():
        raise PreflightError(f"{path} is a symbolic link or a directory; refusing to write the side document there")
    sources = {pf.main.resolve()} | {(pf.srcdir / f).resolve() for f, _ in pf.source.origins if (pf.srcdir / f).exists()}
    if path.resolve() in sources:
        raise PreflightError(f"{path} is one of the document's source files; the side document cannot live there")


def _write_side_document(path: Path, text: str, pf: Preflight, registry) -> bool:
    """Write ``text`` to ``path`` unless it already holds it. An existing file the tool did not write is never replaced."""
    from .pipeline import _snapshot, commit_file

    _vet(path, pf)
    data = text.encode("utf-8")
    if path.is_file():
        owned = registry.owns(path)
        if owned and path.read_bytes() == data:
            return False
        if not owned and path.read_bytes() != data:
            raise PreflightError(f"{path} exists and was not written by texdocxdrafter; move it away to continue")
        # same bytes but not recorded (for example shipped inside an archive): rewrite it, so that a PDF that came
        # with it is older than the document and is not used until the user compiles
    path.parent.mkdir(parents=True, exist_ok=True)
    commit_file(path, data, _snapshot(path), "side document")
    registry.record(path)
    return True


def user_compiled_pdf(pf: Preflight, side: SideDir, stem: str, text: str, audit: Audit, kind: str,
                      bib: str | None = None, extra: dict[str, str] | None = None) -> Path | None:
    """Write ``stem.tex`` into the side directory and return ``stem.pdf`` if the user has compiled that very text.

    ``extra`` maps file names to text written next to the document when it is (re)written, such as the
    labels from the main document's ``.aux``. When the PDF is missing, older than the document, or the
    document is not one the tool recorded writing, an audit entry gives the exact command and None is returned."""
    from .pipeline import write_atomic

    tex = side.dir / f"{stem}.tex"
    pdf = side.dir / f"{stem}.pdf"
    changed = _write_side_document(tex, text, pf, side.registry)
    if changed:
        for name, content in (extra or {}).items():
            dest = side.dir / name
            _vet(dest, pf)
            write_atomic(dest, content.encode("utf-8"))
    if not changed and pdf.is_file() and pdf.stat().st_size > 0 and pdf.stat().st_mtime >= tex.stat().st_mtime:
        return pdf
    audit.add(kind, f"no OS sandbox here, so the {kind} side document was written for you to compile: run\n"
                    f"    {compile_command(pf, side.dir, stem, bib)}\n"
                    "  and then rerun texdocxdrafter. The side document contains the project's preamble: compile it only "
                    "if you trust the project, as you would when compiling the paper itself.")
    return None
