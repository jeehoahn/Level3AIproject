"""GUI와 알고리즘을 연결하는 유일한 엔진. 입출력과 벤치마크를 담당."""
from __future__ import annotations
import hashlib
from pathlib import Path
import os
import tempfile
import time
import csv
from typing import Callable
from algorithms.registry import get_codec
from core.container_format import pack, unpack
from core.metrics import summarize, averages, EXPORT_FIELDS, diff_bytes

Progress = Callable[[float, str], None] | None


class Cancelled(Exception):
    pass


def emit(progress: Progress, value: float, status: str):
    if progress:
        progress(min(1.0, max(0.0, value)), status)


def write_atomic(path: str | Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.huf_tmp_', delete=False) as f:
            temp_name = f.name
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def not_same_path(a: str | Path, b: str | Path):
    if Path(a).resolve() == Path(b).resolve():
        raise ValueError('입력과 출력 파일 경로가 동일하여 덮어쓸 위험이 있습니다.')


def compress_file(source: str, destination: str, codec_id: str,
                  progress: Progress = None) -> dict:
    not_same_path(source, destination)
    start = time.perf_counter()
    original = Path(source).read_bytes()
    emit(progress, .03, '원본 파일 읽기 / 빈도 분석')
    codec = get_codec(codec_id)
    packed = codec.encode(original, lambda f: emit(progress, .05 + .88*f, '압축 비트 생성'))
    output = pack(codec.id, original, packed.metadata, packed.payload, packed.bit_length)
    write_atomic(destination, output)
    elapsed = time.perf_counter() - start
    emit(progress, 1, '압축 파일 저장 완료')
    return summarize(original, len(output), len(output)-len(packed.payload), packed.bit_length,
                     codec.name, elapsed, None, packed.codes, packed.code_note,
                     None, source, destination)


def decompress_file(source: str, destination: str,
                    progress: Progress = None, *,
                    strict_integrity: bool = True) -> tuple[dict, bytes]:
    not_same_path(source, destination)
    start = time.perf_counter()
    raw = Path(source).read_bytes()
    archive = unpack(raw)
    codec = get_codec(archive.codec_id)
    emit(progress, .04, '컨테이너 헤더/메타데이터 검증')
    decoded = codec.decode(archive.payload, archive.payload_bits, archive.metadata,
                           archive.original_size, lambda f: emit(progress, .05 + .88*f, '원본 복원'))
    size_ok = len(decoded.data) == archive.original_size
    digest_ok = hashlib.sha256(decoded.data).digest() == archive.digest
    integrity_ok = size_ok and digest_ok
    if not integrity_ok and strict_integrity:
        raise ValueError('원본 크기 또는 SHA-256 무결성 검증 실패: 잘못된/손상된 압축 파일입니다.')
    if integrity_ok:
        write_atomic(destination, decoded.data)
    # strict_integrity=False는 비교실험 전용입니다. 손실 있는 결과를 측정하되,
    # 무결성에 실패한 데이터를 정상 복원 파일처럼 저장하지 않습니다.
    elapsed = time.perf_counter() - start
    emit(progress, 1, '복원 검증 ' + ('완료' if integrity_ok else '실패(출력 저장 안 함)'))
    record = summarize(decoded.data, len(raw), archive.header_size, archive.payload_bits,
                       codec.name, None, elapsed, decoded.codes, decoded.code_note,
                       None, destination, source, destination)
    # 원본이 별도로 주어지지 않은 독립 복원: 자기 자신과 비교해 손실률 0%라고
    # 주장하지 않음. SHA-256 해시 일치는 별도 지표로 표시합니다.
    record['verified'] = integrity_ok
    record['integrity_method'] = 'SHA-256 + 원본 길이 검증 (원본과 직접 비교하지 않음)'
    if not integrity_ok:
        record['restored_path'] = ''
    return record, decoded.data


def compress_and_verify(source: str, destination: str, restore_dest: str,
                        codec_id: str, progress: Progress = None) -> dict:
    not_same_path(source, restore_dest)
    not_same_path(destination, restore_dest)
    compressed = compress_file(source, destination, codec_id,
                               lambda f, msg: emit(progress, f*0.5, msg))
    restored, restored_bytes = decompress_file(
        destination, restore_dest, lambda f, msg: emit(progress, .5+f*.5, msg),
        strict_integrity=False)
    original = Path(source).read_bytes()
    mismatch, loss_rate = diff_bytes(original, restored_bytes)
    success = mismatch == 0 and restored['verified'] is True
    compressed.update({
        'restored_path': restore_dest if success else '',
        'decompression_sec': restored['decompression_sec'],
        'decompression_mib_s': restored['decompression_mib_s'],
        'mismatched_bytes': mismatch, 'loss_pct': loss_rate, 'verified': success,
        'integrity_method': '원본과 바이트 직접 비교 + SHA-256' +
                            ('' if success else ' (검증 실패: 복원 파일 미저장)'),
    })
    return compressed


def benchmark(paths: list[str], codec_ids: list[str], output_dir: str,
              progress: Progress = None) -> tuple[list[dict], list[dict]]:
    if not paths or not codec_ids:
        raise ValueError('실험 파일과 알고리즘을 모두 선택해야 합니다.')
    total = len(paths) * len(codec_ids)
    results: list[dict] = []
    done = 0
    for path in paths:
        if not Path(path).is_file():
            raise ValueError(f'파일이 존재하지 않습니다: {path}')
        for codec_id in codec_ids:
            suffix = hashlib.sha256(str(Path(path).resolve()).encode('utf-8')).hexdigest()[:8]
            name = f'{Path(path).stem}_{suffix}_{codec_id}'
            compressed = str(Path(output_dir) / (name + '.huf'))
            restored = str(Path(output_dir) / (name + '.restored' + Path(path).suffix))
            result = compress_and_verify(path, compressed, restored, codec_id,
                lambda f, msg, idx=done: emit(progress, (idx + f)/total,
                       f'[{idx+1}/{total}] {Path(path).name} · {codec_id}: {msg}'))
            results.append(result)
            done += 1
    emit(progress, 1, '모든 실험 완료')
    return results, averages(results)


def export_csv(records: list[dict], destination: str):
    """엑셀에서 한글이 정상 표시되도록 UTF-8 BOM을 사용합니다."""
    with open(destination, 'w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([label for _, label in EXPORT_FIELDS])
        for record in records:
            writer.writerow([record.get(key, '') if record.get(key) is not None else ''
                             for key, _ in EXPORT_FIELDS])
