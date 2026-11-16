"""GUI 설치가 없어도 정렬, 누락 값 처리, 영속화 동작을 검증합니다."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest

from core.result_view import chart_rows, load_records, save_records


class ResultViewTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {'source': '/tmp/나.txt', 'algorithm': 'A', 'saving_pct': -8.0,
             'compression_sec': 0.12, 'original_bytes': 10,
             'compressed_bytes': 11, 'symbols': [{'symbol': '0x41', 'code': '0'}]},
            {'source': '/tmp/가.txt', 'algorithm': 'B', 'saving_pct': 62.5,
             'compression_sec': None, 'original_bytes': 100,
             'compressed_bytes': 40, 'symbols': []},
            {'source': '/tmp/다.txt', 'algorithm': 'A', 'saving_pct': 0.0,
             'compression_sec': 0.23, 'original_bytes': 100,
             'compressed_bytes': 100, 'symbols': []},
        ]

    def test_value_sort_and_negative_values(self):
        rows, omitted = chart_rows(self.records, 'saving_pct', 'value_desc')
        self.assertEqual([row['value'] for row in rows], [62.5, 0, -8])
        self.assertEqual(omitted, 0)
        rows, _ = chart_rows(self.records, 'saving_pct', 'value_asc')
        self.assertEqual([row['value'] for row in rows], [-8, 0, 62.5])

    def test_names_algorithms_and_missing_metrics(self):
        rows, omitted = chart_rows(self.records, 'compression_sec', 'name_asc')
        self.assertEqual([row['file'] for row in rows], ['나.txt', '다.txt'])
        self.assertEqual(omitted, 1)
        rows, omitted = chart_rows(self.records, 'saving_pct', 'value_desc', 'A')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['algorithm'] == 'A' for row in rows))
        self.assertEqual(omitted, 0)

    def test_reject_invalid_metric(self):
        with self.assertRaises(ValueError):
            chart_rows(self.records, 'not-a-metric')

    def test_save_load_with_code_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'huffman_record.json'
            save_records(self.records, str(path))
            restored = load_records(str(path))
            self.assertEqual(restored, self.records)
            self.assertEqual(restored[0]['symbols'][0]['code'], '0')
            self.assertIn('가.txt', path.read_text(encoding='utf-8'))

    def test_invalid_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text(json.dumps({'format': 'x', 'records': []}))
            with self.assertRaises(ValueError):
                load_records(str(path))


if __name__ == '__main__':
    unittest.main()
