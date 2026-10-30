"""Round 14: the biblatex harvest side document."""
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from texdocxdrafter import sandbox
from texdocxdrafter.harvest import build_harvest_tex, linkify, missing_keys
from texdocxdrafter.parser.model import Run
from texdocxdrafter.pipeline import build

HAVE_TOOLS = all(shutil.which(t) for t in ("pdflatex", "biber", "pdftotext"))


def test_missing_keys_from_both_quote_styles_and_bibers_log():
    """B5: LaTeX writes Citation 'key' with a straight quote now; biber reports in its own .blg."""
    log = "LaTeX Warning: Citation 'newstyle' on page 1 undefined on input line 3.\nLaTeX Warning: Citation `oldstyle' undefined\n"
    blg = "INFO - Found BibTeX data source refs.bib\nWARN - I didn't find a database entry for 'inblg' (section 0)\n"
    assert missing_keys(log, blg) == ["inblg", "newstyle", "oldstyle"]
    assert missing_keys("", "") == []


def test_linkify_keeps_existing_targets():
    """B4: a run that already has a target (\\href, \\doi) is left alone."""
    runs = linkify([Run("doi:10.1/x", frozenset(), "https://doi.org/10.1/x"), Run("see https://ex.com/p. ", frozenset())])
    assert [(r.text, r.url) for r in runs] == [("doi:10.1/x", "https://doi.org/10.1/x"), ("see ", None),
                                                ("https://ex.com/p", "https://ex.com/p"), (". ", None)]


def test_side_document_ends_each_citation_with_relax(tmp_path):
    """B2: \\cites{a}{b} must not read the marker group after it as a third key."""
    from texdocxdrafter.audit import Audit
    from texdocxdrafter.parser import parse_document
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "main.tex"
    src.write_text(DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    pf = preflight(src, need_build_files=False)
    doc = parse_document(pf, Audit())
    tex, _ = build_harvest_tex(pf, doc, "abcd")
    assert "\\cites{knuth84}{lamport94}\\relax{\\ttfamily @@abcdEND@@abcd}" in tex, tex
    assert "\\renewcommand{\\LaTeX}{LaTeX}" in tex


DOC = r"""\documentclass{article}
\usepackage[style=authoryear,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
Both \cites{knuth84}{lamport94} and one \parencite{knuth84} and a missing \cite{nothere}.
\printbibliography
\end{document}
"""
BIB = r"""@book{knuth84, author={Donald E. Knuth}, title={The {\TeX}book}, year={1984}, publisher={Addison-Wesley}}
@book{lamport94, author={Leslie Lamport}, title={{\LaTeX}: A Document Preparation System}, year={1994}, publisher={Addison-Wesley}}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_real_harvest_multicite_logos_and_missing_key(tmp_path):
    """B2, B5, B7 end to end: the harvested text of \\cites has two names and no stray marker, the logos read
    LaTeX/TeX, and the missing key is reported."""
    src = tmp_path / "main.tex"
    src.write_text(DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    assert (tmp_path / "main.bbl").is_file()
    res = build(src, log=lambda *_: None)
    xml = zipfile.ZipFile(tmp_path / "main.docx").read("word/document.xml").decode()
    import re

    text = "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", xml))
    assert "Knuth 1984; Lamport 1994" in text, text
    assert "ttfamily" not in text and "@@" not in text
    assert "The TeXbook" in text and "LaTeX: A Document" in text and "L A TEX" not in text, text
    assert any("not found in the .bib" in e.message and "nothere" in e.message for e in res.audit.entries), \
        [e.message for e in res.audit.entries]
