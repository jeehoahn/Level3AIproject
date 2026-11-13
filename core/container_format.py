"""모든 알고리즘에서 공유하는 .huf 이진 컨테이너 규격 (v1).

고정 헤더 (네트워크 바이트 순서, big-endian):
  magic 4s = b'HUF1'          4바이트
  version B                   1바이트 (1)
  algorithm_id_length B       1바이트
  original_byte_length Q      8바이트
  valid_payload_bits Q        8바이트
  metadata_length I           4바이트
  original_SHA256 32s        32바이트
가변 헤더: algorithm_id(ASCII) + algorithm_metadata
페이로드: 상위 비트부터 기록된 8비트 바이트 배열 (마지막 패딩은 0)
총 길이=58 + 알고리즘 ID 길이 + 메타데이터 길이 + ceil(payload_bits / 8)
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import struct
from core.bitstream import BitReader

MAGIC = b'HUF1'
VERSION = 1
STRUCT = struct.Struct('>4sBBQQI32s')
MAX_METADATA = 2 * 1024 * 1024
MAX_ORIGINAL = 512 * 1024 * 1024  # 비정상적인 길이 헤더/메모리 고갈 방지


@dataclass
class Archive:
    codec_id: str
    original_size: int
    digest: bytes
    metadata: bytes
    payload: bytes
    payload_bits: int
    header_size: int

    @property
    def total_size(self):
        return self.header_size + len(self.payload)


def pack(codec_id: str, data: bytes, metadata: bytes,
         payload: bytes, payload_bits: int) -> bytes:
    raw_id = codec_id.encode('ascii')
    if not 1 <= len(raw_id) <= 64:
        raise ValueError('알고리즘 식별자는 1~64자의 ASCII 문자열이어야 합니다.')
    if len(metadata) > MAX_METADATA:
        raise ValueError('메타데이터 크기 제한 초과')
    if len(data) > MAX_ORIGINAL:
        raise ValueError('최대 512 MiB 원본 파일까지만 처리할 수 있습니다.')
    BitReader(payload, payload_bits)  # 패딩 검증
    fixed = STRUCT.pack(MAGIC, VERSION, len(raw_id), len(data), payload_bits,
                        len(metadata), hashlib.sha256(data).digest())
    return fixed + raw_id + metadata + payload


def unpack(raw: bytes) -> Archive:
    if len(raw) < STRUCT.size:
        raise ValueError('파일 길이가 헤더보다 짧습니다.')
    magic, version, name_len, original_size, bits, meta_len, digest = STRUCT.unpack_from(raw)
    if magic != MAGIC or version != VERSION:
        raise ValueError('지원하지 않는 압축 파일(MAGIC/버전 불일치)입니다.')
    if not (1 <= name_len <= 64) or meta_len > MAX_METADATA:
        raise ValueError('압축 파일의 알고리즘 ID/테이블 길이가 비정상적입니다.')
    if original_size > MAX_ORIGINAL:
        raise ValueError('원본 크기 헤더가 안전 제한인 512 MiB를 넘었습니다.')
    header_size = STRUCT.size + name_len + meta_len
    if len(raw) < header_size or len(raw) - header_size != (bits + 7) // 8:
        raise ValueError('헤더/페이로드 길이가 맞지 않거나 데이터가 잘렸습니다.')
    try:
        codec_id = raw[STRUCT.size:STRUCT.size + name_len].decode('ascii')
    except UnicodeDecodeError as exc:
        raise ValueError('알고리즘 ID는 ASCII여야 합니다.') from exc
    metadata = raw[STRUCT.size + name_len:header_size]
    payload = raw[header_size:]
    BitReader(payload, bits)
    return Archive(codec_id, original_size, digest, metadata, payload, bits, header_size)
