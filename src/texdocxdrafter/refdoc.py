"""Stage 7: reference.docx. Make sure every style named in the style map exists and is wired up.

Starting from python-docx's built-in template (Word's own style set), the module adds the custom
styles, links heading styles to a multilevel list, and creates the table styles. With a user template
only missing styles are created (and reported); existing definitions are left alone.
"""
from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from .audit import Audit
from .writer.numbering import NumberingManager
from .writer.xmlutil import style_id

NS = nsdecls("w")


def _attr(value: str) -> str:
    """A style name (from the style map or a template) as an XML attribute value."""
    return escape(value, {'"': "&quot;"})


class TemplateError(ValueError):
    """The reference document cannot be used as a template."""
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

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
    parts = [f'<w:style {NS} w:type="{kind}" w:customStyle="1" w:styleId="{sid}">', f'<w:name w:val="{_attr(name)}"/>']
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
    try:
        doc = Document(str(template)) if user else Document()
    except ValueError as exc:  # python-docx: not a plain Word document (a .docm/.dotm, or not a docx at all)
        raise TemplateError(f"the reference document cannot be used: {exc}; macro-enabled (.docm/.dotm) templates "
                            "are not accepted, save it as .docx or .dotx first") from exc
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    if user:
        _scrub_template(doc)

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


# parts of a user template that carry its content or its author's data rather than its styles
_DOC_RELS = ("/comments", "/commentsExtended", "/commentsIds", "/commentsExtensible", "/people",
             "/footnotes", "/endnotes", "/glossaryDocument", "/customXml", "/vbaProject", "/vbaData")
# in a header or footer: embedded objects and controls, and the parts they are bound to
_STORY_RELS = ("/oleObject", "/package", "/control", "/aFChunk", "/subDocument", "/vbaProject")
_PKG_RELS = ("/custom-properties", "/thumbnail")
_COMMENT_MARKS = ("w:commentRangeStart", "w:commentRangeEnd", "w:commentReference", "w:footnoteReference",
                  "w:endnoteReference")
_APP_XML = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            b'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            b'<Application>Microsoft Office Word</Application></Properties>')


def _scrub_template(doc) -> None:
    """Keep only what a reference document is for, its styles, numbering, page setup, headers and footers.

    Everything that belongs to the template's own text or its author goes: comments and the people list,
    footnotes and endnotes (the body that referenced them is gone; the writer makes its own footnotes part),
    the glossary, custom XML, custom and extended properties, the thumbnail of its first page, every core
    property, the attached-template path and document variables in settings."""
    part = doc.part
    if "macroEnabled" in (part.content_type or ""):  # python-docx refuses these already; belt and braces
        raise TemplateError("the reference document is macro-enabled (.docm/.dotm); save it as .docx or .dotx first")
    for rid, rel in list(part.rels.items()):
        if rel.reltype.endswith(_DOC_RELS):
            del part.rels[rid]
    pkg = part.package
    for rid, rel in list(pkg.rels.items()):
        if rel.reltype.endswith(_PKG_RELS):
            del pkg.rels[rid]
        elif rel.reltype.endswith("/extended-properties"):
            rel.target_part._blob = _APP_XML
    for other in pkg.iter_parts():
        el = getattr(other, "element", None)
        if el is None or not hasattr(el, "iter"):
            continue
        for mark in [e for tag in _COMMENT_MARKS for e in el.iter(qn(tag))]:
            mark.getparent().remove(mark)
    settings = doc.settings.element
    for tag in ("w:attachedTemplate", "w:docVars", "w:mailMerge"):
        for e in settings.findall(qn(tag)):
            settings.remove(e)
    spart = doc.part.part_related_by("http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings")
    for rid, rel in list(spart.rels.items()):
        if rel.reltype.endswith(("/attachedTemplate", "/mailMergeSource", "/mailMergeHeaderSource", "/recipientData")):
            del spart.rels[rid]
    core = doc.core_properties._element
    for child in list(core):
        core.remove(child)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(microsecond=0)
    doc.core_properties.created = now
    doc.core_properties.modified = now
    for rid, rel in list(part.rels.items()):
        if rel.reltype.endswith(("/header", "/footer")):
            _scrub_story(rel.target_part)


# Fields a header or footer may keep: they are computed from the document itself. Everything else goes,
# in particular INCLUDETEXT, INCLUDEPICTURE, LINK, DDE, DDEAUTO, IMPORT, RD, DATABASE and AUTOTEXT, which
# make Word fetch local or remote content when the fields are updated, and the writer asks for exactly
# that (updateFields) so that its own REF and SEQ fields show the right numbers.
_SAFE_FIELDS = frozenset("""PAGE NUMPAGES SECTIONPAGES SECTION DATE TIME CREATEDATE SAVEDATE PRINTDATE EDITTIME
TITLE AUTHOR SUBJECT KEYWORDS COMMENTS REVNUM NUMWORDS NUMCHARS STYLEREF SEQ REF PAGEREF NOTEREF
TOC INDEX XE TC SYMBOL HYPERLINK QUOTE IF COMPARE SET FORMULA = LISTNUM AUTONUM AUTONUMLGL AUTONUMOUT
ADVANCE EQ""".split())  # not MERGEFIELD: with a w:mailMerge data source Word would connect to it


def _field_name(instr: str) -> str:
    m = re.match(r"\s*([A-Za-z=]+)", instr or "")
    return m.group(1).upper() if m else ""


def _scrub_story(part) -> None:
    """Drop from a header or footer what could reach outside the document: fields other than the safe set,
    alternate-format chunks and sub-documents, and anything bound to an external resource except hyperlinks."""
    root = part.element
    for fld in [e for e in root.iter(qn("w:fldSimple")) if _field_name(e.get(qn("w:instr"))) not in _SAFE_FIELDS]:
        fld.getparent().remove(fld)
    open_fields: list[tuple[list, list]] = []  # (runs, instruction pieces) of the complex fields being read
    doomed: list = []
    for r in list(root.iter(qn("w:r"))):
        kinds = [c.get(qn("w:fldCharType")) for c in r.findall(qn("w:fldChar"))]
        if "begin" in kinds:
            open_fields.append(([], []))
        for runs, pieces in open_fields:
            runs.append(r)
            pieces.extend(t.text or "" for t in r.findall(qn("w:instrText")))
        if "end" in kinds and open_fields:
            runs, pieces = open_fields.pop()
            if _field_name("".join(pieces)) not in _SAFE_FIELDS:
                doomed.extend(runs)
    for runs, pieces in open_fields:  # unterminated field: nothing to trust
        doomed.extend(runs)
    for tag in ("w:altChunk", "w:subDoc", "w:contentPart", "w:object", "w:control"):
        doomed.extend(root.iter(qn(tag)))
    gone = {rid for rid, rel in part.rels.items()
            if (rel.is_external and not rel.reltype.endswith("/hyperlink")) or rel.reltype.endswith(_STORY_RELS)}
    if gone:
        for e in root.iter():
            if any(v in gone for k, v in e.attrib.items() if k.startswith("{%s}" % _R_NS)):
                run = next((a for a in e.iterancestors() if a.tag == qn("w:r")), None)
                doomed.append(run if run is not None else e)
        for rid in gone:
            del part.rels[rid]
    for e in doomed:
        parent = e.getparent()
        if parent is not None:
            parent.remove(e)


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
    xml = (f'<w:style {NS} w:type="table" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{_attr(name)}"/>'
           f'<w:basedOn w:val="TableNormal"/><w:uiPriority w:val="59"/><w:qFormat/>'
           f'<w:tblPr><w:tblInd w:w="0" w:type="dxa"/>{borders}'
           f'<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
           f'<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr>{first}</w:style>')
    _styles_el(doc).append(parse_xml(xml))
