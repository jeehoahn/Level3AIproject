"""GUI에 종속되지 않는 결과 저장 / 그래프 항목 정렬 유틸리티.

압축 알고리즘과 GUI를 독립적으로 유지하기 위해 Qt를 import하지 않습니다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


# key, label, unit, preferred precision
CHART_METRICS: tuple[tuple[str, str, str, int], ...] = (
    ('saving_pct', '용량 절감률 (헤더 포함)', '%', 3),
    ('compressed_bytes', '압축 파일 크기', 'B', 0),
    ('original_bytes', '원본 파일 크기', 'B', 0),
    ('size_ratio', '압축 배율 (원본 / 압축)', '×', 3),
    ('compression_sec', '압축 시간(전체)', 's', 6),
    ('compression_algorithm_sec', '압축 스트리밍 인코더 시간(청크 I/O 포함)', 's', 6),
    ('decompression_algorithm_sec', '복원 스트리밍 디코더 시간(청크 I/O 포함)', 's', 6),
    ('compression_trace_peak_mib', '압축 Python 추적 피크', 'MiB', 3),
    ('compression_rss_peak_mib', '압축 피크 RSS(샘플)', 'MiB', 3),
    ('compression_rss_increase_mib', '압축 RSS 증가 피크', 'MiB', 3),
    ('decompression_trace_peak_mib', '복원 Python 추적 피크', 'MiB', 3),
    ('decompression_rss_peak_mib', '복원 피크 RSS(샘플)', 'MiB', 3),
    ('decompression_rss_increase_mib', '복원 RSS 증가 피크', 'MiB', 3),
    ('decompression_sec', '복원 시간', 's', 6),
    ('compression_mib_s', '압축 처리량', 'MiB/s', 3),
    ('decompression_mib_s', '복원 처리량', 'MiB/s', 3),
    ('entropy', '정보 엔트로피', 'bit/byte', 3),
    ('avg_payload_bits', '평균 페이로드 비트 수', 'bit/byte', 3),
    ('avg_file_bits', '평균 파일 비트 수 (헤더 포함)', 'bit/byte', 3),
    ('redundancy_bits', '엔트로피 대비 비트 차이', 'bit/byte', 3),
    ('entropy_efficiency_pct', '엔트로피 대비 효율', '%', 3),
    ('header_bytes', '헤더 크기', 'B', 0),
    ('header_share_pct', '헤더 비중', '%', 3),
    ('payload_bits', '유효 데이터 비트 수', 'bit', 0),
    ('symbol_types', '바이트 종류 수', '종', 0),
    ('loss_pct', '복원 손실률', '%', 6),
    ('mismatched_bytes', '서로 다른 바이트 수', 'B', 0),
)
METRIC_INFO = {key: (label, unit, precision) for key, label, unit, precision in CHART_METRICS}
VALID_ORDERS = {'value_desc', 'value_asc', 'name_asc', 'name_desc'}


def chart_rows(records: list[dict], metric: str, order: str = 'value_desc',
               algorithm: str | None = None, *,
               source: str | None = None) -> tuple[list[dict], int]:
    """유효한 수치만 막대그래프에 표시. 숫자가 없는 결과는 별도 집계."""
    if metric not in METRIC_INFO:
        raise ValueError(f'지원하지 않는 그래프 지표: {metric}')
    if order not in VALID_ORDERS:
        raise ValueError(f'지원하지 않는 정렬 방식: {order}')
    rows: list[dict] = []
    omitted = 0
    for index, record in enumerate(records):
        if source is not None and record.get('source') != source:
            continue
        if algorithm and record.get('algorithm') != algorithm:
            continue
        raw = record.get(metric)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            omitted += 1
            continue
        file_name = Path(record.get('source') or '알 수 없는 파일').name
        rows.append({'record_index': index, 'file': file_name,
                     'algorithm': record.get('algorithm', ''), 'value': float(raw),
                     'source': record.get('source', ''),
                     'verified': record.get('verified'), 'run': index + 1})
    if order == 'value_desc':
        rows.sort(key=lambda row: (-row['value'], row['file'].casefold(), row['algorithm'], row['run']))
    elif order == 'value_asc':
        rows.sort(key=lambda row: (row['value'], row['file'].casefold(), row['algorithm'], row['run']))
    else:
        rows.sort(key=lambda row: (row['file'].casefold(), row['algorithm'], row['run']),
                  reverse=order == 'name_desc')
    return rows, omitted


def save_records(records: list[dict], path: str) -> None:
    """빈도/코드표까지 포함한 결과 기록을 재실행 없이 다시 열 수 있도록 저장."""
    document = {'format': 'huffman-results', 'version': 1, 'records': records}
    target = Path(path)
    temp = target.with_name(target.name + '.tmp')
    try:
        temp.write_text(json.dumps(document, ensure_ascii=False, indent=2,
                                   allow_nan=False), encoding='utf-8')
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()


def load_records(path: str) -> list[dict]:
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('format') != 'huffman-results' or document.get('version') != 1:
        raise ValueError('허프만 결과 기록 형식이 아니거나 지원하지 않는 버전입니다.')
    records = document.get('records')
    if not isinstance(records, list) or len(records) > 100_000:
        raise ValueError('결과 목록이 올바르지 않습니다.')
    for record in records:
        if not isinstance(record, dict) or not all(k in record for k in
                ('source', 'algorithm', 'symbols', 'original_bytes', 'compressed_bytes')):
            raise ValueError('결과 기록에 필수 정보가 누락되었습니다.')
        if not isinstance(record['symbols'], list):
            raise ValueError('코드표 형식이 잘못되었습니다.')
    return records
