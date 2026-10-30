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


# ---- round 15 ----------------------------------------------------------------------------------------------


def test_normalise_turns_dotless_i_plus_mark_into_the_accented_i():
    """C3: pdftotext's 'ı' + combining diaeresis is an ï."""
    from texdocxdrafter.harvest import normalise

    assert normalise("na\u0131\u0308ve Zu\u0308rich") == "naïve Zürich"
    assert normalise("\u0131") == "\u0131"  # a real dotless ı (Turkish) without a mark stays


def test_bibliography_labels_of_every_style_are_moved_to_their_entry():
    """C6: [1], [Knu84], 12. labels print before the entry marker; the first sits in the lead chunk."""
    from texdocxdrafter.audit import Audit
    from texdocxdrafter.harvest import parse_text

    for lab1, lab2, lab3 in (("[1]", "[2]", "[3]"), ("[Knu84]", "[Lam94]", "[Smi20]"), ("1.", "2.", "3.")):
        text = (f"@@xBIB 0 0@@x {lab1} @@xENTRY knuth84@@x Knuth, The TeXbook. 1984. {lab2} @@xENTRY lamport94@@x "
                f"Lamport, LaTeX. 1994. {lab3} @@xENTRY smith20@@x Smith. 2020. @@xENDBIB@@x")
        res = parse_text(text, 0, [(0, 0)], Audit(), "x")
        entries = res.bibs[(0, 0)]
        texts = ["".join(r.text for r in runs) for _, runs in entries]
        assert texts == [f"{lab1} Knuth, The TeXbook. 1984.", f"{lab2} Lamport, LaTeX. 1994.", f"{lab3} Smith. 2020."], texts


VERBOSE_DOC = r"""\documentclass{article}
\usepackage[style=verbose,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
First mention\autocite[12]{knuth84} and again\autocite[14]{knuth84} and Lamport\autocite{lamport94}.
\printbibliography
\end{document}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_footnote_citations_of_a_verbose_style_become_word_footnotes(tmp_path):
    """C4: \\autocite in a verbose style is a footnote in LaTeX; the docx gets one footnote per citation with
    the citation text, and no literal footnote digits in the body."""
    import re

    src = tmp_path / "main.tex"
    src.write_text(VERBOSE_DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    res = build(src, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    z = zipfile.ZipFile(tmp_path / "main.docx")
    body = "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", z.read("word/document.xml").decode()))
    assert "First mention and again and Lamport." in body, body
    fn = z.read("word/footnotes.xml").decode()
    notes = re.findall(r'<w:footnote w:id="\d+">(.*?)</w:footnote>', fn, re.S)
    texts = ["".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", n)) for n in notes]
    assert len(texts) == 3, texts
    assert "Knuth" in texts[0] and "TeXbook" in texts[0] and "p. 12" in texts[0], texts
    assert "Knuth" in texts[1] and "p. 14" in texts[1] and "Lamport" in texts[2], texts
    assert res.audit.census.get("citations in footnotes") == 3
    assert not [e for e in res.audit.entries if e.kind == "fidelity"], [e.message for e in res.audit.entries]


REFSECTIONS_DOC = r"""\documentclass{article}
\usepackage[style=authoryear,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
\begin{refsection}
\section{One}
Cite \parencite{knuth84}.
\printbibliography[heading=subbibliography]
\end{refsection}
\begin{refsection}
\section{Two}
Cite again \parencite{knuth84} and \parencite{lamport94}.
\printbibliography[heading=subbibliography]
\end{refsection}
\end{document}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_same_work_in_two_refsections_builds_with_linked_citations(tmp_path):
    """C5: the entry bookmarks are per refsection, so a work in two bibliographies is no duplicate."""
    import re

    src = tmp_path / "main.tex"
    src.write_text(REFSECTIONS_DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    res = build(src, link_citations=True, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    xml = zipfile.ZipFile(tmp_path / "main.docx").read("word/document.xml").decode()
    assert xml.count("Addison-Wesley") >= 3  # Knuth twice, Lamport once
    anchors = re.findall(r'w:anchor="([^"]+)"', xml)
    assert len(anchors) == 3 and len(set(anchors)) == 3, anchors  # each citation points into its own section's list


def test_no_label_is_peeled_from_an_unlabelled_list():
    """D2: author-year lists have no labels; an entry ending "p. 5." keeps its page number."""
    from texdocxdrafter.audit import Audit
    from texdocxdrafter.harvest import parse_text

    text = ("@@xBIB 0 0@@x @@xENTRY a@@x Knuth, Donald (1984). The TeXbook. In: J, p. 5. @@xENTRY b@@x Report, Rita (2019). "
            "Vol. 3. @@xENTRY c@@x Smith (2020). No. 12. @@xENDBIB@@x")
    res = parse_text(text, 0, [(0, 0)], Audit(), "x")
    texts = ["".join(r.text for r in runs) for _, runs in res.bibs[(0, 0)]]
    assert texts == ["Knuth, Donald (1984). The TeXbook. In: J, p. 5.", "Report, Rita (2019). Vol. 3.", "Smith (2020). No. 12."], texts
    # a numbered list still moves its labels, and only labels of its own form
    text = "@@xBIB 0 0@@x 1. @@xENTRY a@@x Knuth. In: J, p. 5. 2. @@xENTRY b@@x Report [3]. 3. @@xENTRY c@@x Smith. @@xENDBIB@@x"
    res = parse_text(text, 0, [(0, 0)], Audit(), "x")
    texts = ["".join(r.text for r in runs) for _, runs in res.bibs[(0, 0)]]
    assert texts == ["1. Knuth. In: J, p. 5.", "2. Report [3].", "3. Smith."], texts


MIXED_DOC = r"""\documentclass{article}
\usepackage[style=verbose,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
First\footcite{knuth84} and then\autocite[7]{knuth84} and Lamport\footcite[3]{lamport94}.
\printbibliography
\end{document}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_footcite_and_autocite_share_bibliographys_repeat_tracking(tmp_path):
    """D3: \\footcite is typeset as a footnote in the side document too, so the repeat is short and the notes
    keep their closing period."""
    import re

    src = tmp_path / "main.tex"
    src.write_text(MIXED_DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    res = build(src, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    z = zipfile.ZipFile(tmp_path / "main.docx")
    fn = z.read("word/footnotes.xml").decode()
    notes = re.findall(r'<w:footnote w:id="\d+">(.*?)</w:footnote>', fn, re.S)
    texts = ["".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", n)).strip() for n in notes]
    assert len(texts) == 3, texts
    assert texts[0].startswith("Donald E. Knuth. The TeXbook") and texts[0].endswith("."), texts
    assert texts[1] == "Knuth, The TeXbook, p. 7.", texts  # the repeat is short: both citations are footnotes
    assert texts[2].startswith("Leslie Lamport") and "p. 3" in texts[2] and texts[2].endswith("."), texts


def test_marked_labels_belong_to_the_entry_that_follows():
    """E1: the side document marks every list label; the label before an entry marker is that entry's, whatever
    its shape ("[Kant KpV]" with a space, "[1]", "12."), and an unlabelled list marks empty labels."""
    from texdocxdrafter.audit import Audit
    from texdocxdrafter.harvest import parse_text

    text = ("@@xBIB 0 0@@x @@xLABEL@@x[Kant KpV]@@x/LABEL@@x @@xENTRY kant@@x Immanuel Kant. KpV. 1788. "
            "@@xLABEL@@x [Knu84] @@x/LABEL@@x @@xENTRY knuth84@@x Knuth, The TeXbook. 1984. @@xLABEL@@x12.@@x/LABEL@@x "
            "@@xENTRY lamport94@@x Lamport, LaTeX. 1994. [3] @@xENDBIB@@x")
    res = parse_text(text, 0, [(0, 0)], Audit(), "x")
    texts = ["".join(r.text for r in runs) for _, runs in res.bibs[(0, 0)]]
    assert texts == ["[Kant KpV] Immanuel Kant. KpV. 1788.", "[Knu84] Knuth, The TeXbook. 1984.",
                     "12. Lamport, LaTeX. 1994. [3]"], texts
    text = ("@@xBIB 0 0@@x @@xLABEL@@x@@x/LABEL@@x @@xENTRY a@@x Knuth, Donald (1984). The TeXbook, p. 5. "
            "@@xLABEL@@x @@x/LABEL@@x @@xENTRY b@@x Report (2019). Vol. 3. @@xENDBIB@@x")
    res = parse_text(text, 0, [(0, 0)], Audit(), "x")
    texts = ["".join(r.text for r in runs) for _, runs in res.bibs[(0, 0)]]
    assert texts == ["Knuth, Donald (1984). The TeXbook, p. 5.", "Report (2019). Vol. 3."], texts


FOOTNOTE_DOC = r"""\documentclass{article}
\usepackage[style=verbose,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
Body text.\footnote{See \cite[5]{knuth84}.} More\footcite[7]{knuth84} and body \cite{knuth84} then \cite{lamport94} and \cite{lamport94}.
Nested\footnote{A note with \footcite[9]{lamport94} inside.} end.
\printbibliography
\end{document}
"""


def test_side_document_keeps_a_footnotes_citation_in_footnote_context(tmp_path):
    """E2: a citation inside \\footnote is typeset inside biblatex's footnote context in the side document."""
    from texdocxdrafter.audit import Audit
    from texdocxdrafter.parser import parse_document
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "main.tex"
    src.write_text(FOOTNOTE_DOC)
    (tmp_path / "refs.bib").write_text(BIB)
    pf = preflight(src, need_build_files=False)
    doc = parse_document(pf, Audit())
    tex, _ = build_harvest_tex(pf, doc, "abcd")
    assert "\\wfInFN{\\cite[5]{knuth84}\\relax}\\relax{\\ttfamily @@abcdEND@@abcd}" in tex, tex
    assert "\\wfInFN{\\footcite[9]{lamport94}\\relax}" in tex, tex
    assert "@@abcdCITE 1@@abcd} \\footcite[7]{knuth84}\\relax" in tex, tex  # a body citation is not wrapped
    assert "\\toggletrue{blx@footnote}" in tex and "@@abcdLABEL@@abcd" in tex


def _compile(tmp_path, doc: str, bib: str = BIB):
    src = tmp_path / "main.tex"
    src.write_text(doc)
    (tmp_path / "refs.bib").write_text(bib)
    for cmd in (["pdflatex", "-interaction=batchmode", "main"], ["biber", "main"], ["pdflatex", "-interaction=batchmode", "main"]):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=120)
    assert (tmp_path / "main.bbl").is_file()
    return src


def _texts(tmp_path):
    import re

    z = zipfile.ZipFile(tmp_path / "main.docx")

    def t(xml):
        return "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", xml))

    doc = z.read("word/document.xml").decode()
    paras = [t(p) for p in re.findall(r"<w:p[ >].*?</w:p>", doc, re.S)]
    notes = []
    if "word/footnotes.xml" in z.namelist():
        fn = z.read("word/footnotes.xml").decode()
        notes = [t(n).strip() for n in re.findall(r'<w:footnote w:id="\d+">(.*?)</w:footnote>', fn, re.S)]
    return [p for p in paras if p.strip()], notes


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_citations_in_footnotes_keep_footnote_context_and_nested_notes_are_parenthesised(tmp_path):
    """E2, E3 end to end against what LaTeX prints: the footnote tracker has seen the \\cite inside \\footnote,
    so the \\footcite repeat is short and the first body citation full; a \\footcite inside a footnote is
    biblatex's parenthesised note, once, without a closing period and with a single space before it."""
    src = _compile(tmp_path, FOOTNOTE_DOC)
    res = build(src, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    paras, notes = _texts(tmp_path)
    assert paras[0] == ("Body text. More and body Donald E. Knuth. The TeXbook. Addison-Wesley, 1984 then Leslie Lamport. "
                        "LaTeX: A Document Preparation System. Addison-Wesley, 1994 and Lamport, LaTeX: A Document "
                        "Preparation System. Nested end."), paras[0]
    assert notes == ["See Donald E. Knuth. The TeXbook. Addison-Wesley, 1984, p. 5.",
                     "Knuth, The TeXbook, p. 7.",
                     "A note with (Leslie Lamport. LaTeX: A Document Preparation System. Addison-Wesley, 1994, p. 9) inside."], notes


IBID_DOC = r"""\documentclass{article}
\usepackage[style=authoryear-ibid,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
Body text.\footnote{See \cite{lamport94}.} More.\footnote{Again \cite{lamport94}.} Then \cite{lamport94} and \cite{lamport94}.
\printbibliography
\end{document}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_ibid_follows_footnote_context_and_the_period_after_it_is_not_doubled(tmp_path):
    """E2 with authoryear-ibid: the second footnote is "ibid." because the first footnote cited the work; the body
    starts afresh. The period the source puts after "ibid." is dropped as biblatex drops it."""
    src = _compile(tmp_path, IBID_DOC)
    res = build(src, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    paras, notes = _texts(tmp_path)
    assert paras[0] == "Body text. More. Then Lamport 1994 and ibid.", paras[0]
    assert notes == ["See Lamport 1994.", "Again ibid."], notes


ALPHA_DOC = r"""\documentclass{article}
\usepackage[style=alphabetic,backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
Cites \cite{kant}, \cite{knuth84}, \cite{lamport94}.
\printbibliography
\end{document}
"""

KANT = r"""@book{kant, author={Immanuel Kant}, title={Kritik der praktischen Vernunft}, year={1788}, shorthand={Kant KpV}, publisher={Hartknoch}}
"""


@pytest.mark.skipif(not (HAVE_TOOLS and sandbox.have_os_sandbox()), reason="pdflatex, biber, poppler or bubblewrap missing")
def test_alphabetic_labels_with_a_shorthand_stay_with_their_entry(tmp_path):
    """E1 end to end: a shorthand with a space sorted first did not fit the old label pattern, and every label
    then went to the entry before its own."""
    src = _compile(tmp_path, ALPHA_DOC, BIB + KANT)
    res = build(src, log=lambda *_: None)
    assert res.check.ok, res.check.errors
    paras, _ = _texts(tmp_path)
    assert paras[-3:] == ["[Kant KpV] Immanuel Kant. Kritik der praktischen Vernunft. Hartknoch, 1788.",
                          "[Knu84] Donald E. Knuth. The TeXbook. Addison-Wesley, 1984.",
                          "[Lam94] Leslie Lamport. LaTeX: A Document Preparation System. Addison-Wesley, 1994."], paras
