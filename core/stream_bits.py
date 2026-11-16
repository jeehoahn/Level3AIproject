"""파일 기반 비트 스트림. 비트는 MSB부터 기록하며 메모리는 청크 크기로 제한됩니다."""
from __future__ import annotations
from typing import BinaryIO

CHUNK_SIZE = 256 * 1024


class StreamBitWriter:
    def __init__(self, output: BinaryIO, *, buffer_size: int = CHUNK_SIZE):
        self.output = output
        self.buffer = bytearray()
        self.buffer_size = buffer_size
        self.pending = 0
        self.pending_bits = 0
        self.bit_length = 0
        self.byte_length = 0

    def write(self, code: int, width: int) -> None:
        if width < 0 or code < 0 or code >= (1 << width):
            raise ValueError('잘못된 비트 코드 또는 길이')
        self.bit_length += width
        self.pending = (self.pending << width) | code
        self.pending_bits += width
        while self.pending_bits >= 8:
            self.pending_bits -= 8
            self.buffer.append((self.pending >> self.pending_bits) & 255)
            self.pending &= (1 << self.pending_bits) - 1
            if len(self.buffer) >= self.buffer_size:
                self._flush()

    def _flush(self) -> None:
        if self.buffer:
            self.output.write(self.buffer)
            self.byte_length += len(self.buffer)
            self.buffer.clear()

    def finish(self) -> int:
        if self.pending_bits:
            self.buffer.append((self.pending << (8 - self.pending_bits)) & 255)
            self.pending = 0
            self.pending_bits = 0
        self._flush()
        return self.bit_length


class StreamBitReader:
    """읽는 바이트 수와 유효 비트 수를 헤더 값으로 제한합니다."""
    def __init__(self, source: BinaryIO, bit_length: int, *, buffer_size: int = CHUNK_SIZE):
        if bit_length < 0:
            raise ValueError('음수 비트 길이')
        self.source = source
        self.bit_length = bit_length
        self.position = 0
        self.bytes_remaining = (bit_length + 7) // 8
        self.buffer_size = buffer_size
        self.buffer = b''
        self.index = 0
        self.active_byte = 0
        self.active_bits = 0

    def read_bit(self) -> int:
        if self.position >= self.bit_length:
            raise ValueError('압축 비트스트림이 중간에 끝났습니다.')
        if self.active_bits == 0:
            if self.index >= len(self.buffer):
                if self.bytes_remaining <= 0:
                    raise ValueError('압축 파일 페이로드가 잘렸습니다.')
                amount = min(self.bytes_remaining, self.buffer_size)
                self.buffer = self.source.read(amount)
                if len(self.buffer) != amount:
                    raise ValueError('압축 파일 페이로드가 잘렸습니다.')
                self.bytes_remaining -= len(self.buffer)
                self.index = 0
            self.active_byte = self.buffer[self.index]
            self.index += 1
            self.active_bits = 8
        self.active_bits -= 1
        self.position += 1
        return (self.active_byte >> self.active_bits) & 1

    def read(self, width: int) -> int:
        if width < 0:
            raise ValueError('음수 비트 길이')
        value = 0
        for _ in range(width):
            value = (value << 1) | self.read_bit()
        return value

    def remaining(self) -> int:
        return self.bit_length - self.position

    def finish(self) -> None:
        if self.remaining():
            raise ValueError('복원 후에도 처리되지 않은 비트가 남았습니다.')
        if self.active_bits and self.active_byte & ((1 << self.active_bits) - 1):
            raise ValueError('압축 데이터의 패딩 비트가 0이 아닙니다.')
