"""Graphics lookup and conversion (pdftoppm, gs) for \\includegraphics."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .. import sandbox

EXTS = [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".gif", ".bmp", ".tif", ".tiff", ".svg"]
TEXT_WIDTH_IN = 6.5


MAX_PIXELS = 6000  # longest side of a rasterised page


def find_graphic(name: str, srcdir: Path, graphicspath: list[str], roots: list[Path] | None = None) -> Path | None:
    """Locate an \\includegraphics file; only files under ``roots`` (default: srcdir) are returned."""
    from ..preflight import inside

    roots = roots or [srcdir]
    dirs = [srcdir] + [(srcdir / g) for g in graphicspath]
    cands = [name] if Path(name).suffix.lower() in EXTS else [name + e for e in EXTS]
    for d in dirs:
        for c in cands:
            p = d / c
            if p.is_file() and inside(p, roots):
                return p
    return None


def safe_dpi(pdf: Path, dpi: int = 300, max_px: int = MAX_PIXELS) -> int:
    """Resolution that keeps every page of ``pdf`` within ``max_px`` on its longest side."""
    if not shutil.which("pdfinfo"):
        return dpi
    r = sandbox.run(["pdfinfo", "-f", "1", "-l", "200", str(pdf)], cwd=pdf.parent, timeout=30, rw=[], ro=[pdf.parent])
    dims = [float(x) for pair in re.findall(r"size:\s+([0-9.]+) x ([0-9.]+)", r.stdout) for x in pair]
    if not dims:
        return dpi
    longest = max(dims) / 72.0  # inches
    if longest * dpi > max_px:
        return max(1, int(max_px / longest))
    return dpi


def _cache_key(path: Path, options: str) -> str:
    h = hashlib.sha256()
    h.update(str(path.resolve()).encode())
    h.update(options.encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def to_raster(path: Path, workdir: Path) -> Path | None:
    """Return a PNG/JPEG usable by Word, converting PDF/EPS pages with poppler/ghostscript."""
    suf = path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"):
        return path
    workdir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(path, "png,300,first-page")
    out_base = workdir / f"{path.stem}-{key}"
    out = Path(str(out_base) + ".png")  # not with_suffix: a stem such as figure.v1 keeps its dots
    if sandbox.is_plain_file(out):
        return out
    src = path
    if suf in (".eps", ".ps"):
        if not shutil.which("gs"):
            return None
        tmp = workdir / f"{path.stem}-{key}-eps.pdf"
        r = sandbox.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dEPSCrop", "-sDEVICE=pdfwrite",
                         f"-sOutputFile={tmp}", str(path)], cwd=workdir, timeout=sandbox.TOOL_TIMEOUT,
                        max_memory=sandbox.MAX_CONVERTER_MEMORY, rw=[workdir], ro=[path.parent])
        if r.returncode != 0 or not sandbox.is_plain_file(tmp):
            return None
        src = tmp
        suf = ".pdf"
    if suf == ".pdf":
        if not shutil.which("pdftoppm"):
            return None
        dpi = safe_dpi(src, 300)
        r = sandbox.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", "-f", "1", "-l", "1", str(src),
                         str(out_base)], cwd=workdir, timeout=sandbox.TOOL_TIMEOUT,
                        max_memory=sandbox.MAX_CONVERTER_MEMORY, rw=[workdir], ro=[src.parent])
        return out if r.returncode == 0 and sandbox.is_plain_file(out) else None
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
