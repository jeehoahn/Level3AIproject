"""1GB 스트리밍 기능 확인용: 합성 0바이트 데이터로 압축/복원/원본 바이트 비교.

주의: 테스트 도중 압축+복원 출력으로 대략 1.2GB 이상의 디스크 여유가 필요합니다.
실제 enwik9 데이터가 아니며 성능 수치를 일반화해서는 안 됩니다.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import tempfile
from time import perf_counter
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.engine import compress_and_verify


def main():
    parser = argparse.ArgumentParser(description='대용량 허프만 스트리밍 왕복 검증')
    parser.add_argument('--size-bytes', type=int, default=1_000_000_000)
    args = parser.parse_args()
    if args.size_bytes < 0:
        parser.error('파일 크기는 음수일 수 없습니다.')
    with tempfile.TemporaryDirectory(prefix='huffman_large_stream_') as td:
        directory = Path(td)
        source = directory/'source_zeros.bin'
        with source.open('wb') as output:
            output.truncate(args.size_bytes)
        print('합성 0 바이트 파일 크기:', f'{args.size_bytes:,}B')
        print('주의: 실제 enwik9 내용과 압축 속도를 대표하지 않습니다.')
        start=perf_counter()
        record=compress_and_verify(str(source), str(directory/'archive.huf'),
                                   str(directory/'restored.bin'), 'static',
                                   measure_memory=False)
        print('검증 상태:', record['status'])
        print('복원 바이트 불일치:', record['mismatched_bytes'])
        print('압축 파일 크기:', f"{record['compressed_bytes']:,}B")
        print('압축 시간:', f"{record['compression_sec']:.3f}s")
        print('복원 시간:', f"{record['decompression_sec']:.3f}s")
        print('전체 시간:', f'{perf_counter()-start:.3f}s')
        if not record['verified']:
            raise SystemExit('검증 실패')


if __name__ == '__main__':
    main()
