"""Stage 9: structural checks on a finished docx, and the style usage table behind ``texdocxdrafter check``."""
from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from .writer.xmlutil import safe_url

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"

# The active-content policy, checked over the whole package after the writer is done (refdoc scrubs the
# template; this establishes that nothing prohibited is left anywhere, whatever its origin).
FORBIDDEN_RELS = ("/vbaProject", "/vbaData", "/oleObject", "/package", "/control", "/aFChunk", "/subDocument",
                  "/attachedTemplate", "/mailMergeSource", "/mailMergeHeaderSource", "/recipientData", "/frame",
                  "/customXml", "/glossaryDocument")
FORBIDDEN_CONTENT_TYPES = ("vbaProject", "vbaData", "oleObject", "macroEnabled", "activeX", "ms-office.vbaData",
                           "application/vnd.openxmlformats-officedocument.oleObject", "octet-stream")
FORBIDDEN_ELEMENTS = ("altChunk", "subDoc", "contentPart", "object", "control", "attachedTemplate", "mailMerge",
                      "documentProtection", "writeProtection", "frameset", "background", "docVars")


def w(t: str) -> str:
    return "{%s}%s" % (W, t)


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    paragraph_styles: Counter = field(default_factory=Counter)
    character_styles: Counter = field(default_factory=Counter)
    direct_format_runs: int = 0
    fields: Counter = field(default_factory=Counter)
    todo_runs: int = 0
    words: int = 0
    equations: int = 0
    tables: int = 0
    footnotes: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def check_policy(z: zipfile.ZipFile, trees: dict, res: CheckResult) -> None:
    """Every relationship, content type, element and field of the package against the active-content policy."""
    from .refdoc import _safe_field  # here, not at the top: refdoc and the writer import each other
    for name, tree in trees.items():
        if name.endswith(".rels"):
            for rel in tree.iter("{%s}Relationship" % PKG_R):
                rtype, target = rel.get("Type") or "", rel.get("Target") or ""
                if rtype.endswith(FORBIDDEN_RELS):
                    res.errors.append(f"policy: {name}: prohibited relationship {rtype.rsplit('/', 1)[-1]} -> {target}")
                elif rel.get("TargetMode") == "External" and not (rtype.endswith("/hyperlink") and safe_url(target)):
                    res.errors.append(f"policy: {name}: external target {target[:80]} ({rtype.rsplit('/', 1)[-1]})")
        elif name == "[Content_Types].xml":
            for e in tree.iter():
                ct = e.get("ContentType") or ""
                if any(f in ct for f in FORBIDDEN_CONTENT_TYPES):
                    res.errors.append(f"policy: content type {ct} ({e.get('PartName') or e.get('Extension')})")
        elif name.startswith("word/") and name.endswith(".xml"):
            for e in tree.iter():
                if e.tag.startswith("{%s}" % W) and etree.QName(e).localname in FORBIDDEN_ELEMENTS:
                    res.errors.append(f"policy: {name}: element w:{etree.QName(e).localname}")
                    break
            for fld in tree.iter(w("fldSimple")):
                if not _safe_field(fld.get(w("instr")) or ""):
                    res.errors.append(f"policy: {name}: field {(fld.get(w('instr')) or '').strip()[:60]}")
            code: list[str] = []
            for r in tree.iter(w("r")):
                kinds = [c.get(w("fldCharType")) for c in r.findall(w("fldChar"))]
                code.extend(t.text or "" for t in r.findall(w("instrText")))
                if "separate" in kinds or "end" in kinds:
                    instr = "".join(code).strip()
                    code = []
                    if instr and not _safe_field(instr):
                        res.errors.append(f"policy: {name}: field {instr[:60]}")
    for info in z.infolist():
        if info.filename.lower().endswith((".bin", ".exe", ".dll", ".vbs", ".js", ".hta")):
            res.errors.append(f"policy: embedded part {info.filename}")


def check_docx(path) -> CheckResult:
    res = CheckResult()
    z = zipfile.ZipFile(path)
    trees: dict[str, etree._ElementTree] = {}
    for name in z.namelist():
        if name.endswith((".xml", ".rels")):
            try:
                trees[name] = etree.fromstring(z.read(name))
            except etree.XMLSyntaxError as exc:
                res.errors.append(f"{name}: not well-formed XML ({exc})")
    doc = trees.get("word/document.xml")
    styles = trees.get("word/styles.xml")
    if doc is None or styles is None:
        res.errors.append("document.xml or styles.xml missing or unreadable")
        return res
    check_policy(z, trees, res)
    style_ids = {s.get(w("styleId")): (s.get(w("type")), (s.find(w("name")).get(w("val")) if s.find(w("name")) is not None else ""))
                 for s in styles.findall(w("style"))}
    # relationships
    rels = trees.get("word/_rels/document.xml.rels")
    rel_ids = {r.get("Id") for r in rels} if rels is not None else set()
    for el in doc.iter():
        for attr in ("id", "embed", "link"):
            v = el.get("{%s}%s" % (R, attr))
            if v and v not in rel_ids:
                res.errors.append(f"unresolved relationship {v}")
    for name, tree in trees.items():
        if name.startswith("word/") and name.endswith(".xml") and name != "word/styles.xml":
            for el in tree.iter(w("pStyle"), w("rStyle"), w("tblStyle")):
                sid = el.get(w("val"))
                if sid not in style_ids:
                    res.errors.append(f"{name}: style '{sid}' is used but not defined")
    parts = [doc] + [t for n, t in trees.items() if n == "word/footnotes.xml"]
    bookmarks = {b.get(w("name")) for b in doc.iter(w("bookmarkStart"))}
    for root in parts:
        for p in root.iter(w("p")):
            ps = p.find(w("pPr") + "/" + w("pStyle"))
            sid = ps.get(w("val")) if ps is not None else "Normal"
            res.paragraph_styles[style_ids.get(sid, ("", sid))[1] or sid] += 1
        for r in root.iter(w("r")):
            rp = r.find(w("rPr"))
            if rp is None:
                continue
            rs = rp.find(w("rStyle"))
            if rs is not None:
                nm = style_ids.get(rs.get(w("val")), ("", rs.get(w("val"))))[1]
                res.character_styles[nm] += 1
                if nm == "Drafter Todo":
                    res.todo_runs += 1
            extra = [c for c in rp if c.tag != w("rStyle")]
            if extra and not (r.find(w("fldChar")) is not None or r.find(w("instrText")) is not None):
                res.direct_format_runs += 1
        for it in root.iter(w("instrText")):
            code = (it.text or "").strip()
            kind = code.split(" ")[0] if code else ""
            res.fields[kind] += 1
            m = re.match(r"(?:REF|PAGEREF)\s+(\S+)", code)
            if m and m.group(1) not in bookmarks:
                res.errors.append(f"field '{code}' points to missing bookmark {m.group(1)}")
        for t in root.iter(w("t")):
            res.words += len((t.text or "").split())
    res.equations = len(list(doc.iter("{%s}oMath" % M)))
    res.tables = len(list(doc.iter(w("tbl"))))
    if "word/footnotes.xml" in trees:
        res.footnotes = max(0, len(trees["word/footnotes.xml"].findall(w("footnote"))) - 2)
    # bookmark ids unique
    ids = [b.get(w("id")) for b in doc.iter(w("bookmarkStart"))]
    if len(ids) != len(set(ids)):
        res.errors.append("duplicate bookmark ids")
    names = [b.get(w("name")) for b in doc.iter(w("bookmarkStart"))]
    dup = [n for n, c in Counter(names).items() if c > 1]
    if dup:
        res.errors.append(f"duplicate bookmark names: {dup[:5]}")
    # numbering references
    numbering = trees.get("word/numbering.xml")
    num_ids = {n.get(w("numId")) for n in numbering.findall(w("num"))} if numbering is not None else set()
    for root in parts:
        for n in root.iter(w("numId")):
            if n.get(w("val")) not in num_ids and n.get(w("val")) != "0":
                res.errors.append(f"numId {n.get(w('val'))} not defined in numbering.xml")
    for root in parts:
        _check_order(root, res)
    return res


# Word refuses a file ("unreadable content") when these children are out of schema order.
ORDERS = {
    "tblPr": ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW",
              "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook",
              "tblCaption", "tblDescription"],
    "sectPr": ["headerReference", "footerReference", "footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc",
               "pgBorders", "lnNumType", "pgNumType", "cols", "formProt", "vAlign", "noEndnote", "titlePg",
               "textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings", "sectPrChange"],
    "pPr": ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
            "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
            "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
            "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
            "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"],
}


def _check_order(root, res: CheckResult) -> None:
    seen = set()
    for tag, order in ORDERS.items():
        for e in root.iter(w(tag)):
            ranks = [order.index(etree.QName(c).localname) for c in e if etree.QName(c).localname in order]
            if ranks != sorted(ranks):
                kids = ",".join(etree.QName(c).localname for c in e)
                if (tag, kids) not in seen:
                    seen.add((tag, kids))
                    res.errors.append(f"{tag} children out of schema order: {kids}")
    for d in root.iter(w("del")):
        if d.getparent().tag not in (w("rPr"), w("trPr")) and next(d.iter(w("t"), w("instrText")), None) is not None:
            res.errors.append("deleted text must use w:delText/w:delInstrText")
            break


def render_check(res: CheckResult, path) -> str:
    lines = [f"{path}", ""]
    lines.append("Paragraph styles:")
    for k, v in sorted(res.paragraph_styles.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {v:5d}  {k}")
    lines.append("Character styles:")
    for k, v in sorted(res.character_styles.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {v:5d}  {k}")
    lines.append(f"Direct-formatted runs: {res.direct_format_runs}")
    lines.append("Fields: " + (", ".join(f"{k}={v}" for k, v in sorted(res.fields.items())) or "none"))
    lines.append(f"Tables: {res.tables}  Equations: {res.equations}  Footnotes: {res.footnotes}  Words: {res.words}")
    lines.append(f"Drafter Todo runs: {res.todo_runs}")
    for w_ in res.warnings:
        lines.append(f"WARNING {w_}")
    for e in res.errors:
        lines.append(f"ERROR {e}")
    lines.append("VALID" if res.ok else "INVALID")
    return "\n".join(lines)
