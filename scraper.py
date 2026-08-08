#!/usr/bin/env python3
"""
internship-radar entry point.

Reads sources.json, fetches every configured job board, classifies and dedupes
the results, reconciles them against the previous run, and rewrites
README.md + listings.json.

    python scraper.py                 # full run
    python scraper.py --dry-run       # fetch and report, write nothing
    python scraper.py --only greenhouse,ashby
    python scraper.py --limit 20      # first N sources (useful when debugging)

Standard library only — no install step, runs anywhere Python 3.9+ runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from radar import pipeline, render, store  # noqa: E402

SOURCES = os.path.join(HERE, "sources.json")
LISTINGS = os.path.join(HERE, "listings.json")
README = os.path.join(HERE, "README.md")


def load_sources(path=SOURCES):
    with open(path) as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report metrics without writing files")
    ap.add_argument("--only", default="",
                    help="comma-separated adapter names to run (e.g. greenhouse,ashby)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only run the first N sources")
    ap.add_argument("--sources", default=SOURCES)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    log = (lambda *a, **k: None) if args.quiet else print

    sources = load_sources(args.sources)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        sources = [s for s in sources if s.get("ats") in wanted]
    if args.limit:
        sources = sources[:args.limit]

    log(f"scanning {len(sources)} sources…\n")
    previous = store.load(LISTINGS)
    open_rows, closed_rows, health, metrics = pipeline.build(sources, previous, log=log)

    log("\n── metrics ──")
    for k in ("roles_open", "companies", "sources_ok", "sources_dead",
              "dates_trusted_pct", "dates_known_pct", "first_party_pct"):
        log(f"  {k:<20} {metrics[k]}")
    log(f"  by_level             {metrics['by_level']}")

    if args.dry_run:
        log("\n(dry run — nothing written)")
        return 0

    store.save(LISTINGS, open_rows, closed_rows, health, metrics)
    with open(README, "w") as f:
        f.write(render.render(open_rows, metrics, health))
    log(f"\n{len(open_rows)} roles → listings.json + README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
