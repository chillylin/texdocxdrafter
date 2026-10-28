"""numbering.xml: heading outline, bullets, decimal lists, enumitem formats, one w:num per list."""
from __future__ import annotations

from lxml import etree

from .xmlutil import el, q

BULLETS = ["•", "◦", "▪"]  # bullet, white bullet, small square
SYMBOL_FONT = {"•": "Symbol", "◦": "Courier New", "▪": "Wingdings"}


def _lvl(ilvl: int, fmt: str, text: str, left: int, hanging: int, pstyle: str | None = None,
         bold: bool = False, font: str | None = None, start: int = 1):
    kids = [el("w:start", {"w:val": start}), el("w:numFmt", {"w:val": fmt})]
    if pstyle:
        kids.append(el("w:pStyle", {"w:val": pstyle}))
    kids.append(el("w:lvlText", {"w:val": text}))
    kids.append(el("w:lvlJc", {"w:val": "left"}))
    kids.append(el("w:pPr", None, el("w:ind", {"w:left": left, "w:hanging": hanging})))
    rp = []
    if font:
        rp.append(el("w:rFonts", {"w:ascii": font, "w:hAnsi": font, "w:hint": "default"}))
    if bold:
        rp.append(el("w:b"))
    if rp:
        kids.append(el("w:rPr", None, *rp))
    return el("w:lvl", {"w:ilvl": ilvl}, *kids)


class NumberingManager:
    HEADING_NUM = 1

    def __init__(self, first_abs: int = 0, first_num: int = 1, headings: bool = True):
        self.abstracts: list = []
        self.nums: list = []
        self._next_abs = first_abs
        self._next_num = first_num
        self._cache: dict = {}
        self.preserve = False
        self.heading_abs = None
        if headings:
            self.heading_abs = self._add_abstract(self._heading_levels())
            self.add_num(self.heading_abs)  # numId 1
        self.bullet_abs = self._add_abstract(self._bullet_levels())
        self.BULLET_NUM = self.add_num(self.bullet_abs)
        self.decimal_abs = self._add_abstract(self._decimal_levels())

    # -- definitions -------------------------------------------------------------------------
    @staticmethod
    def _heading_levels():
        out = []
        for i in range(9):
            text = ".".join(f"%{k + 1}" for k in range(i + 1))
            out.append(_lvl(i, "decimal", text, 432 + 144 * i, 432 + 144 * i, pstyle=f"Heading{i + 1}"))
        return out

    @staticmethod
    def _bullet_levels():
        return [_lvl(i, "bullet", BULLETS[i % 3], 720 + 360 * i, 360, font=SYMBOL_FONT[BULLETS[i % 3]])
                for i in range(9)]

    @staticmethod
    def _decimal_levels():
        fmts = [("decimal", "%1."), ("lowerLetter", "(%2)"), ("lowerRoman", "%3."), ("upperLetter", "%4."),
                ("decimal", "%5."), ("lowerLetter", "%6."), ("lowerRoman", "%7."), ("upperLetter", "%8."),
                ("decimal", "%9.")]
        return [_lvl(i, f, t, 720 + 360 * i, 360) for i, (f, t) in enumerate(fmts)]

    def _add_abstract(self, levels) -> int:
        aid = self._next_abs
        self._next_abs += 1
        a = el("w:abstractNum", {"w:abstractNumId": aid}, el("w:multiLevelType", {"w:val": "multilevel"}), *levels)
        self.abstracts.append(a)
        return aid

    def add_num(self, abstract_id: int, restart: bool = False) -> int:
        nid = self._next_num
        self._next_num += 1
        kids = [el("w:abstractNumId", {"w:val": abstract_id})]
        if restart:
            for i in range(9):
                kids.append(el("w:lvlOverride", {"w:ilvl": i}, el("w:startOverride", {"w:val": 1})))
        self.nums.append(el("w:num", {"w:numId": nid}, *kids))
        return nid

    # -- public ------------------------------------------------------------------------------
    def new_decimal_list(self, spec: dict | None, level: int) -> int:
        """A fresh numbering instance for one enumerate; ``spec`` carries enumitem label info."""
        if spec is None:
            return self.add_num(self.decimal_abs, restart=True)
        key = (spec["fmt"], spec["pattern"], bool(spec.get("bold")), level)
        if key not in self._cache:
            levels = []
            for i in range(9):
                if i == level:
                    pat = spec["pattern"].replace("%1", f"%{i + 1}")
                    levels.append(_lvl(i, spec["fmt"], pat, 720 + 360 * i, 360, bold=bool(spec.get("bold"))))
                else:
                    levels.append(_lvl(i, "decimal", f"%{i + 1}.", 720 + 360 * i, 360))
            self._cache[key] = self._add_abstract(levels)
        return self.add_num(self._cache[key], restart=True)

    def bullet_num(self) -> int:
        return self.BULLET_NUM

    def apply(self, numbering_element) -> None:
        from .xmlutil import q as _q

        if not self.preserve:
            for child in list(numbering_element):
                numbering_element.remove(child)
        existing_abs = numbering_element.findall(_q("w:abstractNum"))
        at = numbering_element.index(existing_abs[-1]) + 1 if existing_abs else 0
        for k, a in enumerate(self.abstracts):
            numbering_element.insert(at + k, a)
        for n in self.nums:
            numbering_element.append(n)
