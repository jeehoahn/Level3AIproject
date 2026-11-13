"""GUI 설치 없이 실행: python -m unittest discover -s tests -v"""
from __future__ import annotations
import hashlib
import os
import random
import tempfile
from pathlib import Path
import unittest

from algorithms.registry import CODECS
from core.container_format import pack, unpack, STRUCT
from core.engine import compress_and_verify, compress_file, decompress_file, benchmark
from core.metrics import entropy, char_count, diff_bytes
from scripts.make_test_data import make_text


class AlgorithmTests(unittest.TestCase):
    def test_round_trip_byte_cases(self):
        rng = random.Random(2026)
        cases = [b'', b'Z', b'Z'*10000, b'AB'*5000, bytes(range(256)),
                 bytes(range(256))*50, '허프만 코딩 🎉'.encode('utf-8')*100,
                 bytes(rng.getrandbits(8) for _ in range(20000)),
                 bytes(0 if rng.random()<0.85 else rng.randrange(256) for _ in range(20000))]
        for codec in CODECS.values():
            for data in cases:
                with self.subTest(codec=codec.id, length=len(data), prefix=data[:10]):
                    enc = codec.encode(data)
                    self.assertEqual(len(enc.payload), (enc.bit_length+7)//8)
                    restored = codec.decode(enc.payload, enc.bit_length, enc.metadata, len(data))
                    self.assertEqual(restored.data, data)
                    file_data = pack(codec.id, data, enc.metadata, enc.payload, enc.bit_length)
                    archive = unpack(file_data)
                    self.assertEqual(archive.codec_id, codec.id)
                    self.assertEqual(archive.original_size, len(data))
                    self.assertEqual(archive.digest, hashlib.sha256(data).digest())

    def test_50k_characters_all_codecs(self):
        for i, style in enumerate(('uniform_ascii', 'skewed_ascii', 'repeated_pattern', 'korean_prose')):
            data = make_text(style, 50_000, 2026+i).encode('utf-8')
            self.assertEqual(char_count(data), 50_000)
            for codec in CODECS.values():
                with self.subTest(style=style, codec=codec.id):
                    enc = codec.encode(data)
                    dec = codec.decode(enc.payload, enc.bit_length, enc.metadata, len(data))
                    self.assertEqual(dec.data, data)

    def test_files_and_incorrect_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / 'src.txt'; src.write_text('한글 데이터! AAA\n'*500, encoding='utf-8')
            for codec_id in CODECS:
                destination = root / (codec_id + '.huf')
                restored = root / (codec_id + '.restored.txt')
                stats = compress_and_verify(str(src), str(destination), str(restored), codec_id)
                self.assertTrue(stats['verified'])
                self.assertEqual(stats['mismatched_bytes'], 0)
                self.assertEqual(stats['loss_pct'], 0)
                self.assertEqual(src.read_bytes(), restored.read_bytes())
                # 독립 복원에서는 원본과 직접 비교를 안 하므로 손실률은 N/A
                solo, _ = decompress_file(str(destination), str(root / (codec_id + '.solo.txt')))
                self.assertTrue(solo['verified'])
                self.assertIsNone(solo['loss_pct'])
                # 원본을 덮어쓰는 작업은 거절
                with self.assertRaises(ValueError):
                    compress_file(str(src), str(src), codec_id)
                corrupted = root / (codec_id + '.broken.huf')
                raw = bytearray(destination.read_bytes())
                raw[-1] ^= 0x80
                corrupted.write_bytes(raw)
                with self.assertRaises(ValueError):
                    decompress_file(str(corrupted), str(root / 'bad-output.txt'))
            invalid = root / 'invalid.huf'; invalid.write_bytes(b'not a huffman archive')
            with self.assertRaises(ValueError):
                decompress_file(str(invalid), str(root / 'should-not-exist.txt'))
            self.assertFalse((root / 'should-not-exist.txt').exists())

    def test_batch_and_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs = []
            for i, text in enumerate(('A'*1000, 'Hello, Huffman! '*1000, '가나다라마바사'*100)):
                path = root / f'text{i}.txt'; path.write_text(text, encoding='utf-8')
                inputs.append(str(path))
            records, summary = benchmark(inputs, list(CODECS), str(root / 'out'))
            self.assertEqual(len(records), 6)
            self.assertEqual(len(summary), 2)
            self.assertTrue(all(row['verified'] for row in records))
            self.assertTrue(all(row['pass_count'] == 3 for row in summary))
            self.assertAlmostEqual(entropy(b'A'*100), 0)
            self.assertAlmostEqual(entropy(b'AB'*100), 1)
            self.assertEqual(diff_bytes(b'ABC', b'ADC!')[0], 2)


if __name__ == '__main__':
    unittest.main()

# 자동 측정 과정에서 잘못 구현된 알고리즘이 손실을 발생시킬 때의 동작 테스트
class LossRateTests(unittest.TestCase):
    def test_loss_is_reported_without_saving_bad_output(self):
        from algorithms.base import Codec, PackedData, DecodedData
        from algorithms.registry import CODECS

        class DeliberatelyLossy(Codec):
            id = 'deliberately_lossy_test'
            name = '테스트용 불완전 복원'
            description = '검사 전용 (실제 압축 알고리즘 아님)'

            def encode(self, data, progress=None):
                return PackedData(data, len(data) * 8, b'', {})

            def decode(self, payload, bit_length, metadata, original_size, progress=None):
                # 첫 바이트 변경. 해시도 틀리고 원본 바이트도 다르게 만듦.
                return DecodedData(bytes([payload[0] ^ 1]) + payload[1:])

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.txt'; source.write_bytes(b'ABCDEF')
            archive = Path(td) / 'bad.huf'
            restore = Path(td) / 'restored.txt'
            CODECS[DeliberatelyLossy.id] = DeliberatelyLossy()
            try:
                result = compress_and_verify(str(source), str(archive), str(restore), DeliberatelyLossy.id)
                self.assertFalse(result['verified'])
                self.assertEqual(result['mismatched_bytes'], 1)
                self.assertAlmostEqual(result['loss_pct'], 100 / 6)
                self.assertFalse(restore.exists())
            finally:
                del CODECS[DeliberatelyLossy.id]
