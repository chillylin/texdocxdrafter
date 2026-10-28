"""Command line: build | inventory | harvest | parse | check."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _preflight(args, need_build_files=True):
    from .preflight import PreflightError, preflight

    try:
        return preflight(args.main, need_build_files=need_build_files)
    except PreflightError as exc:
        print(f"texdocxdrafter: {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_build(args) -> int:
    from .pipeline import build
    from .preflight import PreflightError

    try:
        res = build(args.main, out=args.output, style_map=args.style_map, reference_doc=args.reference_doc,
                    link_citations=args.link_citations, keep_work=args.keep_work, no_harvest=args.no_harvest,
                    validate_lo=args.validate_libreoffice, comments=not args.no_comments)
    except PreflightError as exc:
        print(f"texdocxdrafter: {exc}", file=sys.stderr)
        return 2
    warn = len(res.audit.warnings())
    print(f"wrote {res.out}")
    print(f"report {res.report} ({warn} items need attention)")
    print("valid" if res.check.ok else "INVALID: " + "; ".join(res.check.errors[:3]))
    return 0 if res.check.ok else 1


def cmd_parse(args) -> int:
    from .audit import Audit
    from .parser import parse_document
    from .parser.model import to_json

    pf = _preflight(args, need_build_files=False)
    audit = Audit()
    if args.facts:
        print(json.dumps({
            "main": str(pf.main), "documentclass": pf.docclass, "options": pf.class_options, "engine": pf.engine,
            "packages": [n for n, _ in pf.packages], "biblatex": pf.biblatex_options, "bib": pf.bib_resources,
            "flags": pf.bool_flags, "preamble_chars": len(pf.preamble), "body_chars": len(pf.body),
            "issues": pf.issues}, indent=1))
        return 0
    doc = parse_document(pf, audit)
    print(json.dumps(to_json(doc), ensure_ascii=False, indent=1))
    return 0


def cmd_inventory(args) -> int:
    from . import inventory, stylemap
    from .audit import Audit
    from .parser import parse_document

    pf = _preflight(args, need_build_files=False)
    pf.workdir.mkdir(parents=True, exist_ok=True)
    sm_path = pf.workdir / "stylemap.yaml"
    sm = stylemap.load(sm_path)
    audit = Audit()
    doc = parse_document(pf, audit, sm)
    rows = inventory.census(doc, sm)
    print(inventory.render(rows))
    if not sm_path.exists():
        stylemap.dump_skeleton({name: {"count": n, "style": style} for name, n, style in rows}, sm_path)
        print(f"\nwrote {sm_path}")
    else:
        print(f"\n{sm_path} already exists and was not changed")
    unsupported = [e for e in audit.entries if e.kind == "raw"]
    if unsupported:
        print(f"\n{len(unsupported)} unsupported constructs:")
        for e in unsupported[:20]:
            print(f"  {e.where()}: {e.message}")
    return 0


def cmd_harvest(args) -> int:
    from . import harvest
    from .audit import Audit
    from .parser import parse_document

    pf = _preflight(args)
    pf.workdir.mkdir(parents=True, exist_ok=True)
    audit = Audit()
    doc = parse_document(pf, audit)
    res = harvest.run_harvest(pf, doc, audit)
    for i in sorted(res.cites):
        print(f"[{i}] " + "".join(_fmt(r) for r in res.cites[i]))
    for key, entries in res.bibs.items():
        print(f"\nbibliography {key}: {len(entries)} entries")
        for k, runs in entries:
            print(f"  {k}: " + "".join(_fmt(r) for r in runs))
    for e in audit.entries:
        print(f"{e.kind}: {e.message}", file=sys.stderr)
    return 0 if res.ok else 1


def _fmt(r) -> str:
    t = r.text
    if "i" in r.fmt:
        t = f"_{t}_"
    if "b" in r.fmt:
        t = f"**{t}**"
    return t


def cmd_check(args) -> int:
    from .validate import check_docx, render_check

    res = check_docx(args.docx)
    print(render_check(res, args.docx))
    return 0 if res.ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="texdocxdrafter", description="TeX-DOCX Drafter: a clean, style-mapped Word draft from a compiled LaTeX paper")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="convert main.tex to docx")
    b.add_argument("main")
    b.add_argument("-o", "--output")
    b.add_argument("--style-map")
    b.add_argument("--reference-doc")
    b.add_argument("--link-citations", action="store_true")
    b.add_argument("--keep-work", action="store_true")
    b.add_argument("--no-harvest", action="store_true")
    b.add_argument("--no-comments", action="store_true", help="do not turn LaTeX %% comments into Word comments")
    b.add_argument("--validate-libreoffice", action="store_true")
    b.set_defaults(fn=cmd_build)

    i = sub.add_parser("inventory", help="construct census and stylemap.yaml skeleton")
    i.add_argument("main")
    i.set_defaults(fn=cmd_inventory)

    p = sub.add_parser("parse", help="dump the semantic tree as JSON")
    p.add_argument("main")
    p.add_argument("--facts", action="store_true", help="print preamble facts and sizes only")
    p.set_defaults(fn=cmd_parse)

    h = sub.add_parser("harvest", help="side compile only; print citations and entries")
    h.add_argument("main")
    h.set_defaults(fn=cmd_harvest)

    c = sub.add_parser("check", help="style usage table and validation of a docx")
    c.add_argument("docx")
    c.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
