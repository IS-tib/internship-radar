"""
Job identity and de-duplication.

The original implementation deduped on a single set holding both the URL and a
`(company, title)` pair. That had two failure modes:

  * **Over-merging.** Because location was not part of the key, a company posting
    the same role in Seattle, New York, and London kept only whichever row
    happened to be processed first. The other two vanished with no record.
  * **Under-merging.** The same job reached from a direct board and from a
    community feed has different URLs, so URL-keyed dedupe missed it whenever the
    titles differed even slightly ("SWE Intern" vs "Software Engineer Intern").

The strategy here is layered. We compute several identity signals per posting and
merge on the strongest available, keeping the *best* record as canonical and
retaining what we merged so nothing is silently destroyed.

Signal strength, strongest first:
  1. (ats, ats_job_id)                — same posting on the same platform
  2. canonical URL                    — same posting, any path to it
  3. (company, title, location)       — same posting seen via different sources
  4. (company, title) + similar text  — near-duplicate / repost
"""

from __future__ import annotations

import re
from collections import defaultdict

from .dates import TRUSTED


def identity_keys(p) -> list[tuple]:
    """All identity signals for a posting, strongest first."""
    keys = []
    if p.source and p.ats_job_id:
        keys.append(("ats", p.source, p.ats_job_id))
    cu = p.canonical_url
    if cu:
        keys.append(("url", cu))
    if p.key_company and p.key_title:
        keys.append(("cts", p.key_company, p.key_title, p.key_location))
    return keys


def primary_identity(p) -> str:
    """A stable string id used as the record's key across runs.

    Prefers the canonical URL because it survives a company migrating between
    ATS platforms; falls back to the semantic triple for feeds with no URL.
    """
    cu = p.canonical_url
    if cu:
        return f"url:{cu}"
    if p.source and p.ats_job_id:
        return f"ats:{p.source}:{p.ats_job_id}"
    return f"cts:{p.key_company}|{p.key_title}|{p.key_location}"


_TOKEN = re.compile(r"[a-z0-9]+")


def _shingles(text, n=5):
    toks = _TOKEN.findall((text or "").lower())
    if len(toks) < n:
        return set()
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def text_similarity(a: str, b: str) -> float:
    """Jaccard similarity over 5-word shingles. 0.0 when either side is too short."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


NEAR_DUPLICATE_THRESHOLD = 0.82


def _score(p) -> tuple:
    """Rank competing records for the same job; higher is better.

    First-party board data beats a community feed (better URL, real dates), a
    trusted date beats an approximate one, and a longer description is more
    useful for near-duplicate detection downstream.
    """
    return (
        1 if p.is_first_party else 0,
        1 if p.posted.precision in TRUSTED else 0,
        1 if p.posted.known else 0,
        1 if p.deadline.known else 0,
        len(p.description or ""),
        len(p.location or ""),
    )


def deduplicate(postings):
    """Collapse duplicates, returning (unique_postings, stats).

    Merged records are not discarded: the winner gains `merged_from`, listing the
    source label and URL of every record folded into it, so a human can always
    audit what was collapsed and recover an alternate application link.
    """
    # Union-find over identity signals.
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    node_of = {}
    for i, p in enumerate(postings):
        node = f"#{i}"
        parent.setdefault(node, node)
        node_of[i] = node
        for k in identity_keys(p):
            ks = repr(k)
            parent.setdefault(ks, ks)
            union(ks, node)

    groups = defaultdict(list)
    for i, p in enumerate(postings):
        groups[find(node_of[i])].append(p)

    # Second pass: near-duplicate detection for same company + same location where
    # titles differ enough to have escaped the semantic key (reposts, renames).
    unique, stats = [], {"input": len(postings), "merged": 0, "near_dupes": 0}
    by_company = defaultdict(list)

    for members in groups.values():
        members.sort(key=_score, reverse=True)
        winner = members[0]
        if len(members) > 1:
            stats["merged"] += len(members) - 1
            winner.merged_from = [
                {"source": m.source_name or m.source, "url": m.url}
                for m in members[1:] if m.url and m.url != winner.url
            ]
        by_company[(winner.key_company, winner.key_location)].append(winner)

    for bucket in by_company.values():
        kept = []
        for cand in sorted(bucket, key=_score, reverse=True):
            dup_of = None
            for k in kept:
                if not cand.description or not k.description:
                    continue
                if text_similarity(cand.description, k.description) >= NEAR_DUPLICATE_THRESHOLD:
                    dup_of = k
                    break
            if dup_of is None:
                kept.append(cand)
            else:
                stats["near_dupes"] += 1
                extra = getattr(dup_of, "merged_from", None) or []
                extra.append({"source": cand.source_name or cand.source,
                              "url": cand.url, "reason": "near-duplicate text"})
                dup_of.merged_from = extra
        unique.extend(kept)

    stats["output"] = len(unique)
    return unique, stats
