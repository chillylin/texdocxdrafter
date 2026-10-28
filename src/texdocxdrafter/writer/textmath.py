"""Simple inline math as plain text: Greek letters, relations and short sub/superscripts become characters.

``text_math`` returns segments ``(text, italic, vert)`` or None when the formula needs a real equation
(fractions, big operators, accents, font switches other than text/roman/italic, nested scripts).
Italic follows LaTeX: Latin letters and lowercase Greek are italic; digits, uppercase Greek and operators upright.
"""
from __future__ import annotations

import re

GREEK_LOWER = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}
GREEK_UPPER = {
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
RELATIONS = {
    "=": "=", "<": "<", ">": ">", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "sim": "∼", "simeq": "≃", "equiv": "≡", "propto": "∝", "in": "∈", "notin": "∉",
    "subset": "⊂", "subseteq": "⊆", "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "mapsto": "↦", "ll": "≪", "gg": "≫",
}
BINARY = {"+": "+", "-": "−", "times": "×", "cdot": "·", "pm": "±", "mp": "∓", "div": "÷", "ast": "∗"}
ORDINARY = {
    "infty": "∞", "partial": "∂", "nabla": "∇", "ldots": "…", "dots": "…", "cdots": "⋯", "prime": "′",
    "degree": "°", "%": "%", "{": "{", "}": "}", "$": "$", "&": "&", "#": "#", "_": "_", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "ell": "ℓ", "star": "⋆", "circ": "∘", "bullet": "•",
}
SPACES = {",": " ", ";": " ", ":": " ", "!": "", "quad": " ", "qquad": "  ", " ": " "}
TEXT_FONTS = {"text": False, "textrm": False, "mathrm": False, "textit": True, "mathit": True}

_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\d+(?:\.\d+)?|\.\d+|[A-Za-z]|\s+|.", re.S)

Seg = tuple[str, bool, str | None]


class _NotSimple(Exception):
    pass


def _group(tokens: list[str], i: int) -> tuple[list[str], int]:
    """Tokens inside the braces opening at tokens[i]; returns (inner, index after the closing brace)."""
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j] == "{":
            depth += 1
        elif tokens[j] == "}":
            depth -= 1
            if depth == 0:
                return tokens[i + 1:j], j + 1
    raise _NotSimple


def _atom(tok: str) -> tuple[str, str, bool]:
    """Classify one token: (kind, text, italic); kind is var, num, rel, bin, ord, punct, open, close, space."""
    if re.fullmatch(r"[A-Za-z]", tok):
        return "var", tok, True
    if re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", tok):
        return "num", tok, False
    if tok in "=<>":
        return "rel", RELATIONS[tok], False
    if tok in "+-":
        return "bin", BINARY[tok], False
    if tok in "([":
        return "open", tok, False
    if tok in ")]|":
        return ("close" if tok != "|" else "ord"), tok, False
    if tok == ":":
        return "rel", ":", False
    if tok in ",;":
        return "punct", tok, False
    if tok in ".!'/":
        return "ord", "′" if tok == "'" else tok, False
    if tok.startswith("\\"):
        name = tok[1:]
        if name in GREEK_LOWER:
            return "var", GREEK_LOWER[name], True
        if name in GREEK_UPPER:
            return "var", GREEK_UPPER[name], False
        if name in RELATIONS:
            return "rel", RELATIONS[name], False
        if name in BINARY:
            return "bin", BINARY[name], False
        if name in ("{", "lbrace"):
            return "open", "{", False
        if name in ("}", "rbrace"):
            return "close", "}", False
        if name in ORDINARY:
            return "ord", ORDINARY[name], False
        if name in SPACES:
            return "space", SPACES[name], False
    raise _NotSimple


def _script(tokens: list[str], vert: str) -> list[Seg]:
    """A sub/superscript body: plain atoms only, no nested scripts or spacing logic."""
    out: list[Seg] = []
    for tok in tokens:
        if tok.isspace():
            continue
        kind, text, ital = _atom(tok)
        if kind == "space":
            continue
        out.append((text, ital, vert))
    if not out:
        raise _NotSimple
    return out


def _font_text(tokens: list[str]) -> str:
    text = "".join(tokens)
    if not re.fullmatch(r"[A-Za-z0-9 .,:;'()\-]*", text):
        raise _NotSimple
    return text


def text_math(latex: str) -> list[Seg] | None:
    try:
        return _convert(latex)
    except _NotSimple:
        return None


def _convert(latex: str) -> list[Seg]:
    tokens = _TOKEN.findall(latex.strip())
    items: list[tuple[str, list[Seg]]] = []  # (kind, segments)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isspace():
            i += 1
            continue
        if tok in ("^", "_"):
            vert = "superscript" if tok == "^" else "subscript"
            i += 1
            while i < len(tokens) and tokens[i].isspace():
                i += 1
            if i >= len(tokens):
                raise _NotSimple
            if tokens[i] == "{":
                inner, i = _group(tokens, i)
            else:
                inner, i = [tokens[i]], i + 1
            segs = _script(inner, vert)
            if not items:
                items.append(("var", segs))  # a bare script such as ^3
            elif any(s[2] == vert for s in items[-1][1]):
                raise _NotSimple  # x^a^b
            else:
                items[-1] = (items[-1][0], items[-1][1] + segs)
            continue
        if tok == "{":
            inner, i = _group(tokens, i)
            sub = _convert("".join(inner))
            items.append(("var", sub))
            continue
        if tok.startswith("\\") and tok[1:] in TEXT_FONTS:
            if i + 1 >= len(tokens) or tokens[i + 1] != "{":
                raise _NotSimple
            inner, i = _group(tokens, i + 1)
            italic = TEXT_FONTS[tok[1:]]
            items.append(("var", [(_font_text(inner), italic, None)]))
            continue
        kind, text, ital = _atom(tok)
        items.append((kind, [(text, ital, None)]))
        i += 1
    if not items:
        raise _NotSimple
    return _space(items)


def _space(items: list[tuple[str, list[Seg]]]) -> list[Seg]:
    """LaTeX-like spacing: spaces around relations, around binary operators that have a left operand,
    and after commas."""
    out: list[Seg] = []
    operand = False  # something that can be a left operand precedes
    for k, (kind, segs) in enumerate(items):
        last = k == len(items) - 1
        if kind == "rel":
            if out:
                out.append((" ", False, None))
            out.extend(segs)
            if not last:
                out.append((" ", False, None))
            operand = False
        elif kind == "bin":
            if operand and not last:
                out.append((" ", False, None))
                out.extend(segs)
                out.append((" ", False, None))
            else:
                out.extend(segs)
            operand = False
        elif kind == "punct":
            out.extend(segs)
            if not last and segs[0][0] == ",":
                out.append((" ", False, None))
            operand = False
        elif kind == "open":
            out.extend(segs)
            operand = False
        else:
            out.extend(segs)
            operand = True
    return _merge(out)


def _merge(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s in segs:
        if not s[0]:
            continue
        if out and out[-1][1:] == s[1:]:
            out[-1] = (out[-1][0] + s[0],) + s[1:]
        else:
            out.append(s)
    # collapse doubled spaces from adjacent rules
    return [(re.sub(r" {2,}", " ", t), it, v) for t, it, v in out]
