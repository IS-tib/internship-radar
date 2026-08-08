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

from .. import dates
from ..http import post_json
from ..models import Posting
from . import register

#: Workday's search is keyword-based, so we sweep several early-career phrasings.
#: Without this the feed only surfaces titles containing the literal word we ask
#: for, which is how the original single "intern" query missed every new-grad req.
SEARCH_TERMS = ("intern", "internship", "co-op", "university graduate",
                "new grad", "early career")

PAGE = 20
MAX_PER_TERM = 200


@register("workday", requires=("host", "site"))
def fetch_workday(source):
    host, site = source["host"], source["site"]
    tenant = source.get("tenant") or host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

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
                out.append(Posting(
                    company=source["name"],
                    title=j.get("title", ""),
                    url=f"https://{host}/en-US/{site}{path}" if path else "",
                    source="workday",
                    source_name=f"Workday · {source['name']}",
                    location=j.get("locationsText", ""),
                    posted=dates.from_relative(j.get("postedOn", ""), "postedOn"),
                    ats_job_id=str(jid or ""),
                ))
            if total is None:
                total = data.get("total", len(posts))
            offset += PAGE
            if total is not None and offset >= min(total, MAX_PER_TERM):
                break
    return out
