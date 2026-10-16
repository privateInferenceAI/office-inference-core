#!/usr/bin/env python3
"""Write down every format the parser says it can read, so the allowlist can be checked against it.

**Why this exists, and it is the whole point of the change it belongs to.** The format-coverage check used to
compare the corpus against the worker's accepted-suffix list. That list was also the denominator, so the check
could only ever confirm that the corpus contained every format the list named. It reported
`15/15 formats covered (complete)` while a third of what the parser reads was unreachable, and no amount of
running it would have said otherwise: **an allowlist measured against itself is a mirror, not a test.**

This runs where the parser is, asks docling what it advertises, and writes the answer into the repository. The
test that reads the output then asks the question nobody was asking: for every format the parser can read, is
it in the worker's accepted list, or is it excluded with a reason? A gap is now a failing test rather than a
silent absence.

Run it inside the parser image, which is the only place docling is installed. From the deployment:

    docker cp scripts/dump_parser_formats.py parser:/tmp/
    docker exec parser python3 /tmp/dump_parser_formats.py
    docker cp parser:/tmp/parser-formats.json ./docs/parser-formats.json

`scripts/probe.sh` is not the runner for this one: it runs against the session image, and docling is not in
that image.

**A parser upgrade that adds a format therefore shows up as a diff in a committed file**, which is the point:
the drift becomes reviewable instead of invisible.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


def collect() -> dict:
    """Docling's advertised formats, normalised to lowercase extensions without dots."""
    import docling
    from docling.datamodel.base_models import FormatToExtensions

    formats: dict[str, list[str]] = {}
    for fmt, extensions in FormatToExtensions.items():
        # The enum renders as `InputFormat.PDF`; the name is the part after the dot.
        name = str(fmt).rsplit(".", 1)[-1]
        formats[name] = sorted({e.lower().lstrip(".") for e in extensions})
    return {"docling_version": getattr(docling, "__version__", "unknown"),
            "generated": date.today().isoformat(),
            "formats": dict(sorted(formats.items()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="where to write it; stdout when omitted")
    args = parser.parse_args(argv)

    try:
        payload = collect()
    except ImportError as exc:
        print(f"docling is not importable here ({exc}). This has to run inside the parser image.",
              file=sys.stderr)
        return 2

    text = json.dumps(payload, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        total = sum(len(v) for v in payload["formats"].values())
        print(f"wrote {args.out}: docling {payload['docling_version']}, "
              f"{len(payload['formats'])} formats, {total} extensions")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
