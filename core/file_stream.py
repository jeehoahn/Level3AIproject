"""청크 단위 통계/무결성 측정 및 입출력 래퍼. 원본 전체를 메모리에 적재하지 않습니다."""
from __future__ import annotations
from collections import Counter
import codecs
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024


class ByteStatistics:
    def __init__(self):
        self.frequencies: Counter[int] = Counter()
        self.hasher = hashlib.sha256()
        self.size = 0
        self.chars: int | None = 0
        self._decoder = codecs.getincrementaldecoder('utf-8')('strict')

    def update(self, chunk: bytes | bytearray) -> None:
        if not chunk:
            return
        self.size += len(chunk)
        self.hasher.update(chunk)
        # 긴 반복 입력의 카운터 증가를 C 레벨 count로 처리해 1GB 실험 가속.
        if chunk.count(chunk[0]) == len(chunk):
            self.frequencies[chunk[0]] += len(chunk)
        else:
            self.frequencies.update(chunk)
        if self.chars is not None:
            try:
                self.chars += len(self._decoder.decode(chunk, final=False))
            except UnicodeDecodeError:
                self.chars = None

    def finish(self) -> None:
        if self.chars is not None:
            try:
                self.chars += len(self._decoder.decode(b'', final=True))
            except UnicodeDecodeError:
                self.chars = None

    @property
    def digest(self) -> bytes:
        return self.hasher.digest()


def scan_file(path: str | Path) -> ByteStatistics:
    stats = ByteStatistics()
    with open(path, 'rb', buffering=CHUNK_SIZE) as handle:
        while chunk := handle.read(CHUNK_SIZE):
            stats.update(chunk)
    stats.finish()
    return stats


class CheckedSource:
    """이미 스캔한 원본이 인코딩 도중 바뀌었는지 검출합니다."""
    def __init__(self, source: BinaryIO):
        self.source = source
        self.hasher = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        result = self.source.read(size)
        self.size += len(result)
        self.hasher.update(result)
        return result

    @property
    def digest(self) -> bytes:
        return self.hasher.digest()


class VerifiedSink:
    """복원 스트림을 디스크에 기록하면서 통계, SHA-256, 원본과 직접 비교합니다."""
    def __init__(self, target: BinaryIO, compare_file: BinaryIO | None = None):
        self.target = target
        self.stats = ByteStatistics()
        self.compare_file = compare_file
        self.mismatches = 0

    def write(self, chunk: bytes | bytearray) -> int:
        if not chunk:
            return 0
        if self.compare_file is not None:
            expected = self.compare_file.read(len(chunk))
            # 정상 복원은 대부분 bytes C 레벨 비교 한 번으로 일치 확인.
            if expected != chunk:
                self.mismatches += sum(a != b for a, b in zip(expected, chunk))
                self.mismatches += abs(len(expected) - len(chunk))
        self.stats.update(chunk)
        return self.target.write(chunk)

    def finish(self) -> None:
        self.stats.finish()
        if self.compare_file is not None:
            while chunk := self.compare_file.read(CHUNK_SIZE):
                self.mismatches += len(chunk)
