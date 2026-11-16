"""두 조원이 합의해야 하는 유일한 알고리즘 인터페이스입니다.

새 알고리즘을 만들 때 Codec을 상속하고 encode/decode를 구현하세요.
GUI, 파일 헤더, 지표 계산, 일괄 실험 코드는 수정할 필요가 없습니다.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, BinaryIO, Mapping

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


@dataclass
class StreamEncoded:
    bit_length: int
    codes: dict[int, str] = field(default_factory=dict)
    code_note: str = ''


@dataclass
class StreamDecoded:
    codes: dict[int, str] = field(default_factory=dict)
    code_note: str = ''


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


    # 1GB 이상 파일용 선택적 인터페이스. 기존 encode/decode도 유지하여
    # 이전 코덱 및 작은 파일 테스트와의 호환성을 보장합니다.
    def streaming_metadata(self, frequencies: Mapping[int, int]) -> bytes | None:
        """None이면 구형 메모리 기반 인코더만 지원(작은 파일 한정)."""
        return None

    def encode_stream(self, source: BinaryIO, target: BinaryIO,
                      original_size: int, metadata: bytes,
                      progress: Progress = None) -> StreamEncoded:
        raise NotImplementedError('이 알고리즘은 스트리밍 압축을 지원하지 않습니다.')

    def decode_stream(self, source: BinaryIO, target: BinaryIO,
                      payload_bits: int, metadata: bytes, original_size: int,
                      progress: Progress = None) -> StreamDecoded:
        """기존 플러그인 호환: 작은 압축 파일에 한해 decode(bytes)를 실행."""
        max_legacy = 16 * 1024 * 1024
        payload_bytes = (payload_bits + 7) // 8
        if original_size > max_legacy or payload_bytes > max_legacy:
            raise ValueError('해당 알고리즘에는 대용량 스트리밍 복원 구현이 없습니다.')
        decoded = self.decode(source.read(payload_bytes), payload_bits, metadata,
                              original_size, progress)
        target.write(decoded.data)
        return StreamDecoded(decoded.codes, decoded.code_note)
