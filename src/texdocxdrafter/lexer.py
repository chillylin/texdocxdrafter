"""Stage 2a: pylatexenc walker wrapper, macro specs and source-position tracking."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pylatexenc import macrospec
from pylatexenc.latexwalker import (
    LatexCharsNode, LatexCommentNode, LatexEnvironmentNode, LatexGroupNode, LatexMacroNode,
    LatexMathNode, LatexSpecialsNode, LatexWalker, get_default_latex_context_db,
)
from pylatexenc.macrospec import EnvironmentSpec, MacroSpec, MacroStandardArgsParser, ParsedMacroArgs

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"


class RegexArgsParser(MacroStandardArgsParser):
    """Consume whatever a regex matches after the macro name; the verbatim text is kept on the node."""

    def __init__(self, pattern: str):
        super().__init__(argspec="")
        self._re = re.compile(pattern, re.S)

    def parse_args(self, w, pos, parsing_state=None):
        m = self._re.match(w.s, pos)
        n = m.end() - pos if m else 0
        return ParsedMacroArgs(argspec="", argnlist=[]), pos, n


class DefArgsParser(MacroStandardArgsParser):
    r"""``\def\name#1#2{body}``: control sequence, parameter text, balanced body."""

    def __init__(self):
        super().__init__(argspec="")

    def parse_args(self, w, pos, parsing_state=None):
        m = re.compile(r"\s*\\[A-Za-z@]+|\s*\\.", re.S).match(w.s, pos)
        if not m:
            return ParsedMacroArgs(argspec="", argnlist=[]), pos, 0
        p = m.end()
        pm = re.compile(r"(?:\s*#\d)*\s*").match(w.s, p)
        p = pm.end()
        if p < len(w.s) and w.s[p] == "{":
            depth = 0
            j = p
            while j < len(w.s):
                c = w.s[j]
                if c == "\\":
                    j += 2
                    continue
                depth += c == "{"
                depth -= c == "}"
                j += 1
                if depth == 0:
                    break
            p = j
        return ParsedMacroArgs(argspec="", argnlist=[]), pos, p - pos


class VerbatimEnvParser(MacroStandardArgsParser):
    """Raw body of an environment such as lstlisting or Verbatim."""

    def __init__(self, envname: str, optional: bool = False):
        super().__init__(argspec="")
        self.envname = envname
        self.optional = optional

    def parse_args(self, w, pos, parsing_state=None):
        from pylatexenc.macrospec import ParsedVerbatimArgs

        p = pos
        if self.optional:
            m = re.compile(r"\s*\[[^\]]*\]").match(w.s, p)
            if m:
                p = m.end()
        end = w.s.find("\\end{%s}" % self.envname, p)
        if end < 0:
            end = len(w.s)
        node = w.make_node(LatexCharsNode, parsing_state=parsing_state, chars=w.s[p:end], pos=p, len=end - p)
        return ParsedVerbatimArgs(verbatim_chars_node=node), pos, end - pos


MULTICITE = (r"cites parencites textcites autocites smartcites footcites Parencites Textcites "
             r"Autocites supercites").split()
VERBATIM_ENVS = ("verbatim*", "Verbatim", "lstlisting")


@lru_cache(maxsize=1)
def load_specs() -> dict:
    return yaml.safe_load((DEFAULTS_DIR / "macros.yaml").read_text())


@lru_cache(maxsize=1)
def build_context():
    specs = load_specs()
    db = get_default_latex_context_db()
    macros = [MacroSpec(name, args_parser=MacroStandardArgsParser(spec)) for name, spec in specs["macros"].items()]
    macros += [MacroSpec(n, args_parser=RegexArgsParser(r"(?:\s*(?:\[[^\]]*\]|\{[^{}]*\}))+")) for n in MULTICITE]
    macros.append(MacroSpec("cmidrule", args_parser=RegexArgsParser(r"\s*(?:\([^)]*\))?\s*(?:\[[^\]]*\])?\s*\{[^}]*\}")))
    macros.append(MacroSpec("def", args_parser=DefArgsParser()))
    macros.append(MacroSpec("gdef", args_parser=DefArgsParser()))
    envs = [EnvironmentSpec(name, args_parser=MacroStandardArgsParser(spec)) for name, spec in specs["environments"].items()
            if name != "lstlisting"]
    envs.append(EnvironmentSpec("lstlisting", args_parser=VerbatimEnvParser("lstlisting", optional=True)))
    envs.append(EnvironmentSpec("Verbatim", args_parser=VerbatimEnvParser("Verbatim", optional=True)))
    envs.append(EnvironmentSpec("verbatim*", args_parser=VerbatimEnvParser("verbatim*")))
    db.add_context_category("texdocxdrafter", macros=macros, environments=envs, prepend=True)
    return db


def walk(text: str, tolerant: bool = True) -> list:
    """Parse LaTeX text into a pylatexenc node list."""
    w = LatexWalker(text, latex_context=build_context(), tolerant_parsing=tolerant)
    nodes, _, _ = w.get_latex_nodes()
    return nodes


# ---- small node helpers -------------------------------------------------------------------

def is_macro(n, *names) -> bool:
    return isinstance(n, LatexMacroNode) and (not names or n.macroname in names)


def is_env(n, *names) -> bool:
    return isinstance(n, LatexEnvironmentNode) and (not names or n.environmentname in names)


def is_chars(n) -> bool:
    return isinstance(n, LatexCharsNode)


def is_group(n) -> bool:
    return isinstance(n, LatexGroupNode)


def is_special(n, chars: str | None = None) -> bool:
    return isinstance(n, LatexSpecialsNode) and (chars is None or n.specials_chars == chars)


def macro_args(n) -> list:
    return list(n.nodeargd.argnlist) if getattr(n, "nodeargd", None) and n.nodeargd.argnlist else []


def arg_nodes(arg) -> list:
    """Content nodes of a macro argument (group contents, or the single node)."""
    if arg is None:
        return []
    if isinstance(arg, LatexGroupNode):
        return list(arg.nodelist)
    return [arg]


def arg_text(arg) -> str:
    """Verbatim text of an argument without its delimiters."""
    if arg is None:
        return ""
    s = arg.latex_verbatim()
    if isinstance(arg, LatexGroupNode) and len(s) >= 2 and s[0] in "{[" and s[-1] in "}]":
        return s[1:-1]
    return s


def plain_text(nodes) -> str:
    """Flatten nodes to approximate plain text (used for keys, option strings, labels)."""
    out: list[str] = []
    for n in nodes or []:
        if n is None:
            continue
        if isinstance(n, LatexCharsNode):
            out.append(n.chars)
        elif isinstance(n, LatexGroupNode):
            out.append(plain_text(n.nodelist))
        elif isinstance(n, LatexMacroNode):
            for a in macro_args(n):
                out.append(plain_text(arg_nodes(a)))
        elif isinstance(n, LatexEnvironmentNode):
            out.append(plain_text(n.nodelist))
        elif isinstance(n, LatexMathNode):
            out.append(n.latex_verbatim())
        elif isinstance(n, LatexSpecialsNode):
            out.append(" " if n.specials_chars == "~" else n.specials_chars)
    return "".join(out)


__all__ = [
    "walk", "build_context", "is_macro", "is_env", "is_chars", "is_group", "is_special", "macro_args",
    "arg_nodes", "arg_text", "plain_text", "LatexCharsNode", "LatexCommentNode", "LatexEnvironmentNode",
    "LatexGroupNode", "LatexMacroNode", "LatexMathNode", "LatexSpecialsNode", "MULTICITE",
]
