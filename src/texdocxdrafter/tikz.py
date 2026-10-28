"""Render tikzpicture environments to PNG with one side compile (preview package, one page per picture)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .audit import Audit
from .parser.model import Document, Image, walk_tree
from .preflight import Preflight

DPI = 300
PREVIEW = "\\usepackage[active,tightpage]{preview}\n\\setlength\\PreviewBorder{1pt}"


def tikz_images(doc: Document) -> list[Image]:
    found: list[Image] = []
    walk_tree(doc, lambda n: found.append(n) if isinstance(n, Image) and n.tikz else None)
    return found


def build_tikz_tex(pf: Preflight, images: list[Image]) -> str:
    out = [pf.orig_preamble.rstrip(), PREVIEW, "\\begin{document}"]
    for img in images:
        out.append("\\begin{preview}%s\\end{preview}" % img.tikz)
    out.append("\\end{document}")
    return "\n".join(out) + "\n"


def render_tikz(pf: Preflight, doc: Document, audit: Audit) -> int:
    """Compile every tikzpicture of the tree and set Image.path/width_in. Returns the number rendered."""
    images = tikz_images(doc)
    if not images:
        return 0
    work = pf.workdir / "tikz"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("page-*.png"):
        old.unlink()
    (work / "tikz.tex").write_text(build_tikz_tex(pf, images), encoding="utf-8")
    if pf.aux and Path(pf.aux).exists():
        # the document's own labels, so \ref inside a picture prints the LaTeX number
        shutil.copyfile(pf.aux, work / "tikz.aux")
    env = dict(os.environ)
    env["TEXINPUTS"] = f"{pf.srcdir}:" + env.get("TEXINPUTS", "")
    cmd = [pf.engine, "-interaction=batchmode", f"-output-directory={work}", "-jobname=tikz", str(work / "tikz.tex")]
    r = subprocess.run(cmd, cwd=pf.srcdir, env=env, capture_output=True, text=True, timeout=300)
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
    subprocess.run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), str(work / "page")], capture_output=True)
    pages = sorted(work.glob("page-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1)))
    if len(pages) != len(images):
        audit.add("figure", f"tikz compile gave {len(pages)} page(s) for {len(images)} tikzpicture(s); "
                  "pictures kept as visible text", images[0].loc)
        return 0
    from docx.image.image import Image as DImage

    for img, png in zip(images, pages):
        img.path = str(png)
        img.width_in = DImage.from_file(str(png)).px_width / DPI
    audit.count("tikzpicture rendered to PNG", len(images))
    return len(images)
