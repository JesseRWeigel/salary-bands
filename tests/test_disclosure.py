"""Synthetic edge cases and the actual user-facing CLI/report contract."""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bands import aggregate, cli
from bands.disclosure import lines
from scripts import build_docs
from .support import ROOT

ROW = dict(VISA_CLASS='H-1B', CASE_STATUS='Certified', FULL_TIME_POSITION='Y',
           CASE_NUMBER='synthetic-1', WAGE_RATE_OF_PAY_FROM='100000', WAGE_UNIT_OF_PAY='YEAR',
           JOB_TITLE='Software engineer', EMPLOYER_NAME='Example', WORKSITE_CITY='Seattle',
           WORKSITE_STATE='WA', DECISION_DATE='45000')

def build(rows):
    with patch('bands.aggregate.records', return_value=iter(rows)):
        return aggregate.build(['synthetic'], [{'file': 'synthetic.xlsx'}])

class TestDisclosure(unittest.TestCase):
    def test_empty_cohort_does_not_invent_bands_or_ratio(self):
        a = build([])
        self.assertEqual(a['families'], [])
        page = build_docs.main(a)
        self.assertIn('No retained family', page)
        self.assertIn('undefined (zero shuffled spread)', page)
        self.assertNotIn('nan', page.lower())

    def test_small_cohort_is_descriptive_not_matched(self):
        a = build([ROW])
        self.assertEqual(a['families'][0]['n'], 1)
        self.assertEqual(a['employers_all_families'], [])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'aggregate.json'; p.write_text(json.dumps(a))
            out = subprocess.run([sys.executable, '-m', 'bands.cli', 'show', '--data', str(p),
                                  '--family', 'Software engineer'], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
        for expected in ('Small cohort', 'unadjusted', 'not matched', 'synthetic.xlsx', 'n=1',
                         'do not establish actual pay', 'not confidence intervals'):
            self.assertIn(expected, out)

    def test_missing_fields_counted_without_imputation(self):
        row = dict(ROW, JOB_TITLE='', EMPLOYER_NAME='', WORKSITE_CITY='', DECISION_DATE='')
        a = build([row])
        self.assertEqual(a['meta']['rows_kept'], 1)
        self.assertEqual(a['meta']['missing_fields_kept']['JOB_TITLE'], 1)
        self.assertIsNone(a['meta']['decision_date_min'])
        self.assertIsNone(a['meta']['offered_range']['median_top_over_bottom'])
        self.assertEqual(a['metros_all_families'], [])
        page = build_docs.main(a)
        self.assertIn('not observed pay', page)
        self.assertIn('No retained family', page)

    def test_missing_lower_wage_rejected_not_reported_as_zero(self):
        a = build([dict(ROW, WAGE_RATE_OF_PAY_FROM='')])
        self.assertEqual(a['meta']['rows_kept'], 0)
        self.assertEqual(a['meta']['filters']['wage_unconvertible'], 1)
        self.assertEqual(a['families'], [])

    def test_all_sources_and_historical_missingness_disclosed(self):
        a = json.loads((ROOT/'data/aggregates.json').read_text())
        page = build_docs.main(a)
        for source in a['meta']['sources']:
            self.assertIn(source['file'], page)
            self.assertIn(source['sha256'], page)
        self.assertIn('not recorded in this historical aggregate', page)
        self.assertIn('not a causal effect', page)
        self.assertNotIn('Every figure here understates', page)

    def test_same_wages_unmatched_groups_do_not_establish_premium(self):
        a = build([ROW, dict(ROW, CASE_NUMBER='synthetic-2', EMPLOYER_NAME='Different',
                            JOB_TITLE='Nurse', WORKSITE_CITY='Boston', WORKSITE_STATE='MA')])
        self.assertIn('No matched comparisons or causal effects', '\n'.join(lines(a['meta'])))
        self.assertEqual(a['employer_permutation_control']['real_p10_to_p90_spread_of_employer_medians'],0)
        self.assertIn('not evidence of equality', build_docs.main(a))

    def test_empty_cli_and_unknown_family(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'empty.json'; p.write_text(json.dumps(build([])))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(['show','--data',str(p)]),0)
            self.assertIn('No retained families',out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(['show','--data',str(p),'--family','missing']),2)
            self.assertIn('no family matching',err.getvalue())
