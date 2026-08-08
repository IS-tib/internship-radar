# Architecture

`README.md` is generated on every run, so this file is where the design notes
live.

## Layout

```
scraper.py              thin CLI entry point (--dry-run, --only, --limit)
sources.json            every board we read (replaces the old companies.json)
radar/
  http.py               retries, exponential backoff, per-host rate limiting
  dates.py              posting dates with provenance + precision
  models.py             Posting record, URL/title/company normalisation
  classify.py           intern vs new-grad, discipline, target term
  dedupe.py             cross-source identity and near-duplicate merging
  store.py              first_seen/last_seen, closure tracking, atomic writes
  pipeline.py           orchestration + reproducible metrics
  render.py             README generation
  adapters/
    boards.py           Greenhouse, Lever, Ashby, SmartRecruiters,
                        Workable, Recruitee, Breezy, Rippling
    workday.py          Workday (relative dates — see below)
    community.py        community listings feeds
tools/
  verify_sources.py     probe every token; report/disable/prune dead ones
  discover.py           find new boards from YC + community seeds
tests/                  96 offline tests, no network required
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
python tools/discover.py --seed yc --limit 300      # YC companies hiring
python tools/discover.py --seed community           # upgrade feed-only companies
```

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
