"""실용적 압축(빠른 처리량·안정적 복원 중심)을 위한 경량 LZ77 코덱."""
from __future__ import annotations

from .base import Codec, DecodedData, PackedData, Progress
from core.bitstream import BitReader, BitWriter

WINDOW_SIZE = 4096
MAX_MATCH_LEN = 18
MIN_MATCH_LEN = 3


class PracticalLZ77(Codec):
    id = "practical"
    name = "C · 실용적 경량 LZ77"
    description = "반복 구간을 빠르게 찾아 압축·복원 속도와 실용성의 균형을 맞춘 코덱"
    NOTE = "실용적 모드: 빠른 탐색을 위해 윈도우 크기를 제한한 경량 LZ77 방식을 사용합니다."

    def encode(self, data: bytes, progress: Progress = None) -> PackedData:
        writer = BitWriter()
        n = len(data)
        pos = 0
        # 최근 등장 위치(최대 8개만 기록해 탐색 속도 확보)
        positions: dict[int, list[int]] = {}
        interval = max(1024, n // 100) if n else 1

        while pos < n:
            best_dist = 0
            best_len = 0

            if pos + MIN_MATCH_LEN <= n:
                key = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
                candidates = positions.get(key)
                if candidates:
                    min_allowed = max(0, pos - WINDOW_SIZE)
                    for cand in reversed(candidates):
                        if cand < min_allowed:
                            break
                        dist = pos - cand
                        if 1 <= dist <= WINDOW_SIZE:
                            max_possible = min(MAX_MATCH_LEN, n - pos)
                            length = MIN_MATCH_LEN
                            while length < max_possible and data[cand + length] == data[pos + length]:
                                length += 1
                            if length > best_len:
                                best_len = length
                                best_dist = dist
                                if best_len == MAX_MATCH_LEN:
                                    break

            if best_len >= MIN_MATCH_LEN:
                # 태그 1비트 '1' + 거리 12비트(1..4096 -> 0..4095) + 길이 4비트(3..18 -> 0..15)
                writer.write(1, 1)
                writer.write(best_dist - 1, 12)
                writer.write(best_len - MIN_MATCH_LEN, 4)
                step = best_len
            else:
                # 태그 1비트 '0' + 원본 1바이트(8비트)
                writer.write(0, 1)
                writer.write(data[pos], 8)
                step = 1

            end_record = min(n, pos + step)
            for idx in range(pos, end_record):
                if idx + 2 < n:
                    k = (data[idx] << 16) | (data[idx + 1] << 8) | data[idx + 2]
                    bucket = positions.setdefault(k, [])
                    bucket.append(idx)
                    if len(bucket) > 8:
                        del bucket[0]
            pos += step

            if progress and pos % interval == 0:
                progress(pos / max(1, n))

        if progress:
            progress(1.0)

        payload = writer.finish()
        return PackedData(
            payload=payload,
            bit_length=writer.bit_length,
            metadata=b"",
            codes={},
            code_note=self.NOTE,
        )

    def decode(
        self,
        payload: bytes,
        bit_length: int,
        metadata: bytes,
        original_size: int,
        progress: Progress = None,
    ) -> DecodedData:
        if metadata:
            raise ValueError("실용적 경량 LZ77 코덱은 별도 메타데이터를 사용하지 않습니다.")

        reader = BitReader(payload, bit_length)
        restored = bytearray()
        interval = max(1024, original_size // 100) if original_size else 1

        while len(restored) < original_size:
            flag = reader.read_bit()
            if flag == 1:
                dist = reader.read(12) + 1
                length = reader.read(4) + MIN_MATCH_LEN
                start_idx = len(restored) - dist
                if start_idx < 0 or dist > WINDOW_SIZE:
                    raise ValueError("압축 데이터가 손상되었거나 잘못된 참조 거리입니다.")
                for i in range(length):
                    if len(restored) >= original_size:
                        break
                    restored.append(restored[start_idx + i])
            else:
                restored.append(reader.read(8))

            if progress and len(restored) % interval == 0:
                progress(len(restored) / max(1, original_size))

        if reader.remaining():
            raise ValueError("원본 길이만큼 복원했는데 처리되지 않은 비트가 남아 있습니다.")

        if progress:
            progress(1.0)

        return DecodedData(
            data=bytes(restored),
            codes={},
            code_note=self.NOTE,
        )