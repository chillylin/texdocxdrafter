r"""Stage 5b: read labels, kinds and numbers from the .aux file (including the cleveref variant)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .expand import read_group


@dataclass
class AuxLabel:
    key: str
    number: str = ""
    page: str = ""
    title: str = ""
    cref_kind: str | None = None  # section table figure equation item …


def _groups(s: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "{":
            inner, i = read_group(s, i)
            out.append(inner)
        else:
            i += 1
    return out


def read_aux(path: Path | None, _seen: set | None = None) -> dict[str, AuxLabel]:
    labels: dict[str, AuxLabel] = {}
    if path is None or not Path(path).is_file():
        return labels
    seen = _seen if _seen is not None else set()
    path = Path(path)
    if path in seen:
        return labels
    seen.add(path)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\\newlabel\{([^}]*)\}\{(.*)\}\s*$", line)
        if m:
            key, rest = m.group(1), m.group(2)
            g = _groups("{" + rest + "}")
            fields = _groups(g[0]) if g else []
            if key.endswith("@cref"):
                real = key[:-5]
                lab = labels.setdefault(real, AuxLabel(real))
                km = re.match(r"\[([^\]]*)\]\[[^\]]*\]\[[^\]]*\](.*)", fields[0] if fields else "")
                if km:
                    lab.cref_kind = km.group(1)
                continue
            lab = labels.setdefault(key, AuxLabel(key))
            if fields:
                lab.number = _clean(fields[0])
            if len(fields) > 1:
                lab.page = _clean(fields[1])
            if len(fields) > 2:
                lab.title = _clean(fields[2])
            continue
        im = re.match(r"\\@input\{([^}]*)\}", line)
        if im:
            sub = path.parent / im.group(1)
            for k, v in read_aux(sub, seen).items():
                labels.setdefault(k, v)
    return labels


def _clean(s: str) -> str:
    return re.sub(r"\\[a-zA-Z]+\s*|[{}]", "", s).strip()
