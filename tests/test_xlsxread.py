"""The spreadsheet reader, against the encodings a real DOL file actually contains."""

import unittest

from bands import xlsxread

from .support import FIXTURE, HOSTILE


class TestReader(unittest.TestCase):
    def setUp(self):
        self.recs = {r["CASE_NUMBER"] + "|" + r["WAGE_RATE_OF_PAY_FROM"]: r
                     for r in xlsxread.records(FIXTURE)}

    def test_column_letters(self):
        self.assertEqual(xlsxread.col_index("A1"), 0)
        self.assertEqual(xlsxread.col_index("Z9"), 25)
        self.assertEqual(xlsxread.col_index("AA1"), 26)
        self.assertEqual(xlsxread.col_index("BX12345"), 75)

    def test_rich_text_shared_string_is_not_truncated(self):
        # F-001's JOB_TITLE is stored as two runs split mid-string. Reading only the first
        # <t> would give "Software" and the row would fall out of the software bucket.
        self.assertEqual(self.recs["F-001|100000"]["JOB_TITLE"], "Software Engineer")

    def test_inline_string_cell(self):
        # F-004's WAGE_UNIT_OF_PAY is t="inlineStr". Treating it as a shared-string index
        # would raise, or worse, silently return whatever string sits at index 0.
        self.assertEqual(self.recs["F-004|96.00"]["WAGE_UNIT_OF_PAY"], "Hour")

    def test_bare_numeric_cell(self):
        # F-005's wage has no t attribute at all, and must keep its literal form.
        self.assertEqual(self.recs["F-005|10000"]["WAGE_RATE_OF_PAY_FROM"], "10000")

    def test_formula_cell_with_cached_string(self):
        self.assertEqual(self.recs["F-009|80.00"]["EMPLOYER_NAME"], "Cedar Labs")

    def test_an_OMITTED_cell_does_not_shift_the_columns_after_it(self):
        # F-002 has no WAGE_RATE_OF_PAY_TO cell at all. Reading cells positionally would
        # slide WAGE_UNIT_OF_PAY into the TO slot, leave the unit blank, and drop the row.
        row = self.recs["F-002|200000"]
        self.assertEqual(row["WAGE_RATE_OF_PAY_TO"], "")
        self.assertEqual(row["WAGE_UNIT_OF_PAY"], "Year")

    def test_every_row_is_returned(self):
        self.assertEqual(len(list(xlsxread.records(FIXTURE))), 31)

    def test_header_names_are_intact(self):
        first = next(iter(xlsxread.records(FIXTURE)))
        for col in ("CASE_NUMBER", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
                    "WORKSITE_CITY", "EMPLOYER_NAME", "JOB_TITLE"):
            self.assertIn(col, first)

    def test_a_workbook_with_a_DTD_is_refused(self):
        # fixtures/hostile.xlsx carries a billion-laughs entity chain. The reader must
        # raise rather than expand it. defusedxml is not installable on this box, so this
        # is the check that the stdlib-only guard is real and not decorative.
        with self.assertRaises(xlsxread.XmlRejected):
            list(xlsxread.records(HOSTILE))

    def test_NEGATIVE_CONTROL_the_guard_does_not_refuse_the_normal_fixture(self):
        # A guard that refused everything would make the test above pass for free.
        self.assertEqual(len(list(xlsxread.rows(FIXTURE))), 32)


if __name__ == "__main__":
    unittest.main()
