"""Identity, de-duplication, and the regressions the old scheme caused."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import dates
from radar.dedupe import deduplicate, text_similarity, primary_identity
from radar.models import Posting, normalize_url, norm_title, norm_company


def mk(company, title, url="", source="greenhouse", loc="", jid="",
       first_party=True, desc="", posted=None):
    return Posting(company=company, title=title, url=url, source=source,
                   location=loc, ats_job_id=jid, is_first_party=first_party,
                   description=desc,
                   posted=posted or dates.from_iso("2026-08-01", "f"))


class TestNormalisation(unittest.TestCase):
    def test_tracking_params_stripped_identity_kept(self):
        a = normalize_url("https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&utm_source=x")
        b = normalize_url("https://boards.greenhouse.io/acme/jobs/123?gh_jid=123")
        self.assertEqual(a, b)

    def test_trailing_slash_and_case(self):
        self.assertEqual(normalize_url("HTTPS://Jobs.Lever.co/Acme/abc/"),
                         normalize_url("https://jobs.lever.co/Acme/abc"))

    def test_title_noise_removed(self):
        self.assertEqual(norm_title("Software Engineer Intern, Summer 2027"),
                         norm_title("Software Engineer Internship (Summer 2027)"))

    def test_company_legal_suffixes(self):
        self.assertEqual(norm_company("Acme Labs, Inc."), norm_company("Acme"))


class TestDeduplicate(unittest.TestCase):
    def test_same_job_via_url_collapses(self):
        u = "https://boards.greenhouse.io/acme/jobs/1?gh_jid=1"
        rows = [mk("Acme", "SWE Intern", u),
                mk("Acme", "SWE Intern", u + "&utm_campaign=x")]
        out, stats = deduplicate(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(stats["merged"], 1)

    def test_multi_location_roles_are_preserved(self):
        """The old scheme keyed on (company, title) only and destroyed these."""
        rows = [mk("Acme", "SWE Intern", "https://x/1", loc="Seattle, WA", jid="1"),
                mk("Acme", "SWE Intern", "https://x/2", loc="New York, NY", jid="2"),
                mk("Acme", "SWE Intern", "https://x/3", loc="London, UK", jid="3")]
        out, _ = deduplicate(rows)
        self.assertEqual(len(out), 3)

    def test_cross_source_same_role_merges_and_first_party_wins(self):
        direct = mk("Acme", "Software Engineer Intern", "https://boards.greenhouse.io/acme/jobs/9",
                    loc="Seattle, WA", jid="9", first_party=True)
        feed = mk("Acme Inc.", "Software Engineer Internship", "https://simplify.jobs/p/9",
                  source="community", loc="Seattle, WA", first_party=False)
        out, stats = deduplicate([feed, direct])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].is_first_party)
        # The community URL is retained rather than thrown away.
        self.assertTrue(any("simplify" in m["url"] for m in out[0].merged_from))

    def test_near_duplicate_by_description(self):
        body = ("We are looking for a software engineering intern to join the "
                "platform team and build distributed systems at scale for our "
                "customers across the world using modern tooling every day. ") * 3
        rows = [mk("Acme", "SWE Intern", "https://x/1", loc="NYC", jid="1", desc=body),
                mk("Acme", "Software Engineering Intern - Platform", "https://x/2",
                   loc="NYC", jid="2", desc=body)]
        out, stats = deduplicate(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(stats["near_dupes"], 1)

    def test_different_companies_never_merge(self):
        rows = [mk("Acme", "SWE Intern", "https://a/1", jid="1"),
                mk("Globex", "SWE Intern", "https://b/1", jid="1", source="lever")]
        out, _ = deduplicate(rows)
        self.assertEqual(len(out), 2)

    def test_distinct_roles_same_company_survive(self):
        rows = [mk("Acme", "Backend Engineer Intern", "https://x/1", jid="1"),
                mk("Acme", "Machine Learning Intern", "https://x/2", jid="2")]
        out, _ = deduplicate(rows)
        self.assertEqual(len(out), 2)

    def test_empty_input(self):
        out, stats = deduplicate([])
        self.assertEqual(out, [])
        self.assertEqual(stats["output"], 0)


class TestSimilarity(unittest.TestCase):
    def test_identical(self):
        t = "the quick brown fox jumps over the lazy dog again and again today"
        self.assertGreater(text_similarity(t, t), 0.99)

    def test_unrelated(self):
        self.assertLess(text_similarity(
            "we build payment infrastructure for global commerce teams",
            "join our robotics perception team working on lidar sensors"), 0.2)

    def test_short_text_is_not_compared(self):
        self.assertEqual(text_similarity("hi", "hi"), 0.0)


class TestIdentity(unittest.TestCase):
    def test_url_preferred(self):
        p = mk("Acme", "SWE Intern", "https://x/1", jid="7")
        self.assertTrue(primary_identity(p).startswith("url:"))

    def test_falls_back_to_ats_id(self):
        p = mk("Acme", "SWE Intern", "", jid="7")
        self.assertTrue(primary_identity(p).startswith("ats:"))

    def test_falls_back_to_semantic(self):
        p = mk("Acme", "SWE Intern", "", jid="")
        self.assertTrue(primary_identity(p).startswith("cts:"))

    def test_identity_is_stable_across_runs(self):
        a = mk("Acme", "SWE Intern", "https://x/1?utm_source=a", jid="7")
        b = mk("Acme", "SWE Intern", "https://x/1?utm_source=b", jid="7")
        self.assertEqual(primary_identity(a), primary_identity(b))


if __name__ == "__main__":
    unittest.main()
