"""Graphics lookup and conversion (pdftoppm, gs) for \\includegraphics."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

EXTS = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".gif", ".bmp", ".tif", ".tiff", ".svg"]
TEXT_WIDTH_IN = 6.5


def find_graphic(name: str, srcdir: Path, graphicspath: list[str]) -> Path | None:
    dirs = [srcdir] + [(srcdir / g) for g in graphicspath]
    cands = [name] if Path(name).suffix.lower() in EXTS else [name + e for e in EXTS]
    for d in dirs:
        for c in cands:
            p = d / c
            if p.is_file():
                return p
    return None


def to_raster(path: Path, workdir: Path) -> Path | None:
    """Return a PNG/JPEG usable by Word, converting PDF/EPS pages with poppler/ghostscript."""
    suf = path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"):
        return path
    workdir.mkdir(parents=True, exist_ok=True)
    out_base = workdir / (path.stem + "-img")
    out = out_base.with_suffix(".png")
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        return out
    src = path
    if suf in (".eps", ".ps"):
        if not shutil.which("gs"):
            return None
        tmp = workdir / (path.stem + "-eps.pdf")
        r = subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dEPSCrop", "-sDEVICE=pdfwrite",
                            f"-sOutputFile={tmp}", str(path)], capture_output=True)
        if r.returncode != 0 or not tmp.exists():
            return None
        src = tmp
        suf = ".pdf"
    if suf == ".pdf":
        if not shutil.which("pdftoppm"):
            return None
        r = subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile", "-f", "1", "-l", "1", str(src),
                            str(out_base)], capture_output=True)
        return out if r.returncode == 0 and out.exists() else None
    return None


def width_inches(options: dict, natural_in: float | None = None, text_in: float = TEXT_WIDTH_IN) -> float | None:
    """Width implied by includegraphics options, relative to the available width (6.5 in text block)."""
    w = options.get("width")
    if isinstance(w, str):
        m = re.match(r"\s*([0-9.]*)\s*(\\textwidth|\\linewidth|\\columnwidth)\s*$", w)
        if m:
            return (float(m.group(1)) if m.group(1) else 1.0) * text_in
        m = re.match(r"\s*([0-9.]+)\s*(cm|mm|in|pt|bp|em)\s*$", w)
        if m:
            v = float(m.group(1))
            return v * {"cm": 1 / 2.54, "mm": 1 / 25.4, "in": 1.0, "pt": 1 / 72.27, "bp": 1 / 72, "em": 0.14}[m.group(2)]
    sc = options.get("scale")
    if isinstance(sc, str) and natural_in:
        try:
            return float(sc) * natural_in
        except ValueError:
            pass
    return None
