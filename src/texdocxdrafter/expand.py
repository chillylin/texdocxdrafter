r"""Stage 2b: author macros (\newcommand, \def, \newenvironment, \DeclareMathOperator) and conditionals.

Definitions are collected from a first parse of the preamble and registered with the lexer context so
that the body parse knows their argument shapes. Invocations are expanded by textual substitution of the
macro body and re-parsed, with a depth limit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pylatexenc.macrospec import EnvironmentSpec, MacroSpec, MacroStandardArgsParser

from . import lexer
from .lexer import arg_text, is_macro, macro_args, plain_text

MAX_DEPTH = 24


@dataclass
class MacroDef:
    name: str
    nargs: int
    default: str | None
    body: str
    loc: tuple[str, int] | None = None


@dataclass
class EnvDef:
    name: str
    nargs: int
    default: str | None
    begin: str
    end: str


def subst(body: str, args: list[str]) -> str:
    def rep(m: re.Match) -> str:
        if m.group(0) == "##":
            return "#"
        i = int(m.group(1)) - 1
        return args[i] if 0 <= i < len(args) else ""

    return re.sub(r"##|#(\d)", rep, body)


def read_group(s: str, i: int) -> tuple[str, int]:
    """s[i] == '{' → (inner text, index after the closing brace)."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)


class Expander:
    def __init__(self) -> None:
        self.macros: dict[str, MacroDef] = {}
        self.envs: dict[str, EnvDef] = {}
        self.mathops: dict[str, str] = {}
        self.unexpandable: list[tuple[str, tuple | None, str]] = []

    # -- collection --------------------------------------------------------------------------
    _DEF_RE = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand|newenvironment|"
                         r"renewenvironment|DeclareMathOperator|def|gdef|newif)(?![A-Za-z])")

    def scan(self, text: str, loc_fn=None) -> str:
        """Register definitions found in ``text`` and return the text with them blanked out.

        Definition bodies may contain unbalanced \\begin or braces, which would derail the node parser,
        so definitions are read at string level. Blanking keeps every offset and newline in place.
        """
        out = list(text)
        for m in list(self._DEF_RE.finditer(text)):
            if all(c == " " or c == "\n" for c in out[m.start():m.end()]):
                continue  # already inside a blanked definition
            end = self._read_definition(m.group(1), text, m.end(), loc_fn(m.start()) if loc_fn else None)
            if end is None:
                continue
            for k in range(m.start(), end):
                if out[k] != "\n":
                    out[k] = " "
        return "".join(out)

    @staticmethod
    def _skip_ws(s: str, i: int) -> int:
        while i < len(s) and s[i] in " \t\r\n":
            i += 1
        return i

    def _read_cs(self, s: str, i: int) -> tuple[str | None, int]:
        i = self._skip_ws(s, i)
        if i < len(s) and s[i] == "{":
            inner, j = read_group(s, i)
            m = re.fullmatch(r"\s*\\([A-Za-z@]+|.)\s*", inner, re.S)
            return (m.group(1) if m else None), j
        m = re.compile(r"\\([A-Za-z@]+|.)", re.S).match(s, i)
        return (m.group(1), m.end()) if m else (None, i)

    def _read_opt(self, s: str, i: int) -> tuple[str | None, int]:
        j = self._skip_ws(s, i)
        if j < len(s) and s[j] == "[":
            k = s.find("]", j)
            if k > 0:
                return s[j + 1:k], k + 1
        return None, i

    def _read_definition(self, kind: str, s: str, i: int, loc) -> int | None:
        if kind == "newif":
            m = re.compile(r"\s*\\if[A-Za-z]+").match(s, i)
            return m.end() if m else None  # flag values come from preflight
        if kind in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"):
            if i < len(s) and s[i] == "*":
                i += 1
            name, i = self._read_cs(s, i)
            if name is None:
                return None
            nargs_txt, i = self._read_opt(s, i)
            default, i = self._read_opt(s, i)
            i = self._skip_ws(s, i)
            if i >= len(s) or s[i] != "{":
                return None
            body, j = read_group(s, i)
            nargs = int(nargs_txt) if nargs_txt and nargs_txt.strip().isdigit() else 0
            if kind == "providecommand" and name in self.macros:
                return j
            self.macros[name] = MacroDef(name, nargs, default, body, loc)
            return j
        if kind in ("def", "gdef"):
            name, i = self._read_cs(s, i)
            if name is None:
                return None
            pm = re.compile(r"((?:\s*#\d)*)\s*").match(s, i)
            nargs = len(re.findall(r"#\d", pm.group(1)))
            i = pm.end()
            if i >= len(s) or s[i] != "{":
                return None
            body, j = read_group(s, i)
            self.macros[name] = MacroDef(name, nargs, None, body, loc)
            return j
        if kind == "DeclareMathOperator":
            if i < len(s) and s[i] == "*":
                i += 1
            name, i = self._read_cs(s, i)
            i = self._skip_ws(s, i)
            if name is None or i >= len(s) or s[i] != "{":
                return None
            body, j = read_group(s, i)
            self.mathops[name] = body
            return j
        # newenvironment / renewenvironment
        if i < len(s) and s[i] == "*":
            i += 1
        i = self._skip_ws(s, i)
        if i >= len(s) or s[i] != "{":
            return None
        name, i = read_group(s, i)
        nargs_txt, i = self._read_opt(s, i)
        default, i = self._read_opt(s, i)
        i = self._skip_ws(s, i)
        if i >= len(s) or s[i] != "{":
            return None
        begin, i = read_group(s, i)
        i = self._skip_ws(s, i)
        if i >= len(s) or s[i] != "{":
            return None
        end, i = read_group(s, i)
        nargs = int(nargs_txt) if nargs_txt and nargs_txt.strip().isdigit() else 0
        self.envs[name.strip()] = EnvDef(name.strip(), nargs, default, begin, end)
        return i

    # -- lexer registration ------------------------------------------------------------------
    def specs(self):
        macros = []
        for d in self.macros.values():
            if d.name in ("def",):
                continue
            spec = ""
            n = d.nargs
            if d.default is not None and n >= 1:
                spec = "[" + "{" * (n - 1)
            else:
                spec = "{" * n
            macros.append(MacroSpec(d.name, args_parser=MacroStandardArgsParser(spec)))
        for name in self.mathops:
            macros.append(MacroSpec(name, args_parser=MacroStandardArgsParser("")))
        envs = []
        for e in self.envs.values():
            spec = ("[" + "{" * (e.nargs - 1)) if e.default is not None and e.nargs >= 1 else "{" * e.nargs
            envs.append(EnvironmentSpec(e.name, args_parser=MacroStandardArgsParser(spec)))
        return macros, envs

    def context(self):
        from pylatexenc.latexwalker import get_default_latex_context_db  # noqa: F401

        base = lexer.build_context()
        macros, envs = self.specs()
        if not macros and not envs:
            return base
        ctx = base.filter_context()
        ctx.add_context_category("author", macros=macros, environments=envs, prepend=True)
        return ctx

    def walk(self, text: str) -> list:
        from pylatexenc.latexwalker import LatexWalker

        w = LatexWalker(text, latex_context=self.context(), tolerant_parsing=True)
        nodes, _, _ = w.get_latex_nodes()
        return nodes

    # -- expansion ---------------------------------------------------------------------------
    def args_for(self, node, nargs: int, default: str | None) -> list[str]:
        args = macro_args(node)
        out: list[str] = []
        if default is not None and nargs >= 1:
            out.append(arg_text(args[0]) if args and args[0] is not None else default)
            rest = args[1:]
        else:
            rest = args
        for a in rest[: nargs - len(out)]:
            out.append(arg_text(a))
        while len(out) < nargs:
            out.append("")
        return out

    def expand_macro_node(self, node) -> str | None:
        d = self.macros.get(node.macroname)
        if d is None:
            return None
        return subst(d.body, self.args_for(node, d.nargs, d.default))

    def expand_env_node(self, node) -> tuple[str, str] | None:
        d = self.envs.get(node.environmentname)
        if d is None:
            return None
        args = self.args_for(node, d.nargs, d.default)
        return subst(d.begin, args), subst(d.end, args)

    def expand_text(self, s: str, depth: int = 0) -> str:
        """String-level expansion for math: user macros with arguments and DeclareMathOperator names."""
        if depth > MAX_DEPTH or not (self.macros or self.mathops):
            return s
        out: list[str] = []
        i = 0
        changed = False
        while i < len(s):
            c = s[i]
            if c != "\\":
                out.append(c)
                i += 1
                continue
            m = re.compile(r"\\([A-Za-z@]+|.)", re.S).match(s, i)
            if not m:
                out.append(c)
                i += 1
                continue
            name = m.group(1)
            j = m.end()
            if name in self.mathops:
                out.append("\\operatorname{" + self.mathops[name] + "}")
                i = j
                changed = True
                continue
            d = self.macros.get(name)
            if d is None:
                out.append(s[i:j])
                i = j
                continue
            args: list[str] = []
            k = j
            if d.default is not None and d.nargs >= 1:
                om = re.compile(r"\s*\[([^\]]*)\]").match(s, k)
                if om:
                    args.append(om.group(1))
                    k = om.end()
                else:
                    args.append(d.default)
            while len(args) < d.nargs:
                km = re.compile(r"\s*").match(s, k).end()
                if km < len(s) and s[km] == "{":
                    inner, k = read_group(s, km)
                    args.append(inner)
                elif km < len(s):
                    args.append(s[km])
                    k = km + 1
                else:
                    args.append("")
            out.append("{" + subst(d.body, args) + "}" if d.nargs == 0 else subst(d.body, args))
            i = k
            changed = True
        res = "".join(out)
        return self.expand_text(res, depth + 1) if changed else res


# ---- conditionals -------------------------------------------------------------------------

TEX_IFS = {"iftrue", "iffalse", "ifx", "ifnum", "ifdim", "ifcase", "ifodd", "ifvmode", "ifhmode", "ifmmode",
           "ifcat", "if", "ifdefined", "ifcsname", "ifundefined", "ifpdf", "ifxetex", "ifluatex", "ifdraft"}


def resolve_conditionals(nodes: list, flags: dict[str, bool], report=None) -> list:
    """Select branches of \\if…\\else…\\fi at one list level. ``report(node, msg)`` audits unknown tests."""
    flags = flags  # mutated by \xtrue / \xfalse
    out: list = []
    i = 0
    n = len(nodes)

    def is_if(x) -> bool:
        return isinstance(x, lexer.LatexMacroNode) and (x.macroname in TEX_IFS or
                                                        (x.macroname.startswith("if") and x.macroname[2:] in flags))

    def split_branches(start: int) -> tuple[list, list, int]:
        depth = 0
        true_part: list = []
        else_part: list = []
        cur = true_part
        j = start
        while j < n:
            x = nodes[j]
            if is_if(x):
                depth += 1
            elif isinstance(x, lexer.LatexMacroNode) and x.macroname == "fi":
                if depth == 0:
                    return true_part, else_part, j + 1
                depth -= 1
            elif isinstance(x, lexer.LatexMacroNode) and x.macroname == "else" and depth == 0:
                cur = else_part
                j += 1
                continue
            cur.append(x)
            j += 1
        return true_part, else_part, n

    while i < n:
        x = nodes[i]
        if isinstance(x, lexer.LatexMacroNode):
            nm = x.macroname
            m = re.fullmatch(r"(\w+)(true|false)", nm)
            if m and m.group(1) in flags:
                flags[m.group(1)] = m.group(2) == "true"
                i += 1
                continue
            if is_if(x):
                t, e, nxt = split_branches(i + 1)
                if nm == "iftrue":
                    cond = True
                elif nm == "iffalse":
                    cond = False
                elif nm.startswith("if") and nm[2:] in flags:
                    cond = flags[nm[2:]]
                else:
                    cond = None
                if cond is None:
                    if report:
                        report(x, f"\\{nm} not resolved; first branch kept")
                    if nm == "ifx":
                        t = t[2:] if len(t) >= 2 else t
                    cond = True
                out.extend(resolve_conditionals(t if cond else e, flags, report))
                i = nxt
                continue
        out.append(x)
        i += 1
    return out
