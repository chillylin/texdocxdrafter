"""Small WordprocessingML element builders (lxml). python-docx has no API for fields, bookmarks, OMML."""
from __future__ import annotations

import re

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W, "m": M, "r": R}


def q(tag: str) -> str:
    pre, name = tag.split(":")
    return "{%s}%s" % (NSMAP[pre], name)


# characters XML 1.0 cannot carry: control characters other than tab/newline/return, lone surrogates, FFFE/FFFF
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_safe(text: str) -> str:
    """``text`` without the characters lxml refuses (they come from pdftotext, .bbl files or the source)."""
    return _XML_BAD.sub("", text)


# the only targets a hyperlink may have, in the writer's own links and in those kept from a reference document
SAFE_SCHEMES = ("http://", "https://", "mailto:")


def safe_url(url: str | None) -> bool:
    """True for web and mail targets; file:, javascript:, UNC paths and relative paths are not safe."""
    return (url or "").strip().lower().startswith(SAFE_SCHEMES)


def el(tag: str, attrs: dict | None = None, *children, text: str | None = None):
    pre = tag.split(":")[0]
    e = etree.Element(q(tag), nsmap={pre: NSMAP[pre]})
    for k, v in (attrs or {}).items():
        e.set(q(k) if ":" in k else k, xml_safe(str(v)))
    for c in children:
        if c is not None:
            e.append(c)
    if text is not None:
        e.text = xml_safe(text)
    return e


def rpr(rstyle: str | None = None, props: dict | None = None):
    """Run properties. ``props`` keys: i b smallCaps u strike vertAlign color."""
    kids = []
    if rstyle:
        kids.append(el("w:rStyle", {"w:val": rstyle}))
    for k, v in (props or {}).items():
        if k in ("i", "b", "smallCaps"):
            kids.append(el("w:" + k))
        elif k == "strike":
            kids.append(el("w:strike"))
        elif k == "color":
            kids.append(el("w:color", {"w:val": v}))
        elif k == "u":
            kids.append(el("w:u", {"w:val": "single"}))
        elif k == "vertAlign":
            kids.append(el("w:vertAlign", {"w:val": v}))
    if not kids:
        return None
    # schema order: rStyle, b, i, smallCaps, strike, color, u, vertAlign
    order = {"rStyle": 0, "b": 1, "i": 2, "smallCaps": 3, "strike": 4, "color": 5, "u": 6, "vertAlign": 7}
    kids.sort(key=lambda e: order.get(etree.QName(e).localname, 9))
    return el("w:rPr", None, *kids)


def run(text: str, rstyle: str | None = None, props: dict | None = None):
    r = el("w:r", None, rpr(rstyle, props))
    t = el("w:t", None, text=text)
    t.set("{%s}space" % XML, "preserve")
    r.append(t)
    return r


def br(kind: str | None = None):
    r = el("w:r")
    b = el("w:br")
    if kind:
        b.set(q("w:type"), kind)
    r.append(b)
    return r


def fld_char(kind: str, rstyle: str | None = None, props: dict | None = None):
    r = el("w:r", None, rpr(rstyle, props))
    r.append(el("w:fldChar", {"w:fldCharType": kind}))
    return r


def instr(text: str, rstyle: str | None = None, props: dict | None = None):
    r = el("w:r", None, rpr(rstyle, props))
    t = el("w:instrText", None, text=f" {text} ")
    t.set("{%s}space" % XML, "preserve")
    r.append(t)
    return r


def field(code: str, result: str, rstyle: str | None = None, props: dict | None = None) -> list:
    """A complex field with a cached result: begin, instrText, separate, result, end."""
    return [fld_char("begin", rstyle, props), instr(code, rstyle, props), fld_char("separate", rstyle, props),
            run(result, rstyle, props), fld_char("end", rstyle, props)]


def para(style: str | None = None, children=(), ppr_extra=()):
    kids = []
    if style:
        kids.append(el("w:pStyle", {"w:val": style}))
    kids.extend(ppr_extra)
    p = el("w:p")
    if kids:
        p.append(el("w:pPr", None, *kids))
    for c in children:
        if c is not None:
            p.append(c)
    return p


def style_id(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum())
