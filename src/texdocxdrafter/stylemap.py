"""Style map: schema, defaults, validation."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULT_PARAGRAPH = {
    "title": "Title",
    "author": "Author",
    "date": "Date",
    "abstract": "Abstract",
    "keywords": "Keywords",
    "body": "Body Text",
    "first_paragraph": "First Paragraph",
    "bullet_list": "List Bullet",
    "number_list": "List Number",
    "description_list": "List Paragraph",
    "quote": "Block Text",
    "code": "Source Code",
    "caption_table": "Caption Table",
    "caption_figure": "Caption Figure",
    "table_note": "Table Note",
    "figure_note": "Figure Note",
    "table_text": "Table Text",
    "table_header": "Table Header",
    "equation": "Equation",
    "footnote": "Footnote Text",
    "bibliography_heading": "Heading 1",
    "bibliography": "Bibliography",
    "heading": "Heading",  # Heading 1 ... Heading 9
    "center": "Center",
    "figure": "Figure",
    "table_text_center": "Table Text Center",
    "table_text_right": "Table Text Right",
    "table_header_center": "Table Header Center",
    "table_header_right": "Table Header Right",
    "equation_number": "Equation Number",
    "bullet_list_2": "List Bullet 2",
    "bullet_list_3": "List Bullet 3",
    "number_list_2": "List Number 2",
    "number_list_3": "List Number 3",
    "page_break": "Body Text",
}
DEFAULT_CHARACTER = {
    "emph": "Emphasis",
    "strong": "Strong",
    "code": "Verbatim Char",
    "link": "Hyperlink",
    "citation": "Citation",
    "todo": "Drafter Todo",
    "math": "Drafter Math",
    "math_var": "Math Variable",
    "strong_emph": "Strong Emphasis",
    "footnote_ref": "Footnote Reference",
}
DEFAULT_OPTIONS = {
    "heading_offset": "auto",  # auto: article -> \section = Heading 1
    "caption_position": {"table": "above", "figure": "below"},
    "paragraph_as_heading": False,
    "strip_bold_in_headings": True,
    "reference_doc": None,
    "table_style": "Drafter Table",
    "equation_table_style": "Drafter Equation Layout",
    "columns": "auto",  # auto: two columns when the LaTeX layout has them; or 1 / 2
    "revision_macros": "auto",  # review markup macros as tracked changes: auto | {name: ins|del} | false
    "revision_author": "LaTeX",
    # environments that mark a block-level edit as a tracked change: {name: ins|del}
    "revision_environments": {"addblock": "ins", "delblock": "del"},
    "inline_math_as_text": True,  # simple $...$ (Greek, relations, short scripts) as characters, not equations
}
DEFAULT_HEADINGS = {"chapter": 1, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 5}

DEFAULTS = {
    "paragraph": DEFAULT_PARAGRAPH,
    "character": DEFAULT_CHARACTER,
    "options": DEFAULT_OPTIONS,
}


class StyleMapError(ValueError):
    pass


def default_map() -> dict:
    return copy.deepcopy(DEFAULTS)


def validate(m: dict) -> dict:
    out = default_map()
    for sect in ("paragraph", "character", "options"):
        user = m.get(sect) or {}
        if not isinstance(user, dict):
            raise StyleMapError(f"style map section '{sect}' must be a mapping")
        unknown = set(user) - set(out[sect])
        if unknown:
            raise StyleMapError(f"unknown keys in '{sect}': {sorted(unknown)}")
        if sect != "options":
            for k, v in user.items():
                if not isinstance(v, str) or not v.strip():
                    raise StyleMapError(f"{sect}.{k} must be a non-empty style name")
        out[sect].update(user)
    unknown_top = set(m) - set(DEFAULTS) - {"constructs"}
    if unknown_top:
        raise StyleMapError(f"unknown top-level keys: {sorted(unknown_top)}")
    pos = out["options"]["caption_position"]
    if set(pos) != {"table", "figure"} or not all(v in ("above", "below") for v in pos.values()):
        raise StyleMapError("options.caption_position needs table/figure each above|below")
    off = out["options"]["heading_offset"]
    if off != "auto" and not isinstance(off, int):
        raise StyleMapError("options.heading_offset must be 'auto' or an integer")
    out["constructs"] = m.get("constructs") or {}
    return out


def load(path: str | Path | None) -> dict:
    if path is None or not Path(path).exists():
        return validate({})
    return validate(yaml.safe_load(Path(path).read_text()) or {})


def dump_skeleton(constructs: dict, path: str | Path) -> None:
    """Write a stylemap skeleton. Never overwrites an existing file."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    data = default_map()
    data["constructs"] = constructs
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
