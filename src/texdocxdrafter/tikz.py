"""Render tikzpicture environments to PNG with one side compile (preview package, one page per picture)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import sandbox
from .audit import Audit
from .parser.model import Document, Image, walk_tree
from .preflight import Preflight, side_preamble
from .writer.images import safe_dpi

DPI = 300
PREVIEW = "\\usepackage[active,tightpage]{preview}\n\\setlength\\PreviewBorder{1pt}"


def tikz_images(doc: Document) -> list[Image]:
    found: list[Image] = []
    walk_tree(doc, lambda n: found.append(n) if isinstance(n, Image) and n.tikz else None)
    return found


def build_tikz_tex(pf: Preflight, images: list[Image]) -> str:
    out = [side_preamble(pf).rstrip(), PREVIEW, "\\begin{document}"]
    for img in images:
        out.append("\\begin{preview}%s\\end{preview}" % img.tikz)
    out.append("\\end{document}")
    return "\n".join(out) + "\n"


def aux_labels(pf: Preflight) -> str:
    """The ``\\newlabel`` lines of the main document's .aux, and nothing else (never the raw file)."""
    if not pf.aux or not Path(pf.aux).is_file():
        return ""
    text = Path(pf.aux).read_text(encoding="utf-8", errors="replace")
    return "".join(l if l.endswith("\n") else l + "\n" for l in text.splitlines(True) if l.startswith("\\newlabel"))


def render_tikz(pf: Preflight, doc: Document, audit: Audit, timeout: int = sandbox.COMPILE_TIMEOUT,
                side=None) -> int:
    """Compile every tikzpicture of the tree and set Image.path/width_in. Returns the number rendered.

    With ``side`` (a sidecompile.SideDir) the side document is written there for the user to compile (no OS
    sandbox here) and the PDF they produced is used; see sidecompile."""
    images = tikz_images(doc)
    if not images:
        return 0
    work = pf.workdir / "tikz"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("page-*.png"):
        old.unlink()
    tex = build_tikz_tex(pf, images)
    labels = aux_labels(pf)  # only the \newlabel lines of the document's .aux: \ref inside a picture prints the LaTeX number
    if side is not None:
        from .sidecompile import user_compiled_pdf

        pdf = user_compiled_pdf(pf, side, "tikz", tex, audit, "figure", extra={"tikz.aux": labels} if labels else None)
        if pdf is None:
            audit.add("figure", f"{len(images)} tikzpicture(s) kept as visible text until the side document is compiled",
                      images[0].loc)
            return 0
    else:
        (work / "tikz.tex").write_text(tex, encoding="utf-8")
        if labels:
            (work / "tikz.aux").write_text(labels, encoding="utf-8")
        dirs = [pf.srcdir] + list(pf.allow_dirs)
        r = sandbox.compile_tex(pf.engine, work, "tikz", work / "tikz.tex", dirs, timeout)
        pdf = work / "tikz.pdf"
        if not pdf.exists() or pdf.stat().st_size == 0:
            log = work / "tikz.log"
            errs = [l for l in log.read_text(errors="replace").splitlines() if l.startswith("!")][:3] if log.exists() else []
            audit.add("figure", f"tikz compile failed ({pf.engine}): {'; '.join(errs) or r.stderr[:200]}; "
                      f"{len(images)} tikzpicture(s) kept as visible text", images[0].loc)
            return 0
    if not shutil.which("pdftoppm"):
        audit.add("figure", "pdftoppm not found; tikzpicture(s) kept as visible text", images[0].loc)
        return 0
    dpi = safe_dpi(pdf, DPI)
    sandbox.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(work / "page")], cwd=work,
                timeout=sandbox.TOOL_TIMEOUT, max_memory=sandbox.MAX_CONVERTER_MEMORY, rw=[work], ro=[pdf.parent])
    pages = sorted(work.glob("page-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1)))
    if len(pages) != len(images):
        audit.add("figure", f"tikz compile gave {len(pages)} page(s) for {len(images)} tikzpicture(s); "
                  "pictures kept as visible text", images[0].loc)
        return 0
    from docx.image.image import Image as DImage

    for img, png in zip(images, pages):
        img.path = str(png)
        img.width_in = DImage.from_file(str(png)).px_width / dpi
    audit.count("tikzpicture rendered to PNG", len(images))
    return len(images)
