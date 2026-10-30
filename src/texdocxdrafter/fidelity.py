"""Stage 9b: the conversion manifest.

What the semantic tree says the docx must contain, counted again independently in the finished docx XML:
the headings in order and with their text, tables, embedded pictures, equations, footnotes and bibliography
entries. A mismatch means content was lost, duplicated or reordered between the tree and the file. The
word-count check (pipeline.word_loss) covers the running text; this covers the structure around it.

Not covered, and said so in the README: the order of body paragraphs, table cell contents, citations (their
count is in the harvest ratio), metadata and formatting."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from .audit import Audit
from .parser.model import (Bibliography, Cite, Document, Footnote, Heading, InlineMath, Link, Ref, Styled, Table,
                           Text, walk_tree)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def w(t: str) -> str:
    return "{%s}%s" % (W, t)


@dataclass
class Manifest:
    """What the docx must contain. ``headings`` holds the plain text of every heading in document order, or
    None for a heading whose text is not plain (math, citations, references, footnotes in it)."""

    headings: list[str | None] = field(default_factory=list)
    tables: int = 0
    pictures: int = 0
    equations: int = 0
    footnotes: int = 0
    bib_entries: int = 0


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _plain(nodes) -> str | None:
    """Plain text of inline nodes, or None when a node has no fixed text (its rendering is the writer's)."""
    out: list[str] = []
    stack = list(reversed(nodes or []))
    while stack:
        n = stack.pop()
        if isinstance(n, Text):
            out.append(n.text)
        elif isinstance(n, (Styled, Link)):
            stack.extend(reversed(n.children))
        elif isinstance(n, (Cite, Ref, InlineMath, Footnote)):
            return None
        # labels, line breaks, raw inlines: no text of their own
    return _norm("".join(out))


def manifest(doc: Document, audit: Audit) -> Manifest:
    """The expected content: structure from the tree, pictures and equations from the writer's census (whether
    a graphic was found or a formula converted is the writer's decision; that it then reached the file is not)."""
    m = Manifest()
    seen: set[int] = set()  # a node can be reachable twice (an author's \thanks sits in doc.authors and in the title block)

    def visit(n):
        if id(n) in seen:
            return
        seen.add(id(n))
        if isinstance(n, Heading):
            m.headings.append(_plain(n.children))
        elif isinstance(n, Bibliography):  # the writer gives every bibliography its heading, even an empty one
            title = n.title
            if title is None:
                t = n.options.get("title")
                title = [Text(text=re.sub(r"[{}]", "", t))] if isinstance(t, str) and t else [Text(text="References")]
            m.headings.append(_plain(title))
            m.bib_entries += len(n.entries)
        elif isinstance(n, Table):
            m.tables += 1
        elif isinstance(n, Footnote):
            m.footnotes += 1

    walk_tree(doc, visit)
    m.footnotes += audit.census.get("citations in footnotes", 0)  # footnotes the writer makes for \\footcite and co.
    m.pictures = audit.census.get("figures embedded", 0)
    m.equations = audit.census.get("display math (OMML)", 0) + audit.census.get("inline math (OMML)", 0)
    return m


def _style_names(z: zipfile.ZipFile) -> dict[str, str]:
    root = etree.fromstring(z.read("word/styles.xml"))
    out = {}
    for s in root.findall(w("style")):
        nm = s.find(w("name"))
        out[s.get(w("styleId"))] = nm.get(w("val")) if nm is not None else s.get(w("styleId"))
    return out


def found(path, sm: dict) -> Manifest:
    """The same counts taken from the finished file."""
    z = zipfile.ZipFile(path)
    names = _style_names(z)
    doc = etree.fromstring(z.read("word/document.xml"))
    m = Manifest()
    heading = sm["paragraph"]["heading"].lower() + " "
    bib_heading = sm["paragraph"]["bibliography_heading"].lower()
    bib = sm["paragraph"]["bibliography"].lower()
    for p in doc.iter(w("p")):
        ps = p.find(w("pPr") + "/" + w("pStyle"))
        name = names.get(ps.get(w("val")), "").lower() if ps is not None else ""
        if name.startswith(heading) or name == bib_heading:
            m.headings.append(_norm("".join(t.text or "" for t in p.iter(w("t")))))
        elif name == bib:
            m.bib_entries += 1
    eq_style = sm["options"]["equation_table_style"]
    for t in doc.iter(w("tbl")):
        st = t.find(w("tblPr") + "/" + w("tblStyle"))
        if st is None or names.get(st.get(w("val")), st.get(w("val"))) != eq_style:
            m.tables += 1
    m.pictures = len(list(doc.iter(w("drawing"))))
    m.equations = len(list(doc.iter("{%s}oMath" % M)))
    if "word/footnotes.xml" in z.namelist():
        fn = etree.fromstring(z.read("word/footnotes.xml"))
        m.footnotes = sum(1 for f in fn.findall(w("footnote")) if f.get(w("type")) not in ("separator", "continuationSeparator"))
    return m


def compare(expected: Manifest, got: Manifest) -> list[str]:
    """Human-readable mismatches, empty when the file holds what the tree asked for."""
    out: list[str] = []
    for what in ("tables", "pictures", "equations", "footnotes", "bib_entries"):
        e, g = getattr(expected, what), getattr(got, what)
        if e != g:
            out.append(f"{what.replace('_', ' ')}: {e} expected, {g} in the docx")
    if len(expected.headings) != len(got.headings):
        out.append(f"headings: {len(expected.headings)} expected, {len(got.headings)} in the docx")
    else:
        for i, (e, g) in enumerate(zip(expected.headings, got.headings), 1):
            if e is not None and e != g:
                out.append(f"heading {i}: expected '{e[:60]}', found '{g[:60]}'")
                break
    return out


def check(doc: Document, path, sm: dict, audit: Audit) -> list[str]:
    """Compare and record: the mismatches go to the report under "Content check" and are returned."""
    problems = compare(manifest(doc, audit), found(path, sm))
    for p in problems:
        audit.add("fidelity", p)
    return problems
