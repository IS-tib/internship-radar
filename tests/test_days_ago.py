"""The 'N days ago' display: pluralisation, edge cases, and freshness."""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import dates, render

TODAY = dt.date(2026, 8, 8)


def at(iso, field="first_published"):
    return dates.from_iso(iso, field)


class TestFormatting(unittest.TestCase):
    def test_same_day_is_zero_days(self):
        self.assertEqual(at("2026-08-08").days_ago(TODAY), "0 days ago")

    def test_exactly_one_day_is_singular(self):
        self.assertEqual(at("2026-08-07").days_ago(TODAY), "1 day ago")

    def test_two_days_is_plural(self):
        self.assertEqual(at("2026-08-06").days_ago(TODAY), "2 days ago")

    def test_seven_and_thirty(self):
        self.assertEqual(at("2026-08-01").days_ago(TODAY), "7 days ago")
        self.assertEqual(at("2026-07-09").days_ago(TODAY), "30 days ago")

    def test_never_says_one_days(self):
        """Only n==1 uses the singular, and it must never read "1 days ago".

        Compared exactly rather than by substring, because "11 days ago"
        legitimately contains the text "1 days ago".
        """
        for n in range(0, 40):
            text = at((TODAY - dt.timedelta(days=n)).isoformat()).days_ago(TODAY)
            self.assertNotEqual(text, "1 days ago")
            self.assertEqual(text, "1 day ago" if n == 1 else f"{n} days ago")

    def test_over_a_year_is_bucketed(self):
        self.assertEqual(at("2016-02-24").days_ago(TODAY), "365+ days ago")


class TestEdgeCases(unittest.TestCase):
    def test_missing_date_is_unknown_not_invented(self):
        self.assertEqual(dates.UNKNOWN_DATE.days_ago(TODAY), "Unknown")

    def test_slight_future_is_clamped_to_zero(self):
        """Timezone rounding can put a fresh post a few hours ahead."""
        self.assertEqual(at("2026-08-09").days_ago(TODAY), "0 days ago")

    def test_far_future_is_unknown_not_negative(self):
        self.assertEqual(at("2027-01-01").days_ago(TODAY), "Unknown")
        self.assertNotIn("-", at("2027-01-01").days_ago(TODAY))

    def test_garbage_date_is_unknown(self):
        self.assertEqual(dates.from_iso("not a date", "f").days_ago(TODAY), "Unknown")


class TestPrecisionMarkers(unittest.TestCase):
    def test_at_least_keeps_floor_semantics(self):
        d = dates.from_relative("Posted 30+ Days Ago", "postedOn", TODAY)
        self.assertEqual(d.days_ago(TODAY), "≥30 days ago")

    def test_approximate_is_marked(self):
        d = dates.from_relative("Posted 5 Days Ago", "postedOn", TODAY)
        self.assertEqual(d.days_ago(TODAY), "~5 days ago")

    def test_approximate_singular(self):
        d = dates.from_relative("Posted Yesterday", "postedOn", TODAY)
        self.assertEqual(d.days_ago(TODAY), "~1 day ago")


class TestFreshnessIsDynamic(unittest.TestCase):
    """The stored row keeps a timestamp; the display is recomputed each render."""

    def test_same_row_ages_without_being_rewritten(self):
        row = {"posted": {"value": "2026-08-08", "precision": "exact", "field": "f"}}
        self.assertEqual(render._date_cell(row, dt.date(2026, 8, 8)), "0 days ago")
        self.assertEqual(render._date_cell(row, dt.date(2026, 8, 9)), "1 day ago")
        self.assertEqual(render._date_cell(row, dt.date(2026, 8, 15)), "7 days ago")
        # the stored value never changed
        self.assertEqual(row["posted"]["value"], "2026-08-08")

    def test_no_days_ago_value_is_persisted(self):
        from radar.models import Posting
        p = Posting(company="A", title="SWE Intern", url="u", source="greenhouse",
                    posted=at("2026-08-01"))
        d = p.to_dict()
        self.assertNotIn("days_ago", d)
        self.assertEqual(d["posted"]["value"], "2026-08-01")   # timestamp retained


class TestDeadlineRemoved(unittest.TestCase):
    def test_posting_has_no_deadline_field(self):
        from radar.models import Posting
        p = Posting(company="A", title="T", url="u", source="greenhouse")
        self.assertFalse(hasattr(p, "deadline"))
        self.assertNotIn("deadline", p.to_dict())

    def test_table_header_has_no_deadline_column(self):
        self.assertNotIn("Deadline", render.HEAD)
        self.assertEqual(render.HEAD.split("\n")[0].count("|"), 9)

    def test_row_matches_header_column_count(self):
        row = {"company": "A", "title": "SWE Intern", "url": "u",
               "category": "Software Engineering", "level": "intern",
               "term": "Summer 2027", "location_display": "Austin, TX",
               "posted": {"value": "2026-08-06", "precision": "exact", "field": "f"}}
        self.assertEqual(render._row(row, TODAY).count("|"),
                         render.HEAD.split("\n")[0].count("|"))


if __name__ == "__main__":
    unittest.main()
