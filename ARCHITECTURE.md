# Architecture

`README.md` is generated on every run, so this file is where the design notes
live.

## Layout

```
scraper.py              thin CLI entry point (--dry-run, --only, --limit)
sources.json            every board we read (replaces the old companies.json)
radar/
  http.py               retries, exponential backoff, per-host rate limiting
  dates.py              posting dates with provenance + precision + "days ago"
  models.py             Posting record, URL/title/company normalisation
  classify.py           intern vs new-grad, discipline, target term
  eligibility.py        undergraduate eligibility (excludes PhD/MS/senior roles)
  locations.py          US detection and location normalisation
  dedupe.py             cross-source identity and near-duplicate merging
  store.py              first_seen/last_seen, closure tracking, atomic writes
  pipeline.py           orchestration + reproducible metrics
  render.py             README generation
  adapters/
    boards.py           Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
                        Recruitee, Breezy, Rippling, CareerPuck
    workday.py          Workday (relative dates — see below)
    community.py        community listings feeds
tools/
  verify_sources.py     probe every token; report/disable/prune dead ones
  discover.py           find new boards from YC + community seeds
tests/                  164 offline tests, no network required
```

## Adding a source

Append to `sources.json` and run the checker:

```json
{ "name": "Acme", "ats": "greenhouse", "token": "acme" }
```

```bash
python tools/verify_sources.py --check acme      # probes every adapter
python scraper.py --only greenhouse --dry-run
```

Adding a *platform* means writing one function in `radar/adapters/` and
decorating it with `@register("name", requires=("token",))`. Nothing else in the
pipeline changes.

## Dates: the core design constraint

Different platforms expose wildly different date quality, and the previous
implementation flattened all of them into a single `YYYY-MM-DD` string. That
produced three concrete bugs:

1. **Fabricated precision.** Workday only says `"Posted 30+ Days Ago"`. That was
   converted into an exact date 30 days back, so every long-open Workday role
   shared one identical, invented posting date.
2. **Evergreen requisitions read as fresh postings.** Palantir keeps Lever reqs
   open for years; `createdAt` of 2016 is honest but meaningless as "posted".
3. **Discovery date leaking into posted date.** When a source gave no date, the
   row silently fell back to the day this repo first saw it.

Every date now carries `value`, `precision`, and the `field` it came from:

| precision | meaning | earns 🆕? |
| --- | --- | --- |
| `exact` | real timestamp from the source | yes |
| `day` | source gave a bare date | yes |
| `approximate` | derived from "5 days ago" | no |
| `at_least` | derived from "30+ days ago" — a floor | no |
| `unknown` | source publishes nothing | no |

Authoritative field per platform, verified against live API responses rather
than documentation:

| Platform | Field | Note |
| --- | --- | --- |
| Greenhouse | `first_published` | `updated_at` is last-modified — deliberately not a fallback |
| Lever | `createdAt` | epoch ms; the only date the API exposes |
| Ashby | `publishedAt` | |
| SmartRecruiters | `releasedDate` | not `createdOn` |
| Workable | `published_on` | `created_at` is when the req was drafted |
| Recruitee | `published_at` | `"YYYY-MM-DD HH:MM:SS UTC"` |
| Breezy | `published_date` | |
| Rippling | `createdOn` | **detail endpoint only** — list carries no dates |
| Workday | `postedOn` | relative text only |
| Community feeds | `date_posted` | Unix seconds; `date_updated` ignored |

Rows older than 400 days that are still listed are treated as evergreen and
render as `listed since YYYY-MM` rather than claiming a posting day.

## Scope: undergraduate, US-only

Two filters run between classification and de-duplication, and both report what
they dropped (`metrics.filtered_out` in `listings.json`) so the board can never
quietly shrink without explanation.

**Undergraduate eligibility** (`radar/eligibility.py`). Roles restricted to PhD,
Master's, or graduate students are excluded, as are senior/staff/principal and
experience-gated postings. The subtle part is avoiding false positives: real
titles from production include `Product Manager Intern` (contains "Manager") and
`Machine Learning Engineer Intern - Lead Ads` (contains "Lead"), both of which
are ordinary undergraduate internships. The rule that resolves this is that
**seniority language is only meaningful on a non-internship posting** — an
internship is by definition not a staff role — so seniority checks apply to
new-grad postings only. Descriptions are consulted only for unambiguous phrasing
("must be enrolled in a PhD program"), never for a passing mention of a PhD.

**US-only** (`radar/locations.py`). Handles state abbreviations, full state
names, city shorthand (`NYC`, `SF`, `LA`, `DC`), reversed ordering
(`US, CA, Santa Clara`), accented spellings (`Montréal`), and multi-value strings
(`Boston, MA; Seattle, WA`). A role qualifies if **any** component is US, since a
posting open in Boston and London is reachable by a US undergraduate.

The important rule is what happens with no evidence: a bare `Remote` or an opaque
`2 Locations` returns *unknown*, not US, and unknowns are excluded. We never
assume a generic remote role is US-based. For Workday specifically — the only
platform that collapses locations to `"2 Locations"` — the adapter spends a
bounded number of detail requests to recover the real list rather than losing
those roles.

## The Posted column: "days ago"

The table shows age in days, never a raw date. Critically, that value is
**derived at render time and never persisted**: `listings.json` stores the real
timestamp with its precision, and `DateInfo.days_ago()` recomputes the display on
every run. A listing therefore ages from `0 days ago` to `1 day ago` on its own,
without the scraper rewriting the row.

| Shown | Means |
| --- | --- |
| `3 days ago` | exact publish timestamp from the source |
| `1 day ago` | correct singular — never "1 days ago" |
| `~4 days ago` | approximate, from relative text like "posted 4 days ago" |
| `≥30 days ago` | source said "30+ days ago"; a floor, not a measurement |
| `365+ days ago` | open over a year, typically an evergreen requisition |
| `Unknown` | no posting date published — we do not invent one |

Edge cases are deliberate: a timestamp up to a day in the future is treated as
timezone skew and clamped to `0 days ago`; anything further ahead is bad data and
renders `Unknown` rather than a negative number.

The `deadline` field was removed entirely — from the model, the adapters, the
stored schema and the table — rather than hidden, because sources populated it
too rarely to be worth carrying.

## De-duplication

Identity signals, strongest first:

1. `(ats, ats_job_id)` — same posting, same platform
2. canonical URL — tracking parameters stripped, identity parameters kept
3. `(company, title, location)` — same posting reached via different sources
4. `(company, location)` + ≥0.82 Jaccard similarity on description shingles

The location component matters: the old key was `(company, title)` only, so a
company posting one role in three cities kept exactly one of them. Merged records
are never destroyed — the winner keeps `merged_from` with each alternate source
and URL, and first-party board data always outranks a community feed.

## Job lifecycle

`listings.json` tracks `first_seen`, `last_seen`, `misses`, and `status`. A role
is only marked closed after **two consecutive** absences *and* only when its
source fetched successfully that run — otherwise one API outage would mark every
role at that company as filled. Closed roles are retained for 30 days so reposts
are recognisable.

## Source health

Every run records per-source status (`ok`, `not_found`, `fetch_error`,
`adapter_error`, `disabled`) into `listings.json` and surfaces 404s in the
README. This is the fix for the failure mode that motivated the rewrite: 26
configured Ashby boards were yielding six roles between them because several
tokens had been renamed and nothing ever reported it.

`.github/workflows/verify-sources.yml` probes every token weekly.

## Growing coverage

`tools/discover.py` turns seed lists into verified sources:

```bash
python tools/discover.py --seed urls                # harvest tokens from job links (best)
python tools/discover.py --seed yc --limit 300      # YC companies currently hiring
python tools/discover.py --seed community           # upgrade feed-only companies
python tools/discover.py --seed jobhive --limit 500 # opt-in bulk company/ATS dataset
```

`--seed urls` is the highest-yield mode and the one to run regularly. Community
feeds link straight at employers' ATS pages, and those URLs *encode the board
token* (`boards.greenhouse.io/acme/...` → Greenhouse token `acme`). Harvesting
them converts a second-hand community row into a first-party source with better
dates and links — and it scales across a whole platform instead of one
hand-added company at a time. Run against one production dataset it recovered
**203 board tokens** we were not yet reading directly.

`--seed jobhive` uses a third-party dataset mapping ~80k companies to their ATS
and slug. It is opt-in only because its terms of use have not been reviewed;
check that before relying on it.

It guesses board slugs from company names/domains, probes Greenhouse → Ashby →
Lever, and only ever suggests a board that returned HTTP 200 **and** currently
has at least one early-career role. Nothing is written without `--append`, so it
cannot introduce invented companies or dead tokens.

## Testing

```bash
python -m unittest discover -s tests -v     # 96 tests, no network
python scraper.py --dry-run --limit 20      # live smoke test
```

Tests run in CI before the scraper does, so a logic regression fails the build
rather than publishing wrong dates to the board.
