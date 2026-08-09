"""
Persistence and job lifecycle.

The previous version rebuilt listings.json from scratch every run, so a role that
disappeared from a board simply vanished with no trace. That loses genuinely
useful information: *when* a posting closed is a strong signal about how fast a
company fills roles, and a job that disappears for one run because an API blipped
should not be treated the same as one that was actually filled.

This module tracks, per posting identity:

  first_seen   the first run that observed it (never overwritten)
  last_seen    the most recent run that observed it
  misses       consecutive runs where it was absent while its source was healthy
  status       "open" | "closed"

A posting is only marked closed after `CLOSE_AFTER_MISSES` consecutive misses
**and** only when its source fetched successfully — otherwise an outage would
close every job at that company. Closed rows are retained for `ARCHIVE_DAYS`
before being dropped, so the board can show "recently closed" and so reposts are
recognisable.
"""

from __future__ import annotations

import datetime as dt
import json
import os

CLOSE_AFTER_MISSES = 2
ARCHIVE_DAYS = 30

SCHEMA_VERSION = 4


def _today():
    return dt.datetime.now(dt.timezone.utc).date()


def load(path):
    if not os.path.exists(path):
        return {"schema": SCHEMA_VERSION, "listings": [], "closed": []}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"schema": SCHEMA_VERSION, "listings": [], "closed": []}
    data.setdefault("listings", [])
    data.setdefault("closed", [])
    if int(data.get("schema", 0)) < SCHEMA_VERSION:
        data = migrate(data)
    return data


def migrate(data):
    """Bring a pre-v3 store forward.

    v2 rows stored `posted` as a bare string and identified rows by `key`. The
    only thing worth preserving across the upgrade is `first_seen` — every other
    field is rewritten by the next successful fetch anyway.

    Migrated dates are marked `approximate`, not `day`: v2 could not distinguish a
    real timestamp from a Workday "30+ days ago" value it had rounded into a
    concrete date, so we decline to vouch for any of them. A row keeps that
    marking for at most one run, until its board is read again.
    """
    from .models import normalize_url

    def fix(row):
        row = dict(row)
        if "identity" not in row:
            key = row.get("key") or ""
            if key.startswith("http"):
                row["identity"] = f"url:{normalize_url(key)}"
            elif key:
                row["identity"] = f"cts:{key}"
            else:
                row["identity"] = f"cts:{row.get('company','')}|{row.get('title','')}"
        v = row.get("posted")
        if isinstance(v, str):
            row["posted"] = ({"value": v, "precision": "approximate", "field": "migrated:v2"}
                             if v else {"value": "", "precision": "unknown", "field": ""})
        elif not isinstance(v, dict):
            row["posted"] = {"value": "", "precision": "unknown", "field": ""}
        # The deadline column was removed: sources populated it too rarely to be
        # useful, so it is dropped from the schema rather than carried as dead
        # weight through every run.
        row.pop("deadline", None)
        row.pop("date", None)
        row.pop("is_new", None)
        row.pop("priority", None)
        # v2 had no level field (it only ever matched interns). Backfill from the
        # title so a carried-forward row still renders correctly during the one
        # transitional run before its board is read again.
        if not row.get("level"):
            from .classify import classify_level
            row["level"] = classify_level(row.get("title", "")) or "intern"
        if "is_first_party" not in row:
            row["is_first_party"] = str(row.get("source", "")).lower() not in (
                "community", "community (summer 2027)")
        row.setdefault("status", "open")
        row.setdefault("misses", 0)
        return row

    return {
        "schema": SCHEMA_VERSION,
        "listings": [fix(r) for r in data.get("listings", [])],
        "closed": [fix(r) for r in data.get("closed", [])],
    }


def _index(rows):
    out = {}
    for r in rows:
        key = r.get("identity") or r.get("key")
        if key:
            out[key] = r
    return out


def reconcile(current, previous, healthy_sources, today=None):
    """Merge this run's postings with prior state.

    `current`         list of dicts for postings observed this run
    `previous`        the loaded store
    `healthy_sources` set of source labels that fetched successfully this run;
                      absences are only meaningful for these
    Returns (open_rows, closed_rows, stats).
    """
    today = today or _today()
    iso = today.isoformat()

    prev_open = _index(previous.get("listings", []))
    prev_closed = _index(previous.get("closed", []))
    seen_now = set()

    stats = {"new": 0, "returning": 0, "still_open": 0, "closed": 0, "reopened": 0}
    open_rows = []

    for row in current:
        key = row["identity"]
        seen_now.add(key)
        old = prev_open.get(key) or prev_closed.get(key)
        if old is None:
            row["first_seen"] = iso
            row["last_seen"] = iso
            row["misses"] = 0
            row["status"] = "open"
            stats["new"] += 1
        else:
            row["first_seen"] = old.get("first_seen", iso)
            row["last_seen"] = iso
            row["misses"] = 0
            row["status"] = "open"
            if key in prev_closed:
                row["reopened_at"] = iso
                stats["reopened"] += 1
            else:
                stats["still_open"] += 1
        open_rows.append(row)

    # Anything previously open and not seen now.
    closed_rows = []
    for key, old in prev_open.items():
        if key in seen_now:
            continue
        # health_key is the source *config* name (the company whose board this
        # row came from), written by the pipeline. Fall back to the adapter label
        # for rows written by an older schema version.
        src = old.get("health_key") or old.get("source", "")
        if src and src not in healthy_sources:
            # Source failed this run — absence proves nothing. Carry it forward
            # untouched so an outage cannot mass-close a company's roles.
            old.setdefault("misses", 0)
            open_rows.append(old)
            continue
        misses = int(old.get("misses", 0)) + 1
        old["misses"] = misses
        if misses >= CLOSE_AFTER_MISSES:
            old["status"] = "closed"
            old["closed_at"] = iso
            closed_rows.append(old)
            stats["closed"] += 1
        else:
            open_rows.append(old)

    # Retain previously-closed rows for a while, then let them go.
    cutoff = (today - dt.timedelta(days=ARCHIVE_DAYS)).isoformat()
    for key, old in prev_closed.items():
        if key in seen_now:
            continue
        if old.get("closed_at", "0000-00-00") >= cutoff:
            closed_rows.append(old)

    return open_rows, closed_rows, stats


def save(path, open_rows, closed_rows, health, metrics):
    payload = {
        "schema": SCHEMA_VERSION,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(open_rows),
        "metrics": metrics,
        "source_health": health,
        "listings": open_rows,
        "closed": closed_rows,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)   # atomic: a crash mid-write can't corrupt the store
    return payload
