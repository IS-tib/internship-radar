"""Lifecycle: first_seen, last_seen, closure, and outage safety."""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import store

D1 = dt.date(2026, 8, 1)
D2 = dt.date(2026, 8, 2)
D3 = dt.date(2026, 8, 3)


def row(identity, source="Acme", **kw):
    d = {"identity": identity, "company": "Acme", "title": "SWE Intern",
         "source": source, "source_name": f"Greenhouse · {source}"}
    d.update(kw)
    return d


class TestReconcile(unittest.TestCase):
    def test_new_rows_get_first_seen(self):
        cur = [row("url:a")]
        open_rows, closed, stats = store.reconcile(cur, {"listings": [], "closed": []},
                                                   {"Acme"}, D1)
        self.assertEqual(open_rows[0]["first_seen"], "2026-08-01")
        self.assertEqual(open_rows[0]["last_seen"], "2026-08-01")
        self.assertEqual(stats["new"], 1)

    def test_first_seen_is_never_overwritten(self):
        prev = {"listings": [row("url:a", first_seen="2026-07-01",
                                 last_seen="2026-07-30", status="open")],
                "closed": []}
        open_rows, _c, stats = store.reconcile([row("url:a")], prev, {"Acme"}, D2)
        self.assertEqual(open_rows[0]["first_seen"], "2026-07-01")
        self.assertEqual(open_rows[0]["last_seen"], "2026-08-02")
        self.assertEqual(stats["still_open"], 1)

    def test_missing_once_is_not_closed(self):
        prev = {"listings": [row("url:a", first_seen="2026-07-01", misses=0,
                                 status="open")], "closed": []}
        open_rows, closed, stats = store.reconcile([], prev, {"Acme"}, D2)
        self.assertEqual(len(closed), 0)
        self.assertEqual(open_rows[0]["misses"], 1)

    def test_missing_twice_closes(self):
        prev = {"listings": [row("url:a", first_seen="2026-07-01", misses=1,
                                 status="open")], "closed": []}
        open_rows, closed, stats = store.reconcile([], prev, {"Acme"}, D3)
        self.assertEqual(len(open_rows), 0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["status"], "closed")
        self.assertEqual(closed[0]["closed_at"], "2026-08-03")
        self.assertEqual(stats["closed"], 1)

    def test_source_outage_never_closes_jobs(self):
        """A failing board must not be read as 'every role was filled'."""
        prev = {"listings": [row("url:a", first_seen="2026-07-01", misses=1,
                                 status="open")], "closed": []}
        # 'Acme' is NOT in the healthy set -> its absence proves nothing.
        open_rows, closed, stats = store.reconcile([], prev, set(), D3)
        self.assertEqual(len(closed), 0)
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0]["misses"], 1)   # unchanged

    def test_reopened_role_is_flagged(self):
        prev = {"listings": [],
                "closed": [row("url:a", first_seen="2026-06-01",
                               status="closed", closed_at="2026-07-20")]}
        open_rows, _c, stats = store.reconcile([row("url:a")], prev, {"Acme"}, D3)
        self.assertEqual(stats["reopened"], 1)
        self.assertEqual(open_rows[0]["first_seen"], "2026-06-01")
        self.assertEqual(open_rows[0]["status"], "open")

    def test_old_closed_rows_are_eventually_dropped(self):
        old = row("url:z", status="closed", closed_at="2026-01-01")
        recent = row("url:y", status="closed", closed_at="2026-08-01")
        prev = {"listings": [], "closed": [old, recent]}
        _open, closed, _s = store.reconcile([], prev, {"Acme"}, D3)
        keys = {c["identity"] for c in closed}
        self.assertIn("url:y", keys)
        self.assertNotIn("url:z", keys)


class TestLoad(unittest.TestCase):
    def test_missing_file(self):
        d = store.load("/nonexistent/path/listings.json")
        self.assertEqual(d["listings"], [])

    def test_corrupt_file_does_not_crash(self):
        p = "/tmp/_radar_corrupt.json"
        with open(p, "w") as f:
            f.write("{not json")
        try:
            d = store.load(p)
            self.assertEqual(d["listings"], [])
        finally:
            os.remove(p)




class TestMigration(unittest.TestCase):
    """The live listings.json is v2; loading it must not crash the first run."""

    V2 = {
        "updated": "2026-08-08T10:26:22+00:00",
        "count": 1,
        "listings": [{
            "company": "NVIDIA", "title": "SWE Intern",
            "location": "Santa Clara", "url": "https://x/1",
            "posted": "2026-07-09", "deadline": "", "source": "Workday",
            "key": "https://x/1", "category": "Software Engineering",
            "term": "Unspecified", "priority": 3,
            "first_seen": "2026-07-20", "date": "2026-07-09", "is_new": False,
        }],
    }

    def test_v2_rows_are_migrated(self):
        out = store.migrate(self.V2)
        r = out["listings"][0]
        self.assertEqual(out["schema"], store.SCHEMA_VERSION)
        self.assertEqual(r["identity"], "url:https://x/1")
        self.assertIsInstance(r["posted"], dict)
        # v2 could not tell a real timestamp from a rounded "30+ days ago", so a
        # migrated date must not be treated as trustworthy.
        self.assertEqual(r["posted"]["precision"], "approximate")
        self.assertEqual(r["first_seen"], "2026-07-20")
        self.assertNotIn("date", r)
        self.assertNotIn("is_new", r)

    def test_empty_date_becomes_unknown(self):
        v2 = {"listings": [{"key": "k", "posted": "", "deadline": ""}], "closed": []}
        r = store.migrate(v2)["listings"][0]
        self.assertEqual(r["posted"]["precision"], "unknown")

    def test_load_migrates_from_disk(self):
        import json as _json
        p = "/tmp/_radar_v2.json"
        with open(p, "w") as f:
            _json.dump(self.V2, f)
        try:
            data = store.load(p)
            self.assertEqual(data["schema"], store.SCHEMA_VERSION)
            self.assertIsInstance(data["listings"][0]["posted"], dict)
        finally:
            os.remove(p)

    def test_migrated_store_survives_reconcile_and_sort(self):
        from radar import pipeline as pl
        data = store.migrate(self.V2)
        open_rows, closed, _stats = store.reconcile([], data, {"NVIDIA"}, D2)
        open_rows.sort(key=pl._sort_key)   # this crashed before migration existed
        self.assertEqual(len(open_rows), 1)




class TestMigrationBackfill(unittest.TestCase):
    def test_level_backfilled_from_title(self):
        v2 = {"listings": [
            {"key": "a", "title": "Software Engineer Intern", "posted": "", "source": "Greenhouse"},
            {"key": "b", "title": "New Grad Software Engineer", "posted": "", "source": "Greenhouse"},
        ], "closed": []}
        out = store.migrate(v2)["listings"]
        self.assertEqual(out[0]["level"], "intern")
        self.assertEqual(out[1]["level"], "new_grad")

    def test_first_party_backfilled_from_source(self):
        v2 = {"listings": [
            {"key": "a", "title": "X Intern", "posted": "", "source": "Greenhouse"},
            {"key": "b", "title": "Y Intern", "posted": "", "source": "Community (Summer 2027)"},
        ], "closed": []}
        out = store.migrate(v2)["listings"]
        self.assertTrue(out[0]["is_first_party"])
        self.assertFalse(out[1]["is_first_party"])


if __name__ == "__main__":
    unittest.main()
