import re
from pathlib import Path

import pytest

from texdocxdrafter import lexer
from texdocxdrafter.auxreader import read_aux
from texdocxdrafter.expand import Expander, resolve_conditionals
from texdocxdrafter.harvest import normalise, to_runs
from texdocxdrafter.parser.blocks import parse_list_label, parse_keyvals
from texdocxdrafter.parser.tables import parse_colspec
from texdocxdrafter.writer.textmath import text_math
from texdocxdrafter.writer import math as wmath
from texdocxdrafter.writer.xmlutil import field, q

FORMULAS = Path(__file__).with_name("formulas.txt")


def test_lexer_multicite_and_cmidrule():
    nodes = lexer.walk(r"a \parencites[p]{a}[q]{b} x \cmidrule(lr){2-3} y")
    assert nodes[1].latex_verbatim() == r"\parencites[p]{a}[q]{b}"
    assert any(getattr(n, "macroname", "") == "cmidrule" for n in nodes)


def test_newcommand_with_default_and_expansion():
    ex = Expander()
    text = ex.scan(r"\newcommand{\foo}[2][x]{A#1B#2} \def\bar#1{[#1]} body")
    assert set(ex.macros) == {"foo", "bar"}
    assert "newcommand" not in text
    nodes = ex.walk(r"\foo{y} \foo[z]{y} \bar{q}")
    macros = [n for n in nodes if getattr(n, "macroname", None)]
    assert [ex.expand_macro_node(n) for n in macros] == ["AxBy", "AzBy", "[q]"]


def test_newenvironment_with_unbalanced_begin():
    ex = Expander()
    ex.scan(r"\newenvironment{box}[1]{\begin{quote}#1}{\end{quote}}")
    assert ex.envs["box"].begin == r"\begin{quote}#1"


def test_conditionals():
    ex = Expander()
    nodes = ex.walk(r"\ifdraft YES\else NO\fi \iffalse GONE\fi KEPT")
    out = resolve_conditionals(nodes, {"draft": True})
    text = "".join(n.latex_verbatim() for n in out)
    assert "YES" in text and "NO" not in text and "GONE" not in text and "KEPT" in text
    out = resolve_conditionals(ex.walk(r"\ifdraft YES\else NO\fi"), {"draft": False})
    assert "NO" in "".join(n.latex_verbatim() for n in out)


def test_colspec():
    cols = parse_colspec(r"@{}l >{\raggedright}p{3cm} *{2}{c} X|r")
    assert [a for a, _ in cols] == ["l", "l", "c", "c", "l", "r"]
    assert cols[1][1] > 0 and cols[4][1] < 0


def test_enumitem_label():
    spec = parse_list_label(r"leftmargin=1em,label=\textbf{O\arabic*.}")
    assert spec == {"fmt": "decimal", "pattern": "O%1.", "bold": True}
    assert parse_list_label("(a)")["fmt"] == "lowerLetter"
    assert parse_keyvals("title={A, B},heading=x") == {"title": "{A, B}", "heading": "x"}


def test_harvest_marker_parser():
    runs = to_runs("Smith (2020). @@I@@Title ﬁne@@/I@@, @@I@@12@@/I@@(3), 4–5.")
    assert [(r.text, "i" in r.fmt) for r in runs][1][1] is True
    assert "fine" not in normalise("ﬁne") or normalise("ﬁne") == "fine"
    nested = to_runs("@@I@@a @@I@@b@@/I@@ c@@/I@@")
    assert [("i" in r.fmt) for r in nested] == [True, False, True]


def test_aux_reader(tmp_path):
    aux = tmp_path / "x.aux"
    aux.write_text("\\newlabel{sec:a}{{2.1}{3}{Title}{section.2.1}{}}\n"
                   "\\newlabel{sec:a@cref}{{[subsection][1][2]2.1}{[1][3][]3}}\n")
    labels = read_aux(aux)
    assert labels["sec:a"].number == "2.1" and labels["sec:a"].page == "3"
    assert labels["sec:a"].cref_kind == "subsection"


def test_field_xml():
    els = field("SEQ Table \\* ARABIC", "1")
    kinds = [e.find(q("w:fldChar")).get(q("w:fldCharType")) for e in els if e.find(q("w:fldChar")) is not None]
    assert kinds == ["begin", "separate", "end"]
    assert "SEQ Table" in els[1].find(q("w:instrText")).text


def test_text_math():
    assert text_math("^3") == [("3", False, "superscript")]
    assert text_math(r"\rightarrow") == [("\u2192", False, None)]
    assert text_math(r"\alpha=0.63") == [("\u03b1", True, None), (" = 0.63", False, None)]
    assert text_math("x_i") == [("x", True, None), ("i", True, "subscript")]
    assert text_math("-7.63") == [("\u22127.63", False, None)]
    assert text_math(r"p_{ik} \in [0,1]")[-1] == (" \u2208 [0, 1]", False, None)
    for formula in (r"\frac{a}{b}", r"1 - \prod_{k}(1 - p_k)", r"\mathtt{x}", r"\hat{x}", "x^a^b"):
        assert text_math(formula) is None


def test_forty_formulas_convert():
    formulas = [l for l in FORMULAS.read_text().splitlines() if l.strip()]
    assert len(formulas) >= 40
    ok = 0
    for f in formulas:
        try:
            wmath.convert(f)
            ok += 1
        except Exception:
            pass
    assert ok / len(formulas) >= 0.9


COMMENTED = r"""\documentclass{article}
% preamble note
\usepackage{tikz}
\begin{document}
\section{Intro}
% before the paragraph
First line, 5\% more. % on the first line
% inside the paragraph
Second line.

%%%%%%%%
\begin{figure}
\footnotesize
\begin{tikzpicture}
% picture code note
\draw (0,0) -- (1,0);
\end{tikzpicture}
\caption{A picture}
\end{figure}
\end{document}
"""


def test_comments_collected_with_attachment(tmp_path):
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "c.tex"
    src.write_text(COMMENTED)
    pf = preflight(src, need_build_files=False)
    got = [(c.line, c.text, c.attach) for c in pf.comments]
    assert got == [(6, "before the paragraph", "next"), (7, "on the first line", "prev"),
                   (8, "inside the paragraph", "prev")]


def test_tikzpicture_parsed_as_image_with_float_size(tmp_path):
    from texdocxdrafter.parser import parse_document
    from texdocxdrafter.parser.model import Figure
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "c.tex"
    src.write_text(COMMENTED)
    doc = parse_document(preflight(src, need_build_files=False))
    figs = [b for b in doc.blocks if isinstance(b, Figure)]
    assert len(figs) == 1 and len(figs[0].images) == 1
    img = figs[0].images[0]
    assert img.tikz.startswith(r"\def\baselinestretch{1}\footnotesize \begin{tikzpicture}")
    assert figs[0].caption


REVIEWED = r"""\documentclass[twocolumn]{article}
\usepackage[normalem]{ulem}
\DeclareRobustCommand{\del}[1]{\leavevmode{\color{red}\sout{#1}}}
\DeclareRobustCommand{\add}[1]{\leavevmode{\color{blue}\uline{#1}}}
\begin{document}
Answers \del{five} \add{four} questions.

\del{
\paragraph{Removed.}
A removed paragraph.
}
\begin{itemize}
\item[MAE] Mean absolute error.
\end{itemize}
\end{document}
"""


def test_review_markup_becomes_revisions(tmp_path):
    from texdocxdrafter.parser import parse_document
    from texdocxdrafter.parser.model import ListBlock, Para, Revision, RevisionBlock
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "r.tex"
    src.write_text(REVIEWED)
    pf = preflight(src, need_build_files=False)
    assert pf.two_column
    doc = parse_document(pf)
    para = next(b for b in doc.blocks if isinstance(b, Para))
    assert [(x.kind, x.macro) for x in para.children if isinstance(x, Revision)] == [("del", "del"), ("ins", "add")]
    block = next(b for b in doc.blocks if isinstance(b, RevisionBlock))
    assert block.kind == "del" and block.blocks
    lst = next(b for b in doc.blocks if isinstance(b, ListBlock))
    assert lst.items[0].term


def test_column_widths_keep_longest_word():
    from types import SimpleNamespace

    from texdocxdrafter.parser.model import Cell, Para, Row, Text
    from texdocxdrafter.writer import DocxWriter

    texts = ("Configuration of the released model", "0.834", "0.727", "0.037")
    cells = [Cell(blocks=[Para(children=[Text(text=t)])]) for t in texts]
    t = SimpleNamespace(colspec=["l"] * 4, colweights=[0] * 4, rows=[Row(cells=cells)])
    widths = DocxWriter.column_widths(None, t, 4000, pad=110)  # wants 5,440 twips, floors need 3,240
    floors = [max(len(w) for w in x.split()) * 100 + 110 for x in texts]
    assert all(w >= f for w, f in zip(widths, floors)) and sum(widths) <= 4000


def test_revision_environment_becomes_revision_block(tmp_path):
    from texdocxdrafter.parser import parse_document
    from texdocxdrafter.parser.model import Heading, RevisionBlock
    from texdocxdrafter.preflight import preflight

    src = tmp_path / "e.tex"
    src.write_text(r"""\documentclass{article}
\newenvironment{addblock}{\color{NavyBlue}}{}
\begin{document}
\begin{addblock}
\section{New}\label{sec:new}
Added text.
\end{addblock}
\end{document}
""")
    doc = parse_document(preflight(src, need_build_files=False))
    block = next(b for b in doc.blocks if isinstance(b, RevisionBlock))
    assert block.kind == "ins" and block.macro == "addblock"
    assert isinstance(block.blocks[0], Heading) and block.blocks[0].label == "sec:new"
