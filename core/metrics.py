"""표, 엔트로피, 실제 파일 기준 압축률, 복원 정확도 집계."""
from __future__ import annotations
from collections import Counter
import math
from pathlib import Path


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    n = len(data)
    return -sum((count / n) * math.log2(count / n) for count in Counter(data).values())


def char_count(data: bytes) -> int | None:
    try:
        return len(data.decode('utf-8'))
    except UnicodeDecodeError:
        return None


def diff_bytes(original: bytes, restored: bytes) -> tuple[int, float]:
    mismatches = sum(a != b for a, b in zip(original, restored))
    mismatches += abs(len(original) - len(restored))
    return mismatches, mismatches / max(1, len(original), len(restored)) * 100


def symbol_display(b: int) -> str:
    representations = {9: r'\t', 10: r'\n', 13: r'\r', 32: 'SPACE'}
    if b in representations:
        return f'0x{b:02X} ({representations[b]})'
    if 33 <= b <= 126:
        return f'0x{b:02X} ({chr(b)})'
    return f'0x{b:02X}'


def symbol_rows(data: bytes, codes: dict[int, str]) -> list[dict]:
    freqs = Counter(data)
    n = len(data)
    result = []
    for sym, freq in sorted(freqs.items(), key=lambda x: (-x[1], x[0])):
        code = codes.get(sym, '—')
        result.append({'symbol': symbol_display(sym), 'byte': sym, 'frequency': freq,
                       'percentage': freq / n * 100 if n else 0,
                       'code': code, 'length': len(code) if code != '—' else None})
    return result


def summarize(original: bytes, archive_size: int, header_size: int,
              payload_bits: int, algorithm: str, comp_time: float | None,
              decomp_time: float | None, codes: dict[int, str], code_note: str,
              restored: bytes | None, source: str, archive: str, restored_file: str = '') -> dict:
    n = len(original)
    ent = entropy(original)
    avg_payload = payload_bits / n if n else 0.0
    avg_total = archive_size * 8 / n if n else 0.0
    if restored is None:
        mismatch_count = None
        loss_rate = None
        verified = None
    else:
        mismatch_count, loss_rate = diff_bytes(original, restored)
        verified = mismatch_count == 0
    return {
        'source': source, 'archive_path': archive, 'restored_path': restored_file,
        'algorithm': algorithm, 'original_bytes': n, 'original_chars': char_count(original),
        'compressed_bytes': archive_size, 'header_bytes': header_size,
        'payload_bytes': archive_size - header_size, 'payload_bits': payload_bits,
        'symbol_types': len(Counter(original)), 'entropy': ent,
        'avg_payload_bits': avg_payload, 'avg_file_bits': avg_total,
        'redundancy_bits': avg_payload - ent,
        'entropy_efficiency_pct': (ent / avg_payload * 100) if avg_payload else None,
        'saving_pct': (1 - archive_size / n) * 100 if n else None,
        'size_ratio': (n / archive_size) if archive_size else None,
        'header_share_pct': header_size / archive_size * 100 if archive_size else None,
        'compression_sec': comp_time, 'decompression_sec': decomp_time,
        'compression_mib_s': (n / 1048576 / comp_time) if comp_time and comp_time > 0 else None,
        'decompression_mib_s': (n / 1048576 / decomp_time) if decomp_time and decomp_time > 0 else None,
        'mismatched_bytes': mismatch_count, 'loss_pct': loss_rate, 'verified': verified,
        'code_note': code_note, 'symbols': symbol_rows(original, codes),
    }


EXPORT_FIELDS = [
    ('source', '원본 파일'), ('algorithm', '알고리즘'), ('original_bytes', '원본 크기(byte)'),
    ('original_chars', '문자 수(UTF-8)'), ('compressed_bytes', '압축 크기(byte)'),
    ('saving_pct', '용량 절감률(%)'), ('size_ratio', '원본/압축 배율'),
    ('header_bytes', '헤더 크기(byte)'), ('header_share_pct', '헤더 비중(%)'),
    ('payload_bits', '유효 페이로드 비트'), ('symbol_types', '고유 바이트 종류'),
    ('entropy', '엔트로피(bit/byte)'), ('avg_payload_bits', '페이로드 평균(bit/byte)'),
    ('avg_file_bits', '실제 파일 평균(bit/byte)'), ('redundancy_bits', '엔트로피 초과(bit/byte)'),
    ('entropy_efficiency_pct', '엔트로피 대비 효율(%)'),
    ('compression_sec', '압축 시간(s)'), ('decompression_sec', '복원 시간(s)'),
    ('compression_mib_s', '압축 처리량(MiB/s)'), ('decompression_mib_s', '복원 처리량(MiB/s)'),
    ('loss_pct', '복원 손실률(%)'), ('mismatched_bytes', '서로 다른 바이트 수'),
    ('verified', '복원 정확'), ('archive_path', '압축 파일'), ('restored_path', '복원 파일'),
]


def averages(records: list[dict]) -> list[dict]:
    group = {}
    for record in records:
        group.setdefault(record['algorithm'], []).append(record)
    keys = ('saving_pct', 'compression_sec', 'decompression_sec',
            'entropy', 'avg_payload_bits', 'avg_file_bits', 'loss_pct')
    output = []
    for algo, group_records in group.items():
        row = {'algorithm': algo, 'count': len(group_records),
               'pass_count': sum(r['verified'] is True for r in group_records)}
        for key in keys:
            values = [float(r[key]) for r in group_records if r[key] is not None]
            row[key] = sum(values) / len(values) if values else None
        output.append(row)
    return output
