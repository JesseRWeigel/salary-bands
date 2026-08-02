"""The unit conversion, and the negative controls that prove it is not guessing."""

import unittest

from bands import wage


class TestUnits(unittest.TestCase):
    def test_every_unit_annualises_by_its_own_factor(self):
        # Five filings of the same $150,000 job, one per unit the file actually uses.
        cases = [
            ("150000", "Year"),
            ("12500", "Month"),
            ("2884.6153846", "Week"),
            ("5769.2307692", "Bi-Weekly"),
            ("72.1153846", "Hour"),
        ]
        for amount, unit in cases:
            with self.subTest(unit=unit):
                self.assertAlmostEqual(wage.annualise(amount, unit).annual, 150000.0, delta=0.01)

    def test_hourly_and_yearly_twins_land_on_the_same_number(self):
        # The single most important property here. 96.00/hour and 199680/year are the same
        # offer, and any code that reads the amount without the unit will disagree.
        hourly = wage.annualise("96.00", "Hour").annual
        yearly = wage.annualise("199680", "Year").annual
        self.assertEqual(hourly, yearly)
        self.assertEqual(hourly, 199680.0)

    def test_NEGATIVE_CONTROL_units_are_not_interchangeable(self):
        # The control for the test above. If the factors were all 1, or all equal, the test
        # above would still pass on any of the five, because it feeds each unit a different
        # amount. So: one amount, four units, four different answers.
        # (Year is left out only because 4000/year falls under the federal-minimum floor.)
        values = {u: wage.annualise("4000", u).annual
                  for u in ("Month", "Week", "Bi-Weekly", "Hour")}
        self.assertNotIn(None, values.values(), values)
        self.assertEqual(len(set(values.values())), 4, values)
        self.assertEqual(len(set(wage.UNIT_FACTOR.values())), len(wage.UNIT_FACTOR))

    def test_aliases_resolve(self):
        for spelling in ("Hour", "HOUR", "hourly", " Hourly ", "H", "hr"):
            self.assertEqual(wage.canonical_unit(spelling), "HOUR", spelling)
        for spelling in ("Bi-Weekly", "biweekly", "BI WEEKLY", "bw"):
            self.assertEqual(wage.canonical_unit(spelling), "BI-WEEKLY", spelling)

    def test_unknown_and_blank_units_are_refused_not_assumed_yearly(self):
        for bad, reason in (("", "blank_unit"), ("Fortnight", "unknown_unit"),
                            ("per annum", "unknown_unit"), ("Daily", "unknown_unit")):
            got = wage.annualise("120000", bad)
            self.assertIsNone(got.annual, bad)
            self.assertEqual(got.reason, reason, bad)

    def test_unreadable_amounts_are_refused_with_the_right_reason(self):
        self.assertEqual(wage.annualise("", "Year").reason, "blank_wage")
        self.assertEqual(wage.annualise("   ", "Year").reason, "blank_wage")
        self.assertEqual(wage.annualise("N/A", "Year").reason, "nonnumeric_wage")
        self.assertEqual(wage.annualise("see attached", "Year").reason, "nonnumeric_wage")

    def test_formatted_amounts_parse(self):
        self.assertEqual(wage.parse_amount("150,000"), 150000.0)
        self.assertEqual(wage.parse_amount("$150000.50"), 150000.50)
        self.assertEqual(wage.parse_amount("(1000)"), -1000.0)

    def test_an_hourly_rate_typed_into_a_yearly_row_is_dropped(self):
        # 65 with unit Year is not a salary. Keeping it destroys any low percentile.
        got = wage.annualise("65", "Year")
        self.assertIsNone(got.annual)
        self.assertEqual(got.reason, "below_federal_minimum")
        self.assertEqual(wage.FLOOR, 7.25 * 2080)

    def test_the_floor_does_not_eat_a_real_low_salary(self):
        # Negative control for the floor: a genuinely low but legal full-time wage stays.
        self.assertEqual(wage.annualise("16000", "Year").reason, "ok")
        self.assertEqual(wage.annualise("7.50", "Hour").reason, "ok")

    def test_absurd_values_are_dropped_at_the_ceiling(self):
        self.assertEqual(wage.annualise("99999999", "Year").reason, "above_ceiling")
        self.assertEqual(wage.annualise("9000000", "Year").reason, "ok")


if __name__ == "__main__":
    unittest.main()
