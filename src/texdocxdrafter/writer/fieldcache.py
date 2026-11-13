"""Field results computed the way Word computes them, so the docx needs no field update when it opens.

Every REF field carries a cached result. Word shows that result until the fields are updated; asking Word
to update them on open (settings ``updateFields``) makes it ask the user first, with a question about
"fields that may refer to other files". Instead the writer fills in each result from the document itself:

* ``REF bm`` (no ``\\n``): the text inside the bookmark, which is what Word shows (a caption's number, a
  heading's title for ``\\nameref``);
* ``REF bm \\n``: the list number of the paragraph holding the bookmark, from a walk over the document's
  numbered paragraphs with the formats in numbering.xml (headings, appendix headings, enumerate items, and
  whatever a reference document defines).

A result that cannot be computed (an empty bookmark, a paragraph without a number) keeps the cached value
the writer gave it, which is LaTeX's number from the .aux. PAGEREF results stay LaTeX's page numbers: pages
exist only once Word lays the document out.
"""
from __future__ import annotations

import re

from .xmlutil import q

_REF = re.compile(r"\s*REF\s+(\S+)(.*)$", re.S)


# ---- number formats -------------------------------------------------------------------------------------

def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += s
            n -= v
    return out


def _letter(n: int) -> str:
    return chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""


def fmt_number(n: int, fmt: str) -> str:
    if fmt == "upperLetter":
        return _letter(n)
    if fmt == "lowerLetter":
        return _letter(n).lower()
    if fmt == "upperRoman":
        return _roman(n)
    if fmt == "lowerRoman":
        return _roman(n).lower()
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt in ("bullet", "none"):
        return ""
    return str(n)  # decimal and anything Word would show with digits in this simple model


# ---- numbering.xml and styles.xml ---------------------------------------------------------------------------

def _val(e, tag: str, default=None):
    c = e.find(q(tag)) if e is not None else None
    return c.get(q("w:val")) if c is not None else default


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Numbering:
    """The list definitions and the style links of one document."""

    def __init__(self, numbering_el, styles_el):
        self.levels: dict[str, dict[int, tuple[int, str, str]]] = {}  # abstractNumId -> ilvl -> (start, fmt, text)
        self.style_lvl: dict[str, int] = {}  # style id linked from an abstractNum level -> that ilvl
        for a in numbering_el.findall(q("w:abstractNum")) if numbering_el is not None else []:
            aid = a.get(q("w:abstractNumId"))
            lv = {}
            for lvl in a.findall(q("w:lvl")):
                i = _int(lvl.get(q("w:ilvl")))
                lv[i] = (_int(_val(lvl, "w:start", "1"), 1), _val(lvl, "w:numFmt", "decimal"), _val(lvl, "w:lvlText", ""))
                ps = _val(lvl, "w:pStyle")
                if ps:
                    self.style_lvl[ps] = i
            self.levels[aid] = lv
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}  # numId -> (abstractNumId, ilvl -> startOverride)
        for n in numbering_el.findall(q("w:num")) if numbering_el is not None else []:
            over = {}
            for o in n.findall(q("w:lvlOverride")):
                so = _val(o, "w:startOverride")
                if so is not None:
                    over[_int(o.get(q("w:ilvl")))] = _int(so, 1)
            self.nums[n.get(q("w:numId"))] = (_val(n, "w:abstractNumId"), over)
        self.styles: dict[str, tuple[str | None, str | None, str | None]] = {}  # id -> (numId, ilvl, basedOn)
        for s in styles_el.findall(q("w:style")) if styles_el is not None else []:
            np = s.find(q("w:pPr") + "/" + q("w:numPr"))
            self.styles[s.get(q("w:styleId"))] = (_val(np, "w:numId"), _val(np, "w:ilvl"), _val(s, "w:basedOn"))

    def style_num(self, sid: str | None) -> tuple[str | None, int | None]:
        """(numId, ilvl) a paragraph style gives, following basedOn."""
        seen = set()
        num, ilvl = None, None
        cur = sid
        while cur and cur not in seen and cur in self.styles:
            seen.add(cur)
            n, i, based = self.styles[cur]
            if num is None and n is not None:
                num = n
            if ilvl is None and i is not None:
                ilvl = _int(i)
            cur = based
        if num is not None and ilvl is None:
            ilvl = self.style_lvl.get(sid, 0)
        return num, ilvl

    def paragraph_numbers(self, body) -> dict:
        """paragraph element -> the number Word shows before it, for every numbered paragraph of ``body``."""
        counters: dict[str, dict[int, int]] = {}  # abstractNumId -> ilvl -> current value
        started: set = set()
        out = {}
        for p in body.iter(q("w:p")):
            ppr = p.find(q("w:pPr"))
            sid = _val(ppr, "w:pStyle")
            num, ilvl = self.style_num(sid)
            np = ppr.find(q("w:numPr")) if ppr is not None else None
            if np is not None:
                dn, di = _val(np, "w:numId"), _val(np, "w:ilvl")
                if dn is not None:
                    num = dn
                if di is not None:
                    ilvl = _int(di)
            if num is None or num == "0" or num not in self.nums:
                continue
            ilvl = ilvl or 0
            aid, over = self.nums[num]
            levels = self.levels.get(aid)
            if not levels or ilvl not in levels:
                continue
            c = counters.setdefault(aid, {})
            if num not in started:  # a w:num with startOverride restarts its levels where it is first used
                started.add(num)
                for i, s in over.items():
                    c[i] = s - 1
            start = levels[ilvl][0]
            c[ilvl] = c.get(ilvl, start - 1) + 1
            for i in [k for k in c if k > ilvl]:
                del c[i]  # deeper levels restart after this one
            text = levels[ilvl][2]

            def repl(m, c=c, levels=levels):
                k = int(m.group(1)) - 1
                v = c.get(k, levels.get(k, (1,))[0])
                return fmt_number(v, levels.get(k, (1, "decimal", ""))[1])
            out[p] = re.sub(r"%([1-9])", repl, text)
        return out


# ---- bookmarks and fields -----------------------------------------------------------------------------------

def bookmarks(roots) -> tuple[dict[str, str], dict[str, object]]:
    """(name -> visible text inside the bookmark, name -> paragraph that holds its start)."""
    texts: dict[str, list[str]] = {}
    paras: dict[str, object] = {}
    for root in roots:
        open_ids: dict[str, str] = {}  # id -> name
        in_code = 0  # between a field's begin and separate: instruction text, not shown
        for e in root.iter():
            tag = e.tag
            if tag == q("w:bookmarkStart"):
                name = e.get(q("w:name"))
                open_ids[e.get(q("w:id"))] = name
                texts.setdefault(name, [])
                p = e.getparent()
                while p is not None and p.tag != q("w:p"):
                    p = p.getparent()
                if p is not None:
                    paras.setdefault(name, p)
            elif tag == q("w:bookmarkEnd"):
                open_ids.pop(e.get(q("w:id")), None)
            elif tag == q("w:fldChar"):
                kind = e.get(q("w:fldCharType"))
                if kind == "begin":
                    in_code += 1
                elif kind == "separate" and in_code:
                    in_code -= 1
            elif tag == q("w:t") and not in_code:
                for name in open_ids.values():
                    texts[name].append(e.text or "")
    return {k: "".join(v) for k, v in texts.items()}, paras


def fill_results(roots, body, numbering_el, styles_el) -> int:
    """Set the cached result of every REF field in ``roots`` to what Word would compute; return the number of
    PAGEREF fields. A list item's result is Word's number for it ("(a)"), even where LaTeX's reference reads
    "2a": the reference stays a field that renumbers with the list."""
    texts, paras = bookmarks(roots)
    numbers = Numbering(numbering_el, styles_el).paragraph_numbers(body)
    pagerefs = 0
    for root in roots:
        state, code, results = None, "", []
        for r in list(root.iter(q("w:r"))):
            kinds = [c.get(q("w:fldCharType")) for c in r.findall(q("w:fldChar"))]
            if "begin" in kinds:
                state, code, results = "code", "", []
                continue
            if state is None:
                continue
            if state == "code":
                it = r.find(q("w:instrText"))
                if it is not None:
                    code += it.text or ""
                if "separate" in kinds:
                    state = "result"
                continue
            if "end" in kinds:
                state = None
                pagerefs += code.lstrip().startswith("PAGEREF")
                _set(results, _compute(code, texts, paras, numbers))
                continue
            results.extend(r.findall(q("w:t")))
    return pagerefs


def _compute(code: str, texts: dict, paras: dict, numbers: dict) -> str | None:
    m = _REF.match(code)
    if not m:
        return None
    name, switches = m.group(1), m.group(2)
    if re.search(r"\\[nrw]\b", switches):
        p = paras.get(name)
        n = numbers.get(p) if p is not None else None
        return n.rstrip(".") if n else None  # \n: the paragraph number without its trailing period
    t = texts.get(name)
    return t if t else None


def _set(results: list, value: str | None) -> None:
    if value is None or not results:
        return
    results[0].text = value
    if value != value.strip():
        results[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for t in results[1:]:
        t.text = ""
