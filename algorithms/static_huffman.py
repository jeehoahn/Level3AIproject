"""A 담당 예시: 빈도표 + 우선순위 큐 + 정적 정규형(canonical) 허프만 코딩.

메타데이터는 심볼 0..255의 코드 길이 배열(256바이트).
코드를 canonical 형식으로 재구성할 수 있으므로 트리 직렬화는 불필요합니다.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from .base import Codec, PackedData, DecodedData, Progress
from core.bitstream import BitReader, BitWriter


@dataclass
class Node:
    symbol: int | None = None
    left: 'Node | None' = None
    right: 'Node | None' = None


def lengths_from_data(data: bytes) -> list[int]:
    freqs = Counter(data)
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
