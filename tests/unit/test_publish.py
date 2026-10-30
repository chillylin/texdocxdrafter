"""Publication guarantees: nothing is published before validation, the three files change together or not at
all, inputs are confirmed unchanged, decoding loss is reported, and the package policy and content check hold."""
import errno
import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from texdocxdrafter import fidelity, pipeline, stylemap
from texdocxdrafter.pipeline import OutputValidationError, SourceChangedError, build
from texdocxdrafter.validate import CheckResult, check_docx



def _png() -> bytes:
    """A valid 1x1 RGBA PNG."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


PNG = _png()

DOC = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\section{Alpha}
First paragraph with a footnote.\footnote{Note one.}
\input{part}
\section{Gamma}
\begin{table}\caption{A table}
\begin{tabular}{ll} a & b \\ c & d \end{tabular}
\end{table}
\includegraphics{pic.png}
\end{document}
"""
PART = "\\section{Beta}\nSecond paragraph here.\n"


def _project(tmp_path: Path) -> Path:
    src = tmp_path / "main.tex"
    src.write_text(DOC)
    (tmp_path / "part.tex").write_text(PART)
    (tmp_path / "pic.png").write_bytes(PNG)
    src.with_suffix(".aux").write_text("\\relax\n")
    return src


def _digests(tmp_path: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir() if p.is_file()}


def _no_temp_files(tmp_path: Path) -> None:
    assert not [p.name for p in tmp_path.rglob("*.tmp")]


def _quiet(*_):
    pass


def _rezip(path: Path, replace: dict, add: dict) -> None:
    tmp = path.with_suffix(".rezip")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            for old, new in replace.get(item.filename, ()):
                assert old in data, (item.filename, old)
                data = data.replace(old, new)
            zout.writestr(item, data)
        for name, data in add.items():
            zout.writestr(name, data)
    tmp.replace(path)


def test_malformed_output_is_never_published(tmp_path, monkeypatch):
    """Finding 1: the writer's bytes are validated on a private copy; an unreadable package publishes nothing."""
    src = _project(tmp_path)
    build(src, no_harvest=True, log=_quiet)  # a good build first: the target exists and is owned
    before = _digests(tmp_path)
    from texdocxdrafter.writer import DocxWriter

    monkeypatch.setattr(DocxWriter, "write", lambda self, out, comments=True: out.write(b"not a zip at all"))
    with pytest.raises(OutputValidationError, match="nothing was written"):
        build(src, no_harvest=True, log=_quiet)
    assert _digests(tmp_path) == before  # docx, report, sources: all byte for byte as before
    _no_temp_files(tmp_path)


def test_validation_errors_fail_the_build_and_the_cli(tmp_path, monkeypatch):
    """Finding 2: a structural error is fatal; no target is created, the CLI exits nonzero."""
    from texdocxdrafter.cli import main

    src = _project(tmp_path)
    monkeypatch.setattr(pipeline, "check_docx", lambda path: CheckResult(errors=["style 'Nope' is used but not defined"]))
    with pytest.raises(OutputValidationError, match="failed validation"):
        build(src, no_harvest=True, log=_quiet)
    assert not (tmp_path / "main.docx").exists() and not (tmp_path / "main.texdocxdrafter-report.md").exists()
    assert main(["build", str(src), "--no-harvest"]) == 1
    assert not (tmp_path / "main.docx").exists()
    _no_temp_files(tmp_path)


@pytest.mark.parametrize("fail_on", ["report", "outputs.json"])
def test_failure_while_publishing_puts_everything_back(tmp_path, monkeypatch, fail_on):
    """Finding 3: report, docx and registry are committed as one step; ENOSPC on any of them restores the rest."""
    src = _project(tmp_path)
    build(src, no_harvest=True, log=_quiet)
    (tmp_path / "part.tex").write_text(PART + "A new sentence so the docx changes.\n")
    before = _digests(tmp_path)
    registry_before = (tmp_path / "main.texdocxdrafter" / "outputs.json").read_bytes()
    real = pipeline._write_tmp

    def failing(path, data, mode=None):
        if fail_on in path.name:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, data, mode)

    monkeypatch.setattr(pipeline, "_write_tmp", failing)
    with pytest.raises(OSError):
        build(src, no_harvest=True, log=_quiet)
    after = _digests(tmp_path)
    assert after == before, {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert (tmp_path / "main.texdocxdrafter" / "outputs.json").read_bytes() == registry_before
    _no_temp_files(tmp_path)
    # and the restored files are still recognised as the tool's own: the next build goes through
    monkeypatch.setattr(pipeline, "_write_tmp", real)
    res = build(src, no_harvest=True, log=_quiet)
    assert res.check.ok


def test_invalid_utf8_in_sources_is_reported_not_silent(tmp_path):
    """Finding 4: bytes that are not UTF-8 become U+FFFD and the report names the file and offset."""
    src = _project(tmp_path)
    src.write_bytes(DOC.replace("First paragraph", "First \xe9 paragraph").encode("latin-1"))
    (tmp_path / "part.tex").write_bytes(b"\\section{Beta}\nCaf\xe9 here.\n")
    before = _digests(tmp_path)
    res = build(src, no_harvest=True, log=_quiet)
    msgs = [e.message for e in res.audit.entries if "not valid UTF-8" in e.message]
    assert any("main.tex" in m and "first at byte" in m for m in msgs), msgs
    assert any("part.tex" in m for m in msgs), msgs
    assert all(e.severity != "info" for e in res.audit.entries if "not valid UTF-8" in e.message)
    for name in ("main.tex", "part.tex", "pic.png"):
        assert _digests(tmp_path)[name] == before[name]  # the sources are not touched


def test_source_changed_during_conversion_is_refused(tmp_path, monkeypatch):
    """Finding 6: an included file edited after it was read stops the build before anything is published."""
    src = _project(tmp_path)
    build(src, no_harvest=True, log=_quiet)
    before = _digests(tmp_path)
    real = pipeline.resolve

    def edit_then_resolve(*a, **k):
        (tmp_path / "part.tex").write_text(PART + "Edited meanwhile.\n")
        return real(*a, **k)

    monkeypatch.setattr(pipeline, "resolve", edit_then_resolve)
    with pytest.raises(SourceChangedError, match="part.tex changed"):
        build(src, no_harvest=True, log=_quiet)
    after = _digests(tmp_path)
    assert after["main.docx"] == before["main.docx"] and after["main.texdocxdrafter-report.md"] == before["main.texdocxdrafter-report.md"]
    _no_temp_files(tmp_path)


def test_graphic_changed_after_it_was_embedded_is_refused(tmp_path, monkeypatch):
    """Finding 6, assets: a picture replaced between embedding and publication stops the build too."""
    src = _project(tmp_path)
    real = pipeline.check_docx

    def swap_then_check(path):
        (tmp_path / "pic.png").write_bytes(PNG + b"\x00")
        return real(path)

    monkeypatch.setattr(pipeline, "check_docx", swap_then_check)
    with pytest.raises(SourceChangedError, match="pic.png changed"):
        build(src, no_harvest=True, log=_quiet)
    assert not (tmp_path / "main.docx").exists()
    _no_temp_files(tmp_path)


def test_package_policy_is_checked_over_the_whole_file(tmp_path):
    """Finding 7: the validator rejects prohibited relationships, external targets, parts and fields wherever
    they sit, independently of the scrubber."""
    src = _project(tmp_path)
    res = build(src, no_harvest=True, log=_quiet)
    assert res.check.ok
    out = tmp_path / "main.docx"
    W = b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    _rezip(out, {
        "word/_rels/document.xml.rels": [(b"</Relationships>",
            b'<Relationship Id="rIdX1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            b'Target="http://evil.example/x.png" TargetMode="External"/>'
            b'<Relationship Id="rIdX2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            b'Target="file:///C:/x.docx" TargetMode="External"/>'
            b'<Relationship Id="rIdX3" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" '
            b'Target="vbaProject.bin"/></Relationships>')],
        "word/document.xml": [(b"<w:body>", b"<w:body><w:p><w:fldSimple " + W +
            b' w:instr=" INCLUDETEXT &quot;\\\\\\\\evil\\\\share\\\\x.docx&quot; "><w:r><w:t>x</w:t></w:r></w:fldSimple></w:p>')],
        "[Content_Types].xml": [(b"</Types>",
            b'<Override PartName="/word/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>')],
    }, {"word/vbaProject.bin": b"\xd0\xcf\x11\xe0"})
    chk = check_docx(out)
    joined = "\n".join(chk.errors)
    for needle in ("external target http://evil.example/x.png", "external target file:///C:/x.docx", "prohibited relationship vbaProject",
                   "content type application/vnd.ms-office.vbaProject", "field INCLUDETEXT", "embedded part word/vbaProject.bin"):
        assert needle in joined, (needle, joined)


def test_content_check_sees_lost_duplicated_and_reordered_structure(tmp_path, monkeypatch):
    """Finding 5: the manifest from the tree is compared with the docx; the CLI --strict makes a mismatch fatal."""
    src = _project(tmp_path)
    res = build(src, no_harvest=True, log=_quiet)
    assert not [e for e in res.audit.entries if e.kind == "fidelity"], [e.message for e in res.audit.entries]
    out = tmp_path / "main.docx"
    sm = stylemap.load(None)
    good = fidelity.found(out, sm)
    assert good.headings == ["Alpha", "Beta", "Gamma"] and good.tables == 1 and good.pictures == 1 and good.footnotes == 1
    # lose the table, swap two headings
    _rezip(out, {"word/document.xml": [(b"Alpha", b"@@"), (b"Gamma", b"Alpha"), (b"@@", b"Gamma")]}, {})
    doc_xml = zipfile.ZipFile(out).read("word/document.xml")
    tbl_start, tbl_end = doc_xml.index(b"<w:tbl>"), doc_xml.index(b"</w:tbl>") + len(b"</w:tbl>")
    _rezip(out, {"word/document.xml": [(doc_xml[tbl_start:tbl_end], b"")]}, {})
    problems = fidelity.compare(good, fidelity.found(out, sm))
    assert any(p.startswith("tables: 1 expected, 0") for p in problems), problems
    assert any(p.startswith("heading 1: expected 'Alpha'") for p in problems), problems
    # a duplicated heading changes the count
    _rezip(out, {"word/document.xml": [(b"<w:body>", b"<w:body>")]}, {})
    dup = fidelity.Manifest(headings=["Alpha", "Beta", "Gamma", "Gamma"], tables=0, pictures=1, footnotes=1)
    assert any(p.startswith("headings: 3 expected, 4") for p in fidelity.compare(good, dup))
    # strict mode: a mismatch publishes nothing; otherwise it is reported
    monkeypatch.setattr(pipeline.fidelity, "check", lambda doc, path, sm, audit: (audit.add("fidelity", "tables: 1 expected, 0 in the docx"),
                                                                                  ["tables: 1 expected, 0 in the docx"])[1])
    before = _digests(tmp_path)
    with pytest.raises(OutputValidationError, match="--strict: tables"):
        build(src, no_harvest=True, strict=True, overwrite=True, log=_quiet)  # overwrite: the docx was tampered above
    assert _digests(tmp_path) == before
    res = build(src, no_harvest=True, overwrite=True, log=_quiet)
    assert [e.message for e in res.audit.entries if e.kind == "fidelity"] == ["tables: 1 expected, 0 in the docx"]


def test_commit_refuses_a_target_swapped_at_the_last_moment(tmp_path, monkeypatch):
    """Finding 8: the identity of the opened target is compared once more right before the rename."""
    target = tmp_path / "t.docx"
    target.write_bytes(b"old")
    snap = pipeline._snapshot(target)
    real_stat = os.stat

    def swapped(path, *a, **k):
        st = real_stat(path, *a, **k)
        if Path(path) == target:  # another program saved the file between the hash check and the rename
            fresh = tmp_path / "editor-save"
            fresh.write_bytes(b"old")
            os.replace(fresh, target)  # same content, new inode
            return real_stat(path, *a, **k)
        return st

    monkeypatch.setattr(pipeline.os, "stat", swapped)
    with pytest.raises(pipeline.PreflightError, match="changed by something else"):
        pipeline.commit_file(target, b"new", snap, "output")
    assert target.read_bytes() == b"old"
    _no_temp_files(tmp_path)


def test_hard_link_to_a_source_is_refused_as_output(tmp_path):
    """Source preservation: a hard link resolves to its own path, so the collision check also asks samefile."""
    src = _project(tmp_path)
    out = tmp_path / "main.docx"
    os.link(src, out)
    before = src.read_bytes()
    with pytest.raises(pipeline.PreflightError, match="is one of the source files"):
        build(src, no_harvest=True, overwrite=True, log=_quiet)
    assert src.read_bytes() == before and out.read_bytes() == before
