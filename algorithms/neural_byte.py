"""온라인 신경망으로 다음 바이트의 확률을 예측하는 무손실 코덱."""
from __future__ import annotations

from array import array
from io import BytesIO
from typing import BinaryIO, Mapping

from .base import Codec, DecodedData, PackedData, Progress, StreamDecoded, StreamEncoded
from core.arithmetic import ArithmeticDecoder, ArithmeticEncoder, PROBABILITY_TOTAL, STATE_BITS
from core.file_stream import CHUNK_SIZE
from core.stream_bits import StreamBitReader, StreamBitWriter

MID_PROBABILITY = PROBABILITY_TOTAL // 2
MAX_LOGIT = 2304
ORDER2_BITS = 18
ORDER2_SIZE = 1 << ORDER2_BITS
START_BYTE = 256


def _sigmoid_table() -> tuple[int, ...]:
    # logit 256 단위가 실수 1에 해당한다. 고정소수점 odds에
    # exp(1/256)의 정수 근삿값 65793/65536을 반복해서 곱한다.
    one = 1 << 32
    odds = one
    positive = [MID_PROBABILITY]
    for _ in range(MAX_LOGIT):
        odds = (odds * 65793 + 32768) >> 16
        total = odds + one
        positive.append(min(PROBABILITY_TOTAL - 1,
                            (PROBABILITY_TOTAL * odds + total // 2) // total))
    return tuple(PROBABILITY_TOTAL - value for value in reversed(positive[1:])) + tuple(positive)


SIGMOID = _sigmoid_table()


class BytePredictor:
    """세 문맥의 sigmoid 은닉 뉴런과 비트 깊이별 sigmoid 출력 뉴런.

    바이트의 확률은 상위 비트부터 접두사에 조건부인 8개 확률의 곱이다.
    같은 앞부분을 처리한 인코더와 디코더가 같은 가중치를 얻도록 정수로 학습한다.
    """

    def __init__(self):
        self.order0 = array('h', [0]) * 256
        self.order1 = array('h', [0]) * ((START_BYTE + 1) * 256)
        self.order2 = array('h', [0]) * ORDER2_SIZE
        self.mixers = [[32, 64, 64] for _ in range(8)]
        self.last = self.older = START_BYTE

    def start_byte(self) -> None:
        self.base1 = self.last << 8
        context = self.older * (START_BYTE + 1) + self.last
        self.base2 = ((context * 0x9E3779B1) & 0xFFFFFFFF) >> (32 - ORDER2_BITS)

    def predict(self, prefix: int, depth: int) -> int:
        index1 = self.base1 | prefix
        index2 = self.base2 ^ prefix
        score0 = self.order0[prefix]
        score1 = self.order1[index1]
        score2 = self.order2[index2]
        act0 = SIGMOID[MAX_LOGIT + score0] - MID_PROBABILITY
        act1 = SIGMOID[MAX_LOGIT + score1] - MID_PROBABILITY
        act2 = SIGMOID[MAX_LOGIT + score2] - MID_PROBABILITY
        weights = self.mixers[depth]
        logit = (weights[0] * act0 + weights[1] * act1 + weights[2] * act2) >> 7
        probability = SIGMOID[MAX_LOGIT + max(-MAX_LOGIT, min(MAX_LOGIT, logit))]
        self.prediction = (index1, index2, score0, score1, score2, act0, act1, act2,
                           weights, probability)
        return probability

    def learn(self, prefix: int, bit: int) -> None:
        (index1, index2, score0, score1, score2, act0, act1, act2,
         weights, probability) = self.prediction
        target = bit * PROBABILITY_TOTAL
        # 각 은닉 뉴런은 실제 비트에 대한 로지스틱 오차로 온라인 학습한다.
        error = target - MID_PROBABILITY
        self.order0[prefix] = score0 + ((error - act0 + 8) >> 4)
        self.order1[index1] = score1 + ((error - act1 + 8) >> 4)
        self.order2[index2] = score2 + ((error - act2 + 8) >> 4)
        # 출력층은 자신의 오차와 각 은닉 뉴런의 활성값으로 갱신한다.
        error = target - probability
        for index, activation in enumerate((act0, act1, act2)):
            delta = (error * activation + (1 << 18)) >> 19
            weights[index] = max(0, min(192, weights[index] + delta))

    def observe_byte(self, byte: int) -> None:
        self.older, self.last = self.last, byte


class NeuralByte(Codec):
    id = 'neural'
    name = 'D · 신경망 바이트 예측'
    description = '온라인 신경망의 다음 바이트 확률을 정수 산술 부호화로 압축'
    NOTE = '신경망 예측 확률을 산술 부호화합니다. 바이트별 고정 코드표는 없습니다.'

    def streaming_metadata(self, frequencies: Mapping[int, int]) -> bytes:
        return b''

    def encode(self, data: bytes, progress: Progress = None) -> PackedData:
        output = BytesIO()
        encoded = self.encode_stream(BytesIO(data), output, len(data), b'', progress)
        return PackedData(output.getvalue(), encoded.bit_length, b'', code_note=self.NOTE)

    def decode(self, payload: bytes, bit_length: int, metadata: bytes,
               original_size: int, progress: Progress = None) -> DecodedData:
        if len(payload) != (bit_length + 7) // 8:
            raise ValueError('신경망 페이로드 크기가 유효 비트 수와 다릅니다.')
        output = BytesIO()
        self.decode_stream(BytesIO(payload), output, bit_length, metadata, original_size, progress)
        return DecodedData(output.getvalue(), code_note=self.NOTE)

    def encode_stream(self, source: BinaryIO, target: BinaryIO, original_size: int,
                      metadata: bytes, progress: Progress = None) -> StreamEncoded:
        if metadata:
            raise ValueError('신경망 코덱은 모델 가중치를 헤더에 저장하지 않습니다.')
        if original_size == 0:
            if source.read(1):
                raise ValueError('압축 중 원본 길이가 변경되었습니다.')
            if progress:
                progress(1.0)
            return StreamEncoded(0, code_note=self.NOTE)
        predictor = BytePredictor()
        coder = ArithmeticEncoder(StreamBitWriter(target))
        processed = 0
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
            for byte in chunk:
                predictor.start_byte()
                prefix = 1
                for depth in range(8):
                    bit = (byte >> (7 - depth)) & 1
                    coder.write(bit, predictor.predict(prefix, depth))
                    predictor.learn(prefix, bit)
                    prefix = (prefix << 1) | bit
                predictor.observe_byte(byte)
            processed += len(chunk)
            if progress:
                progress(processed / original_size)
        if processed != original_size:
            raise ValueError('압축 중 원본 길이가 변경되었습니다.')
        return StreamEncoded(coder.finish(), code_note=self.NOTE)

    def decode_stream(self, source: BinaryIO, target: BinaryIO, payload_bits: int,
                      metadata: bytes, original_size: int,
                      progress: Progress = None) -> StreamDecoded:
        if metadata or original_size < 0:
            raise ValueError('신경망 코덱의 헤더가 올바르지 않습니다.')
        if original_size == 0:
            if payload_bits != 0:
                raise ValueError('빈 파일의 신경망 페이로드는 비어 있어야 합니다.')
            if progress:
                progress(1.0)
            return StreamDecoded(code_note=self.NOTE)
        # 각 비트의 최대 확률은 4095/4096이다. 이론상 가능한
        # 압축보다도 느슨한 상한으로, 틀린 헤더의 복원 크기를 제한한다.
        if payload_bits < STATE_BITS or original_size > payload_bits * (PROBABILITY_TOTAL // 8):
            raise ValueError('신경망 페이로드의 비트 수가 원본 길이에 맞지 않습니다.')
        predictor = BytePredictor()
        coder = ArithmeticDecoder(StreamBitReader(source, payload_bits))
        remaining = original_size
        while remaining:
            output = bytearray()
            for _ in range(min(remaining, CHUNK_SIZE)):
                predictor.start_byte()
                prefix = 1
                for depth in range(8):
                    bit = coder.read(predictor.predict(prefix, depth))
                    predictor.learn(prefix, bit)
                    prefix = (prefix << 1) | bit
                byte = prefix & 255
                output.append(byte)
                predictor.observe_byte(byte)
            target.write(output)
            remaining -= len(output)
            if progress:
                progress(1 - remaining / original_size)
        coder.finish()
        return StreamDecoded(code_note=self.NOTE)
