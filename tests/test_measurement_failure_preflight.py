"""추가 기능: 성공/실패 기록, 원인, 메모리 계측, 대용량 읽기 전 점검."""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core.engine import compress_file, compress_and_verify, decompress_file, benchmark, export_csv
from core.performance import profile_call
from core.preflight import check_input_size, check_input_file, InputLimitError
from core.result_view import chart_rows, save_records, load_records


class NewRequirementsTests(unittest.TestCase):
    def test_preflight_1gb_without_allocating_1gb(self):
        with patch('core.preflight.psutil', None):
            enwik8 = check_input_size(100_000_000)
            enwik9 = check_input_size(1_000_000_000)
        self.assertTrue(enwik8.size_supported)
        self.assertTrue(enwik9.size_supported)
        self.assertTrue(enwik9.can_attempt)
        self.assertIn('512MiB 제한을 제거', enwik9.explanation)
        self.assertIn('스트리밍', enwik9.explanation)

    def test_preflight_low_memory(self):
        value = check_input_size(1_000_000_000, available_ram_bytes=50_000_000)
        self.assertTrue(value.size_supported)
        self.assertTrue(value.ram_caution)
        self.assertTrue(value.can_attempt)

    def test_large_sparse_file_preflight_without_allocating(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'large.txt'
            with src.open('wb') as f:
                f.truncate(1_000_000_000)  # sparse 파일: 실제 대량 데이터를 읽지 않음
            report = check_input_file(src)
            self.assertTrue(report.can_attempt)
            self.assertEqual(report.input_bytes, 1_000_000_000)

    def test_memory_profile_and_report(self):
        _, profile = profile_call(lambda: bytearray(200_000), measure_memory=True)
        self.assertGreaterEqual(profile.trace_peak_mib, 0.1)
        self.assertIsNotNone(profile.rss_peak_mib)
        self.assertGreaterEqual(profile.rss_peak_mib, profile.rss_start_mib)
        self.assertGreaterEqual(profile.rss_increase_mib, 0)
        _, time_only = profile_call(lambda: 42, measure_memory=False)
        self.assertIsNone(time_only.rss_peak_mib)

    def test_file_success_memory_and_failure_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'src.txt'
            src.write_bytes('안녕하세요 ' .encode('utf-8') * 250)
            archive = root / 'encoded.huf'
            restored = root / 'restored.txt'
            record = compress_and_verify(str(src), str(archive), str(restored), 'static',
                                         measure_memory=True)
            self.assertEqual(record['status'], '성공')
            self.assertEqual(record['loss_pct'], 0)
            self.assertIsNotNone(record['compression_trace_peak_mib'])
            self.assertIsNotNone(record['decompression_rss_peak_mib'])
            self.assertGreaterEqual(record['compression_sec'], record['compression_algorithm_sec'])
            self.assertEqual(src.read_bytes(), restored.read_bytes())
            broken = root / 'broken.huf'
            broken.write_bytes(archive.read_bytes()[:-2])
            with self.assertRaisesRegex(ValueError, '길이 불일치|파일 잘림'):
                decompress_file(str(broken), str(root / 'bad.txt'))
            self.assertFalse((root/'bad.txt').exists())

    def test_batch_continues_after_missing_file_and_exports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            okay = root / 'okay.txt'
            okay.write_bytes(b'ABBA'*30)
            missing = root / 'missing.txt'
            records, summary = benchmark([str(missing), str(okay)], ['static'], str(root/'out'),
                                         measure_memory=False)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]['status'], '실패')
            self.assertEqual(records[0]['error_category'], '파일 없음')
            self.assertEqual(records[1]['status'], '성공')
            self.assertEqual(summary[0]['fail_count'], 1)
            path = root/'records.csv'
            export_csv(records, str(path))
            with path.open('r', encoding='utf-8-sig', newline='') as f:
                lines = list(csv.DictReader(f))
            self.assertIn('파일 없음', lines[0]['오류 분류'])
            saved = root/'history.json'
            save_records(records, str(saved))
            self.assertEqual(load_records(str(saved))[0]['error_category'], '파일 없음')
            rows, omitted = chart_rows(records, 'compression_rss_peak_mib')
            self.assertEqual(len(rows), 0)  # 메모리 계측을 끈 경우 기록 제외
            self.assertEqual(omitted, 2)


if __name__ == '__main__':
    unittest.main()
