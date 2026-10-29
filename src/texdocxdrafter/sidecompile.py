"""Side documents compiled by the user instead of the tool.

Without an OS sandbox the tool never starts a TeX engine. It writes the side document (the citation
harvest or the TikZ pictures) into ``main.texdocxdrafter/``, tells the user how to compile it with their
own TeX setup, and on the next run reads the PDF they produced. The document is rewritten only when its
content changes, so a PDF newer than the document is known to belong to it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .audit import Audit
from .preflight import Preflight, PreflightError


def compile_command(pf: Preflight, side_dir: Path, stem: str, bib: str | None) -> str:
    """The shell line that compiles ``side_dir/stem.tex`` the way the tool would have."""
    dirs = [pf.srcdir] + list(pf.allow_dirs)
    inputs = "".join(f"{d}:" for d in dirs)
    env = f'TEXINPUTS="{inputs}" BIBINPUTS="{inputs}" BSTINPUTS="{inputs}" '
    engine = Path(pf.engine).name
    steps = [f"{engine} {stem}"]
    if bib:
        steps += [f"{bib} {stem}", f"{engine} {stem}", f"{engine} {stem}"]
    return f"cd {side_dir} && {env}" + " && ".join(steps)


def _write_if_changed(path: Path, text: str, pf: Preflight) -> bool:
    """Write ``text`` to ``path`` unless it already holds it. Refuses symlinks and the document's own sources."""
    if path.is_symlink() or path.is_dir():
        raise PreflightError(f"{path} is a symbolic link or a directory; refusing to write the side document there")
    sources = {pf.main.resolve()} | {(pf.srcdir / f).resolve() for f, _ in pf.source.origins if (pf.srcdir / f).exists()}
    if path.resolve() in sources:
        raise PreflightError(f"{path} is one of the document's source files; the side document cannot live there")
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def user_compiled_pdf(pf: Preflight, side_dir: Path, stem: str, text: str, audit: Audit, kind: str,
                      bib: str | None = None, extra: dict[str, Path] | None = None) -> Path | None:
    """Write ``stem.tex`` into ``side_dir`` and return ``stem.pdf`` if the user has compiled that very text.

    ``extra`` maps file names to files copied next to the document when it is (re)written, such as the
    main document's ``.aux``. When the PDF is missing or older than the document, an audit entry tells
    the user the exact command to run, and None is returned."""
    tex = side_dir / f"{stem}.tex"
    pdf = side_dir / f"{stem}.pdf"
    changed = _write_if_changed(tex, text, pf)
    if changed:
        for name, src in (extra or {}).items():
            dest = side_dir / name
            if dest.is_symlink():
                raise PreflightError(f"{dest} is a symbolic link; refusing to write through it")
            dest.write_bytes(Path(src).read_bytes())
    fresh = (not changed and pdf.is_file() and pdf.stat().st_size > 0
             and pdf.stat().st_mtime >= tex.stat().st_mtime)
    if fresh:
        return pdf
    audit.add(kind, f"no OS sandbox here, so the {kind} side document was written for you to compile: run\n"
                    f"    {compile_command(pf, side_dir, stem, bib)}\n  and then rerun texdocxdrafter")
    return None
