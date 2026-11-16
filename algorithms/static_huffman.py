"""A 담당 예시: 빈도표 + 우선순위 큐 + 정적 정규형(canonical) 허프만 코딩.

메타데이터는 심볼 0..255의 코드 길이 배열(256바이트).
코드를 canonical 형식으로 재구성할 수 있으므로 트리 직렬화는 불필요합니다.
"""
from __future__ import annotations
from collections import Counter
from typing import Mapping, BinaryIO
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from .base import Codec, PackedData, DecodedData, Progress, StreamEncoded, StreamDecoded
from core.bitstream import BitReader, BitWriter
from core.stream_bits import StreamBitReader, StreamBitWriter
from core.file_stream import CHUNK_SIZE


@dataclass
class Node:
    symbol: int | None = None
    left: 'Node | None' = None
    right: 'Node | None' = None


def lengths_from_frequencies(freqs: Mapping[int, int]) -> list[int]:
    lengths = [0] * 256
    if not freqs:
        return lengths
    if len(freqs) == 1:
        lengths[next(iter(freqs))] = 1
        return lengths
    serial = count()
    heap = []
    for sym, freq in freqs.items():
        heappush(heap, (freq, sym, next(serial), Node(symbol=sym)))
    while len(heap) > 1:
        w1, min1, _, a = heappop(heap)
        w2, min2, _, b = heappop(heap)
        heappush(heap, (w1 + w2, min(min1, min2), next(serial), Node(left=a, right=b)))
    root = heap[0][3]
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if node.symbol is not None:
            lengths[node.symbol] = depth
        else:
            stack.append((node.right, depth + 1))
            stack.append((node.left, depth + 1))
    return lengths


def lengths_from_data(data: bytes) -> list[int]:
    return lengths_from_frequencies(Counter(data))


def canonical_codes(lengths: list[int]) -> dict[int, tuple[int, int]]:
    if len(lengths) != 256 or any(not isinstance(l, int) or l < 0 or l > 255 for l in lengths):
        raise ValueError('허프만 테이블의 코드 길이가 올바르지 않습니다.')
    entries = sorted((length, sym) for sym, length in enumerate(lengths) if length)
    codes = {}
    if len(entries) == 1 and entries[0][0] != 1:
        raise ValueError('단일 심볼의 허프만 코드 길이는 1이어야 합니다.')
    code = 0
    prev = 0
    for length, sym in entries:
        code <<= length - prev
        if code >= (1 << length):
            raise ValueError('허프만 테이블의 접두 코드 조건이 깨졌습니다.')
        codes[sym] = (code, length)
        code += 1
        prev = length
    return codes


def code_strings(codes: dict[int, tuple[int, int]]) -> dict[int, str]:
    return {sym: format(code, f'0{length}b') for sym, (code, length) in codes.items()}


class StaticHuffman(Codec):
    id = 'static'
    name = 'A · 정적 허프만 (힙 + 정규형)'
    description = '전체 파일 빈도표를 사용하며, 헤더에 256바이트 코드 길이표를 기록'

    def encode(self, data: bytes, progress: Progress = None) -> PackedData:
        lengths = lengths_from_data(data)
        codes = canonical_codes(lengths)
        writer = BitWriter()
        interval = max(1024, len(data) // 100)
        for index, byte in enumerate(data):
            writer.write(*codes[byte])
            if progress and index % interval == 0:
                progress(index / max(1, len(data)))
        if progress:
            progress(1.0)
        return PackedData(writer.finish(), writer.bit_length, bytes(lengths), code_strings(codes),
                          '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')

    def decode(self, payload: bytes, bit_length: int, metadata: bytes,
               original_size: int, progress: Progress = None) -> DecodedData:
        if len(metadata) != 256:
            raise ValueError('정적 허프만 코드 길이표는 정확히 256바이트여야 합니다.')
        codes = canonical_codes(list(metadata))
        if (original_size == 0) != (len(codes) == 0):
            raise ValueError('원본 길이와 코드표가 일치하지 않습니다.')
        if original_size and not codes:
            raise ValueError('압축 파일에 코드표가 없습니다.')
        reader = BitReader(payload, bit_length)
        # (길이, 코드) -> 바이트. 코드 길이가 달라도 접두 관계는 생기지 않습니다.
        lookup = {(width, code): byte for byte, (code, width) in codes.items()}
        max_len = max((width for _, width in codes.values()), default=0)
        restored = bytearray()
        interval = max(1024, original_size // 100)
        while len(restored) < original_size:
            value = 0
            for width in range(1, max_len + 1):
                value = (value << 1) | reader.read_bit()
                byte = lookup.get((width, value))
                if byte is not None:
                    restored.append(byte)
                    break
            else:
                raise ValueError('허프만 테이블로 해석할 수 없는 코드입니다.')
            if progress and len(restored) % interval == 0:
                progress(len(restored) / max(1, original_size))
        if reader.remaining():
            raise ValueError('복원 후에도 처리되지 않은 비트가 남았습니다.')
        if progress:
            progress(1.0)
        return DecodedData(bytes(restored), code_strings(codes),
                           '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')


    def streaming_metadata(self, frequencies: Mapping[int, int]) -> bytes:
        return bytes(lengths_from_frequencies(frequencies))

    def encode_stream(self, source: BinaryIO, target: BinaryIO, original_size: int,
                      metadata: bytes, progress: Progress = None) -> StreamEncoded:
        if len(metadata) != 256:
            raise ValueError('정적 허프만 코드 길이표 크기 오류')
        codes = canonical_codes(list(metadata))
        # 단일 심볼 최적화: 유효 코드가 항상 0 한 비트이므로
        # 1GB 반복 입력도 Python에서 10억 번 비트 쓰기 루프를 돌지 않습니다.
        if len(codes) == 1:
            symbol = next(iter(codes))
            processed = 0
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                if chunk.count(symbol) != len(chunk):
                    raise ValueError('빈도표 계산 이후 원본 데이터 내용이 변경되었습니다.')
                processed += len(chunk)
                if progress:
                    progress(processed / max(1, original_size) * .8)
            remaining = (processed + 7) // 8
            zeros = bytes(CHUNK_SIZE)
            while remaining:
                size = min(remaining, len(zeros))
                target.write(zeros[:size])
                remaining -= size
            if progress:
                progress(1.0)
            return StreamEncoded(processed, code_strings(codes),
                                 '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')
        writer = StreamBitWriter(target)
        processed = 0
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
            for byte in chunk:
                writer.write(*codes[byte])
            processed += len(chunk)
            if progress:
                progress(processed / max(original_size, 1))
        bits = writer.finish()
        if progress:
            progress(1.0)
        return StreamEncoded(bits, code_strings(codes),
                             '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')

    def decode_stream(self, source: BinaryIO, target: BinaryIO,
                      payload_bits: int, metadata: bytes, original_size: int,
                      progress: Progress = None) -> StreamDecoded:
        if len(metadata) != 256:
            raise ValueError('정적 허프만 코드 길이표는 정확히 256바이트여야 합니다.')
        codes = canonical_codes(list(metadata))
        if (original_size == 0) != (len(codes) == 0):
            raise ValueError('원본 길이와 코드표가 일치하지 않습니다.')
        if len(codes) == 1:
            # 한 글자만 존재하는 입력의 정규형 허프만 코드는 0(1비트).
            if payload_bits != original_size:
                raise ValueError('단일 심볼 압축 비트 길이가 원본 크기와 다릅니다.')
            remaining = (payload_bits + 7) // 8
            while remaining:
                data = source.read(min(CHUNK_SIZE, remaining))
                if not data or any(data):
                    raise ValueError('단일 심볼 비트스트림이 손상되었거나 잘렸습니다.')
                remaining -= len(data)
            symbol = next(iter(codes))
            block = bytes([symbol]) * CHUNK_SIZE
            remaining = original_size
            while remaining:
                size = min(CHUNK_SIZE, remaining)
                target.write(block[:size])
                remaining -= size
                if progress:
                    progress(1.0 - remaining / max(1, original_size))
            if progress:
                progress(1.0)
            return StreamDecoded(code_strings(codes),
                                 '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')
        reader = StreamBitReader(source, payload_bits)
        # 하프만 트리 구조로 비트를 따라가며 심볼을 찾습니다.
        root = {}
        for sym, (code, width) in codes.items():
            node = root
            for shift in range(width - 1, 0, -1):
                key = (code >> shift) & 1
                node = node.setdefault(key, {})
            final_key = code & 1
            if final_key in node:
                raise ValueError('중복된 허프만 코드 또는 접두 충돌이 있습니다.')
            node[final_key] = sym
        processed = 0
        buffer = bytearray()
        for _ in range(original_size):
            node = root
            while isinstance(node, dict):
                bit = reader.read_bit()
                node = node.get(bit)
                if node is None:
                    raise ValueError('허프만 테이블로 해석할 수 없는 코드입니다.')
            buffer.append(node)
            processed += 1
            if len(buffer) >= CHUNK_SIZE:
                target.write(buffer)
                buffer.clear()
                if progress:
                    progress(processed / max(1, original_size))
        if buffer:
            target.write(buffer)
        reader.finish()
        if progress:
            progress(1.0)
        return StreamDecoded(code_strings(codes),
                             '정적 코드표: 파일 전체에서 각 심볼의 코드는 동일합니다.')
