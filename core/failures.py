"""작업 실패의 상태/단계/원인을 UI·CSV·JSON에 남기는 공통 형식."""
from __future__ import annotations

from pathlib import Path
import errno

from algorithms.registry import CODECS
from core.preflight import InputLimitError


def failure_details(exc: Exception, stage: str) -> tuple[str, str]:
    """원인 확정 불가 시 '추정'이라고 구분합니다."""
    if isinstance(exc, InputLimitError):
        return '대용량/메모리 사전 검사', str(exc)
    if isinstance(exc, FileNotFoundError):
        return '파일 없음', str(exc)
    if isinstance(exc, PermissionError):
        return '파일 접근 권한', str(exc)
    if isinstance(exc, MemoryError):
        return '메모리 부족', '프로그램에 할당할 메모리가 부족합니다. 파일 크기와 메모리 사용량을 확인하세요.'
    if isinstance(exc, OSError) and getattr(exc, 'errno', None) == errno.ENOSPC:
        return '저장 공간 부족', str(exc)
    if isinstance(exc, OSError):
        return '파일 입출력', str(exc)
    if isinstance(exc, (ValueError, EOFError)):
        if stage.startswith('복원'):
            return '파일 형식/압축 데이터 검증', f'{exc} (파일 손상 또는 디코더 오류 가능)'
        return '압축 데이터/알고리즘 검사', str(exc)
    if isinstance(exc, (KeyError, LookupError)):
        return '알고리즘 식별 오류', str(exc)
    return '예상하지 못한 오류', f'{type(exc).__name__}: {exc}'


def failed_record(source: str, archive: str, restored: str, codec_id: str,
                  stage: str, exc: Exception) -> dict:
    category, message = failure_details(exc, stage)
    codec = CODECS.get(codec_id)
    algorithm = codec.name if codec else codec_id
    try:
        original_size = Path(source).stat().st_size if stage.startswith('압축') else None
    except OSError:
        original_size = None
    # 작업별 독립 오류 기록. 실패를 '복원 손실률 100%'로 왜곡하지 않음.
    return {
        'source': source, 'archive_path': archive, 'restored_path': restored,
        'algorithm': algorithm, 'status': '실패', 'failure_stage': stage,
        'error_type': type(exc).__name__, 'error_category': category,
        'error_message': message, 'verified': None, 'integrity_method': '검증 미완료',
        'original_bytes': original_size,
        'original_chars': None, 'compressed_bytes': None, 'header_bytes': None,
        'payload_bytes': None, 'payload_bits': None, 'symbol_types': None,
        'entropy': None, 'avg_payload_bits': None, 'avg_file_bits': None,
        'redundancy_bits': None, 'entropy_efficiency_pct': None, 'saving_pct': None,
        'size_ratio': None, 'header_share_pct': None, 'compression_sec': None,
        'decompression_sec': None, 'compression_algorithm_sec': None,
        'decompression_algorithm_sec': None, 'compression_mib_s': None,
        'decompression_mib_s': None,
        'compression_trace_peak_mib': None, 'compression_rss_peak_mib': None,
        'compression_rss_increase_mib': None, 'decompression_trace_peak_mib': None,
        'decompression_rss_peak_mib': None, 'decompression_rss_increase_mib': None,
        'mismatched_bytes': None, 'loss_pct': None,
        'code_note': f'{stage} 실패: {message}', 'symbols': [],
    }
