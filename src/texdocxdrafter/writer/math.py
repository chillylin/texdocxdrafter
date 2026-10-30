"""LaTeX math → OMML via latex2mathml and mathml2omml (both pure Python)."""
from __future__ import annotations

import re

from lxml import etree

from .xmlutil import M, NSMAP, W, el, q

PLACEHOLDER = "\ue000"  # stands for an alignment point '&' while converting a line
BINOM_MARK = "\ue002"  # marks the numerator of a \\binom so the OMML fraction can be made bar-less
_UNKNOWN = re.compile(r"<mi>\\([A-Za-z]+)</mi>")  # a control sequence the converter did not know: kept as text

_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\\(?:mathbbm|mathds)\b"), r"\\mathbb"),
    (re.compile(r"\\coloneqq\b"), ":="),
    (re.compile(r"\\eqqcolon\b"), "=:"),
    (re.compile(r"\\operatorname\*"), r"\\operatorname"),
    (re.compile(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b\s*"), ""),
    (re.compile(r"\\(?:big|Big|bigg|Bigg)([lrm]?)(?=[\(\)\[\]\|\\{}.<>/])"), ""),
    (re.compile(r"\\(?:nonumber|notag)\b"), ""),
    (re.compile(r"\\(?:vphantom|hphantom|phantom)\{[^{}]*\}"), ""),
    (re.compile(r"\\(?:mathstrut)\b"), ""),
    (re.compile(r"\\hspace\*?\{[^{}]*\}"), r"\\ "),
    (re.compile(r"\\(?:qquad)\b"), r"\\quad\\quad"),
    (re.compile(r"\\substack\{([^{}]*)\}"), lambda m: m.group(1).replace("\\\\", ",")),
    (re.compile(r"\\textnormal\{"), r"\\text{"),
    (re.compile(r"\\mathrm\{\\mathrm"), r"\\mathrm{"),
    (re.compile(r"\\(?:limits|nolimits)\b"), ""),
    (re.compile(r"\\boldsymbol\{\\(\w+)\}"), r"\\boldsymbol{\\\1}"),
    (re.compile(r"\\ensuremath\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\;|\\:|\\,|\\!|\\>"), " "),  # the thin spaces, not a plain ; (p(y;\\mu) keeps its semicolon)
    (re.compile(r"\\(?:dbinom|tbinom)\b"), r"\\binom"),
    (re.compile(r"\\binom\{"), r"\\binom{" + BINOM_MARK),  # found again in the OMML: the fraction gets no bar
    (re.compile(r"\\lvert|\\rvert"), "|"),
    (re.compile(r"\\lVert|\\rVert"), "‖"),
    (re.compile(r"\\leqslant\b"), r"\\leq"),
    (re.compile(r"\\geqslant\b"), r"\\geq"),
    (re.compile(r"\\varnothing\b"), "∅"),
    (re.compile(r"\\mathscr\b"), r"\\mathcal"),
    (re.compile(r"\\dfrac\b|\\tfrac\b"), r"\\frac"),
    (re.compile(r"\\argmax\b"), r"\\operatorname{arg\\,max}"),
    (re.compile(r"\\argmin\b"), r"\\operatorname{arg\\,min}"),
]


def preprocess(latex: str) -> str:
    s = latex
    for pat, rep in _REPLACEMENTS:
        s = pat.sub(rep, s)
    # nested aligned/split/gathered environments are handled by the caller; here flatten leftovers
    s = re.sub(r"\\begin\{(?:aligned|split|gathered|alignedat)\}(?:\{\d+\})?", "", s)
    s = re.sub(r"\\end\{(?:aligned|split|gathered|alignedat)\}", "", s)
    return s.strip()


MAX_MATH_CHARS = 200_000  # a formula longer than this is not a formula; it stays as text


def _unwrap_boxes(root) -> None:
    for box in list(root.iter(q("m:box"))):
        if box.find(q("m:boxPr")) is not None:
            continue
        e = box.find(q("m:e"))
        parent = box.getparent()
        if parent is None:
            continue
        for c in (list(e) if e is not None else []):
            box.addprevious(c)  # no index()/insert(): those are linear in the siblings, quadratic overall
        parent.remove(box)


def _fix_radicals(root) -> None:
    for rad in root.iter(q("m:rad")):
        if rad.find(q("m:deg")) is None:
            pr = rad.find(q("m:radPr"))
            if pr is None:
                pr = el("m:radPr")
                rad.insert(0, pr)
            pr.append(el("m:degHide", {"m:val": "1"}))
            rad.insert(list(rad).index(rad.find(q("m:e"))), el("m:deg"))


def convert(latex: str, unknown: list | None = None):
    """Return an ``m:oMath`` element, or raise on failure. Control sequences the converter does not know come out
    as literal text; their names are appended to ``unknown`` when given, so the writer can report them."""
    import latex2mathml.converter as l2m
    import mathml2omml

    if len(latex) > MAX_MATH_CHARS:
        raise ValueError(f"formula longer than {MAX_MATH_CHARS} characters")
    src = preprocess(latex)
    if not src:
        raise ValueError("empty formula")
    mathml = l2m.convert(src)
    if unknown is not None:
        unknown.extend(m.group(1) for m in _UNKNOWN.finditer(mathml))
    omml = mathml2omml.convert(mathml)
    # mathml2omml 0.0.2 leaves <m:groupChrPr> unclosed (accents, \\vec, \\underbrace)
    omml = re.sub(r"(<m:groupChrPr>(?:<m:(?:chr|pos|vertJc)[^>]*/>)*)</m:groupChr>", r"\1</m:groupChrPr>", omml)
    wrapper = etree.fromstring(f'<root xmlns:m="{M}" xmlns:w="{W}">{omml}</root>')
    node = wrapper.find(q("m:oMath"))
    if node is None:
        raise ValueError("no oMath in converter output")
    _unwrap_boxes(node)
    _fix_radicals(node)
    _binomials(node)
    return node


def _binomials(node) -> None:
    """A fraction whose numerator starts with BINOM_MARK is a binomial coefficient: no bar."""
    for t in list(node.iter(q("m:t"))):
        if t.text and BINOM_MARK in t.text:
            t.text = t.text.replace(BINOM_MARK, "")
            frac = next((a for a in t.iterancestors() if a.tag == q("m:f")), None)
            if frac is not None:
                pr = frac.find(q("m:fPr"))
                if pr is None:
                    pr = el("m:fPr")
                    frac.insert(0, pr)
                if pr.find(q("m:type")) is None:
                    pr.append(el("m:type", {"m:val": "noBar"}))
            if not t.text:
                r = t.getparent()
                if r is not None and r.tag == q("m:r") and r.getparent() is not None and len(r.getparent()) > 1:
                    r.getparent().remove(r)


def omath_para(oMath) -> "etree._Element":
    return el("m:oMathPara", None, oMath)


def _alignment_points(e) -> None:
    """Turn the placeholder run into an ``m:aln`` mark on the following run (OMML's alignment point)."""
    for r in list(e.iter(q("m:r"))):
        t = r.find(q("m:t"))
        if t is None or t.text is None or PLACEHOLDER not in t.text:
            continue
        rest = t.text.replace(PLACEHOLDER, "")
        nxt = r.getnext()
        parent = r.getparent()
        if rest:
            t.text = rest
            target = r
        else:
            parent.remove(r)
            target = nxt
        if target is not None and target.tag == q("m:r"):
            pr = target.find(q("m:rPr"))
            if pr is None:
                pr = el("m:rPr")
                target.insert(0, pr)
            pr.append(el("m:aln"))


def eq_array(lines: list[str], unknown: list | None = None):
    """Several aligned lines as one OMML equation array; '&' marks alignment points."""
    rows = []
    for ln in lines:
        node = convert(ln.replace("&", PLACEHOLDER), unknown)
        e = el("m:e")
        for c in list(node):
            e.append(c)
        _alignment_points(e)
        rows.append(e)
    return el("m:oMath", None, el("m:eqArr", None, *rows))


def inline_math(latex: str, unknown: list | None = None):
    return convert(latex.replace("&", " "), unknown)
