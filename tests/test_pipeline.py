"""End-to-end pipeline behaviour with stubbed adapters."""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import adapters, dates, pipeline, render
from radar.http import NotFound, FetchError
from radar.models import Posting

TODAY = dt.date(2026, 8, 8)


def _p(company, title, url, source="greenhouse", loc="San Francisco, CA", jid="",
       posted=None, first_party=True):
    return Posting(company=company, title=title, url=url, source=source,
                   source_name=f"{source} · {company}", location=loc,
                   ats_job_id=jid, is_first_party=first_party,
                   posted=posted if posted is not None
                   else dates.from_iso("2026-08-05T00:00:00Z", "first_published"))


@adapters.register("_stub_ok", requires=("token",))
def _stub_ok(source):
    return [
        _p("Acme", "Software Engineer Intern, Summer 2027", "https://acme/1", jid="1"),
        _p("Acme", "New Grad Software Engineer", "https://acme/2", jid="2"),
        _p("Acme", "Marketing Intern", "https://acme/3", jid="3"),          # wrong discipline
        _p("Acme", "Senior Staff Engineer", "https://acme/4", jid="4"),     # wrong level
        _p("Acme", "SWE Intern Summer 2024", "https://acme/5", jid="5"),    # stale term
    ]


@adapters.register("_stub_dead", requires=("token",))
def _stub_dead(source):
    raise NotFound("HTTP 404", status=404, url="x")


@adapters.register("_stub_broken", requires=("token",))
def _stub_broken(source):
    raise ValueError("adapter blew up")


SOURCES = [
    {"name": "Acme", "ats": "_stub_ok", "token": "acme"},
    {"name": "Ghost", "ats": "_stub_dead", "token": "ghost"},
    {"name": "Broken", "ats": "_stub_broken", "token": "broken"},
    {"name": "NoAdapter", "ats": "_nope", "token": "x"},
    {"name": "BadConfig", "ats": "_stub_ok"},                # missing token
    {"name": "Off", "ats": "_stub_ok", "token": "z", "enabled": False},
]


class TestCollect(unittest.TestCase):
    def test_health_records_every_failure_mode(self):
        rows, health = pipeline.collect(SOURCES, log=lambda *a, **k: None)
        self.assertEqual(health["Acme"]["status"], "ok")
        self.assertEqual(health["Ghost"]["status"], "not_found")
        self.assertEqual(health["Broken"]["status"], "adapter_error")
        self.assertEqual(health["NoAdapter"]["status"], "error")
        self.assertEqual(health["BadConfig"]["status"], "error")
        self.assertEqual(health["Off"]["status"], "disabled")
        self.assertEqual(len(rows), 5)

    def test_one_bad_source_does_not_stop_the_run(self):
        rows, _h = pipeline.collect(SOURCES, log=lambda *a, **k: None)
        self.assertTrue(any(r.company == "Acme" for r in rows))


class TestRefine(unittest.TestCase):
    def test_filters_by_level_discipline_and_term(self):
        rows, _ = pipeline.collect(SOURCES, log=lambda *a, **k: None)
        kept, _rej = pipeline.refine(rows, TODAY)
        titles = {r.title for r in kept}
        self.assertIn("Software Engineer Intern, Summer 2027", titles)
        self.assertIn("New Grad Software Engineer", titles)
        self.assertNotIn("Marketing Intern", titles)
        self.assertNotIn("Senior Staff Engineer", titles)
        self.assertNotIn("SWE Intern Summer 2024", titles)

    def test_new_grad_can_be_excluded(self):
        rows, _ = pipeline.collect(SOURCES, log=lambda *a, **k: None)
        kept, _rej = pipeline.refine(rows, TODAY, include_levels=("intern",))
        self.assertTrue(all(r.level == "intern" for r in kept))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.open_rows, self.closed, self.health, self.metrics = pipeline.build(
            SOURCES, {"listings": [], "closed": []}, TODAY, log=lambda *a, **k: None)

    def test_produces_rows_and_metrics(self):
        self.assertEqual(len(self.open_rows), 2)
        self.assertEqual(self.metrics["roles_open"], 2)
        self.assertEqual(self.metrics["companies"], 1)
        self.assertEqual(self.metrics["sources_dead"], 1)
        self.assertEqual(self.metrics["by_level"], {"intern": 1, "new_grad": 1})
        self.assertEqual(self.metrics["dates_trusted_pct"], 100.0)

    def test_rows_are_json_serialisable(self):
        import json
        json.dumps(self.open_rows)   # must not raise

    def test_description_is_not_persisted(self):
        for r in self.open_rows:
            self.assertNotIn("description", r)

    def test_readme_renders(self):
        out = render.render(self.open_rows, self.metrics, self.health, TODAY)
        self.assertIn("internship-radar", out)
        self.assertIn("Software Engineer Intern", out)
        self.assertIn("New grad", out)
        # dead source is surfaced, not hidden
        self.assertIn("Ghost", out)

    def test_second_run_preserves_first_seen(self):
        prev = {"listings": self.open_rows, "closed": []}
        later = dt.date(2026, 8, 20)
        rows2, _c, _h, _m = pipeline.build(SOURCES, prev, later,
                                           log=lambda *a, **k: None)
        for r in rows2:
            self.assertEqual(r["first_seen"], "2026-08-08")
            self.assertEqual(r["last_seen"], "2026-08-20")


class TestOrdering(unittest.TestCase):
    """Trusted+recent first; guesses next; unknown last."""

    def _rows(self):
        return [
            {"company": "D", "posted": {"value": "", "precision": "unknown"}},
            {"company": "B", "posted": {"value": "2026-08-01", "precision": "exact"}},
            {"company": "C", "posted": {"value": "2026-08-07", "precision": "at_least"}},
            {"company": "A", "posted": {"value": "2026-08-06", "precision": "exact"}},
        ]

    def test_order(self):
        rows = sorted(self._rows(), key=pipeline._sort_key)
        self.assertEqual([r["company"] for r in rows], ["A", "B", "C", "D"])

    def test_untrusted_recent_never_outranks_trusted(self):
        """An 'at_least' floor dated today must not beat a real timestamp."""
        rows = sorted([
            {"company": "floor", "posted": {"value": "2026-08-08", "precision": "at_least"}},
            {"company": "real", "posted": {"value": "2026-07-01", "precision": "exact"}},
        ], key=pipeline._sort_key)
        self.assertEqual(rows[0]["company"], "real")


class TestOutageSafety(unittest.TestCase):
    def test_failed_board_does_not_close_its_roles(self):
        """End-to-end: the source that produced rows goes dark next run."""
        first, _c, _h, _m = pipeline.build(
            SOURCES, {"listings": [], "closed": []}, TODAY, log=lambda *a, **k: None)
        self.assertEqual(len(first), 2)

        broken = [{"name": "Acme", "ats": "_stub_dead", "token": "acme"}]
        rows, closed, _h, _m = pipeline.build(
            broken, {"listings": first, "closed": []}, TODAY, log=lambda *a, **k: None)
        # Acme's board 404'd — its roles must be carried forward, not closed.
        self.assertEqual(len(closed), 0)
        self.assertEqual(len(rows), 2)

    def test_healthy_board_dropping_a_role_eventually_closes_it(self):
        first, _c, _h, _m = pipeline.build(
            SOURCES, {"listings": [], "closed": []}, TODAY, log=lambda *a, **k: None)

        @adapters.register("_stub_one", requires=("token",))
        def _one(source):
            return [_p("Acme", "Software Engineer Intern, Summer 2027",
                       "https://acme/1", jid="1")]

        cfg = [{"name": "Acme", "ats": "_stub_one", "token": "acme"}]
        state = {"listings": first, "closed": []}
        for _ in range(2):      # needs CLOSE_AFTER_MISSES consecutive misses
            rows, closed, _h, _m = pipeline.build(
                cfg, state, TODAY, log=lambda *a, **k: None)
            state = {"listings": rows, "closed": closed}
        self.assertEqual(len(state["closed"]), 1)
        self.assertEqual(state["closed"][0]["title"], "New Grad Software Engineer")


class TestRenderHonesty(unittest.TestCase):
    def test_approximate_dates_never_get_new_badge(self):
        row = {"company": "N", "title": "SWE Intern", "url": "u", "category":
               "Software Engineering", "level": "intern", "term": "Summer 2027",
               "posted": {"value": TODAY.isoformat(), "precision": "at_least",
                          "field": "postedOn"}}
        self.assertFalse(render._is_new(row, TODAY))
        cell = render._date_cell(row, TODAY)
        self.assertIn("≥", cell)
        self.assertIn("day", cell)

    def test_exact_recent_date_gets_badge(self):
        row = {"posted": {"value": "2026-08-06", "precision": "exact", "field": "f"}}
        self.assertTrue(render._is_new(row, TODAY))

    def test_unknown_date_renders_as_unknown(self):
        row = {"posted": {"value": "", "precision": "unknown", "field": ""}}
        self.assertEqual(render._date_cell(row, TODAY), "Unknown")
        self.assertFalse(render._is_new(row, TODAY))


if __name__ == "__main__":
    unittest.main()
