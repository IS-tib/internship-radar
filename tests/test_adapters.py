"""
Adapter tests.

Fixtures mirror the real response shapes captured from each platform's live API,
so these lock in the specific claim that matters: each adapter reads the
*authoritative* posting-date field and not a last-modified field.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import dates
from radar.adapters import boards, workday


GREENHOUSE = {"jobs": [{
    "id": 8559344002,
    "absolute_url": "https://databricks.com/company/careers/open-positions/job?gh_jid=8559344002",
    "title": "Software Engineering Intern (Summer 2027)",
    "location": {"name": "San Francisco, CA"},
    "updated_at": "2026-08-03T07:12:33-04:00",       # last edit — must NOT be used
    "first_published": "2026-06-29T02:02:50-04:00",  # authoritative
    "application_deadline": "2026-09-30",
    "metadata": [{"name": "Department", "value": "Engineering"}],
}]}

LEVER = [{
    "id": "4d29249a-d7e8-4c39-880d-3b35d7b2f6f6",
    "text": "Software Engineer, Internship",
    "categories": {"location": "Palo Alto, CA", "team": "Engineering"},
    "hostedUrl": "https://jobs.lever.co/palantir/4d29249a",
    "createdAt": 1711403416463,
    "workplaceType": "onsite",
    "descriptionPlain": "Build things.",
}]

ASHBY = {"jobs": [
    {"id": "abc", "title": "Software Engineer, Early Career",
     "locationName": "New York", "publishedAt": "2026-07-06T16:47:27.463+00:00",
     "jobUrl": "https://jobs.ashbyhq.com/notion/abc", "isListed": True,
     "isRemote": False, "department": "Engineering", "descriptionPlain": "x"},
    {"id": "hidden", "title": "Unlisted Intern Role", "publishedAt": "2026-07-06T00:00:00Z",
     "jobUrl": "https://jobs.ashbyhq.com/notion/hidden", "isListed": False},
]}

WORKABLE = {"jobs": [{
    "shortcode": "ABC123", "title": "ML Engineering Intern",
    "city": "Berlin", "country": "Germany",
    "published_on": "2026-07-30",         # authoritative
    "created_at": "2026-06-01",           # drafted earlier
    "url": "https://apply.workable.com/hf/j/ABC123", "department": "Research",
}]}

RECRUITEE = {"offers": [{
    "id": 42, "title": "Backend Intern", "city": "Singapore", "country": "SG",
    "status": "published", "published_at": "2026-08-04 05:24:50 UTC",
    "careers_url": "https://x.recruitee.com/o/backend-intern",
}]}

BREEZY = [{
    "id": "b1", "name": "Software Intern", "url": "https://acme.breezy.hr/p/b1",
    "published_date": "2026-01-30T17:33:59.644Z",
    "location": {"city": "Austin", "country": {"name": "United States"}},
    "department": {"name": "Eng"},
}]

WORKDAY_PAGE = {"total": 2, "jobPostings": [
    {"title": "Software Engineering Intern", "externalPath": "/job/SWE-Intern_R1",
     "locationsText": "Santa Clara, CA", "postedOn": "Posted 5 Days Ago",
     "bulletFields": ["R1"]},
    {"title": "University Graduate, Software", "externalPath": "/job/UG_R2",
     "locationsText": "Austin, TX", "postedOn": "Posted 30+ Days Ago",
     "bulletFields": ["R2"]},
]}


class TestGreenhouse(unittest.TestCase):
    def test_uses_first_published_not_updated_at(self):
        with mock.patch.object(boards, "get_json", return_value=GREENHOUSE):
            rows = boards.fetch_greenhouse({"name": "Databricks", "token": "databricks"})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.posted.value, "2026-06-29")
        self.assertEqual(r.posted.field, "first_published")
        self.assertEqual(r.posted.precision, dates.EXACT)
        self.assertEqual(r.ats_job_id, "8559344002")
        self.assertTrue(r.is_first_party)

    def test_missing_first_published_stays_unknown(self):
        payload = {"jobs": [{"id": 1, "title": "X Intern", "absolute_url": "u",
                             "updated_at": "2026-08-03T07:12:33-04:00"}]}
        with mock.patch.object(boards, "get_json", return_value=payload):
            rows = boards.fetch_greenhouse({"name": "A", "token": "a"})
        # Falling back to updated_at would make a long-open req look fresh.
        self.assertFalse(rows[0].posted.known)

    def test_eu_region_switches_host(self):
        seen = {}

        def fake(url, **kw):
            seen["url"] = url
            return {"jobs": []}

        with mock.patch.object(boards, "get_json", side_effect=fake):
            boards.fetch_greenhouse({"name": "B", "token": "b", "region": "eu"})
        self.assertIn("boards-api.eu.greenhouse.io", seen["url"])


class TestLever(unittest.TestCase):
    def test_created_at_epoch_ms(self):
        with mock.patch.object(boards, "get_json", return_value=LEVER):
            rows = boards.fetch_lever({"name": "Palantir", "token": "palantir"})
        r = rows[0]
        self.assertEqual(r.posted.value, "2024-03-25")
        self.assertEqual(r.posted.field, "createdAt")

    def test_non_list_payload_is_safe(self):
        with mock.patch.object(boards, "get_json", return_value={"error": "x"}):
            self.assertEqual(boards.fetch_lever({"name": "A", "token": "a"}), [])


class TestAshby(unittest.TestCase):
    def test_published_at_and_unlisted_filtered(self):
        with mock.patch.object(boards, "get_json", return_value=ASHBY):
            rows = boards.fetch_ashby({"name": "Notion", "token": "notion"})
        self.assertEqual(len(rows), 1)                 # unlisted dropped
        self.assertEqual(rows[0].posted.value, "2026-07-06")
        self.assertEqual(rows[0].posted.field, "publishedAt")


class TestWorkable(unittest.TestCase):
    def test_prefers_published_on(self):
        with mock.patch.object(boards, "get_json", return_value=WORKABLE):
            rows = boards.fetch_workable({"name": "HF", "token": "hf"})
        self.assertEqual(rows[0].posted.value, "2026-07-30")
        self.assertEqual(rows[0].posted.field, "published_on")
        self.assertEqual(rows[0].posted.precision, dates.DAY)


class TestRecruitee(unittest.TestCase):
    def test_published_at_with_utc_suffix(self):
        with mock.patch.object(boards, "get_json", return_value=RECRUITEE):
            rows = boards.fetch_recruitee({"name": "OGP", "token": "ogp"})
        self.assertEqual(rows[0].posted.value, "2026-08-04")


class TestBreezy(unittest.TestCase):
    def test_published_date(self):
        with mock.patch.object(boards, "get_json", return_value=BREEZY):
            rows = boards.fetch_breezy({"name": "Acme", "token": "acme"})
        self.assertEqual(rows[0].posted.value, "2026-01-30")
        self.assertIn("Austin", rows[0].location)

    def test_empty_array_is_not_an_error(self):
        with mock.patch.object(boards, "get_json", return_value=[]):
            self.assertEqual(boards.fetch_breezy({"name": "A", "token": "a"}), [])


class TestWorkday(unittest.TestCase):
    def test_relative_dates_keep_their_precision(self):
        with mock.patch.object(workday, "post_json", return_value=WORKDAY_PAGE):
            rows = workday.fetch_workday({"name": "NVIDIA", "host": "n.wd5.myworkdayjobs.com",
                                          "site": "NV", "search_terms": ["intern"]})
        by_title = {r.title: r for r in rows}
        five = by_title["Software Engineering Intern"]
        plus = by_title["University Graduate, Software"]
        self.assertEqual(five.posted.precision, dates.APPROXIMATE)
        # The old code stamped this as an exact date 30 days back.
        self.assertEqual(plus.posted.precision, dates.AT_LEAST)
        self.assertFalse(plus.posted.trusted)

    def test_dedupes_across_search_terms(self):
        with mock.patch.object(workday, "post_json", return_value=WORKDAY_PAGE):
            rows = workday.fetch_workday({"name": "N", "host": "h", "site": "s",
                                          "search_terms": ["intern", "new grad"]})
        self.assertEqual(len(rows), 2)   # not 4




WORKDAY_COLLAPSED = {"total": 1, "jobPostings": [
    {"title": "Software Engineering Intern", "externalPath": "/job/SWE_R9",
     "locationsText": "2 Locations", "postedOn": "Posted 3 Days Ago",
     "bulletFields": ["R9"]},
]}

WORKDAY_DETAIL = {"jobPostingInfo": {
    "location": "Austin, TX",
    "additionalLocations": ["Seattle, WA", "Austin, TX"],
}}


class TestWorkdayCollapsedLocations(unittest.TestCase):
    """'2 Locations' carries no geography and would be dropped by the US filter."""

    def test_detail_endpoint_resolves_real_locations(self):
        with mock.patch.object(workday, "post_json", return_value=WORKDAY_COLLAPSED), \
             mock.patch.object(workday, "get_json", return_value=WORKDAY_DETAIL):
            rows = workday.fetch_workday({"name": "J&J", "host": "h", "site": "s",
                                          "search_terms": ["intern"]})
        self.assertEqual(rows[0].location, "Austin, TX; Seattle, WA")

    def test_detail_failure_leaves_row_intact(self):
        def boom(*a, **k):
            raise workday.FetchError("nope")

        with mock.patch.object(workday, "post_json", return_value=WORKDAY_COLLAPSED), \
             mock.patch.object(workday, "get_json", side_effect=boom):
            rows = workday.fetch_workday({"name": "J&J", "host": "h", "site": "s",
                                          "search_terms": ["intern"]})
        self.assertEqual(rows[0].location, "2 Locations")   # unchanged, not crashed

    def test_budget_limits_detail_calls(self):
        calls = {"n": 0}

        def counting(*a, **k):
            calls["n"] += 1
            return WORKDAY_DETAIL

        with mock.patch.object(workday, "post_json", return_value=WORKDAY_COLLAPSED), \
             mock.patch.object(workday, "get_json", side_effect=counting):
            workday.fetch_workday({"name": "X", "host": "h", "site": "s",
                                   "search_terms": ["intern"], "detail_budget": 0})
        self.assertEqual(calls["n"], 0)

    def test_normal_location_never_triggers_a_detail_call(self):
        calls = {"n": 0}

        def counting(*a, **k):
            calls["n"] += 1
            return WORKDAY_DETAIL

        with mock.patch.object(workday, "post_json", return_value=WORKDAY_PAGE), \
             mock.patch.object(workday, "get_json", side_effect=counting):
            workday.fetch_workday({"name": "X", "host": "h", "site": "s",
                                   "search_terms": ["intern"]})
        self.assertEqual(calls["n"], 0)


if __name__ == "__main__":
    unittest.main()
