"""Safety properties: sandboxed compiles, private work directories, output vetting, include limits, comments opt-in,
image cache keys and tool limits."""
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from texdocxdrafter import sandbox
from texdocxdrafter import pipeline
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
    r = sandbox.compile_tex("pdflatex", work, "evil", tex, [work], timeout=60, check_policy=False)
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
    r = sandbox.compile_tex("lualatex", work, "evil", tex, [work], timeout=120, check_policy=False)
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
    r = sandbox.compile_tex("lualatex", work, "evil", tex, [work], timeout=120, check_policy=False)
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


def test_no_tex_engine_runs_without_os_sandbox(monkeypatch):
    assert sandbox.sandbox_missing() is None or not sandbox.have_os_sandbox()
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    assert "bubblewrap" in sandbox.sandbox_missing()


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


# ---- third review round --------------------------------------------------------------------------

def test_compile_tex_is_the_policy_boundary(tmp_path, monkeypatch):
    """Every side compile goes through compile_tex, which refuses to start an engine without an OS sandbox."""
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    work = tmp_path / "work"
    work.mkdir()
    tex = work / "x.tex"
    tex.write_text(DOC)
    for engine in ("pdflatex", "xelatex", "lualatex"):
        with pytest.raises(sandbox.SandboxPolicyError):
            sandbox.compile_tex(engine, work, "x", tex, [work], timeout=30)
    assert not (work / "x.log").exists()  # no engine ran


def test_harvest_command_without_os_sandbox_writes_the_side_document(tmp_path, monkeypatch, capsys):
    from texdocxdrafter import cli

    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    src = _write(tmp_path)
    assert cli.main(["harvest", str(src)]) == 1
    err = capsys.readouterr().err
    assert "harvest.tex" not in err or "compile" in err
    assert (tmp_path / "main.texdocxdrafter" / "harvest.tex").is_file()
    assert "pdflatex harvest" in err
    assert not list(tmp_path.glob("**/harvest.log"))  # the tool itself ran nothing


@pytest.mark.skipif(not HAVE_PDFLATEX, reason="pdflatex not installed")
def test_pdftex_cannot_read_outside_the_inputs_with_os_sandbox(tmp_path):
    if not sandbox.have_os_sandbox():
        pytest.skip("bubblewrap not available")
    secret = tmp_path / "secret.tex"
    secret.write_text(r"\message{TOPSECRET}")
    work = tmp_path / "work"
    work.mkdir()
    tex = work / "peek.tex"
    tex.write_text("\\documentclass{article}\\begin{document}\\input{%s} x\\end{document}\n" % secret)
    r = sandbox.compile_tex("pdflatex", work, "peek", tex, [work], timeout=60)
    assert r.returncode != -1
    assert "TOPSECRET" not in (work / "peek.log").read_text(errors="replace")


def _build_with_intervening(src, tmp_path, during, **kw):
    """Run build(); ``during`` runs after the targets were vetted, before anything is written."""
    real = pipeline.parse_document

    def hooked(*a, **k):
        during()
        return real(*a, **k)

    pipeline.parse_document = hooked
    try:
        return build(src, no_harvest=True, log=lambda *_: None, **kw)
    finally:
        pipeline.parse_document = real


def test_file_created_during_the_run_is_not_overwritten(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "main.docx"
    with pytest.raises(PreflightError):
        _build_with_intervening(src, tmp_path, lambda: out.write_text("a coauthor saved this meanwhile"))
    assert out.read_text() == "a coauthor saved this meanwhile"
    assert not list(tmp_path.glob(".main.docx.*.tmp"))


def test_file_changed_during_the_run_is_not_overwritten(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "main.docx"
    build(src, no_harvest=True, log=lambda *_: None)  # our own output: a rerun would normally replace it
    with pytest.raises(PreflightError):
        _build_with_intervening(src, tmp_path, lambda: out.write_text("edited meanwhile"))
    assert out.read_text() == "edited meanwhile"
    # even with --overwrite, a file that changed between vetting and commit is left alone
    with pytest.raises(PreflightError):
        _build_with_intervening(src, tmp_path, lambda: out.write_text("edited again"), overwrite=True)
    assert out.read_text() == "edited again"


def test_registry_never_replaces_a_source_or_foreign_file(tmp_path):
    cfg = tmp_path / "main.texdocxdrafter"
    cfg.mkdir()
    reg = cfg / "outputs.json"
    # 1. the registry path is pulled in as a source file
    reg.write_text("included text\n")
    src = _write(tmp_path, DOC.replace("Hello world.", r"\input{main.texdocxdrafter/outputs.json}"))
    with pytest.raises(PreflightError, match="source"):
        build(src, no_harvest=True, log=lambda *_: None)
    assert reg.read_text() == "included text\n"
    # 2. something that is not a hash registry sits at the path
    src = _write(tmp_path)
    for junk in ('{"a": 1}', "not json", "[]"):
        reg.write_text(junk)
        with pytest.raises(PreflightError, match="registry"):
            build(src, no_harvest=True, log=lambda *_: None)
        assert reg.read_text() == junk
    # 3. a registry changed by another run between vetting and commit is left alone
    reg.unlink()
    build(src, no_harvest=True, log=lambda *_: None)
    good = reg.read_text()
    with pytest.raises(PreflightError):
        _build_with_intervening(src, tmp_path, lambda: reg.write_text("{}"))
    assert reg.read_text() == "{}"
    assert good != "{}"


def test_reference_doc_comments_do_not_leak(tmp_path):
    import docx

    tpl = tmp_path / "template.docx"
    d = docx.Document()
    para = d.add_paragraph("template body text")
    d.add_comment(para.runs[0], text="PRIVATE-TEMPLATE-NOTE", author="Reviewer")
    d.save(str(tpl))
    assert "word/comments.xml" in zipfile.ZipFile(tpl).namelist()

    src = _write(tmp_path)
    out = tmp_path / "main.docx"
    build(src, reference_doc=tpl, no_harvest=True, log=lambda *_: None)
    z = zipfile.ZipFile(out)
    assert "word/comments.xml" not in z.namelist()
    assert all(b"PRIVATE-TEMPLATE-NOTE" not in z.read(n) for n in z.namelist())
    assert all(b"Reviewer" not in z.read(n) for n in z.namelist() if n.startswith("word/"))

    build(src, reference_doc=tpl, no_harvest=True, comments=True, log=lambda *_: None)
    z = zipfile.ZipFile(out)
    comments = z.read("word/comments.xml")
    assert b"a private note" in comments and b"PRIVATE-TEMPLATE-NOTE" not in comments


# ---- side compiles done by the user, and bibtex without any compile -------------------------------

BIBLATEX_DOC = r"""\documentclass{article}
\usepackage[style=authoryear,backend=biber]{biblatex}
\usepackage{tikz}
\addbibresource{refs.bib}
\begin{document}
Text \parencite{knuth84} here.
\begin{figure}\centering\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}\caption{A line.}\end{figure}
\printbibliography
\end{document}
"""
BIB = r"""@book{knuth84, author={Donald E. Knuth}, title={The TeXbook}, year={1984}, publisher={Addison-Wesley}}
"""


def _docx_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("word/document.xml").decode()


@pytest.mark.skipif(not (HAVE_PDFLATEX and HAVE_POPPLER and shutil.which("biber")), reason="pdflatex, biber or poppler missing")
def test_without_os_sandbox_the_user_compiles_the_side_documents(tmp_path, monkeypatch):
    import subprocess

    src = tmp_path / "main.tex"
    src.write_text(BIBLATEX_DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    # the user's own compile of the paper (this is what the tool assumes exists)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    assert (tmp_path / "main.bbl").is_file()

    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    engine_calls = []
    real_run = sandbox.run

    def spy(cmd, *a, **k):
        if Path(cmd[0]).name in ("pdflatex", "xelatex", "lualatex", "biber", "bibtex"):
            engine_calls.append(cmd[0])
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(sandbox, "run", spy)
    res = build(src, log=lambda *_: None)
    cfg = tmp_path / "main.texdocxdrafter"
    assert (cfg / "harvest.tex").is_file() and (cfg / "tikz.tex").is_file()
    assert not engine_calls  # the tool itself ran no TeX
    msgs = "\n".join(e.message for e in res.audit.entries)
    assert "pdflatex harvest" in msgs and "biber harvest" in msgs and "pdflatex tikz" in msgs
    xml = _docx_xml(tmp_path / "main.docx")
    assert "[knuth84]" in xml and "Knuth" not in xml
    assert "word/media/" not in "\n".join(zipfile.ZipFile(tmp_path / "main.docx").namelist())

    # the user runs the two printed commands
    from texdocxdrafter.sidecompile import compile_command
    from texdocxdrafter.preflight import preflight

    pf = preflight(src)
    for stem, bib in (("harvest", "biber"), ("tikz", None)):
        r = subprocess.run(compile_command(pf, cfg, stem, bib) + " -interaction=batchmode", shell=True,
                           capture_output=True, text=True, timeout=300)
        assert (cfg / f"{stem}.pdf").is_file(), r.stdout[-800:]
    engine_calls.clear()
    res = build(src, log=lambda *_: None)
    assert not engine_calls
    xml = _docx_xml(tmp_path / "main.docx")
    assert "Knuth" in xml and "1984" in xml and "[knuth84]" not in xml
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(tmp_path / "main.docx").namelist())
    assert res.audit.info["harvest"].startswith("1/1")


NATBIB_DOC = r"""\documentclass{article}
\usepackage[round]{natbib}
\begin{document}
As \citet{smith2020a} showed \citep[see][p.~3]{jones2019,smith2020a}.
\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""
NATBIB_BBL = r"""\begin{thebibliography}{2}
\providecommand{\natexlab}[1]{#1}
\bibitem[Jones and M\"{u}ller(2019)]{jones2019}
K.~Jones and A.~M\"{u}ller.
\newblock \emph{A Book About Stuff}.
\newblock Publisher \& Sons, 2019.

\bibitem[{Smith et~al.(2020{\natexlab{a}})Smith, Jones, and Lee}]{smith2020a}
J.~Smith, K.~Jones, and M.~Lee.
\newblock The title of the paper.
\newblock {\em Journal of Things}, 12(3):45--67, 2020{\natexlab{a}}.

\end{thebibliography}
"""


def test_bibtex_document_needs_no_compile(tmp_path, monkeypatch):
    src = _write(tmp_path, NATBIB_DOC)
    src.with_suffix(".bbl").write_text(NATBIB_BBL)
    monkeypatch.setitem(sandbox._disabled, "bwrap", True)
    monkeypatch.setattr(sandbox, "compile_tex", lambda *a, **k: pytest.fail("a TeX engine was started"))
    res = build(src, log=lambda *_: None)
    assert res.audit.info["harvest source"].startswith("main.bbl")
    assert res.audit.info["harvest"] == "2/2 citations, 2 bibliography entries"
    xml = _docx_xml(tmp_path / "main.docx")
    assert "Smith et al. (2020a)" in xml
    assert "(see Jones and Müller, 2019; Smith et al., 2020a, p. 3)" in xml
    assert "Journal of Things" in xml and "Publisher &amp; Sons" in xml
    import re

    italic = re.findall(r"<w:r>(?:(?!</w:r>).)*(?:<w:i/>|w:val=\"Emphasis\")(?:(?!</w:r>).)*<w:t[^>]*>([^<]*)</w:t>", xml, re.S)
    assert "Journal of Things" in italic and "A Book About Stuff" in italic
    assert not (tmp_path / "main.texdocxdrafter" / "harvest.tex").exists()
