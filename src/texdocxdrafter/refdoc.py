"""Stage 7: reference.docx. Make sure every style named in the style map exists and is wired up.

Starting from python-docx's built-in template (Word's own style set), the module adds the custom
styles, links heading styles to a multilevel list, and creates the table styles. With a user template
only missing styles are created (and reported); existing definitions are left alone.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from .audit import Audit
from .writer.numbering import NumberingManager
from .writer.xmlutil import style_id

NS = nsdecls("w")

# name -> (type, basedOn, next, pPr xml, rPr xml). Sizes are half-points, spacing/indent twips.
PARAGRAPH = {
    "Author": ("Normal", None, '<w:jc w:val="center"/>', ""),
    "Date": ("Normal", None, '<w:jc w:val="center"/>', ""),
    "Abstract": ("Body Text", None, '<w:ind w:left="720" w:right="720"/>', ""),
    "Keywords": ("Abstract", None, "", ""),
    "First Paragraph": ("Body Text", "Body Text", "", ""),
    "Block Text": ("Normal", "Body Text", '<w:spacing w:before="120" w:after="120"/><w:ind w:left="720" w:right="720"/>', ""),
    "Source Code": ("Normal", None, '<w:spacing w:before="0" w:after="0"/>',
                    '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="18"/>'),
    "Caption Table": ("Caption", "Body Text", '<w:keepNext/><w:spacing w:before="240" w:after="120"/>', ""),
    "Caption Figure": ("Caption", "Body Text", '<w:spacing w:before="120" w:after="240"/>', ""),
    "Table Note": ("Normal", "Body Text", '<w:spacing w:before="40" w:after="120"/>', '<w:sz w:val="18"/>'),
    "Figure Note": ("Normal", "Body Text", '<w:spacing w:before="40" w:after="120"/>', '<w:sz w:val="18"/>'),
    "Table Text": ("Normal", None, '<w:spacing w:before="40" w:after="40"/>', '<w:sz w:val="20"/>'),
    "Table Text Center": ("Table Text", None, '<w:jc w:val="center"/>', ""),
    "Table Text Right": ("Table Text", None, '<w:jc w:val="right"/>', ""),
    "Table Header": ("Table Text", None, '<w:keepNext/>', "<w:b/>"),
    "Table Header Center": ("Table Header", None, '<w:jc w:val="center"/>', ""),
    "Table Header Right": ("Table Header", None, '<w:jc w:val="right"/>', ""),
    "Equation": ("Normal", None, '<w:spacing w:before="120" w:after="120"/><w:jc w:val="center"/>', ""),
    "Equation Number": ("Normal", None, '<w:spacing w:before="120" w:after="120"/><w:jc w:val="right"/>', ""),
    "Footnote Text": ("Normal", None, '<w:spacing w:after="0"/>', '<w:sz w:val="20"/>'),
    "Bibliography": ("Normal", "Bibliography", '<w:spacing w:after="120"/><w:ind w:left="720" w:hanging="720"/>', ""),
    "Center": ("Body Text", None, '<w:jc w:val="center"/>', ""),
    "Figure": ("Normal", "Caption Figure", '<w:keepNext/><w:spacing w:before="120" w:after="60"/><w:jc w:val="center"/>', ""),
}
CHARACTER = {
    "Citation": "",
    "Strong Emphasis": "<w:b/><w:i/>",
    "Drafter Todo": '<w:color w:val="C00000"/><w:highlight w:val="yellow"/>',
    "Math Variable": "<w:i/>",
    "Drafter Math": '<w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/><w:i/>',
    "Verbatim Char": '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/>',
    "Hyperlink": '<w:color w:val="0563C1"/><w:u w:val="single"/>',
    "Footnote Reference": '<w:vertAlign w:val="superscript"/>',
}


def _styles_el(doc):
    return doc.styles.element


def _find(doc, name: str):
    for s in doc.styles:
        if s.name == name:
            return s.element
    return None


def _make(doc, name: str, kind: str, based: str | None, nxt: str | None, ppr: str, rpr: str) -> None:
    sid = style_id(name)
    parts = [f'<w:style {NS} w:type="{kind}" w:customStyle="1" w:styleId="{sid}">', f'<w:name w:val="{name}"/>']
    if based:
        parts.append(f'<w:basedOn w:val="{style_id(based)}"/>')
    if nxt:
        parts.append(f'<w:next w:val="{style_id(nxt)}"/>')
    parts.append('<w:qFormat/>')
    if ppr:
        parts.append(f"<w:pPr>{ppr}</w:pPr>")
    if rpr:
        parts.append(f"<w:rPr>{rpr}</w:rPr>")
    parts.append("</w:style>")
    _styles_el(doc).append(parse_xml("".join(parts)))


def _set_num(style_el, num_id: int, ilvl: int | None) -> None:
    ppr = style_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = parse_xml(f"<w:pPr {NS}/>")
        style_el.append(ppr)
    old = ppr.find(qn("w:numPr"))
    if old is not None:
        ppr.remove(old)
    inner = (f'<w:ilvl w:val="{ilvl}"/>' if ilvl is not None else "") + f'<w:numId w:val="{num_id}"/>'
    ppr.insert(0, parse_xml(f"<w:numPr {NS}>{inner}</w:numPr>"))


def _strip_num(style_el) -> None:
    ppr = style_el.find(qn("w:pPr"))
    if ppr is not None:
        old = ppr.find(qn("w:numPr"))
        if old is not None:
            ppr.remove(old)


def build_reference(sm: dict, template: str | Path | None = None, audit: Audit | None = None):
    """Return (Document with empty body, NumberingManager, info dict)."""
    user = template is not None
    doc = Document(str(template)) if user else Document()
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)

    paragraph_names = {v for k, v in sm["paragraph"].items() if k != "heading"} | {f"{sm['paragraph']['heading']} {i}" for i in range(1, 10)}
    char_names = set(sm["character"].values())
    missing: list[str] = []
    existing = {s.name for s in doc.styles}

    if not user:
        _customise_defaults(doc)
    for name, (based, nxt, ppr, rpr) in PARAGRAPH.items():
        if name in existing:
            continue
        if user and name not in paragraph_names:
            continue
        _make(doc, name, "paragraph", based if based in existing or based in PARAGRAPH else "Normal", nxt, ppr, rpr)
        existing.add(name)
        if user:
            missing.append(name)
    for name, rpr in CHARACTER.items():
        if name in existing:
            continue
        _make(doc, name, "character", None, None, "", rpr)
        existing.add(name)
        if user:
            missing.append(name)
    # any style named in the map but unknown to both the template and our table
    for name in sorted((paragraph_names | char_names) - existing):
        kind = "character" if name in char_names else "paragraph"
        _make(doc, name, kind, None if kind == "character" else "Normal", None, "", "")
        existing.add(name)
        missing.append(name)
    for name in (sm["options"]["table_style"], sm["options"]["equation_table_style"]):
        if name not in existing:
            _make_table_style(doc, name, rules=(name == sm["options"]["table_style"]))
            existing.add(name)
    if user and missing and audit is not None:
        audit.add("style", f"styles missing from the reference document were created: {', '.join(sorted(set(missing)))}",
                  severity="warn")

    nm = _numbering(doc, user)
    if not user:
        _link_styles(doc, nm)
    return doc, nm, {"user_template": user, "missing": missing}


def _numbering(doc, user: bool) -> NumberingManager:
    if not user:
        return NumberingManager()
    root = doc.part.numbering_part.element
    abs_ids = [int(a.get(qn("w:abstractNumId"))) for a in root.findall(qn("w:abstractNum"))]
    num_ids = [int(a.get(qn("w:numId"))) for a in root.findall(qn("w:num"))]
    nm = NumberingManager(first_abs=max(abs_ids, default=-1) + 1, first_num=max(num_ids, default=0) + 1,
                          headings=False)
    nm.preserve = True
    return nm


def _link_styles(doc, nm: NumberingManager) -> None:
    for i in range(1, 10):
        s = _find(doc, f"Heading {i}")
        if s is not None:
            _set_num(s, nm.HEADING_NUM, i - 1)
    for name, ilvl in (("List Bullet", 0), ("List Bullet 2", 1), ("List Bullet 3", 2)):
        s = _find(doc, name)
        if s is not None:
            _set_num(s, nm.BULLET_NUM, ilvl)
    for name in ("List Number", "List Number 2", "List Number 3"):
        s = _find(doc, name)
        if s is not None:
            _strip_num(s)


def _customise_defaults(doc) -> None:
    """Small adjustments to Word's built-in styles so the output reads well out of the box."""
    s = _find(doc, "Body Text")
    if s is not None:
        ppr = s.find(qn("w:pPr"))
        if ppr is None:
            ppr = parse_xml(f"<w:pPr {NS}/>")
            s.append(ppr)
        for sp in ppr.findall(qn("w:spacing")):
            ppr.remove(sp)
        ppr.append(parse_xml(f'<w:spacing {NS} w:before="0" w:after="120" w:line="276" w:lineRule="auto"/>'))
    # headings: black, no colour theme, so a template can change them once
    for i in range(1, 10):
        s = _find(doc, f"Heading {i}")
        if s is None:
            continue
        rpr = s.find(qn("w:rPr"))
        if rpr is not None:
            for c in rpr.findall(qn("w:color")):
                rpr.remove(c)


def _make_table_style(doc, name: str, rules: bool) -> None:
    sid = style_id(name)
    if rules:
        borders = ('<w:tblBorders><w:top w:val="single" w:sz="8" w:space="0" w:color="000000"/>'
                   '<w:bottom w:val="single" w:sz="8" w:space="0" w:color="000000"/></w:tblBorders>')
        first = ('<w:tblStylePr w:type="firstRow"><w:tcPr><w:tcBorders>'
                 '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/></w:tcBorders></w:tcPr>'
                 '<w:rPr><w:b/></w:rPr></w:tblStylePr>')
    else:
        borders, first = "", ""
    xml = (f'<w:style {NS} w:type="table" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
           f'<w:basedOn w:val="TableNormal"/><w:uiPriority w:val="59"/><w:qFormat/>'
           f'<w:tblPr><w:tblInd w:w="0" w:type="dxa"/>{borders}'
           f'<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
           f'<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr>{first}</w:style>')
    _styles_el(doc).append(parse_xml(xml))
