"""Safety properties: sandboxed compiles, private work directories, output vetting, include limits, comments opt-in,
image cache keys and tool limits."""
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from texdocxdrafter import sandbox
from texdocxdrafter.pipeline import WORK_MARKER, build, output_path, write_atomic
from texdocxdrafter.auxreader import read_aux
from texdocxdrafter.preflight import PreflightError, preflight, side_preamble
from texdocxdrafter.writer.images import find_graphic, to_raster

HAVE_PDFLATEX = shutil.which("pdflatex") is not None
HAVE_LUALATEX = shutil.which("lualatex") is not None
HAVE_POPPLER = shutil.which("pdftoppm") is not None and shutil.which("pdfinfo") is not None

DOC = r"""\documentclass{article}
\begin{document}
Hello world. % a private note
\end{document}
"""


def _write(tmp_path, text=DOC, name="main.tex", compiled=True):
    src = tmp_path / name
    src.write_text(text)
    if compiled:
        src.with_suffix(".aux").write_text("\\relax\n")
    return src


def _compile(src: Path):
    r = sandbox.run(["pdflatex", "-interaction=batchmode", src.name], cwd=src.parent, timeout=60)
    assert src.with_suffix(".pdf").exists(), r.stderr


# ---- 1. TeX side compiles cannot write outside their directory or run shell commands --------------------

@pytest.mark.skipif(not HAVE_PDFLATEX, reason="pdflatex not installed")
def test_tex_compile_cannot_escape(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    shell_mark = tmp_path / "shell-ran.txt"
    work = tmp_path / "work"
    work.mkdir()
    tex = work / "evil.tex"
    tex.write_text(r"""\documentclass{article}
\begin{document}
\newwrite\w
\immediate\openout\w=%s
\immediate\write\w{pwned}
\immediate\closeout\w
\immediate\write18{touch %s}
x
\end{document}
""" % (victim, shell_mark))
    r = sandbox.compile_tex("pdflatex", work, "evil", tex, [work], timeout=60)
    assert r.returncode != -1
    assert victim.read_text() == "untouched"
    assert not shell_mark.exists()
    assert (work / "evil.log").exists()  # the engine ran and wrote only into its work directory


def test_timeout_is_enforced():
    t0 = time.time()
    r = sandbox.run(["sleep", "5"], cwd="/tmp", timeout=1)
    assert r.returncode == -1 and "timed out" in r.stderr
    assert time.time() - t0 < 4


# ---- 2. Cleanup never touches files the tool did not create ------------------------------------------

@pytest.mark.skipif(not HAVE_PDFLATEX, reason="pdflatex not installed")
def test_config_dir_contents_survive_and_work_is_kept_separately(tmp_path):
    src = _write(tmp_path)
    cfg = tmp_path / "main.texdocxdrafter"
    cfg.mkdir()
    decoy = cfg / "my-notes.txt"
    decoy.write_text("mine")
    foreign = cfg / "work"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("not ours")
    build(src, out=tmp_path / "o.docx", no_harvest=True, keep_work=True, log=lambda *_: None)
    assert decoy.read_text() == "mine"
    assert (foreign / "precious.txt").read_text() == "not ours"  # a foreign 'work' dir is left alone
    kept = [d for d in cfg.iterdir() if d.is_dir() and d.name.startswith("work-")]
    assert len(kept) == 1 and (kept[0] / WORK_MARKER).exists() and (kept[0] / "tree.json").exists()
    # a second run replaces only the directory carrying our marker
    build(src, out=tmp_path / "o.docx", no_harvest=True, keep_work=True, log=lambda *_: None)
    assert (foreign / "precious.txt").exists()


def test_no_leftover_temp_dirs(tmp_path):
    src = _write(tmp_path)
    before = {p.name for p in Path("/tmp").glob("texdocxdrafter-*")}
    build(src, out=tmp_path / "o.docx", no_harvest=True, log=lambda *_: None)
    after = {p.name for p in Path("/tmp").glob("texdocxdrafter-*")}
    assert after <= before


# ---- 3. Output never overwrites sources or foreign documents -----------------------------------------

def test_output_cannot_be_a_source_or_non_docx(tmp_path):
    src = _write(tmp_path)
    pf = preflight(src, need_build_files=False)
    with pytest.raises(PreflightError):
        output_path(pf, src)
    with pytest.raises(PreflightError):
        output_path(pf, tmp_path / "main.tex")
    with pytest.raises(PreflightError):
        output_path(pf, tmp_path / "out.txt")


def test_foreign_docx_is_protected_but_own_output_is_replaced(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "main.docx"
    out.write_bytes(b"a coauthor's draft, not a zip at all")
    with pytest.raises(PreflightError):
        build(src, no_harvest=True, log=lambda *_: None)
    assert out.read_bytes().startswith(b"a coauthor")
    build(src, no_harvest=True, overwrite=True, log=lambda *_: None)
    assert zipfile.is_zipfile(out)
    # our own untouched output may be replaced without the flag
    build(src, no_harvest=True, log=lambda *_: None)
    # a document saved by someone else (lastModifiedBy changed) is protected again
    edited = tmp_path / "edited.docx"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(edited, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = data.replace(b"<cp:lastModifiedBy>texdocxdrafter</cp:lastModifiedBy>",
                                    b"<cp:lastModifiedBy>A Coauthor</cp:lastModifiedBy>")
            zout.writestr(item, data)
    with pytest.raises(PreflightError):
        build(src, out=edited, no_harvest=True, log=lambda *_: None)


# ---- 4. \input and graphics stay inside allowed directories -----------------------------------------

def test_input_outside_source_dir_is_refused_unless_allowed(tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("SECRET")
    proj = tmp_path / "proj"
    proj.mkdir()
    src = _write(proj, text="\\documentclass{article}\\begin{document}\\input{../private.txt}\\end{document}\n")
    pf = preflight(src, need_build_files=False)
    assert "SECRET" not in pf.body
    assert any("outside the source directory" in i for i in pf.issues)
    pf = preflight(src, need_build_files=False, allow_dirs=[tmp_path])
    assert "SECRET" in pf.body


def test_symlinked_input_is_resolved(tmp_path):
    outside = tmp_path / "private.tex"
    outside.write_text("SECRET")
    proj = tmp_path / "proj"
    proj.mkdir()
    os.symlink(outside, proj / "link.tex")
    src = _write(proj, text="\\documentclass{article}\\begin{document}\\input{link}\\end{document}\n")
    pf = preflight(src, need_build_files=False)
    assert "SECRET" not in pf.body


def test_graphics_outside_source_dir_not_found(tmp_path):
    (tmp_path / "fig.png").write_bytes(b"\x89PNG")
    proj = tmp_path / "proj"
    proj.mkdir()
    assert find_graphic("../fig.png", proj, []) is None
    assert find_graphic("../fig.png", proj, [], [proj, tmp_path]).resolve() == (tmp_path / "fig.png").resolve()
    assert find_graphic("fig", tmp_path, []) == tmp_path / "fig.png"


# ---- 5. Comments are exported only on request -----------------------------------------------------

def test_comments_are_opt_in(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "o.docx"
    build(src, out=out, no_harvest=True, log=lambda *_: None)
    assert "word/comments.xml" not in zipfile.ZipFile(out).namelist()
    build(src, out=out, no_harvest=True, comments=True, log=lambda *_: None)
    assert "private note" in zipfile.ZipFile(out).read("word/comments.xml").decode()


# ---- 6. Different figures never share a raster ---------------------------------------------------

@pytest.mark.skipif(not (HAVE_PDFLATEX and HAVE_POPPLER), reason="pdflatex or poppler not installed")
def test_distinct_figures_get_distinct_rasters(tmp_path):
    pngs = []
    for d, txt in (("a", "AAAA"), ("b", "BBBBBBBBBBBB")):
        sub = tmp_path / d
        sub.mkdir()
        fig = sub / "figure.tex"
        fig.write_text("\\documentclass{article}\\pagestyle{empty}\\begin{document}%s\\end{document}\n" % txt)
        _compile(fig)
        pngs.append(to_raster(sub / "figure.pdf", tmp_path / "work"))
    assert pngs[0] != pngs[1]
    assert pngs[0].read_bytes() != pngs[1].read_bytes()


# ---- 7. Converters run with limits ------------------------------------------------------------------

@pytest.mark.skipif(not (HAVE_PDFLATEX and HAVE_POPPLER), reason="pdflatex or poppler not installed")
def test_huge_page_is_rasterised_within_pixel_limit(tmp_path):
    fig = tmp_path / "big.tex"
    fig.write_text("\\documentclass{article}\\usepackage[paperwidth=200in,paperheight=200in]{geometry}"
                   "\\pagestyle{empty}\\begin{document}x\\end{document}\n")
    _compile(fig)
    png = to_raster(tmp_path / "big.pdf", tmp_path / "work")
    assert png is not None
    from docx.image.image import Image

    im = Image.from_file(str(png))
    assert max(im.px_width, im.px_height) <= 6000


def test_side_compile_preamble_rebases_allowed_relative_paths(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "pre.tex").write_text("\\usepackage{xcolor}\n")
    (shared / "refs.bib").write_text("")
    proj = tmp_path / "proj"
    proj.mkdir()
    src = _write(proj, text="\\documentclass{article}\n\\input{../shared/pre}\n\\addbibresource{../shared/refs.bib}\n"
                            "\\graphicspath{{../shared/}{figs/}}\n\\begin{document}x\\end{document}\n")
    pf = preflight(src, need_build_files=False)
    assert "../shared/pre" in side_preamble(pf)  # not allowed: left as written (fails loudly in the side compile)
    pf = preflight(src, need_build_files=False, allow_dirs=[shared])
    side = side_preamble(pf)
    assert "\\input{%s}" % (shared / "pre.tex") in side
    assert "\\addbibresource{%s}" % (shared / "refs.bib") in side
    assert "\\graphicspath{{%s/}{figs/}}" % shared in side


LUA_EVIL = r"""\documentclass{article}
\begin{document}
\directlua{
  local f = io.open("%(victim)s", "w") if f then f:write("pwned") f:close() end
  local g = io.open("%(new)s", "w") if g then g:write("pwned") g:close() end
  os.remove("%(victim)s")
  os.execute("touch %(shell)s")
  local p = io.popen("touch %(popen)s") if p then p:close() end
  local ok, lfs = pcall(require, "lfs") if ok then lfs.mkdir("%(mkdir)s") end
}
x
\end{document}
"""


def _lua_escape_attempt(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    names = {k: str(tmp_path / f"{k}.txt") for k in ("new", "shell", "popen", "mkdir")}
    names["victim"] = str(victim)
    work = tmp_path / "work"
    work.mkdir()
    tex = work / "evil.tex"
    tex.write_text(LUA_EVIL % names)
    r = sandbox.compile_tex("lualatex", work, "evil", tex, [work], timeout=120)
    assert r.returncode != -1, r.stderr
    assert (work / "evil.log").exists()
    assert victim.read_text() == "untouched"
    for k in ("new", "shell", "popen", "mkdir"):
        assert not Path(names[k]).exists(), k


@pytest.mark.skipif(not HAVE_LUALATEX, reason="lualatex not installed")
def test_luatex_cannot_escape_with_os_sandbox(tmp_path):
    if not sandbox.have_os_sandbox():
        pytest.skip("bubblewrap not available")
    _lua_escape_attempt(tmp_path)


@pytest.mark.skipif(not HAVE_LUALATEX, reason="lualatex not installed")
def test_luatex_cannot_escape_without_os_sandbox(tmp_path, monkeypatch):
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    assert not sandbox.have_os_sandbox()
    _lua_escape_attempt(tmp_path)


@pytest.mark.skipif(not HAVE_PDFLATEX, reason="pdflatex not installed")
def test_pdftex_cannot_escape_without_os_sandbox(tmp_path, monkeypatch):
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    test_tex_compile_cannot_escape(tmp_path)


# ---- second review round -------------------------------------------------------------------------

LUA_CHDIR = r"""\documentclass{article}
\begin{document}
\directlua{
  local lfs = require("lfs")
  lfs.chdir("..")
  local f = io.open("victim.txt", "w") if f then f:write("pwned") f:close() end
  local g = io.open("../victim.txt", "w") if g then g:write("pwned") g:close() end
  lfs.link("work/evil.tex", "%(link)s")
  lfs.link("work/evil.tex", "%(symlink)s", true)
  local s = io.open("%(secret)s", "r")
  if s then texio.write_nl("SECRET-CONTENT:" .. (s:read("a") or "")) s:close() end
}
x
\end{document}
"""


def _lua_chdir_attempt(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    work = tmp_path / "work"
    work.mkdir()
    names = {"link": str(tmp_path / "link.txt"), "symlink": str(tmp_path / "symlink.txt"), "secret": str(secret)}
    tex = work / "evil.tex"
    tex.write_text(LUA_CHDIR % names)
    r = sandbox.compile_tex("lualatex", work, "evil", tex, [work], timeout=120)
    assert r.returncode != -1, r.stderr
    assert victim.read_text() == "untouched"
    assert not (tmp_path / "link.txt").exists() and not (tmp_path / "symlink.txt").is_symlink()
    return (work / "evil.log").read_text(errors="replace")


@pytest.mark.skipif(not HAVE_LUALATEX, reason="lualatex not installed")
def test_luatex_chdir_and_link_bypass_closed_without_os_sandbox(tmp_path, monkeypatch):
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    _lua_chdir_attempt(tmp_path)


@pytest.mark.skipif(not HAVE_LUALATEX, reason="lualatex not installed")
def test_os_sandbox_hides_files_outside_the_inputs(tmp_path):
    if not sandbox.have_os_sandbox():
        pytest.skip("bubblewrap not available")
    log = _lua_chdir_attempt(tmp_path)
    assert "TOPSECRET" not in log  # the secret next to the work directory is not even readable


def test_luatex_without_os_sandbox_requires_trust():
    assert sandbox.side_compiles_allowed("pdflatex", False) is None
    assert sandbox.side_compiles_allowed("xelatex", False) is None
    sandbox._disabled["bwrap"] = True
    try:
        assert sandbox.side_compiles_allowed("lualatex", False)
        assert sandbox.side_compiles_allowed("lualatex", True) is None
    finally:
        sandbox._disabled["bwrap"] = False


def test_environment_is_filtered():
    env = sandbox.filtered_env({"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "x", "TEXMFHOME": "/h", "LC_ALL": "C",
                                "HOME": "/home/u", "GITHUB_TOKEN": "y"})
    assert set(env) == {"PATH", "TEXMFHOME", "LC_ALL", "HOME"}


def test_edited_docx_is_protected_even_if_properties_survive(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "main.docx"
    build(src, no_harvest=True, log=lambda *_: None)
    import docx

    d = docx.Document(str(out))  # a python-docx edit keeps lastModifiedBy; the content hash still changes
    d.add_paragraph("a coauthor's addition")
    d.save(str(out))
    with pytest.raises(PreflightError):
        build(src, no_harvest=True, log=lambda *_: None)
    assert "coauthor" in zipfile.ZipFile(out).read("word/document.xml").decode()


def test_symlinked_output_and_report_are_refused(tmp_path):
    src = _write(tmp_path)
    target = tmp_path / "elsewhere.docx"
    target.write_text("keep me")
    out = tmp_path / "main.docx"
    os.symlink(target, out)
    with pytest.raises(PreflightError):
        build(src, no_harvest=True, overwrite=True, log=lambda *_: None)
    assert target.read_text() == "keep me"
    out.unlink()
    rep_target = tmp_path / "elsewhere.md"
    rep_target.write_text("keep me too")
    os.symlink(rep_target, tmp_path / "main.texdocxdrafter-report.md")
    with pytest.raises(PreflightError):
        build(src, no_harvest=True, overwrite=True, log=lambda *_: None)
    assert rep_target.read_text() == "keep me too"


def test_write_atomic_never_follows_a_planted_symlink(tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("untouched")
    # even a temporary name already present as a symlink is not written through: mkstemp creates exclusively
    for name in (".out.docx.tmp", ".out.docx.abc.tmp"):
        os.symlink(target, tmp_path / name)
    write_atomic(tmp_path / "out.docx", b"data")
    assert target.read_text() == "untouched"
    assert (tmp_path / "out.docx").read_bytes() == b"data"


def test_report_is_replaced_when_untouched_but_protected_when_edited(tmp_path):
    src = _write(tmp_path)
    build(src, out=tmp_path / "o.docx", no_harvest=True, log=lambda *_: None)
    rep = tmp_path / "o.texdocxdrafter-report.md"
    build(src, out=tmp_path / "o.docx", no_harvest=True, log=lambda *_: None)  # untouched report: fine
    rep.write_text(rep.read_text() + "\nmy annotation\n")
    with pytest.raises(PreflightError):
        build(src, out=tmp_path / "o.docx", no_harvest=True, log=lambda *_: None)
    assert "my annotation" in rep.read_text()


@pytest.mark.skipif(not (HAVE_PDFLATEX and HAVE_POPPLER), reason="pdflatex or poppler not installed")
def test_figure_names_with_dots_convert(tmp_path):
    fig = tmp_path / "figure.v1.tex"
    fig.write_text("\\documentclass{article}\\pagestyle{empty}\\begin{document}dots\\end{document}\n")
    _compile(fig)
    png = to_raster(tmp_path / "figure.v1.pdf", tmp_path / "work")
    assert png is not None and png.exists() and png.suffix == ".png"


def test_timeout_partial_output_is_text():
    r = sandbox.run(["sh", "-c", "echo partial; sleep 5"], cwd="/tmp", timeout=1)
    assert r.returncode == -1 and isinstance(r.stdout, str) and "partial" in r.stdout


def test_aux_self_include_terminates_and_outside_aux_is_ignored(tmp_path):
    (tmp_path / "sub").mkdir()
    main = tmp_path / "main.aux"
    main.write_text("\\newlabel{a}{{1}{1}}\n\\@input{sub/../main.aux}\n\\@input{../outside.aux}\n\\@input{sub/x.aux}\n")
    (tmp_path / "sub" / "x.aux").write_text("\\newlabel{b}{{2}{1}}\n\\@input{../main.aux}\n")
    (tmp_path.parent / "outside.aux").write_text("\\newlabel{c}{{3}{1}}\n")
    try:
        labels = read_aux(main, [tmp_path])
    finally:
        (tmp_path.parent / "outside.aux").unlink()
    assert set(labels) == {"a", "b"}
