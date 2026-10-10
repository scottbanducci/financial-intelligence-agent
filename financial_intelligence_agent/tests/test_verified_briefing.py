import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = json.loads((ROOT / 'src/weekly_briefing.ipynb').read_text())
HELPER_CODE = next(''.join(c['source']) for c in NOTEBOOK['cells'] if 'verified_helpers' in c.get('metadata', {}).get('tags', []))
ns = {}
exec(compile(HELPER_CODE, 'notebook_helpers', 'exec'), ns)
INPUT = json.loads((ROOT / 'fixtures/briefing_2026_10_10.json').read_text())
IDS = ['fed_balances', 'fed_reserve_changes', 'bank_balances', 'bank_cash_changes', 'overnight_rate_levels', 'treasury_and_overnight_spreads', 'evidence_limits']

class VerifiedBriefingTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(INPUT)
        self.candidates = ns['build_candidates'](self.data)
        self.selection = {'selected_ids': IDS.copy()}

    def test_verified_snapshot_values_dates_units(self):
        text, evidence = ns['validate_and_render'](self.selection, self.candidates)
        for expected in ['2026-10-07', '2026-09-30', '2026-10-08', '$6,747.6 billion', '$3,029.7 billion', '+$81.6 billion', '-$68.4 billion', '$19,883.2 billion', '-$12.0 billion', '17.0 basis points', '-1.0 basis points']:
            self.assertIn(expected, text)
        self.assertNotIn('170.0 basis points', text)
        self.assertNotIn('-10.0 basis points', text)
        self.assertEqual(len(evidence), 7)

    def test_rrp_remains_correct_in_billions_display(self):
        self.assertIn('$2.3 billion', self.candidates['fed_rrp_changes']['text'])
        self.assertIn('-$9.2 billion', self.candidates['fed_rrp_changes']['text'])

    def test_float_tails_accepted_and_not_displayed(self):
        self.assertIn('-3.0 basis points', self.candidates['overnight_spreads']['text'])
        self.assertNotIn('999999', str(self.candidates))

    def test_inconsistent_170_bp_input_rejected(self):
        self.data['rates']['dtb3_effr_spread_bps'] = 170
        with self.assertRaisesRegex(ValueError, 'Spread mismatch'):
            ns['build_candidates'](self.data)

    def test_inconsistent_minus_10_bp_input_rejected(self):
        self.data['rates']['sofr_effr_spread_bps'] = -10
        with self.assertRaisesRegex(ValueError, 'Spread mismatch'):
            ns['build_candidates'](self.data)

    def test_other_rate_scenarios_not_hardcoded(self):
        self.data['rates'].update(sofr=4.25, effr=4.00, iorb=4.10, dtb3=3.75,
            sofr_effr_spread_bps=25, sofr_iorb_spread_bps=15,
            effr_iorb_spread_bps=-10, dtb3_effr_spread_bps=-25)
        c = ns['build_candidates'](self.data)
        self.assertIn('15.0 basis points', c['overnight_spreads']['text'])
        self.assertIn('25.0 basis points', c['overnight_spreads']['text'])
        self.assertIn('-25.0 basis points', c['treasury_and_overnight_spreads']['text'])

    def test_model_cannot_inject_numbers_or_text(self):
        for key, value in [('text', '170 bp'), ('commentary', 'widest since mid-2023')]:
            bad = {**self.selection, key: value}
            with self.assertRaises(ValueError):
                ns['validate_and_render'](bad, self.candidates)

    def test_unknown_id_rejected(self):
        self.selection['selected_ids'][5] = 'spread_is_170_bp'
        with self.assertRaises(ValueError):
            ns['validate_and_render'](self.selection, self.candidates)

    def test_duplicate_and_wrong_group_rejected(self):
        for replacement in ['bank_balances', 'bank_funding_changes']:
            self.selection['selected_ids'][0] = replacement
            with self.assertRaises(ValueError):
                ns['validate_and_render'](self.selection, self.candidates)

    def test_missing_or_nonfinite_required_data_rejected(self):
        for invalid in [None, float('nan'), float('inf'), True, 'bad']:
            d = copy.deepcopy(INPUT)
            d['rates']['sofr'] = invalid
            with self.assertRaises(ValueError):
                ns['build_candidates'](d)

    def test_missing_optional_tbill_not_zero_filled(self):
        self.data['rates']['dtb3'] = None
        c = ns['build_candidates'](self.data)
        self.assertNotIn('treasury_and_overnight_spreads', c)
        self.selection['selected_ids'][5] = 'overnight_spreads'
        text, _ = ns['validate_and_render'](self.selection, c)
        self.assertIn('-3.0 basis points', text)

    def test_invalid_date_rejected(self):
        self.data['bank_balance_sheet']['observation_date'] = 'not-a-date'
        with self.assertRaises(ValueError):
            ns['build_candidates'](self.data)

    def test_string_and_block_responses(self):
        text = json.dumps(self.selection)
        for content in [text, [{'type':'reasoning','text':'not output'}, {'type':'text','text':text}]]:
            response = {'choices':[{'message':{'content':content}}]}
            self.assertEqual(ns['parse_selection'](ns['response_text'](response)), self.selection)

    def test_json_fence_and_malformed_response(self):
        self.assertEqual(ns['parse_selection']('```json\n'+json.dumps(self.selection)+'\n```'), self.selection)
        with self.assertRaises(ValueError):
            ns['parse_selection']('The spread is 170 basis points.')
        with self.assertRaises(ValueError):
            ns['response_text']({'choices':[{'message':{'content':[]}}]})

    def test_every_notebook_code_cell_compiles(self):
        for c in NOTEBOOK['cells']:
            if c['cell_type'] == 'code':
                compile(''.join(c['source']), c['id'], 'exec')

if __name__ == '__main__':
    unittest.main()
