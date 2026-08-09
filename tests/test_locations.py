"""US location detection, built against real strings seen in production."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import locations


class TestUS(unittest.TestCase):
    def test_state_abbreviations(self):
        for s in ["San Jose, CA", "Austin, TX", "Redmond, WA", "Ardmore, PA",
                  "McLean, VA", "Boston, MA", "New York, NY"]:
            self.assertIs(locations.is_us(s), True, s)

    def test_full_state_names(self):
        for s in ["Atlanta, Georgia, United States", "Austin, Texas",
                  "Seattle, Washington"]:
            self.assertIs(locations.is_us(s), True, s)

    def test_city_shorthand(self):
        for s in ["NYC", "SF", "LA", "Chicago", "Washington, D.C.", "Bay Area"]:
            self.assertIs(locations.is_us(s), True, s)

    def test_country_only(self):
        for s in ["United States", "USA", "US", "Chicago, United States"]:
            self.assertIs(locations.is_us(s), True, s)

    def test_reversed_ordering(self):
        # Real Workday-style string: "US, CA, Santa Clara"
        self.assertIs(locations.is_us("US, CA, Santa Clara"), True)

    def test_us_remote(self):
        for s in ["Remote in USA", "Remote in US", "Remote; US",
                  "Multiple Locations, United States", "Remote - United States"]:
            self.assertIs(locations.is_us(s), True, s)


class TestNonUS(unittest.TestCase):
    def test_clear_international(self):
        for s in ["London, UK", "Toronto, ON, Canada", "Singapore",
                  "London, United Kingdom", "Montreal, QC, Canada",
                  "Bangalore, India", "Tokyo, Japan", "Remote in Canada"]:
            self.assertIs(locations.is_us(s), False, s)

    def test_canada_ca_is_not_california(self):
        """'Ontario, CA' style strings must not be read as California."""
        self.assertIs(locations.is_us("Ontario, Canada"), False)
        self.assertIs(locations.is_us("Vancouver, BC, Canada"), False)

    def test_all_international_multi(self):
        self.assertIs(locations.is_us("London, UK; Paris, France"), False)


class TestAmbiguous(unittest.TestCase):
    def test_bare_remote_is_unknown_not_us(self):
        """The instruction case: a generic 'Remote' is not evidence of US."""
        self.assertIsNone(locations.is_us("Remote"))
        self.assertIsNone(locations.is_us("Anywhere"))

    def test_opaque_counts(self):
        for s in ["2 Locations", "6 Locations", "Multiple Locations", "TBD", ""]:
            self.assertIsNone(locations.is_us(s), s)

    def test_unknown_city_is_unknown(self):
        self.assertIsNone(locations.is_us("Zzyzx Valley"))


class TestMixed(unittest.TestCase):
    def test_any_us_component_qualifies(self):
        # A role open in Boston and London is reachable by a US undergraduate.
        self.assertIs(locations.is_us("Boston, MA; Seattle, WA"), True)
        self.assertIs(locations.is_us("London, UK; New York, NY"), True)
        self.assertIs(locations.is_us("San Jose, CA; Remote in USA"), True)

    def test_us_plus_unknown(self):
        self.assertIs(locations.is_us("San Francisco, CA; Remote"), True)


class TestNormalize(unittest.TestCase):
    def test_shorthand_expanded(self):
        self.assertEqual(locations.normalize("NYC"), "New York, NY")
        self.assertEqual(locations.normalize("SF"), "San Francisco, CA")

    def test_reversed_reordered(self):
        self.assertEqual(locations.normalize("US, CA, Santa Clara"), "Santa Clara, CA")

    def test_us_remote_labelled(self):
        self.assertEqual(locations.normalize("Remote in USA"), "Remote (US)")

    def test_multi_truncated(self):
        out = locations.normalize("Boston, MA; Seattle, WA; Austin, TX; Miami, FL")
        self.assertIn("+1 more", out)

    def test_empty(self):
        self.assertEqual(locations.normalize(""), "")

    def test_duplicates_collapsed(self):
        self.assertEqual(locations.normalize("NYC; New York City"), "New York, NY")


class TestRemoteFlag(unittest.TestCase):
    def test_detects_remote(self):
        self.assertTrue(locations.is_remote("Remote in USA"))
        self.assertTrue(locations.is_remote("Work from home"))
        self.assertFalse(locations.is_remote("San Jose, CA"))




class TestAccents(unittest.TestCase):
    """Accented spellings must classify the same as their plain forms."""

    def test_accented_international_cities(self):
        for s in ["Montréal", "Montréal, QC", "São Paulo, Brazil", "Zürich"]:
            self.assertIs(locations.is_us(s), False, s)

    def test_accented_us_city_still_us(self):
        self.assertIs(locations.is_us("San José, CA"), True)


class TestAdditionalCountries(unittest.TestCase):
    def test_newly_covered(self):
        for s in ["Belgrade, Serbia", "Belgrade", "Kyiv, Ukraine",
                  "Sofia, Bulgaria", "Karachi, Pakistan"]:
            self.assertIs(locations.is_us(s), False, s)


if __name__ == "__main__":
    unittest.main()
