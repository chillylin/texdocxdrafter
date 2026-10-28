"""Display math: equation/align family, numbering, labels, line splitting."""
from __future__ import annotations

import re

from .model import DisplayMath, EqLine

NUMBERED_MULTI = {"align", "gather", "eqnarray", "flalign", "alignat"}
_LABEL = re.compile(r"\\label\{([^}]*)\}")
_NONUM = re.compile(r"\\(nonumber|notag)\b")
_TAG = re.compile(r"\\tag\*?\{[^}]*\}")


def split_lines(body: str) -> list[str]:
    """Split on top-level ``\\\\`` (not inside braces or nested environments)."""
    out: list[str] = []
    depth_brace = 0
    depth_env = 0
    cur: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("\\begin{", i):
            depth_env += 1
        elif body.startswith("\\end{", i):
            depth_env -= 1
        c = body[i]
        if c == "\\" and body.startswith("\\\\", i):
            if depth_brace == 0 and depth_env == 0:
                i += 2
                m = re.match(r"\s*\[[^\]]*\]", body[i:])
                if m:
                    i += m.end()
                out.append("".join(cur))
                cur = []
                continue
            cur.append("\\\\")
            i += 2
            continue
        if c == "\\":
            cur.append(body[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth_brace += 1
        elif c == "}":
            depth_brace -= 1
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


class MathMixin:
    def display_from_node(self, n) -> DisplayMath:
        latex = self.math_source(n)
        return self.make_display("displaymath", latex, self.loc(n))

    def display_from_env(self, n) -> DisplayMath:
        name = n.environmentname
        text = n.latex_verbatim()
        m = re.match(r"\\begin\{[^}]*\}", text)
        body = text[m.end():] if m else text
        body = re.sub(r"\\end\{%s\}\s*$" % re.escape(name), "", body)
        if name.startswith(("alignat", "flalign")) or name == "alignat":
            body = re.sub(r"^\s*\{\d+\}", "", body)
        return self.make_display(name, self.ex.expand_text(body.strip()), self.loc(n))

    def make_display(self, env: str, body: str, loc) -> DisplayMath:
        base = env.rstrip("*")
        starred = env.endswith("*") or env in ("displaymath", "math")
        multi = base in NUMBERED_MULTI
        pieces = split_lines(body) if (multi or base == "multline" or "\\\\" in body) else [body]
        if base == "equation" or base == "multline" or base in ("displaymath", "math"):
            pieces = [body] if base != "multline" else [" ".join(pieces)]
        lines: list[EqLine] = []
        for p in pieces:
            if not p.strip():
                continue
            label = None
            lm = _LABEL.search(p)
            if lm:
                label = lm.group(1).strip()
            suppress = bool(_NONUM.search(p))
            if _TAG.search(p):
                self.audit.add("numbering", "\\tag equation: numbered automatically instead", loc)
            clean = _TAG.sub("", _NONUM.sub("", _LABEL.sub("", p))).strip()
            numbered = (not starred) and not suppress
            lines.append(EqLine(latex=clean, numbered=numbered, label=label, loc=loc))
        self.audit.count(f"display math ({env})")
        return DisplayMath(env=env, lines=lines, latex=body, loc=loc)
