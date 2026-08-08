"""
Community-feed adapters.

These are the wide net: volunteer-maintained lists that cover employers with no
public board API (custom career sites, Oracle/Taleo, Eightfold, Avature). They
are second-class by design — `is_first_party=False` — so the dedupe stage always
prefers a direct-from-board record when both describe the same job.

The Pitt CSC / Simplify lineage (SimplifyJobs, vanshb03, and forks) all publish
an identical `listings.json` schema, so one adapter serves all of them.
"""

from __future__ import annotations

from .. import dates
from ..http import get_json
from ..models import Posting
from . import register


@register("community_listings", requires=("url",))
def fetch_community_listings(source):
    """Pitt CSC / Simplify-schema listings.json.

    Schema: {company_name, title, url, locations[], date_posted, date_updated,
             active, is_visible, terms[], season, sponsorship, source}
    `date_posted` and `date_updated` are Unix timestamps in *seconds*. We use
    date_posted only — date_updated moves whenever the maintainers touch a row.
    """
    data = get_json(source["url"])
    rows = data if isinstance(data, list) else data.get("listings", [])

    out = []
    for j in rows:
        if j.get("active") is False or j.get("is_visible") is False:
            continue
        locs = j.get("locations") or []
        where = "; ".join(str(x) for x in locs[:2]) if isinstance(locs, list) else str(locs)
        terms = j.get("terms") or []
        title = j.get("title", "")
        # Some feeds put the season in `terms`/`season` rather than the title;
        # append it so the term classifier can see it instead of guessing.
        season_hint = j.get("season") or (terms[0] if terms else "")
        out.append(Posting(
            company=j.get("company_name", ""),
            title=title,
            url=j.get("url", ""),
            source="community",
            source_name=source["name"],
            location=where,
            posted=dates.from_timestamp(j.get("date_posted"), "date_posted", "s"),
            ats_job_id=str(j.get("id") or ""),
            department=str(season_hint or ""),
            is_first_party=False,
        ))
    return out


@register("yc_companies", requires=("url",))
def fetch_yc_companies(source):
    """Y Combinator company directory (yc-oss/api mirror).

    This yields **no job postings** — it is a company/domain seed list used by
    tools/discover.py to find new boards to probe. It is registered as an adapter
    only so the same config format and health tracking apply; the main pipeline
    filters it out because it produces no Postings.
    """
    return []
