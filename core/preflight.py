"""스트리밍 입력 사전 검사: 1GB 및 더 큰 입력을 전체 메모리 적재 없이 지원.

사전 통과는 실행 시간, 디스크 여유 또는 실제 실행 완료를 보장하지 않습니다.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import stat
import tempfile
from core.container_format import MAX_ORIGINAL
from core.performance import MIB, psutil

# 고정 청크 기반 처리. 프로그램/GUI 기본 메모리를 제외한 권장 여유 용량입니다.
RESERVE_BYTES = 128 * MIB


class InputLimitError(ValueError):
    """포맷 범위 또는 아주 낮은 RAM 가용량으로 실행 불가"""


@dataclass
class Preflight:
    input_bytes: int
    limit_bytes: int
    available_ram_bytes: int | None
    suggested_ram_bytes: int
    size_supported: bool
    ram_caution: bool
    can_attempt: bool
    explanation: str

    def as_dict(self):
        return asdict(self)


def check_input_size(size: int, *, available_ram_bytes: int | None = None) -> Preflight:
    if size < 0:
        raise ValueError('입력 크기는 음수일 수 없습니다.')
    if available_ram_bytes is None and psutil is not None:
        available_ram_bytes = int(psutil.virtual_memory().available)
    supported = size <= MAX_ORIGINAL
    caution = available_ram_bytes is not None and available_ram_bytes < RESERVE_BYTES
    # RAM 경고는 허용하되 명시적 제한 초과만 차단: 파일 크기와 메모리 요구량은 비례하지 않음.
    can_attempt = supported
    if not supported:
        explanation = '지원 불가: 64비트 컨테이너 원본 크기 필드를 초과했습니다.'
    elif caution:
        explanation = (
            f'스트리밍 지원(원본 {size:,} B): 파일 전체를 RAM에 올리지 않습니다. '
            f'다만 가용 RAM {available_ram_bytes/MIB:,.0f} MiB가 권장 여유 '
            f'{RESERVE_BYTES/MIB:.0f} MiB보다 적어 메모리 주의가 필요합니다. '
            '저장 공간과 실행 시간도 확인하세요.'
        )
    else:
        explanation = (
            f'스트리밍 입력 지원(원본 {size:,} B): 512MiB 제한을 제거했습니다. '
            '전체 원본을 RAM에 적재하지 않으며 청크 단위로 처리합니다. '
            '사전 검사는 실제 압축·복원 완료를 보장하지 않습니다. '
            '특히 Python FGK는 1GB 처리에 매우 오래 걸릴 수 있습니다.'
        )
    return Preflight(size, MAX_ORIGINAL, available_ram_bytes, RESERVE_BYTES,
                     supported, caution, can_attempt, explanation)


def check_input_file(path: str | Path) -> Preflight:
    """입력 파일을 통째로 읽지 않고 존재·일반 파일·열기 가능 여부와 크기 확인."""
    source = Path(path)
    info = source.stat()  # 존재/접근 오류는 그대로 전달하여 원인을 구분
    if not stat.S_ISREG(info.st_mode):
        raise IsADirectoryError(f'일반 파일이 아닙니다: {source}')
    with source.open('rb'):
        pass  # 실제 데이터는 읽지 않으며, 읽기 가능한지만 검사
    return check_input_size(info.st_size)


def require_input_ready(path: str | Path) -> Preflight:
    report = check_input_file(path)
    if not report.can_attempt:
        raise InputLimitError(report.explanation)
    return report


def require_output_ready(path: str | Path) -> None:
    """출력 폴더에 임시 파일을 만들 수 있는지 확인 (최종 파일은 건드리지 않음).

    이 검사는 실제 디스크 여유·작업 중 권한 변경까지 보장하지 않습니다.
    """
    target = Path(path)
    if target.is_dir():
        raise IsADirectoryError(f'출력 대상이 파일이 아닌 폴더입니다: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    # 실제 출력은 동일한 폴더에 임시 파일을 만든 뒤 원자적으로 교체합니다.
    # 이 경로를 미리 점검하면 1GB 입력 스캔을 마친 뒤에야 경로 오류를
    # 발견하는 일을 줄일 수 있습니다.
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.huf_check_', delete=True):
        pass
