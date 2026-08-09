"""
Workday adapter.

Workday is the one major platform that does **not** publish a posting timestamp.
Its feed returns human strings like "Posted Today", "Posted 5 Days Ago", and
"Posted 30+ Days Ago". The previous implementation converted all of those into
concrete dates, which meant every long-open role at a Workday employer was
stamped with the identical date exactly 30 days in the past — a fabricated
timestamp that also made those roles sort as though they shared a posting day.

We now preserve the distinction: a bounded string becomes an `approximate` date,
and an open-ended "30+" becomes `at_least` (the value is a *ceiling* on recency,
not a posting date). Rendering and the 🆕 badge respect that.
"""

from __future__ import annotations

import re

from .. import dates
from ..http import get_json, post_json, FetchError
from ..models import Posting
from . import register

#: Workday collapses multi-site postings to "2 Locations" / "6 Locations", which
#: carries no geography at all — so a US role at a US employer gets dropped by
#: the US filter for lack of evidence. The detail endpoint returns the real list,
#: so we resolve those (and only those) with a bounded number of extra requests.
_COLLAPSED_RE = re.compile(r"^\s*\d+\s*\+?\s*locations?\s*$", re.I)
DETAIL_BUDGET = 20

#: Workday's search is keyword-based, so we sweep several early-career phrasings.
#: Without this the feed only surfaces titles containing the literal word we ask
#: for, which is how the original single "intern" query missed every new-grad req.
SEARCH_TERMS = ("intern", "internship", "co-op", "university graduate",
                "new grad", "early career")

PAGE = 20
MAX_PER_TERM = 200


def _resolve_locations(host, tenant, site, path):
    """Ask the detail endpoint for a posting's real location list."""
    detail = get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}")
    info = detail.get("jobPostingInfo") or {}
    parts = []
    primary = info.get("location")
    if primary:
        parts.append(str(primary))
    extra = info.get("additionalLocations") or []
    if isinstance(extra, list):
        parts += [str(x) for x in extra if x]
    # De-duplicate while preserving order.
    seen, uniq = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            uniq.append(p)
    return "; ".join(uniq)


@register("workday", requires=("host", "site"))
def fetch_workday(source):
    host, site = source["host"], source["site"]
    tenant = source.get("tenant") or host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    budget = int(source.get("detail_budget", DETAIL_BUDGET))

    from ..classify import classify_level   # local import avoids a cycle

    seen, out = set(), []
    for term in source.get("search_terms") or SEARCH_TERMS:
        offset, total = 0, None
        while offset < MAX_PER_TERM:
            payload = {"appliedFacets": {}, "limit": PAGE,
                       "offset": offset, "searchText": term}
            data = post_json(api, payload)
            posts = data.get("jobPostings") or []
            if not posts:
                break
            for j in posts:
                path = j.get("externalPath", "")
                jid = j.get("bulletFields", [None])[0] if j.get("bulletFields") else None
                key = path or jid or j.get("title", "")
                if key in seen:
                    continue
                seen.add(key)

                where = j.get("locationsText", "") or ""
                # Only spend a detail request when the summary string is the
                # useless "N Locations" form and the title looks early-career.
                if (_COLLAPSED_RE.match(where) and path and budget > 0
                        and classify_level(j.get("title", ""))):
                    budget -= 1
                    try:
                        resolved = _resolve_locations(host, tenant, site, path)
                        if resolved:
                            where = resolved
                    except (FetchError, Exception):
                        pass    # keep the collapsed string; the filter will drop it

                out.append(Posting(
                    company=source["name"],
                    title=j.get("title", ""),
                    url=f"https://{host}/en-US/{site}{path}" if path else "",
                    source="workday",
                    source_name=f"Workday · {source['name']}",
                    location=where,
                    posted=dates.from_relative(j.get("postedOn", ""), "postedOn"),
                    ats_job_id=str(jid or ""),
                ))
            if total is None:
                total = data.get("total", len(posts))
            offset += PAGE
            if total is not None and offset >= min(total, MAX_PER_TERM):
                break
    return out
