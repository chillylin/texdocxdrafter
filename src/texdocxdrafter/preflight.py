"""Stage 1: locate files, detect the engine, flatten \\input, strip comments, read the preamble.

The flattened text carries a source map, so every parsed node can be traced back to
``(file, line)`` in the original sources.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

VERBATIM_ENVS = ("verbatim", "verbatim*", "lstlisting", "minted", "Verbatim", "comment")
_BEGIN_VERB = re.compile(r"\\begin\{(" + "|".join(re.escape(e) for e in VERBATIM_ENVS) + r")\}")
_INPUT = re.compile(r"\\(input|include)\s*\{([^{}]*)\}")


class PreflightError(RuntimeError):
    pass


@dataclass
class Source:
    """Flattened text plus a map from offset to (file, line)."""

    text: str
    starts: list[int]
    origins: list[tuple[str, int]]
    _ranks: dict | None = field(default=None, repr=False)

    def rank(self, loc: tuple[str, int] | None) -> int:
        """Order of a (file, line) in the flattened text: the index of its first piece, -1 if unknown."""
        if self._ranks is None:
            self._ranks = {}
            for i, o in enumerate(self.origins):
                self._ranks.setdefault(o, i)
        return self._ranks.get(tuple(loc), -1) if loc else -1

    def locate(self, pos: int) -> tuple[str, int]:
        if not self.starts:
            return ("?", 0)
        i = max(0, bisect.bisect_right(self.starts, pos) - 1)
        start, (file, line) = self.starts[i], self.origins[i]
        return file, line + self.text.count("\n", start, max(start, pos))


@dataclass
class Preflight:
    main: Path
    srcdir: Path
    workdir: Path
    source: Source
    orig_preamble: str
    preamble: str
    body: str
    body_offset: int
    docclass: str = "article"
    class_options: list[str] = field(default_factory=list)
    packages: list[tuple[str, str]] = field(default_factory=list)
    biblatex_options: str | None = None
    bib_resources: list[str] = field(default_factory=list)
    graphicspath: list[str] = field(default_factory=list)
    bool_flags: dict[str, bool] = field(default_factory=dict)
    numberwithin: list[str] = field(default_factory=list)
    engine: str = "pdflatex"
    aux: Path | None = None
    bbl: Path | None = None
    bcf: Path | None = None
    log: Path | None = None
    comments: list[TexComment] = field(default_factory=list)
    two_column: bool = False
    allow_dirs: list[Path] = field(default_factory=list)  # directories besides srcdir that \input/graphics may use
    issues: list[str] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)  # sha256 of every input as it was when read

    @property
    def uses_biblatex(self) -> bool:
        return self.biblatex_options is not None

    def locate(self, pos: int) -> tuple[str, int]:
        return self.source.locate(pos)

    def has_package(self, name: str) -> bool:
        return any(n == name for n, _ in self.packages)


@dataclass
class TexComment:
    """A % comment of the source: where it is, its text, and which block it annotates."""

    file: str
    line: int
    text: str
    attach: str  # "prev": the block holding the line; "next": the block after it


_DECORATIVE = re.compile(r"^[\W_]*$")
# a line that starts a structure, so a comment next to it is not inside running text
_BLOCK_LINE = re.compile(r"\s*\\(part|chapter|(sub)*section|(sub)?paragraph|begin|end|item|label|caption|centering|"
                         r"maketitle|clearpage|newpage|printbibliography|bibliography|appendix|input|include)\b")
_NO_COMMENT_ENVS = re.compile(r"\\(begin|end)\{(tikzpicture)\}")


def _is_comment_line(line: str) -> bool:
    return line.lstrip(" \t").startswith("%")


def _neighbour_has_text(lines: list[str], i: int, step: int) -> bool:
    j = i + step
    while 0 <= j < len(lines) and _is_comment_line(lines[j]):
        j += step
    return 0 <= j < len(lines) and bool(lines[j].strip()) and not _BLOCK_LINE.match(lines[j])


def _strip_comment(line: str) -> tuple[str, bool]:
    """Return (text before an unescaped %, whether a comment was found)."""
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            return line[:i], True
        i += 1
    return line, False


def read_source_text(path: Path, issues: list[str], shown: str | None = None) -> str:
    """A user-authored file (.tex, .bbl, .aux) as text. UTF-8 is required; bytes that are not UTF-8 are replaced
    by U+FFFD and reported in ``issues`` with the file and the offset of the first one, so the loss is never
    silent (the bytes on disk are of course untouched)."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        text = data.decode("utf-8", errors="replace")
        n = text.count("\ufffd") - data.decode("utf-8", errors="ignore").count("\ufffd")
        issues.append(f"{shown or path.name}: {n} byte sequence(s) are not valid UTF-8 (first at byte {exc.start}); "
                      "they appear as \ufffd in the docx. Save the file as UTF-8 to fix this")
        return text


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(pf: "Preflight", path: Path) -> None:
    """Remember what an input file looked like when it was read (first sight wins), for ``changed_inputs``."""
    key = str(path)
    if key not in pf.fingerprints:
        try:
            pf.fingerprints[key] = _sha256_file(path)
        except OSError:
            pass


def changed_inputs(pf: "Preflight") -> list[str]:
    """The inputs whose content differs from when they were read: sources, build files, graphics."""
    out = []
    for key, digest in pf.fingerprints.items():
        try:
            now = _sha256_file(Path(key))
        except OSError:
            now = None
        if now != digest:
            out.append(key)
    return out


def inside(path: Path, roots: list[Path]) -> bool:
    """True when ``path`` (symlinks resolved) lies in one of ``roots``."""
    try:
        rp = path.resolve(strict=True)
    except OSError:
        return False
    for r in roots:
        rr = r.resolve()
        if rp == rr or rr in rp.parents:
            return True
    return False


_SIDE_PATHS = re.compile(r"\\(input|include|addbibresource|bibliography|graphicspath)\s*(\[[^\]]*\])?\s*\{")


def side_preamble(pf: "Preflight") -> str:
    """The original preamble for a side compile that runs in a private directory.

    Relative \\input, \\include, \\addbibresource, \\bibliography and \\graphicspath arguments are rewritten
    to absolute paths when the target lies in an allowed directory, so the side document compiles from
    any directory (TeX does not search TEXINPUTS for explicitly relative names such as ``../shared/x``,
    and a user compiling the side document by hand has no TEXINPUTS at all). Names that resolve nowhere
    are left as they are and fail in the side compile, which the report shows."""
    text = pf.orig_preamble
    roots = [pf.srcdir] + list(pf.allow_dirs)
    exts = {"input": (".tex",), "include": (".tex",), "addbibresource": (), "bibliography": (".bib",),
            "graphicspath": ()}

    def rebase(name: str, kinds) -> str:
        name = name.strip()
        if not name or name.startswith("/") or re.match(r"[A-Za-z]:[\\/]", name):
            return name
        for ext in list(kinds) + [""]:
            cand = pf.srcdir / (name + ext)
            if (cand.is_file() or cand.is_dir()) and inside(cand, roots):
                return str(cand.resolve()) + ("" if ext else "")
        return name

    out = []
    pos = 0
    for m in _SIDE_PATHS.finditer(text):
        inner, end = _read_balanced(text, m.end() - 1)
        cmd = m.group(1)
        if cmd == "graphicspath":
            new = re.sub(r"\{([^{}]*)\}", lambda g: "{" + rebase(g.group(1).rstrip("/"), ()).rstrip("/") + "/}", inner)
        elif cmd == "bibliography":
            new = ",".join(rebase(part, exts[cmd]) for part in inner.split(","))
        else:
            new = rebase(inner, exts[cmd])
        if new != inner:
            out.append(text[pos:m.end()] + new + "}")
            pos = end
    out.append(text[pos:])
    return "".join(out)


def _find_tex(srcdir: Path, name: str, roots: list[Path] | None = None) -> Path | str | None:
    """Path of an \\input target, None if absent, or "outside" if it exists but is not under an allowed root."""
    name = name.strip()
    roots = roots or [srcdir]
    for cand in (name, name + ".tex"):
        p = (srcdir / cand)
        if p.is_file():
            return p if inside(p, roots) else "outside"
    return None


MAX_INPUTS = 500  # files included per document, over all chains
MAX_INPUT_CHARS = 50_000_000  # characters of included text per document


def _flatten_file(path: Path, srcdir: Path, stack: tuple[Path, ...], issues: list[str],
                  comments: list[TexComment] | None = None,
                  roots: list[Path] | None = None, budget: dict | None = None) -> list[tuple[str, str, int]]:
    """Return pieces (text, file, line) for one file with \\input expanded and comments removed.

    Removed comments are appended to ``comments``, except inside environments drawn as pictures. The
    per-chain cycle check does not bound breadth (N files each including the others fan out as N!), so
    ``budget`` counts inclusions and characters over the whole document."""
    pieces: list[tuple[str, str, int]] = []
    shown = _display(path, srcdir)
    if budget is None:
        budget = {"files": 0, "chars": 0, "noted": False}
    try:
        text_all = read_source_text(path, issues, shown)
    except OSError as exc:
        issues.append(f"cannot read {path}: {exc}")
        return pieces
    budget["files"] += 1
    budget["chars"] += len(text_all)
    lines = text_all.splitlines(keepends=True)
    verb_end: str | None = None
    keep_verb = True
    swallow_leading = False
    picture_depth = 0
    for lineno, raw in enumerate(lines, start=1):
        if verb_end is not None:
            if keep_verb:
                pieces.append((raw, shown, lineno))
            elif verb_end in raw:
                pieces.append((raw.split(verb_end, 1)[1], shown, lineno))
            if verb_end in raw:
                verb_end = None
            continue
        line = raw.lstrip(" \t") if swallow_leading else raw
        swallow_leading = False
        text, had_comment = _strip_comment(line)
        for em in _NO_COMMENT_ENVS.finditer(text):
            picture_depth = max(0, picture_depth + (1 if em.group(1) == "begin" else -1))
        if had_comment:
            swallow_leading = True
            note = line[len(text) + 1:].strip()
            if comments is not None and picture_depth == 0 and not _DECORATIVE.match(note):
                if text.strip():
                    attach = "prev"
                else:
                    i = lineno - 1
                    inside = _neighbour_has_text(lines, i, -1) and _neighbour_has_text(lines, i, 1)
                    attach = "prev" if inside else "next"
                comments.append(TexComment(shown, lineno, note, attach))
        m = _BEGIN_VERB.search(text)
        if m and m.group(1) != "comment":
            end = "\\end{%s}" % m.group(1)
            if end not in text[m.end():]:
                verb_end = end
                keep_verb = True
            pieces.append((text if not had_comment else raw, shown, lineno))
            continue
        if m and m.group(1) == "comment":
            # the comments package: drop everything up to \end{comment}
            end = "\\end{comment}"
            if end not in text[m.end():]:
                verb_end = end
                keep_verb = False
                pieces.append((text[: m.start()], shown, lineno))
            continue
        pos = 0
        for im in _INPUT.finditer(text):
            target = _find_tex(srcdir, im.group(2), roots)
            if target is None:
                issues.append(f"{shown}:{lineno}: \\{im.group(1)}{{{im.group(2)}}} not found; left in place")
                continue
            if target == "outside":
                issues.append(f"{shown}:{lineno}: \\{im.group(1)}{{{im.group(2)}}} lies outside the source directory "
                              "and was not included (allow it with --allow-dir)")
                continue
            if target in stack or len(stack) > 20:
                issues.append(f"{shown}:{lineno}: recursive \\input of {target.name}")
                continue
            if budget["files"] >= MAX_INPUTS or budget["chars"] >= MAX_INPUT_CHARS:
                if not budget["noted"]:
                    budget["noted"] = True
                    issues.append(f"{shown}:{lineno}: more than {MAX_INPUTS} \\input files or {MAX_INPUT_CHARS // 1000000} "
                                  "million characters included; further \\input commands are left in place")
                continue
            if im.start() > pos:
                pieces.append((text[pos:im.start()], shown, lineno))
            pieces.extend(_flatten_file(target, srcdir, stack + (target,), issues, comments, roots, budget))
            pos = im.end()
        pieces.append((text[pos:], shown, lineno))
    return pieces


def _display(path: Path, srcdir: Path) -> str:
    try:
        return str(path.resolve().relative_to(srcdir.resolve()))
    except ValueError:
        return str(path)


def _split_args(raw: str | None) -> list[str]:
    return [a.strip() for a in (raw or "").split(",") if a.strip()]


def _read_balanced(s: str, i: int, open_c: str = "{", close_c: str = "}") -> tuple[str, int]:
    """Read a balanced group starting at s[i] == open_c. Returns (inner, index after)."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)


def detect_engine(log: Path | None, preamble: str) -> str:
    if log and log.is_file():
        first = log.read_text(encoding="utf-8", errors="replace")[:400]
        if "XeTeX" in first:
            return "xelatex"
        if "LuaHBTeX" in first or "LuaTeX" in first:
            return "lualatex"
        if "pdfTeX" in first:
            return "pdflatex"
    if re.search(r"\\usepackage(\[[^\]]*\])?\{(fontspec|unicode-math)\}", preamble):
        return "xelatex"
    return "pdflatex"


_TWOCOLUMN = re.compile(r"(?m)^[^%\n]*\\twocolumn\b")
TWO_COLUMN_CLASSES = {"IEEEtran", "IEEEconf", "sig-alternate", "aa", "revtex4", "revtex4-1", "revtex4-2"}


def _two_column(pf: "Preflight") -> bool:
    """Two-column layout from the class options, the preamble, a known class, or a local .cls/.sty file."""
    opts = set(pf.class_options)
    if "onecolumn" in opts:
        return False
    if "twocolumn" in opts or pf.docclass in TWO_COLUMN_CLASSES or _TWOCOLUMN.search(pf.preamble):
        return True
    local = [pf.srcdir / f"{pf.docclass}.cls"] + [pf.srcdir / f"{name}.sty" for name, _ in pf.packages]
    for f in local:
        if f.is_file() and _TWOCOLUMN.search(f.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def preflight(main: str | Path, need_build_files: bool = True, allow_dirs=()) -> Preflight:
    main = Path(main).resolve()
    if not main.is_file():
        raise PreflightError(f"{main}: no such file")
    srcdir = main.parent
    allowed = [Path(d).resolve() for d in allow_dirs]
    for d in allowed:
        if not d.is_dir():
            raise PreflightError(f"--allow-dir {d}: not a directory")
    roots = [srcdir] + allowed
    issues: list[str] = []
    comments: list[TexComment] = []
    pieces = _flatten_file(main, srcdir, (main,), issues, comments, roots)
    text = "".join(p[0] for p in pieces)
    starts: list[int] = []
    origins: list[tuple[str, int]] = []
    off = 0
    for t, f, ln in pieces:
        starts.append(off)
        origins.append((f, ln))
        off += len(t)
    source = Source(text, starts, origins)

    m = re.search(r"\\begin\{document\}", text)
    if not m:
        raise PreflightError(f"{main.name}: no \\begin{{document}} found")
    preamble = text[: m.start()]
    end = text.rfind("\\end{document}")
    body_start = m.end()
    body = text[body_start: end if end > body_start else len(text)]

    raw_main = read_source_text(main, [])  # already reported by _flatten_file
    rm = re.search(r"\\begin\{document\}", raw_main)
    orig_preamble = raw_main[: rm.start()] if rm else preamble

    pf = Preflight(main=main, srcdir=srcdir, workdir=main.with_name(main.stem + ".texdocxdrafter"),
                   source=source, orig_preamble=orig_preamble, preamble=preamble,
                   body=body, body_offset=body_start, issues=issues, allow_dirs=allowed)
    begin_rank = bisect.bisect_right(starts, m.start()) - 1
    pf.comments = [c for c in comments if source.rank((c.file, c.line)) > begin_rank]

    dm = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", preamble)
    if dm:
        pf.docclass = dm.group(2).strip()
        pf.class_options = _split_args(dm.group(1))
    for um in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[((?:[^\]]|\n)*)\])?\s*\{([^}]*)\}", preamble):
        for pkg in _split_args(um.group(2)):
            pf.packages.append((pkg, (um.group(1) or "").replace("\n", " ")))
            if pkg == "biblatex":
                pf.biblatex_options = (um.group(1) or "").replace("\n", " ")
    pf.two_column = _two_column(pf)
    for bm in re.finditer(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", preamble):
        pf.bib_resources.append(bm.group(1).strip())
    gm = re.search(r"\\graphicspath\s*\{", preamble)
    if gm:
        inner, _ = _read_balanced(preamble, gm.end() - 1)
        pf.graphicspath = re.findall(r"\{([^{}]*)\}", inner)
    for nm in re.finditer(r"\\newif\s*\\if(\w+)", preamble):
        pf.bool_flags[nm.group(1)] = False
    for name in list(pf.bool_flags):
        for sm in re.finditer(r"\\%s(true|false)\b" % re.escape(name), preamble):
            pf.bool_flags[name] = sm.group(1) == "true"
    for nm in re.finditer(r"\\numberwithin\s*\{(\w+)\}\s*\{(\w+)\}", preamble):
        pf.numberwithin.append(f"{nm.group(1)} within {nm.group(2)}")

    stem = main.with_suffix("")
    pf.aux, pf.bbl, pf.bcf, pf.log = (Path(str(stem) + e) for e in (".aux", ".bbl", ".bcf", ".log"))
    roots = [pf.srcdir] + list(pf.allow_dirs)
    for f in (pf.aux, pf.bbl, pf.bcf, pf.log):
        # build files are read (and the .aux is copied for the TikZ side compile): a symlink must not lead outside
        if f.is_symlink() and not inside(f, roots):
            raise PreflightError(f"{f.name} is a symbolic link to a file outside the project; refusing to read it")
    if pf.workdir.is_symlink() or (pf.workdir.exists() and not pf.workdir.is_dir()):
        raise PreflightError(f"{pf.workdir.name} must be a real directory next to {main.name}; it is a symbolic link or a file")
    pf.engine = detect_engine(pf.log, preamble)

    if need_build_files:
        if not pf.aux.is_file():
            raise PreflightError(f"{pf.aux.name} is missing: compile {main.name} first (latex + biber)")
        if pf.uses_biblatex and not pf.bbl.is_file():
            raise PreflightError(f"{pf.bbl.name} is missing: run biber on {main.name} first")
    # what every input looks like now; the build refuses to publish if any of them changes meanwhile
    for f in [main] + [srcdir / f for f, _ in source.origins] + [pf.aux, pf.bbl, pf.bcf]:
        if f.is_file():
            fingerprint(pf, f)
    for name in pf.bib_resources:
        for cand in (srcdir / name, srcdir / (name + ".bib")):
            if cand.is_file():
                fingerprint(pf, cand)
    return pf
