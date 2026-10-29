"""The .bbl reader: natbib labels, entry formatting, citation commands and style options."""
from texdocxdrafter.bbl import CiteStyle, cite_style, parse_bbl, parse_cite, render, render_cite

BBL = r"""\begin{thebibliography}{3}
\providecommand{\natexlab}[1]{#1}
\bibitem[{Smith et~al.(2020{\natexlab{a}})Smith, Jones, and Lee}]{smith2020a}
J.~Smith, K.~Jones, and M.~Lee.
\newblock The title of the {ESG} paper.
\newblock {\em Journal of Things}, 12(3):45--67, 2020{\natexlab{a}}.
\newblock \doi{10.1000/xyz}.

\bibitem[Jones and M\"{u}ller(2019)]{jones2019}
K.~Jones and A.~M\"{u}ller.
\newblock \emph{A Book About Stuff}.
\newblock Publisher \& Sons, 2nd edition, 2019.
\newblock URL \url{https://example.org/book}.

\bibitem[{Smith et~al.(2021)Smith and Lee}]{smith2021}
J.~Smith and M.~Lee.
\newblock Another one.

\end{thebibliography}
"""


def _text(runs):
    return "".join(r.text for r in runs)


def test_labels_and_entries():
    es = parse_bbl(BBL)
    assert [e.key for e in es] == ["smith2020a", "jones2019", "smith2021"]
    assert (es[0].short, es[0].year, es[0].long) == ("Smith et al.", "2020a", "Smith, Jones, and Lee")
    assert (es[1].short, es[1].year, es[1].long) == ("Jones and Müller", "2019", "Jones and Müller")
    runs = render(es[0].body)
    assert _text(runs) == "J. Smith, K. Jones, and M. Lee. The title of the ESG paper. Journal of Things, 12(3):45–67, 2020a. doi:10.1000/xyz."
    assert [r.text for r in runs if "i" in r.fmt] == ["Journal of Things"]
    assert [r.url for r in runs if r.url] == ["https://doi.org/10.1000/xyz"]
    runs = render(es[1].body)
    assert "Publisher & Sons" in _text(runs)
    assert [r.text for r in runs if r.url] == ["https://example.org/book"]


def test_parse_cite():
    assert parse_cite(r"\citep*[see][p.~3]{a, b}") == ("citep", True, "see", "p.~3", ["a", "b"])
    assert parse_cite(r"\citet[p.~3]{a}") == ("citet", False, "", "p.~3", ["a"])
    assert parse_cite(r"\Citep{a}")[0] == "Citep"


def test_author_year_citations():
    by = {e.key: e for e in parse_bbl(BBL)}
    st = CiteStyle()
    cases = {
        r"\citep{smith2020a}": "(Smith et al., 2020a)",
        r"\citet{smith2020a,jones2019}": "Smith et al. (2020a); Jones and Müller (2019)",
        r"\citep[see][p.~3]{jones2019}": "(see Jones and Müller, 2019, p. 3)",
        r"\citet[p.~3]{jones2019}": "Jones and Müller (2019, p. 3)",
        r"\citep{smith2020a,smith2021}": "(Smith et al., 2020a, 2021)",
        r"\citealp{jones2019}": "Jones and Müller, 2019",
        r"\citealt{jones2019}": "Jones and Müller 2019",
        r"\citeauthor{smith2020a}": "Smith et al.",
        r"\citeauthor*{smith2020a}": "Smith, Jones, and Lee",
        r"\citeyear{jones2019}": "2019",
        r"\citeyearpar{jones2019}": "(2019)",
        r"\cite{smith2020a}": "Smith et al. (2020a)",
        r"\citetext{priv.\ comm.}": "(priv. comm.)",
    }
    for raw, want in cases.items():
        assert _text(render_cite(raw, by, st)) == want, raw
    assert render_cite(r"\citep{unknown}", by, st) is None
    assert render_cite(r"\nocite{*}", by, st) is None
    st.open, st.close, st.citesep, st.aysep = "[", "]", ",", ";"
    assert _text(render_cite(r"\citep{smith2020a,jones2019}", by, st)) == "[Smith et al.; 2020a, Jones and Müller; 2019]"


def test_numeric_citations():
    by = {e.key: e for e in parse_bbl(BBL)}
    st = CiteStyle(mode="numbers", open="[", close="]", citesep=",")
    assert _text(render_cite(r"\citep{smith2020a,jones2019,smith2021}", by, st)) == "[1, 2, 3]"
    st.compress = True
    assert _text(render_cite(r"\citep{smith2020a,jones2019,smith2021}", by, st)) == "[1–3]"
    assert _text(render_cite(r"\citet{jones2019}", by, st)) == "Jones and Müller [2]"
    assert _text(render_cite(r"\cite[p.~3]{jones2019}", by, st)) == "[2, p. 3]"
    sup = render_cite(r"\cite{smith2020a,smith2021}", by, CiteStyle(mode="super", compress=True))
    assert _text(sup) == "1,3" and "sup" in sup[0].fmt
    plain = CiteStyle(natbib=False, mode="numbers", open="[", close="]")
    es = parse_bbl(r"\begin{thebibliography}{9}\bibitem{a} A.\bibitem[Smi20]{b} B.\end{thebibliography}")
    assert _text(render_cite(r"\cite{a,b}", {e.key: e for e in es}, plain)) == "[1, Smi20]"


class _PF:
    def __init__(self, packages, preamble=""):
        self.packages = packages
        self.orig_preamble = preamble


def test_style_from_preamble():
    es = parse_bbl(BBL)
    st = cite_style(_PF([("natbib", "square,comma")]), es)
    assert (st.mode, st.open, st.close, st.citesep) == ("authoryear", "[", "]", ",")
    st = cite_style(_PF([("natbib", "numbers,sort&compress")]), es)
    assert (st.mode, st.open, st.close, st.citesep, st.compress) == ("numbers", "[", "]", ",", True)
    st = cite_style(_PF([("natbib", "")], r"\bibpunct{(}{)}{;}{a}{}{,}"), es)
    assert (st.mode, st.aysep) == ("authoryear", "")
    st = cite_style(_PF([("natbib", "")], r"\setcitestyle{square,aysep={;}}"), es)
    assert (st.open, st.aysep) == ("[", ";")
    st = cite_style(_PF([("hyperref", "")]), es)
    assert (st.natbib, st.mode, st.open) == (False, "numbers", "[")
    # natbib loaded but the .bst produced no author-year labels: numeric
    plain = parse_bbl(r"\begin{thebibliography}{9}\bibitem{a} A.\end{thebibliography}")
    assert cite_style(_PF([("natbib", "")]), plain).mode == "numbers"
