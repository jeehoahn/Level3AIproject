"""두 조원이 합의해야 하는 유일한 알고리즘 인터페이스입니다.

새 알고리즘을 만들 때 Codec을 상속하고 encode/decode를 구현하세요.
GUI, 파일 헤더, 지표 계산, 일괄 실험 코드는 수정할 필요가 없습니다.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

Progress = Callable[[float], None] | None  # 0.0 ~ 1.0


@dataclass
class PackedData:
    payload: bytes                    # 실제로 8비트씩 묶은 비트스트림
    bit_length: int                   # 패딩을 제외한 유효 비트 수
    metadata: bytes                   # 해당 알고리즘의 복원용 테이블/초기 상태
    codes: dict[int, str] = field(default_factory=dict)  # 보고용 코드표
    code_note: str = ""             # 적응형 코드는 시점에 따라 달라짐


@dataclass
class DecodedData:
    data: bytes
    codes: dict[int, str] = field(default_factory=dict)
    code_note: str = ""


class Codec(ABC):
    id: str             # ASCII 문자열, 파일 포맷에 기록됨. 기존 값 변경 금지!
    name: str
    description: str

    @abstractmethod
    def encode(self, data: bytes, progress: Progress = None) -> PackedData:
        """원본 바이트 -> 압축 페이로드 및 복원에 필요한 메타데이터"""

    @abstractmethod
    def decode(
        self, payload: bytes, bit_length: int, metadata: bytes,
        original_size: int, progress: Progress = None,
    ) -> DecodedData:
        """압축 페이로드 + 메타데이터 -> 원본 바이트"""
