"""32비트 이진 산술 부호화. 확률은 1/4096 단위 정수로 받는다."""
from core.stream_bits import StreamBitReader, StreamBitWriter

PROBABILITY_BITS = 12
PROBABILITY_TOTAL = 1 << PROBABILITY_BITS
STATE_BITS = 32
MASK = (1 << STATE_BITS) - 1
HALF = 1 << (STATE_BITS - 1)
QUARTER = HALF >> 1
TRAILER_BITS = STATE_BITS - 2


class ArithmeticEncoder:
    def __init__(self, writer: StreamBitWriter):
        self.writer = writer
        self.low, self.high = 0, MASK
        self.pending = 0

    def _emit(self, bit: int) -> None:
        self.writer.write(bit, 1)
        # E3 구간에서 지연한 비트는 확정 비트의 반대로 기록한다.
        while self.pending:
            self.writer.write(bit ^ 1, 1)
            self.pending -= 1

    def write(self, bit: int, probability_one: int) -> None:
        low, high = self.low, self.high
        split = low + (((high - low + 1) * (PROBABILITY_TOTAL - probability_one))
                       >> PROBABILITY_BITS)
        if bit:
            low = split
        else:
            high = split - 1
        while True:
            if (low ^ high) < HALF:
                self._emit(low >> (STATE_BITS - 1))
            elif QUARTER <= low and high < 3 * QUARTER:
                self.pending += 1
                low -= QUARTER
                high -= QUARTER
            else:
                break
            low = (low << 1) & MASK
            high = ((high << 1) | 1) & MASK
        self.low, self.high = low, high

    def finish(self) -> int:
        # 최종 구간을 지정하는 두 비트에 30개의 0을 붙여 복원 시
        # 입력 밖의 가상 비트 없이 32비트 코드 레지스터를 채운다.
        self.pending += 1
        self._emit(int(self.low >= QUARTER))
        self.writer.write(0, TRAILER_BITS)
        return self.writer.finish()


class ArithmeticDecoder:
    def __init__(self, reader: StreamBitReader):
        self.reader = reader
        self.low, self.high = 0, MASK
        self.code = reader.read(STATE_BITS)

    def read(self, probability_one: int) -> int:
        low, high, code = self.low, self.high, self.code
        split = low + (((high - low + 1) * (PROBABILITY_TOTAL - probability_one))
                       >> PROBABILITY_BITS)
        bit = int(code >= split)
        if bit:
            low = split
        else:
            high = split - 1
        while True:
            if (low ^ high) < HALF:
                pass
            elif QUARTER <= low and high < 3 * QUARTER:
                low -= QUARTER
                high -= QUARTER
                code -= QUARTER
            else:
                break
            low = (low << 1) & MASK
            high = ((high << 1) | 1) & MASK
            code = ((code << 1) | self.reader.read_bit()) & MASK
        self.low, self.high, self.code = low, high, code
        return bit

    def finish(self) -> None:
        self.reader.finish()
        if self.code & ((1 << TRAILER_BITS) - 1):
            raise ValueError('산술 부호화 종료 비트가 올바르지 않습니다.')
