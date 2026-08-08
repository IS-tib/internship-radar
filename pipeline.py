"""
Orchestration: collect -> classify -> dedupe -> reconcile -> metrics.

Source health is a first-class output. The previous version printed a warning for
a failing board and moved on, which meant a token could rot for months with
nobody noticing — that is exactly how 26 configured Ashby boards ended up
yielding six roles. Every run now records, per source, whether it succeeded, how
many rows it returned, and the precise failure reason, and writes that into
listings.json so regressions are visible and `tools/verify_sources.py` can prune.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import adapters
from .classify import categorize, classify_level, classify_term, is_phd_role
from .dedupe import deduplicate, primary_identity
from .http import FetchError, NotFound
from .dates import TRUSTED

MAX_WORKERS = 12


def collect(sources, workers=MAX_WORKERS, log=print):
    """Fetch every configured source concurrently.

    Returns (postings, health) where health maps source name -> result record.
    """
    postings, health = [], {}
    active = []

    for s in sources:
        if s.get("enabled") is False:
            health[s["name"]] = {"status": "disabled", "count": 0, "ats": s.get("ats", "")}
            continue
        spec = adapters.get(s.get("ats", ""))
        if spec is None:
            health[s["name"]] = {"status": "error", "count": 0,
                                 "ats": s.get("ats", ""),
                                 "error": f"unknown adapter '{s.get('ats')}'"}
            continue
        missing = spec.validate(s)
        if missing:
            health[s["name"]] = {"status": "error", "count": 0, "ats": s["ats"],
                                 "error": f"missing config: {', '.join(missing)}"}
            continue
        active.append((s, spec))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(spec, s): s for s, spec in active}
        for fut in as_completed(futures):
            s = futures[fut]
            name = s["name"]
            try:
                rows = fut.result()
                postings.extend(rows)
                health[name] = {"status": "ok", "count": len(rows), "ats": s["ats"]}
                log(f"  ok    {name:<28} {len(rows):>4} postings")
            except NotFound as e:
                health[name] = {"status": "not_found", "count": 0, "ats": s["ats"],
                                "error": str(e)}
                log(f"  DEAD  {name:<28} token 404 — needs updating")
            except FetchError as e:
                health[name] = {"status": "fetch_error", "count": 0, "ats": s["ats"],
                                "error": str(e)}
                log(f"  fail  {name:<28} {e}")
            except Exception as e:  # adapter bug — surface it, don't hide it
                health[name] = {"status": "adapter_error", "count": 0,
                                "ats": s["ats"], "error": f"{type(e).__name__}: {e}"}
                log(f"  ERR   {name:<28} {type(e).__name__}: {e}")
    return postings, health


def refine(postings, today=None, include_levels=("intern", "new_grad")):
    """Filter to early-career technical roles and attach classification."""
    today = today or dt.date.today()
    kept = []
    for p in postings:
        level = classify_level(p.title)
        if level is None or level not in include_levels:
            continue
        category = categorize(p.title)
        if category is None:
            continue
        term = classify_term(p.title, p.posted, today)
        if term is None:
            continue
        label, priority, inferred = term
        p.level = level
        p.category = category
        p.term = label
        p.term_priority = priority
        p.term_inferred = inferred
        p.is_phd = is_phd_role(p.title)
        p.identity = primary_identity(p)
        kept.append(p)
    return kept


def _sort_key(row):
    """Ordering key: trusted-and-recent first, guesses next, unknown last.

    Sorting purely on the date string would let an `at_least` value (a floor, not
    a posting date) and an evergreen 2016 requisition compete for the top of the
    list against real timestamps. Tiering first keeps the promise the README
    makes: the top of the board is genuinely the freshest verifiable postings.
    """
    posted = row.get("posted") or {}
    if not isinstance(posted, dict):        # defensive: pre-v3 row that escaped migration
        posted = {"value": str(posted), "precision": "unknown"}
    value = posted.get("value") or ""
    trusted = posted.get("precision") in TRUSTED and bool(value)
    tier = 0 if trusted else (1 if value else 2)
    # Negate by sorting the tier ascending and the date descending; Python can't
    # mix directions in one key, so date is inverted via a reversed string trick.
    return (tier, _invert_date(value), row.get("company", ""))


def _invert_date(value: str) -> str:
    """Map a date so that ascending sort yields newest-first."""
    if not value:
        return "~"          # sorts after any digit, pushing unknowns last
    return "".join(chr(ord("9") - int(c)) if c.isdigit() else c for c in value[:10])


def build(sources, previous, today=None, log=print):
    """Run the whole pipeline. Returns (open_rows, closed_rows, health, metrics)."""
    from .store import reconcile

    today = today or dt.datetime.now(dt.timezone.utc).date()
    raw, health = collect(sources, log=log)
    log(f"\n{len(raw)} raw postings from {sum(1 for h in health.values() if h['status'] == 'ok')} healthy sources")

    matched = refine(raw, today)
    log(f"{len(matched)} early-career technical roles after classification")

    unique, dedupe_stats = deduplicate(matched)
    log(f"{len(unique)} unique after dedupe "
        f"(merged {dedupe_stats['merged']}, near-dupes {dedupe_stats['near_dupes']})")

    rows = []
    for p in unique:
        d = p.to_dict()
        d["identity"] = p.identity
        d["term_inferred"] = getattr(p, "term_inferred", False)
        d["is_phd"] = getattr(p, "is_phd", False)
        merged = getattr(p, "merged_from", None)
        if merged:
            d["merged_from"] = merged
        rows.append(d)

    # `health` is keyed by the *source config name* (usually the company), while a
    # row only knows its adapter label. Stamp each row with its config name so
    # reconcile() can tell "this company's board failed, so absence means
    # nothing" apart from "this board is fine, so the role really is gone".
    healthy = {name for name, h in health.items() if h["status"] == "ok"}
    for r in rows:
        r["health_key"] = r.get("source_name", "").split(" · ")[-1] or r.get("source", "")

    open_rows, closed_rows, life = reconcile(rows, previous, healthy, today)
    open_rows.sort(key=_sort_key)

    metrics = compute_metrics(open_rows, closed_rows, health, dedupe_stats, life)
    return open_rows, closed_rows, health, metrics


def compute_metrics(open_rows, closed_rows, health, dedupe_stats, life):
    """Reproducible quality metrics, embedded in listings.json every run."""
    def pct(n, d):
        return round(100.0 * n / d, 1) if d else 0.0

    total = len(open_rows)
    trusted = sum(1 for r in open_rows
                  if (r.get("posted") or {}).get("precision") in TRUSTED)
    known = sum(1 for r in open_rows if (r.get("posted") or {}).get("value"))
    inferred_terms = sum(1 for r in open_rows if r.get("term_inferred"))
    first_party = sum(1 for r in open_rows if r.get("is_first_party"))

    by_status = {}
    for h in health.values():
        by_status[h["status"]] = by_status.get(h["status"], 0) + 1

    return {
        "roles_open": total,
        "roles_closed_tracked": len(closed_rows),
        "companies": len({r.get("company", "") for r in open_rows}),
        "sources_configured": len(health),
        "sources_ok": by_status.get("ok", 0),
        "sources_dead": by_status.get("not_found", 0),
        "sources_failed": by_status.get("fetch_error", 0) + by_status.get("adapter_error", 0),
        "by_level": _count(open_rows, "level"),
        "by_category": _count(open_rows, "category"),
        "by_source": _count(open_rows, "source"),
        "dates_trusted": trusted,
        "dates_trusted_pct": pct(trusted, total),
        "dates_known": known,
        "dates_known_pct": pct(known, total),
        "dates_unknown": total - known,
        "terms_inferred": inferred_terms,
        "first_party": first_party,
        "first_party_pct": pct(first_party, total),
        "dedupe": dedupe_stats,
        "lifecycle": life,
    }


def _count(rows, field):
    out = {}
    for r in rows:
        k = r.get(field) or "unknown"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
