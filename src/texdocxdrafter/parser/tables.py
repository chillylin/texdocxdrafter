"""The tabular family: rows, cells, multicolumn, multirow, rules, column specs."""
from __future__ import annotations

import re

from ..expand import read_group
from ..lexer import (
    LatexCharsNode, LatexEnvironmentNode, LatexGroupNode, LatexMacroNode, LatexSpecialsNode, arg_nodes, arg_text,
    macro_args, plain_text,
)
from .model import Cell, Para, Row, Styled, Table, Text

RULE_MACROS = {"hline", "toprule", "midrule", "bottomrule", "cline", "cmidrule", "addlinespace", "hdashline",
               "specialrule", "Xhline"}
LONGTABLE_MARKERS = {"endhead", "endfirsthead", "endfoot", "endlastfoot"}
BODY_RULES = {"hline", "midrule", "cline", "cmidrule", "specialrule", "Xhline", "hdashline"}


MAX_COLUMNS = 200  # more than Word can lay out; also bounds *{n}{...}


def parse_colspec(spec: str) -> list[tuple[str, float]]:
    """Column spec → list of (alignment l/c/r, relative width or 0)."""
    cols: list[tuple[str, float]] = []
    i = 0
    n = len(spec)
    while i < n:
        c = spec[i]
        if c in "lcr":
            cols.append((c, 0.0))
        elif c in "LCR":
            cols.append((c.lower(), 0.0))
        elif c in "pmb" and i + 1 < n and spec[i + 1] == "{":
            inner, i = read_group(spec, i + 1)
            cols.append(("l", _width_ratio(inner)))
            continue
        elif c in "XY":
            cols.append(("l", -1.0))  # flexible column: takes the remaining width
        elif c in "@!><" and i + 1 < n and spec[i + 1] == "{":
            _, i = read_group(spec, i + 1)
            continue
        elif c == "*" and i + 1 < n and spec[i + 1] == "{":
            cnt, j = read_group(spec, i + 1)
            if j < n and spec[j] == "{":
                inner, j = read_group(spec, j)
                try:
                    cols.extend(parse_colspec(inner) * min(int(cnt.strip()), MAX_COLUMNS))
                except ValueError:
                    pass
                del cols[MAX_COLUMNS:]  # *{999999999}{l} is not a table
                i = j
                continue
        elif c.isalpha():
            cols.append(("l", 0.0))  # author-defined column type
        i += 1
    return cols


def _width_ratio(w: str) -> float:
    m = re.match(r"\s*([0-9.]+)\s*(\\textwidth|\\linewidth|\\columnwidth|cm|mm|in|pt|em)?", w)
    if not m:
        return 1.0
    v = float(m.group(1)) if m.group(1) not in (".", "") else 1.0
    unit = m.group(2) or ""
    if unit.startswith("\\"):
        return v * 16.0  # centimetres on a typical text block
    return {"cm": v, "mm": v / 10, "in": v * 2.54, "pt": v * 0.0353, "em": v * 0.42}.get(unit, v)


class TableMixin:
    def standalone_table(self, env) -> list:
        t = self.tabular(env)
        t.floating = False
        return [t]

    def tabular(self, env) -> Table:
        name = env.environmentname
        args = macro_args(env)
        spec_text = arg_text(args[-1]) if args else ""
        spec = parse_colspec(spec_text)
        loc = self.loc(env)
        self.audit.count(f"environment {name}")
        table = Table(colspec=[a for a, _ in spec], colweights=[w for _, w in spec], loc=loc)

        rows_nodes: list[list[list]] = []  # row → cell → nodes
        rules_above: list[bool] = []
        rules_below: list[bool] = []
        cells: list[list] = [[]]
        row_rule_above = False
        first_body_rule_row: int | None = None
        header_end_marker: int | None = None
        pending_rule = False
        dropped_rows: set[int] = set()

        def finish_row(force: bool = False) -> None:
            nonlocal cells, pending_rule
            empty = all(self._blank(c) for c in cells)
            if empty and len(cells) == 1 and not force:
                cells = [[]]
                return
            rows_nodes.append(cells)
            rules_above.append(pending_rule)
            rules_below.append(False)
            pending_rule = False
            cells = [[]]

        for x in self.prep(env.nodelist):
            if isinstance(x, LatexSpecialsNode) and x.specials_chars == "&":
                cells.append([])
            elif isinstance(x, LatexMacroNode) and x.macroname in ("\\", "tabularnewline"):
                finish_row(force=True)
            elif isinstance(x, LatexMacroNode) and x.macroname in RULE_MACROS:
                if self._blank_row(cells):
                    if rows_nodes:
                        rules_below[-1] = True
                        if x.macroname in BODY_RULES and first_body_rule_row is None:
                            first_body_rule_row = len(rows_nodes)
                    pending_rule = True
                else:
                    self.audit.add("table", f"\\{x.macroname} in the middle of a row ignored", self.loc(x))
            elif isinstance(x, LatexMacroNode) and x.macroname in LONGTABLE_MARKERS:
                if x.macroname == "endfirsthead":
                    header_end_marker = len(rows_nodes)
                elif x.macroname == "endhead" and header_end_marker is not None:
                    for k in range(header_end_marker, len(rows_nodes)):
                        dropped_rows.add(k)
                elif x.macroname == "endhead":
                    header_end_marker = len(rows_nodes)
                elif x.macroname in ("endfoot", "endlastfoot"):
                    start = max(header_end_marker or 0, 0)
                    # rows between the last head marker and here are footers: drop them
                    for k in range(start, len(rows_nodes)):
                        if k not in dropped_rows and x.macroname == "endfoot":
                            dropped_rows.add(k)
                cells = [[]]
            else:
                cells[-1].append(x)
        finish_row()

        # longtable: caption/label live inside rows
        for r in rows_nodes:
            for c in r:
                for x in list(c):
                    if isinstance(x, LatexMacroNode) and x.macroname == "caption":
                        a = macro_args(x)
                        table.caption = self.caption_inlines(a[-1]) if a else None
                        c.remove(x)
                    elif isinstance(x, LatexMacroNode) and x.macroname == "label":
                        a = macro_args(x)
                        table.label = plain_text(arg_nodes(a[0])).strip() if a else None
                        c.remove(x)
        keep = [k for k, r in enumerate(rows_nodes)
                if k not in dropped_rows and not all(self._blank(c) for c in r)]
        if header_end_marker is not None and name == "longtable":
            hdr = header_end_marker
        else:
            hdr = first_body_rule_row if first_body_rule_row is not None else 0
        hdr_kept = sum(1 for k in keep if k < hdr)

        occupied: dict[int, int] = {}
        for ri, k in enumerate(keep):
            row = Row(rule_above=rules_above[k], rule_below=rules_below[k], loc=loc)
            col = 0
            is_header = ri < hdr_kept
            for cn in rows_nodes[k]:
                cell, content_nodes = self._cell(cn, spec, col)
                cell.header = is_header
                while occupied.get(col, 0) > 0 and not cell.vmerge:
                    # a rowspan from above covers this column: this source cell is its placeholder
                    cell.vmerge = True
                if occupied.get(col, 0) > 0:
                    for cc in range(col, col + cell.colspan):
                        occupied[cc] = occupied.get(cc, 1) - 1
                    cell.vmerge = True
                else:
                    cell.blocks = self.cell_blocks(content_nodes, is_header)
                    if cell.rowspan > 1:
                        for cc in range(col, col + cell.colspan):
                            occupied[cc] = cell.rowspan - 1
                row.cells.append(cell)
                col += cell.colspan
            table.rows.append(row)
        table.header_rows = hdr_kept if hdr_kept < len(table.rows) else 0
        ncols = max([len(spec)] + [sum(c.colspan for c in r.cells) for r in table.rows])
        while len(table.colspec) < ncols:
            table.colspec.append("l")
            table.colweights.append(0.0)
        for r in table.rows:
            width = sum(c.colspan for c in r.cells)
            if width < ncols:
                r.cells.append(Cell(colspan=ncols - width, loc=loc))
        return table

    # ---------------------------------------------------------------------------------------
    @staticmethod
    def _blank(nodes) -> bool:
        return all(isinstance(x, LatexCharsNode) and not x.chars.strip() for x in nodes)

    def _blank_row(self, cells) -> bool:
        return all(self._blank(c) for c in cells)

    def _cell(self, nodes: list, spec: list, col: int) -> tuple[Cell, list]:
        nodes = [x for x in nodes if not (isinstance(x, LatexCharsNode) and not x.chars.strip())]
        align = spec[col][0] if col < len(spec) else "l"
        cell = Cell(align=align, loc=self.loc(nodes[0]) if nodes else None)
        content = nodes
        if len(nodes) == 1 and isinstance(nodes[0], LatexMacroNode) and nodes[0].macroname == "multicolumn":
            a = macro_args(nodes[0])
            try:
                cell.colspan = max(1, int(plain_text(arg_nodes(a[0])).strip()))
            except ValueError:
                cell.colspan = 1
            cs = parse_colspec(arg_text(a[1]))
            if cs:
                cell.align = cs[0][0]
            content = [x for x in self.prep(arg_nodes(a[2])) if not (isinstance(x, LatexCharsNode) and not x.chars.strip())]
            self.audit.count("\\multicolumn")
        if len(content) == 1 and isinstance(content[0], LatexMacroNode) and content[0].macroname == "multirow":
            a = macro_args(content[0])
            try:
                cell.rowspan = max(1, int(plain_text(arg_nodes(a[1])).strip()))
            except ValueError:
                cell.rowspan = 1
            content = self.prep(arg_nodes(a[3]))
            self.audit.count("\\multirow")
        return cell, content

    def cell_blocks(self, nodes: list, header: bool) -> list:
        blocks = self.blocks(nodes)
        if header:
            for b in blocks:
                if isinstance(b, Para) and len(b.children) == 1 and isinstance(b.children[0], Styled) \
                        and b.children[0].kind == "strong":
                    b.children = b.children[0].children
        return blocks
