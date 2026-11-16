"""스트리밍 경로 테스트: 구버전 호환, 청크, 메모리 기본 해제, 손상 검출."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from algorithms.registry import CODECS
from core.container_format import pack, unpack, inspect_stream, STRUCT, header_bytes
from core.engine import compress_file, compress_and_verify, decompress_file, RETURN_BYTES_MAX
from core.file_stream import CHUNK_SIZE
from core.preflight import check_input_size


class StreamingTests(unittest.TestCase):
    def test_streaming_roundtrip_crosses_chunks_all_codecs(self):
        # 서로 다른 청크의 UTF-8 멀티바이트 경계, 임의 바이트, 빈 파일
        import random
        randomizer = random.Random(7788)
        cases = [b'', b'X', b'Q'*5001, bytes(range(256))*200,
                 b'AB'*600_001,
                 '가나다💡'.encode()*35000,
                 bytes(randomizer.getrandbits(8) for _ in range(260_000))]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for codec_id in CODECS:
                for i, data in enumerate(cases):
                    with self.subTest(codec_id=codec_id, case=i):
                        src = base/'src.bin'; src.write_bytes(data)
                        archive = base/'coded.huf'; restored = base/'restored.bin'
                        result = compress_and_verify(str(src), str(archive), str(restored),
                                                     codec_id, measure_memory=False)
                        self.assertTrue(result['verified'])
                        self.assertEqual(result['mismatched_bytes'], 0)
                        self.assertEqual(data, restored.read_bytes())
                        self.assertIsNone(result['compression_trace_peak_mib'])
                        self.assertEqual(result['original_bytes'], len(data))
                        self.assertEqual(inspect_stream(archive).original_size, len(data))

    def test_version_1_archive_backward_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            data=('허프만 Huffman '*25).encode()
            for codec in CODECS.values():
                packed=codec.encode(data)
                arc=root/'v1.huf'
                arc.write_bytes(pack(codec.id, data, packed.metadata,
                                     packed.payload, packed.bit_length))
                result,_=decompress_file(str(arc), str(root/'restored.bin'))
                self.assertTrue(result['verified'])
                self.assertEqual((root/'restored.bin').read_bytes(),data)

    def test_new_archive_decodable_by_old_in_memory_reader(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            original=b'ABCCDDDDD'*300
            src=root/'src';src.write_bytes(original)
            for codec in CODECS.values():
                arc=root/'coded.huf'
                compress_file(str(src),str(arc),codec.id)
                parsed=unpack(arc.read_bytes())
                self.assertEqual(codec.decode(parsed.payload, parsed.payload_bits,
                                              parsed.metadata, parsed.original_size).data,original)

    def test_streaming_does_not_use_path_read_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/'big.txt'
            # > 단일 입출력 청크; 원본 파일 전체 읽기 API 호출 차단
            with src.open('wb') as f:
                f.write(b'a'*(CHUNK_SIZE+7))
            arc=root/'out.huf'; dest=root/'dest.bin'
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('전체 메모리 읽기 금지')):
                compress_file(str(src),str(arc),'static')
                # 작은 복원 파일은 호환 API 반환을 위해 read_bytes를 호출할 수 있으므로
                # 여기에서는 4MiB보다 큰 파일만 해당 금지 조건을 검사해야 함.
            result,_=decompress_file(str(arc),str(dest))
            self.assertTrue(result['verified'])

    def test_large_decode_return_none_avoids_oversized_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/'in'; src.write_bytes(b'A'*(RETURN_BYTES_MAX+1))
            arc=root/'out.huf';dest=root/'copy'
            compress_file(str(src),str(arc),'static')
            stat,ret=decompress_file(str(arc),str(dest))
            self.assertIsNone(ret)
            self.assertTrue(stat['verified'])
            self.assertEqual(hashlib.sha256(dest.read_bytes()).digest(), hashlib.sha256(src.read_bytes()).digest())

    def test_64bit_archive_length_and_1gb_preflight(self):
        self.assertTrue(check_input_size(1_000_000_000).can_attempt)
        self.assertTrue(check_input_size(1_500_000_000).can_attempt)
        data=header_bytes('static',1_000_000_000,bytes(256),1_000_000_000,b'\x00'*32)
        self.assertEqual(STRUCT.unpack(data[:STRUCT.size])[3],1_000_000_000)
        with tempfile.TemporaryDirectory() as td:
            arc=Path(td)/'malicious.huf'
            # 실제 원본 1GB라는 헤더는 파싱하지만 잘린 페이로드는 거부.
            arc.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'길이 불일치|파일 잘림'):
                inspect_stream(arc)

    def test_corrupt_payload_does_not_write_restored_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/'src';src.write_bytes(b'AB' * 20000)
            arc=root/'coded.huf';broken=root/'damaged.huf';dest=root/'bad.txt'
            compress_file(str(src),str(arc),'static')
            raw=bytearray(arc.read_bytes())
            raw[-5] ^= 0x40
            broken.write_bytes(raw)
            with self.assertRaises(ValueError):
                decompress_file(str(broken),str(dest))
            self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()
