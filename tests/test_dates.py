"""Date parsing: the rules that keep us from inventing precision."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from radar import dates


TODAY = dt.date(2026, 8, 8)


class TestIso(unittest.TestCase):
    def test_timestamp_with_offset_is_exact(self):
        d = dates.from_iso("2026-06-29T02:02:50-04:00", "first_published")
        self.assertEqual(d.value, "2026-06-29")
        self.assertEqual(d.precision, dates.EXACT)
        self.assertEqual(d.field, "first_published")
        self.assertTrue(d.trusted)

    def test_zulu(self):
        d = dates.from_iso("2023-01-30T17:33:59.644Z", "published_date")
        self.assertEqual(d.value, "2023-01-30")
        self.assertEqual(d.precision, dates.EXACT)

    def test_bare_date_is_day_precision(self):
        d = dates.from_iso("2026-07-30", "published_on")
        self.assertEqual(d.value, "2026-07-30")
        self.assertEqual(d.precision, dates.DAY)
        self.assertTrue(d.trusted)

    def test_recruitee_utc_suffix(self):
        d = dates.from_iso("2026-08-04 05:24:50 UTC", "published_at")
        self.assertEqual(d.value, "2026-08-04")
        self.assertEqual(d.precision, dates.EXACT)

    def test_utc_normalisation_does_not_shift_day_wrongly(self):
        # 23:30 at -04:00 is 03:30 UTC the NEXT day; we normalise to UTC.
        d = dates.from_iso("2026-06-29T23:30:00-04:00", "f")
        self.assertEqual(d.value, "2026-06-30")

    def test_empty_and_garbage(self):
        for bad in ("", None, "not a date", "0000"):
            self.assertFalse(dates.from_iso(bad, "f").known)


class TestTimestamp(unittest.TestCase):
    def test_lever_milliseconds(self):
        # Verified live from Lever: createdAt 1711403416463 -> 2024-03-25
        d = dates.from_timestamp(1711403416463, "createdAt", "ms")
        self.assertEqual(d.value, "2024-03-25")
        self.assertEqual(d.precision, dates.EXACT)

    def test_community_seconds(self):
        d = dates.from_timestamp(1754611200, "date_posted", "s")
        self.assertEqual(d.precision, dates.EXACT)
        self.assertTrue(d.value.startswith("2025-08"))

    def test_wrong_unit_is_rejected_not_guessed(self):
        # Seconds value fed as ms lands in 1970 -> we refuse rather than emit it.
        self.assertFalse(dates.from_timestamp(1754611200, "x", "ms").known)

    def test_zero_and_none(self):
        self.assertFalse(dates.from_timestamp(0, "x").known)
        self.assertFalse(dates.from_timestamp(None, "x").known)


class TestRelative(unittest.TestCase):
    """The Workday bug: '30+ days ago' must NOT become an exact date."""

    def test_today(self):
        d = dates.from_relative("Posted Today", "postedOn", TODAY)
        self.assertEqual(d.value, "2026-08-08")
        self.assertEqual(d.precision, dates.APPROXIMATE)
        self.assertFalse(d.trusted)          # never earns a 🆕 badge

    def test_n_days_is_approximate(self):
        d = dates.from_relative("Posted 5 Days Ago", "postedOn", TODAY)
        self.assertEqual(d.value, "2026-08-03")
        self.assertEqual(d.precision, dates.APPROXIMATE)

    def test_open_ended_is_at_least_not_exact(self):
        d = dates.from_relative("Posted 30+ Days Ago", "postedOn", TODAY)
        self.assertEqual(d.precision, dates.AT_LEAST)
        self.assertFalse(d.trusted)
        self.assertIn("≥", d.label(TODAY))

    def test_months(self):
        d = dates.from_relative("Posted 3+ Months Ago", "postedOn", TODAY)
        self.assertEqual(d.precision, dates.AT_LEAST)

    def test_unparseable(self):
        self.assertFalse(dates.from_relative("Posted recently", "postedOn", TODAY).known)


class TestLabels(unittest.TestCase):
    def test_evergreen_requisition_is_flagged(self):
        # Palantir keeps Lever reqs open for years; honest date, misleading label.
        d = dates.from_timestamp(1456300000000, "createdAt", "ms")   # 2016
        self.assertTrue(d.is_evergreen(TODAY))
        self.assertTrue(d.label(TODAY).startswith("listed since"))

    def test_recent_date_renders_plainly(self):
        d = dates.from_iso("2026-08-04T10:00:00Z", "publishedAt")
        self.assertEqual(d.label(TODAY), "2026-08-04")

    def test_unknown_says_unknown(self):
        self.assertEqual(dates.UNKNOWN_DATE.label(TODAY), "unknown")


class TestPick(unittest.TestCase):
    def test_prefers_exact_over_approximate(self):
        exact = dates.from_iso("2026-08-01T00:00:00Z", "published")
        approx = dates.from_relative("Posted 2 Days Ago", "postedOn", TODAY)
        self.assertEqual(dates.pick(approx, exact).precision, dates.EXACT)

    def test_falls_back_when_first_unknown(self):
        got = dates.pick(dates.from_iso("", "a"), dates.from_iso("2026-01-01", "b"))
        self.assertEqual(got.field, "b")

    def test_all_unknown(self):
        self.assertFalse(dates.pick(dates.UNKNOWN_DATE, dates.UNKNOWN_DATE).known)


if __name__ == "__main__":
    unittest.main()
