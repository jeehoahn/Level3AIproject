"""버튼 없이 압축·복원을 실행할 때 자동 사전 검사가 수행되는지 검증."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core.engine import StageError, benchmark, compress_file, decompress_file
from core.failures import failed_record


class AutomaticPreflightTests(unittest.TestCase):
    def test_missing_input_fails_before_scanning_with_recordable_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'missing.txt'
            dest = root / 'result.huf'
            with patch('core.engine.scan_file', side_effect=AssertionError('스캔 실행 금지')):
                with self.assertRaises(StageError) as context:
                    compress_file(str(source), str(dest), 'static')
            exc = context.exception
            self.assertEqual(exc.stage, '압축 사전 검사')
            self.assertIsInstance(exc.original, FileNotFoundError)
            record = failed_record(str(source), str(dest), '', 'static', exc.stage, exc.original)
            self.assertEqual(record['status'], '실패')
            self.assertEqual(record['error_category'], '파일 없음')
            self.assertEqual(record['failure_stage'], '압축 사전 검사')
            self.assertFalse(dest.exists())

    def test_directory_is_not_valid_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(StageError) as context:
                compress_file(str(root), str(root / 'out.huf'), 'static')
            self.assertEqual(context.exception.stage, '압축 사전 검사')
            self.assertIsInstance(context.exception.original, IsADirectoryError)

    def test_same_input_output_path_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'in.txt'
            source.write_bytes(b'ABC' * 10)
            with self.assertRaises(StageError) as context:
                compress_file(str(source), str(source), 'static')
            self.assertEqual(context.exception.stage, '압축 사전 검사')
            self.assertIn('동일', str(context.exception))
            self.assertEqual(source.read_bytes(), b'ABC' * 10)

    def test_output_directory_blocked_before_reading_whole_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'in.txt'
            source.write_bytes(b'ABC' * 10)
            destination = root / 'output_folder'
            destination.mkdir()
            with patch('core.engine.scan_file', side_effect=AssertionError('스캔 실행 금지')):
                with self.assertRaises(StageError) as context:
                    compress_file(str(source), str(destination), 'static')
            self.assertEqual(context.exception.stage, '압축 사전 검사')
            self.assertIsInstance(context.exception.original, IsADirectoryError)

    def test_good_file_compress_and_restore_automatically_checked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = root / 'input.txt'
            original.write_bytes(('한글 테스트\n' * 30).encode())
            target = root / 'new_dir' / 'archive.huf'
            recovered = root / 'new_dir' / 'recovered.txt'
            compressed = compress_file(str(original), str(target), 'static')
            self.assertEqual(compressed['status'], '성공')
            self.assertIn('스트리밍', compressed['preflight_explanation'])
            self.assertIsNone(compressed['compression_rss_peak_mib'])  # 기본 메모리 측정 해제
            result, _ = decompress_file(str(target), str(recovered), compare_source=str(original))
            self.assertTrue(result['verified'])
            self.assertEqual(recovered.read_bytes(), original.read_bytes())

    def test_broken_header_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            invalid = root / 'bad.huf'
            invalid.write_bytes(b'broken header')
            output = root / 'recovered.txt'
            with self.assertRaises(StageError) as context:
                decompress_file(str(invalid), str(output))
            self.assertEqual(context.exception.stage, '복원 사전 검사')
            record = failed_record(str(invalid), str(invalid), str(output),
                                   '헤더 자동 식별', context.exception.stage,
                                   context.exception.original)
            self.assertEqual(record['error_category'], '파일 형식/압축 데이터 검증')
            self.assertFalse(output.exists())

    def test_missing_comparison_source_detected_before_decode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = root / 'input.txt'
            original.write_bytes(b'ABABABAB')
            archive = root / 'input.huf'
            compress_file(str(original), str(archive), 'static')
            original.unlink()
            with self.assertRaises(StageError) as context:
                decompress_file(str(archive), str(root / 'restored.txt'),
                                compare_source=str(original))
            self.assertEqual(context.exception.stage, '복원 사전 검사')
            self.assertIsInstance(context.exception.original, FileNotFoundError)

    def test_batch_failure_stage_and_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = root / 'good.txt'
            good.write_bytes(b'BANANA' * 25)
            bad = root / 'absent.txt'
            records, summary = benchmark([str(bad), str(good)], ['static'], str(root / 'out'))
            self.assertEqual(records[0]['failure_stage'], '압축 사전 검사')
            self.assertEqual(records[0]['status'], '실패')
            self.assertEqual(records[1]['status'], '성공')
            self.assertEqual(summary[0]['pass_count'], 1)

    def test_gui_has_no_manual_preflight_buttons_and_memory_is_off(self):
        ui_source = (Path(__file__).resolve().parents[1] / 'ui' / 'main_window.py').read_text('utf-8')
        self.assertNotIn('선택한 원본 파일 점검', ui_source)
        self.assertNotIn('enwik9 (1GB) 스트리밍 사전 점검', ui_source)
        self.assertNotIn('def _preflight_selected', ui_source)
        self.assertNotIn('def _preflight_gigabyte', ui_source)
        self.assertIn('self.memory_check.setChecked(False)', ui_source)
        self.assertIn('압축·복원 실행 시 입력 파일·출력 위치·압축 헤더를 자동 검사', ui_source)


if __name__ == '__main__':
    unittest.main()
