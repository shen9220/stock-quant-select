"""Score recovery must never mix signal dates, models, paths or symbols."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from v3_scores import ResultScores, attach_scores, number

CALENDAR = ['2026-11-05', '2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11']


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def write(self, name, text):
        (self.directory / name).write_text(text, encoding='utf-8')

    def index(self, program='stock'):
        return ResultScores(program, self.directory, CALENDAR)

    def row(self, code='000001', day='2026-11-09', **fields):
        return dict(stock_code=code, date=day, **fields)

    def test_zero_negative_and_threshold_labels_are_scores(self):
        for value, expected in [(0, 0), ('0.00', 0), (-1.5, -1.5), ('43.0(未过门槛)', 43)]:
            self.assertEqual(number(value), expected)
        for value in (None, '', ' ', '—', 'NaN', float('inf'), True):
            self.assertIsNone(number(value))

    def test_top_rank_long_block_and_remainder_include_scores(self):
        self.write('20261106_recommendations.md', '买入日(D1): 2026-11-09\n排名 1: 000001 - 甲\n' +
                   '  操作说明\n' * 100 + '  综合得分: 14.12\n=== 其他未入选 ===\n  300001 - 乙: 综合得分 0.00 - 未入选\n')
        index = self.index()
        self.assertEqual(index.fields(self.row())['score'], 14.12)
        self.assertEqual(index.fields(self.row('300001'))['score'], 0)

    def test_missing_score_never_borrows_next_stock_or_section(self):
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n排名 2: 000002 - 乙\n 综合得分: 88\n=== 汇总 ===\n 综合得分: 99\n')
        index = self.index()
        self.assertEqual(index.fields(self.row()), {})
        self.assertEqual(index.fields(self.row('000002'))['score'], 88)

    def test_filename_limit_day_moves_to_next_exchange_session(self):
        self.write('20261106_slow_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 74.17\n')
        index = self.index('slow')
        self.assertEqual(index.fields(self.row())['score'], 74.17)
        self.assertEqual(index.fields(self.row(day='2026-11-06')), {})

    def test_explicit_buy_day_is_authoritative(self):
        self.write('20261105_recommendations.md', '买入日(D1): 2026-11-09\n排名 1: 000001 - 甲\n综合得分: 12\n')
        index = self.index()
        self.assertEqual(index.fields(self.row())['score'], 12)
        self.assertEqual(index.fields(self.row(day='2026-11-06')), {})

    def test_d2_uses_original_signal_date_not_shifted_entry(self):
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 12\n')
        self.write('20261109_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 99\n')
        row = self.row(day='2026-11-10', signal_date='2026-11-09')
        self.assertEqual(self.index().fields(row)['score'], 12)

    def test_programs_cannot_borrow_each_others_scores(self):
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 12\n')
        self.write('20261106_stock_updata_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 21.20\n')
        self.assertEqual(self.index().fields(self.row())['score'], 12)
        self.assertEqual(self.index('stock_updata').fields(self.row())['score'], 21.2)

    def test_v2_json_is_exact_on_path_and_prefers_total_over_components(self):
        self.write('2026-11-09_recommendations.json', json.dumps(dict(d1='2026-11-09', recommendations=[
            dict(code='000001', path='A', score_total=42, above_threshold=False, score_components={'x': 100}),
            dict(code='000001', path='B', score_total=65, above_threshold=True),
            dict(code='000002', path='A', trade_date='2026-11-10', score_total=99)])))
        self.write('2026-11-09_recommendations.md', '—— 路径A ——\n000001 甲 得分5(未过门槛)\n')
        index = self.index('stock_v2')
        self.assertEqual(index.fields(self.row(path='A'))['score'], 42)
        self.assertEqual(index.fields(self.row(path='B'))['score'], 65)
        self.assertIn('未过门槛', index.fields(self.row(path='A'))['score_label'])
        self.assertEqual(index.fields(self.row()), {})
        self.assertEqual(index.fields(self.row('000002', path='A')), {})

    def test_partial_json_write_still_allows_same_file_markdown(self):
        self.write('2026-11-09_recommendations.json', '{"recommendations":')
        self.write('2026-11-09_recommendations.md', '—— 路径B ——\n000001 甲 得分43.0(未过门槛)\n')
        self.assertEqual(self.index('stock_v2').fields(self.row(path='B'))['score'], 43)

    def test_same_trade_recorded_note_is_recovered_without_recomputation(self):
        row = self.row(notes='V2 B1 score=59.02 gap=0.604 thr=Y q=BACKFILL_OPEN', path='B')
        got = self.index('stock_v2').fields(row)
        self.assertEqual(got['score'], 59.02)
        self.assertEqual(got['score_source'], 'recorded_notes')

    def test_existing_zero_and_label_are_preserved_and_source_is_unmodified(self):
        rows = [self.row(score=0, notes='score=99'), self.row('000002', score='', score_label='43.0(未过门槛)')]
        before = json.dumps(rows)
        enriched = self.index().enrich(rows)
        self.assertEqual(enriched[0]['score'], 0)
        self.assertEqual(enriched[1]['score'], 43)
        self.assertEqual(json.dumps(rows), before)

    def test_empty_placeholders_never_receive_a_score(self):
        for row in (self.row('', notes='score=10'), self.row(is_board_placeholder=True, notes='score=10')):
            self.assertEqual(self.index().fields(row), {})

    def test_substitution_uses_final_symbol_and_drops_original_notes_and_label(self):
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 12\n排名 2: 000002 - 乙\n综合得分: 21\n')
        row = self.row('000002', signal_date='2026-11-09', substituted_from='000001', score=12,
                       score_label='12(过门槛)', notes='score=12')
        got = attach_scores({'rows': [row]}, [], self.index())['rows'][0]
        self.assertEqual(got['score'], 21)
        self.assertIsNone(got['score_label'])
        missing = dict(row, stock_code='000003')
        got = attach_scores({'rows': [missing]}, [], self.index())['rows'][0]
        self.assertIsNone(got['score'])

    def test_cache_changes_when_result_is_written(self):
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 12\n')
        before = self.index()
        self.write('20261106_recommendations.md', '排名 1: 000001 - 甲\n综合得分: 1234\n')
        after = self.index()
        self.assertNotEqual(before.signature, after.signature)
        self.assertEqual(after.fields(self.row())['score'], 1234)


if __name__ == '__main__':
    unittest.main(verbosity=2)
