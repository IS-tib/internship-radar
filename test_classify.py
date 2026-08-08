"""Level / discipline / term classification."""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import classify

AUG_2026 = dt.date(2026, 8, 8)
FEB_2026 = dt.date(2026, 2, 10)


class TestLevel(unittest.TestCase):
    def test_intern_variants(self):
        for t in ["Software Engineer Intern", "SWE Internship - Summer 2027",
                  "Engineering Co-op", "Co-Op Software Developer",
                  "Data Science Interns"]:
            self.assertEqual(classify.classify_level(t), classify.INTERN, t)

    def test_does_not_match_internal_international_internet(self):
        for t in ["Internal Tools Engineer", "International Payments Engineer",
                  "Internet Infrastructure Engineer"]:
            self.assertIsNone(classify.classify_level(t), t)

    def test_new_grad_variants(self):
        for t in ["New Grad Software Engineer", "University Graduate, Software",
                  "Graduate Software Engineer", "Software Engineer, Class of 2027",
                  "Entry-Level Backend Developer", "Campus Hire - Engineering",
                  "Early Career Software Engineer"]:
            self.assertEqual(classify.classify_level(t), classify.NEW_GRAD, t)

    def test_recruiting_roles_excluded(self):
        for t in ["Manager, Early Career Programs", "University Recruiter",
                  "Head of Early Career Talent"]:
            self.assertIsNone(classify.classify_level(t), t)

    def test_internship_wins_over_grad_phrasing(self):
        self.assertEqual(
            classify.classify_level("Early Career Software Engineer Internship"),
            classify.INTERN)

    def test_senior_intern_still_counts(self):
        # "Senior" + internship is rare but real (e.g. PhD interns); keep it.
        self.assertEqual(classify.classify_level("PhD Research Intern"),
                         classify.INTERN)

    def test_unrelated(self):
        self.assertIsNone(classify.classify_level("Account Executive"))


class TestCategory(unittest.TestCase):
    def test_swe(self):
        for t in ["Software Engineer Intern", "Backend Engineer Intern",
                  "SDE Intern", "Gameplay Engineer Intern"]:
            self.assertEqual(classify.categorize(t), "Software Engineering", t)

    def test_data_ml(self):
        for t in ["Machine Learning Intern", "Data Scientist Intern",
                  "Research Scientist Intern, NLP", "Quantitative Research Intern"]:
            self.assertEqual(classify.categorize(t), "Data / ML / AI", t)

    def test_other_technical(self):
        for t in ["Security Engineer Intern", "SRE Intern", "FPGA Intern",
                  "Embedded Software Co-op"]:
            self.assertEqual(classify.categorize(t), "Other Technical", t)

    def test_quant_dev_is_software_engineering(self):
        # A quant developer at a trading firm is a SWE role; students looking
        # for software roles should find it under SWE, not a catch-all bucket.
        for t in ["Quantitative Developer Intern", "Quantitative Technologist Intern"]:
            self.assertEqual(classify.categorize(t), "Software Engineering", t)

    def test_pm(self):
        self.assertEqual(classify.categorize("Product Manager Intern"),
                         "Product Management")

    def test_non_technical_rejected(self):
        self.assertIsNone(classify.categorize("Marketing Intern"))


class TestTerm(unittest.TestCase):
    def test_explicit_term_not_inferred(self):
        label, prio, inferred = classify.classify_term(
            "SWE Intern, Summer 2027", None, AUG_2026)
        self.assertEqual(label, "Summer 2027")
        self.assertEqual(prio, 0)
        self.assertFalse(inferred)

    def test_missing_term_is_inferred_and_flagged(self):
        label, _prio, inferred = classify.classify_term(
            "Software Engineer Intern", None, AUG_2026)
        self.assertEqual(label, "Summer 2027")
        self.assertTrue(inferred)   # caller renders this with a "~"

    def test_past_year_dropped(self):
        self.assertIsNone(classify.classify_term(
            "SWE Intern Summer 2024", None, AUG_2026))

    def test_current_summer_dropped_after_it_started(self):
        self.assertIsNone(classify.classify_term(
            "SWE Intern Summer 2026", None, AUG_2026))

    def test_current_summer_kept_before_it_starts(self):
        got = classify.classify_term("SWE Intern Summer 2026", None, FEB_2026)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "Summer 2026")

    def test_season_without_year_infers_year(self):
        label, _p, inferred = classify.classify_term(
            "Fall Co-op, Software", None, AUG_2026)
        self.assertTrue(inferred)
        self.assertTrue(label.startswith("Fall"))

    def test_future_year_kept(self):
        got = classify.classify_term("SWE Intern Summer 2028", None, AUG_2026)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "Summer 2028")


if __name__ == "__main__":
    unittest.main()
