"""
Undergraduate eligibility.

Several of these cases come straight from titles that appeared in a production
run and would have been mishandled by naive keyword matching.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar.eligibility import undergrad_eligible, is_undergrad_targeted


def ok(title, level="intern", desc=""):
    return undergrad_eligible(title, level, desc)[0]


def why(title, level="intern", desc=""):
    return undergrad_eligible(title, level, desc)[1]


class TestIncluded(unittest.TestCase):
    def test_core_undergrad_titles(self):
        for t in ["Software Engineer Intern", "Software Engineering Intern",
                  "SWE Intern", "Software Development Intern",
                  "Software Engineering Co-op", "CS Intern",
                  "Undergraduate Software Engineer",
                  "Backend Engineer Intern - Summer 2027"]:
            self.assertTrue(ok(t), f"{t} -> {why(t)}")

    def test_new_grad_swe_included(self):
        for t in ["New Grad Software Engineer", "University Graduate, Software",
                  "Software Engineer, Class of 2027"]:
            self.assertTrue(ok(t, "new_grad"), f"{t} -> {why(t, 'new_grad')}")

    def test_product_manager_intern_is_not_a_senior_role(self):
        """Real case: 'Manager' here is the job function, not a seniority."""
        self.assertTrue(ok("Product Manager Intern"))
        self.assertTrue(ok("AI Product Manager Intern - Content Ecosystem"))

    def test_lead_in_product_name_is_not_seniority(self):
        """Real case: 'Lead Ads' is a product surface, not a lead engineer."""
        self.assertTrue(ok("Machine Learning Engineer Intern - Lead Ads"))

    def test_undergrad_wording_overrides_soft_signals(self):
        self.assertTrue(ok("Undergraduate Research Intern, PhD Lab"))

    def test_bachelors_mention_is_fine(self):
        self.assertTrue(ok("Software Engineer Intern",
                           desc="Pursuing a Bachelor's degree in Computer Science."))


class TestExcludedByDegree(unittest.TestCase):
    def test_phd_titles(self):
        for t in ["PhD Research Scientist Intern",
                  "Quantitative Researcher – PhD Intern",
                  "Quantitative Research Intern, PhD (Summer 2027)",
                  "AI/LLM Network Research Intern - High Speed Network - PhD",
                  "Campus AI Researcher, PhD/Postdoc (Intern)",
                  "Quantitative Research Intern (PHD)"]:
            self.assertFalse(ok(t), t)
            self.assertEqual(why(t), "phd_required")

    def test_masters_titles(self):
        for t in ["Master's Data Science Internship",
                  "2027 Internship - Quantitative Researcher (Master or PhD)"]:
            self.assertFalse(ok(t), t)

    def test_graduate_student_wording(self):
        self.assertFalse(ok("Research Intern for Graduate Students"))

    def test_description_only_when_unambiguous(self):
        self.assertFalse(ok("Research Intern",
                            desc="Candidates must be enrolled in a PhD program."))
        # A passing mention of a PhD on the team is not a requirement.
        self.assertTrue(ok("Software Engineer Intern",
                           desc="You'll work alongside PhD researchers on ranking."))


class TestExcludedBySeniority(unittest.TestCase):
    def test_senior_new_grad_titles_rejected(self):
        for t in ["Senior Software Engineer", "Staff Software Engineer",
                  "Principal Engineer", "Engineering Manager",
                  "Director of Engineering", "Software Engineer III"]:
            self.assertFalse(ok(t, "new_grad"), t)

    def test_experience_requirement_rejected(self):
        self.assertFalse(ok("Software Engineer", "new_grad",
                            desc="Requires 5+ years of professional experience."))

    def test_small_experience_is_allowed(self):
        self.assertTrue(ok("Software Engineer, New Grad", "new_grad",
                           desc="0-2 years of experience."))

    def test_seniority_words_ignored_for_internships(self):
        """An internship is never a staff role, so these must survive."""
        for t in ["Intern, Staff Operations", "Senior Design Systems Intern",
                  "Product Manager Intern"]:
            self.assertTrue(ok(t, "intern"), t)


class TestHelpers(unittest.TestCase):
    def test_undergrad_targeted(self):
        self.assertTrue(is_undergrad_targeted("Undergraduate Research Assistant"))
        self.assertTrue(is_undergrad_targeted("Rising Junior Software Intern"))
        self.assertFalse(is_undergrad_targeted("Software Engineer Intern"))

    def test_reason_is_ok_when_eligible(self):
        self.assertEqual(why("Software Engineer Intern"), "ok")


if __name__ == "__main__":
    unittest.main()
