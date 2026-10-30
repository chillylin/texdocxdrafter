"""Coverage report: every construct that was dropped, approximated or could not be resolved."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# kind -> (heading, severity). Severity "info" items are expected presentation drops.
KINDS = {
    "raw": "Unsupported constructs kept as visible Drafter Todo text",
    "dropped": "Presentation commands dropped",
    "direct-format": "Direct formatting used (no matching Word style)",
    "macro": "Macros that could not be expanded",
    "conditional": "TeX conditionals not resolved",
    "math": "Math kept as LaTeX text",
    "figure": "Figures",
    "label": "Labels and cross-references",
    "numbering": "Numbering differences between LaTeX and Word",
    "harvest": "Citation harvest",
    "table": "Tables",
    "style": "Style map",
    "words": "Word-count check",
    "fidelity": "Content check (tree against docx)",
    "preflight": "Preflight",
    "other": "Other",
}


@dataclass
class Entry:
    kind: str
    message: str
    loc: tuple[str, int] | None = None
    severity: str = "warn"

    def where(self) -> str:
        return f"{self.loc[0]}:{self.loc[1]}" if self.loc else ""


@dataclass
class Audit:
    entries: list[Entry] = field(default_factory=list)
    census: Counter = field(default_factory=Counter)
    info: dict = field(default_factory=dict)

    def add(self, kind: str, message: str, loc=None, severity: str = "warn") -> None:
        self.entries.append(Entry(kind, message, loc, severity))

    def count(self, construct: str, n: int = 1) -> None:
        self.census[construct] += n

    def of(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    def warnings(self) -> list[Entry]:
        return [e for e in self.entries if e.severity != "info"]

    def render(self, title: str, stylemap: dict | None = None, style_usage: dict | None = None) -> str:
        lines = [f"# texdocxdrafter report: {title}", ""]
        n_warn = len(self.warnings())
        lines.append(f"{n_warn} items need attention; {len(self.entries) - n_warn} informational.")
        lines.append("")
        if self.info:
            lines.append("## Run summary")
            lines.append("")
            for k, v in self.info.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        if style_usage:
            lines.append("## Style usage")
            lines.append("")
            lines.append("| Style | Paragraphs |")
            lines.append("|---|---|")
            for k, v in sorted(style_usage.items(), key=lambda kv: -kv[1]):
                lines.append(f"| {k} | {v} |")
            lines.append("")
        if self.census:
            lines.append("## Construct census")
            lines.append("")
            lines.append("| Construct | Count |")
            lines.append("|---|---|")
            for k, v in sorted(self.census.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append(f"| {k} | {v} |")
            lines.append("")
        if stylemap:
            lines.append("## Style map used")
            lines.append("")
            for sect in ("paragraph", "character"):
                lines.append(f"- {sect}: " + ", ".join(f"{k}={v}" for k, v in stylemap[sect].items()))
            lines.append("- options: " + ", ".join(f"{k}={v}" for k, v in stylemap["options"].items()))
            lines.append("")
        by_kind: dict[str, list[Entry]] = {}
        for e in self.entries:
            by_kind.setdefault(e.kind, []).append(e)
        for kind in KINDS:
            items = by_kind.get(kind)
            if not items:
                continue
            lines.append(f"## {KINDS[kind]} ({len(items)})")
            lines.append("")
            if kind == "dropped":
                grouped = Counter(e.message for e in items)
                for msg, n in grouped.most_common():
                    first = next(e for e in items if e.message == msg)
                    lines.append(f"- {msg} x{n} (first at {first.where()})")
            else:
                for e in items:
                    lines.append(f"- {e.where() + ': ' if e.loc else ''}{e.message}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write(self, path: str | Path, title: str, **kw) -> None:
        Path(path).write_text(self.render(title, **kw), encoding="utf-8")
