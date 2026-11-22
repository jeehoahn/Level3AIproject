"""선택 파일의 경로, 내용 및 묶음 복원의 출력 처리 검증."""
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from core.bundle import benchmark_bundle, restore_bundle
from core.engine import compress_file


class BundleTests(unittest.TestCase):
    def test_bundle_continues_after_codec_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.bin'
            source.write_bytes(b'abc')
            records, _ = benchmark_bundle(
                [str(source)], ['missing-codec', 'static'], str(root / 'out'),
            )
            self.assertEqual(records[0]['status'], '실패')
            self.assertEqual(records[0]['original_bytes'], records[1]['original_bytes'])
            self.assertFalse(records[0]['archive_path'])
            self.assertTrue(records[1]['verified'])
            self.assertEqual(list(Path(records[1]['source']).iterdir()),
                             [Path(records[1]['archive_path']).parent])

    def test_bundle_roundtrip_preserves_paths_and_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = {'a/같은이름.txt': b'A\r\n', 'b/같은이름.txt': b'\0\xff',
                      'b/empty.bin': b''}
            for name, data in inputs.items():
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
            records, summary = benchmark_bundle(
                [str(root / name) for name in inputs], ['static', 'neural'], str(root / 'out'),
            )
            self.assertEqual(len(records), 2)
            self.assertEqual([r['pass_count'] for r in summary], [1, 1])
            self.assertEqual(records[0]['source'], records[1]['source'])
            for index, record in enumerate(records):
                self.assertTrue(record['verified'])
                self.assertEqual(record['mismatched_bytes'], 0)
                fresh = root / f'fresh-{index}'
                standalone = restore_bundle(record['archive_path'], str(fresh))
                self.assertTrue(standalone['verified'])
                for restored in (Path(record['restored_path']), fresh):
                    actual = {path.relative_to(restored).as_posix(): path.read_bytes()
                              for path in restored.rglob('*') if path.is_file()}
                    self.assertEqual(actual, inputs)
                with self.assertRaises(FileExistsError):
                    restore_bundle(record['archive_path'], str(fresh))

    def test_bundle_bad_entry_leaves_no_output_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            opaque_file, archive, target = (root / name for name in
                                            ('input.zip', 'files.hufbundle', 'restored'))
            with ZipFile(opaque_file, 'w') as payload:
                payload.writestr('valid.txt', b'first')
                payload.writestr('../outside.txt', b'invalid')
            compress_file(str(opaque_file), str(archive), 'static')
            with self.assertRaises(ValueError):
                restore_bundle(str(archive), str(target))
            self.assertFalse(target.exists())
            self.assertFalse((root / 'outside.txt').exists())
