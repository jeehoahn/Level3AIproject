"""HUF1 파일 스트리밍 압축/복원, 원자적 저장, 실험 비교, 오류·메모리 기록."""
from __future__ import annotations

import csv
from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from algorithms.registry import get_codec
from core.container_format import pack, header_bytes, inspect_stream
from core.failures import failed_record
from core.file_stream import CheckedSource, VerifiedSink, scan_file, CHUNK_SIZE
from core.metrics import summarize_stream, averages, EXPORT_FIELDS
from core.performance import profile_call
from core.preflight import require_input_ready, require_output_ready, check_input_size

Progress = Callable[[float, str], None] | None
LEGACY_MAX_BYTES = 16 * 1024 * 1024
RETURN_BYTES_MAX = 4 * 1024 * 1024


class StageError(ValueError):
    def __init__(self, stage: str, original: Exception):
        self.stage = stage
        self.original = original
        super().__init__(f'{stage} 단계 실패: {original}')


def emit(progress: Progress, value: float, status: str):
    if progress:
        progress(min(1.0, max(0.0, value)), status)


def not_same_path(a: str | Path, b: str | Path):
    if Path(a).resolve() == Path(b).resolve():
        raise ValueError('입력과 출력 파일 경로가 동일하여 덮어쓸 위험이 있습니다.')


def write_atomic(path: str | Path, data: bytes):
    """작은 비스트리밍 코덱 호환성을 위한 원자적 쓰기."""
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


def _temp_file(target: str | Path):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    return tempfile.NamedTemporaryFile(dir=target.parent, prefix='.huf_tmp_', mode='w+b',
                                       buffering=CHUNK_SIZE, delete=False)


def _set_profile(record: dict, prefix: str, profile):
    record[f'{prefix}_algorithm_sec'] = profile.seconds
    record[f'{prefix}_trace_peak_mib'] = profile.trace_peak_mib
    record[f'{prefix}_rss_peak_mib'] = profile.rss_peak_mib
    record[f'{prefix}_rss_increase_mib'] = profile.rss_increase_mib


def compress_file(source: str, destination: str, codec_id: str,
                  progress: Progress = None, *, measure_memory: bool = False) -> dict:
    emit(progress, 0.01, '자동 사전 검사: 입력 파일 / 출력 위치 / 스트리밍 용량')
    try:
        not_same_path(source, destination)
        preflight = require_input_ready(source)
        require_output_ready(destination)
    except Exception as exc:
        raise StageError('압축 사전 검사', exc) from exc
    codec = get_codec(codec_id)
    start = time.perf_counter()
    stats = scan_file(source)  # 항상 최대 CHUNK_SIZE씩 읽음; 빈도/문자/해시 수집
    emit(progress, .20, '원본 데이터 빈도·해시 계산 완료')
    metadata = codec.streaming_metadata(stats.frequencies)
    temp_name = None
    try:
        if metadata is None:
            # 실험용/외부 코덱의 기존 encode(bytes) 인터페이스 유지 (작은 파일 한정).
            if stats.size > LEGACY_MAX_BYTES:
                raise ValueError('이 알고리즘에는 대용량 스트리밍 압축 구현이 없습니다. '
                                 'encode_stream / streaming_metadata를 작성하세요.')
            data = Path(source).read_bytes()
            if len(data) != stats.size or hashlib.sha256(data).digest() != stats.digest:
                raise ValueError('압축 중 원본 파일이 변경되었습니다.')
            packed, profile = profile_call(
                lambda: codec.encode(data, lambda f: emit(progress, .20 + .70*f, '압축 비트 생성')),
                measure_memory=measure_memory)
            output = pack(codec.id, data, packed.metadata, packed.payload, packed.bit_length)
            write_atomic(destination, output)
            archive_size = len(output)
            header_size = archive_size - len(packed.payload)
            bit_length = packed.bit_length
            codes, code_note = packed.codes, packed.code_note
        else:
            # 메타데이터는 입력 빈도표만으로 미리 결정. 예약 헤더를 쓰고 끝난 뒤
            # payload_bits 필드만 패치하므로 1GB 페이로드 임시 중복 복사가 불필요함.
            placeholder = header_bytes(codec.id, stats.size, metadata, 0, stats.digest)
            with _temp_file(destination) as output:
                temp_name = output.name
                output.write(placeholder)
                with open(source, 'rb', buffering=CHUNK_SIZE) as raw_source:
                    checked_source = CheckedSource(raw_source)
                    encoded, profile = profile_call(
                        lambda: codec.encode_stream(
                            checked_source, output, stats.size, metadata,
                            lambda f: emit(progress, .20 + .70*f, '스트리밍 압축 중')),
                        measure_memory=measure_memory)
                    if checked_source.size != stats.size or checked_source.digest != stats.digest:
                        raise ValueError('압축 도중 원본 파일 내용/크기가 변경되었습니다.')
                bit_length = encoded.bit_length
                if bit_length < 0 or output.tell() != len(placeholder) + (bit_length+7)//8:
                    raise ValueError('인코더의 유효 비트 길이와 출력 바이트 수가 일치하지 않습니다.')
                output.seek(0)
                output.write(header_bytes(codec.id, stats.size, metadata,
                                          bit_length, stats.digest))
                output.flush()
                os.fsync(output.fileno())
                archive_size = output.seek(0, os.SEEK_END)
            os.replace(temp_name, destination)
            temp_name = None
            header_size = len(placeholder)
            codes, code_note = encoded.codes, encoded.code_note
        elapsed = time.perf_counter() - start
        result = summarize_stream(stats, archive_size, header_size, bit_length,
                                  codec.name, elapsed, None, codes, code_note,
                                  source, destination)
        result.update(status='성공', failure_stage='', error_type='', error_category='',
                      error_message='', preflight_explanation=preflight.explanation)
        _set_profile(result, 'compression', profile)
        emit(progress, 1.0, '압축 파일 저장 완료')
        return result
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _inspect_archive_before_loading(path: str | Path):
    """구형 내부 함수 이름 유지; 전체 파일은 읽지 않습니다."""
    archive = inspect_stream(path)
    preflight = check_input_size(archive.original_size)
    if not preflight.can_attempt:
        raise ValueError('복원 스트리밍 사전 검사: ' + preflight.explanation)
    return preflight


def decompress_file(source: str, destination: str, progress: Progress = None, *,
                    strict_integrity: bool = True, measure_memory: bool = False,
                    compare_source: str | None = None) -> tuple[dict, bytes | None]:
    """SHA-256 검증에 성공해야 출력 파일을 확정합니다.

    작은 복원본(4MiB 이하)은 두 번째 반환값에 bytes를 돌려주지만,
    큰 복원본은 메모리 보호를 위해 None을 반환합니다. 파일은 destination에 저장.
    """
    emit(progress, .01, '자동 사전 검사: 압축 파일 헤더 / 원본 크기 / 출력 위치')
    try:
        not_same_path(source, destination)
        require_input_ready(source)
        if compare_source is not None:
            not_same_path(compare_source, destination)
            require_input_ready(compare_source)
        archive = inspect_stream(source)
        preflight = check_input_size(archive.original_size)
        if not preflight.can_attempt:
            raise ValueError('복원 지원 범위 초과: ' + preflight.explanation)
        codec = get_codec(archive.codec_id)
        require_output_ready(destination)
    except Exception as exc:
        raise StageError('복원 사전 검사', exc) from exc
    start = time.perf_counter()
    temp_name = None
    comp_size = Path(compare_source).stat().st_size if compare_source else None
    try:
        with _temp_file(destination) as output:
            temp_name = output.name
            with (open(compare_source, 'rb', buffering=CHUNK_SIZE)
                  if compare_source else nullcontext(None)) as comparison:
                sink = VerifiedSink(output, comparison)
                with open(source, 'rb', buffering=CHUNK_SIZE) as archive_input:
                    archive_input.seek(archive.header_size)
                    decoded, profile = profile_call(
                        lambda: codec.decode_stream(
                            archive_input, sink, archive.payload_bits, archive.metadata,
                            archive.original_size,
                            lambda f: emit(progress, .05 + .85*f, '스트리밍 복원 중')),
                        measure_memory=measure_memory)
                sink.finish()
            output.flush()
            os.fsync(output.fileno())
        size_ok = sink.stats.size == archive.original_size
        digest_ok = sink.stats.digest == archive.digest
        same_bytes = compare_source is None or (sink.mismatches == 0 and comp_size == sink.stats.size)
        integrity_ok = size_ok and digest_ok and same_bytes
        if not integrity_ok and strict_integrity:
            raise ValueError('복원 검증 실패: 저장된 SHA-256/원본 크기 또는 원본 바이트 비교 불일치. '
                             '압축파일 손상이나 알고리즘 오류 가능성이 있습니다.')
        if integrity_ok:
            os.replace(temp_name, destination)
            temp_name = None
        elapsed = time.perf_counter() - start
        record = summarize_stream(sink.stats, archive.file_size, archive.header_size,
                                  archive.payload_bits, codec.name, None, elapsed,
                                  decoded.codes, decoded.code_note,
                                  compare_source or destination, source,
                                  destination if integrity_ok else '')
        record['verified'] = integrity_ok
        if compare_source is not None:
            record['mismatched_bytes'] = sink.mismatches
            record['loss_pct'] = sink.mismatches / max(1, comp_size, sink.stats.size) * 100
            record['integrity_method'] = '원본 바이트 직접 비교 + SHA-256 + 길이'
        else:
            record['integrity_method'] = 'SHA-256 + 원본 길이 (원본 직접 비교 없음)'
        record.update(status='성공' if integrity_ok else '실패',
                      failure_stage='' if integrity_ok else '복원 검증',
                      error_type='' if integrity_ok else 'IntegrityError',
                      error_category='' if integrity_ok else '복원 무결성',
                      error_message='' if integrity_ok else
                      'SHA-256, 원본 길이 또는 직접 바이트 비교 불일치. 출력 파일은 저장되지 않았습니다.',
                      preflight_explanation=preflight.explanation)
        _set_profile(record, 'decompression', profile)
        emit(progress, 1.0, '복원 검증 ' + ('완료' if integrity_ok else '실패'))
        restored_data = (Path(destination).read_bytes() if integrity_ok and
                         sink.stats.size <= RETURN_BYTES_MAX else None)
        return record, restored_data
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def compress_and_verify(source: str, destination: str, restore_dest: str,
                        codec_id: str, progress: Progress = None, *,
                        measure_memory: bool = False) -> dict:
    not_same_path(source, restore_dest)
    not_same_path(destination, restore_dest)
    try:
        compressed = compress_file(
            source, destination, codec_id,
            lambda f, msg: emit(progress, f*.5, msg), measure_memory=measure_memory)
    except StageError:
        raise
    except Exception as exc:
        raise StageError('압축', exc) from exc
    try:
        restored, _ = decompress_file(
            destination, restore_dest, lambda f, msg: emit(progress, .5+f*.5, msg),
            strict_integrity=False, measure_memory=measure_memory, compare_source=source)
    except StageError:
        raise
    except Exception as exc:
        raise StageError('복원', exc) from exc
    success = restored['verified'] is True
    compressed.update({
        'restored_path': restore_dest if success else '',
        'decompression_sec': restored['decompression_sec'],
        'decompression_algorithm_sec': restored['decompression_algorithm_sec'],
        'decompression_mib_s': restored['decompression_mib_s'],
        'decompression_trace_peak_mib': restored['decompression_trace_peak_mib'],
        'decompression_rss_peak_mib': restored['decompression_rss_peak_mib'],
        'decompression_rss_increase_mib': restored['decompression_rss_increase_mib'],
        'mismatched_bytes': restored['mismatched_bytes'],
        'loss_pct': restored['loss_pct'], 'verified': success,
        'status': '성공' if success else '실패',
        'failure_stage': '' if success else '복원 검증',
        'error_type': '' if success else 'IntegrityError',
        'error_category': '' if success else '원본 바이트 불일치 / 무결성 실패',
        'error_message': '' if success else (
            f"원본과 {restored['mismatched_bytes']:,}바이트 불일치"
            f"(손실률 {restored['loss_pct']:.6f}%). "
            'SHA-256/크기 검증도 확인하세요. 검증 실패 파일은 저장하지 않았습니다.'),
        'integrity_method': '원본과 바이트 직접 비교(스트리밍) + SHA-256 + 길이',
    })
    return compressed


def benchmark(paths: list[str], codec_ids: list[str], output_dir: str,
              progress: Progress = None, *, measure_memory: bool = False) -> tuple[list[dict], list[dict]]:
    if not paths or not codec_ids:
        raise ValueError('실험 파일과 알고리즘을 모두 선택해야 합니다.')
    total = len(paths) * len(codec_ids)
    results = []
    done = 0
    for path in paths:
        for codec_id in codec_ids:
            suffix = hashlib.sha256(str(Path(path).resolve()).encode('utf-8')).hexdigest()[:8]
            name = f'{Path(path).stem}_{suffix}_{codec_id}'
            compressed = str(Path(output_dir) / (name + '.huf'))
            restored = str(Path(output_dir) / (name + '.restored' + Path(path).suffix))
            try:
                result = compress_and_verify(path, compressed, restored, codec_id,
                    lambda f, msg, idx=done: emit(progress, (idx + f)/total,
                        f'[{idx+1}/{total}] {Path(path).name} · {codec_id}: {msg}'),
                    measure_memory=measure_memory)
            except Exception as exc:
                stage = exc.stage if isinstance(exc, StageError) else '압축·복원'
                cause = exc.original if isinstance(exc, StageError) else exc
                result = failed_record(path, compressed, restored, codec_id, stage, cause)
            results.append(result)
            done += 1
            emit(progress, done/total, f'[{done}/{total}] 완료 / 실패 기록 포함')
    emit(progress, 1.0, '모든 실험 완료 (실패 건도 기록됨)')
    return results, averages(results)


def export_csv(records: list[dict], destination: str):
    with open(destination, 'w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([label for _, label in EXPORT_FIELDS])
        for record in records:
            writer.writerow([record.get(key, '') if record.get(key) is not None else ''
                             for key, _ in EXPORT_FIELDS])
