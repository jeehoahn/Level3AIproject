"""외부 압축 라이브러리 없이 직접 비트 읽기/쓰기를 수행합니다."""


class BitWriter:
    __slots__ = ('_buffer', '_pending', '_pending_count', 'bit_length')

    def __init__(self):
        self._buffer = bytearray()
        self._pending = 0
        self._pending_count = 0
        self.bit_length = 0

    def write(self, code: int, length: int) -> None:
        if length < 0 or code < 0 or code >= (1 << length):
            raise ValueError('잘못된 비트 코드 또는 코드 길이')
        self.bit_length += length
        self._pending = (self._pending << length) | code
        self._pending_count += length
        while self._pending_count >= 8:
            self._pending_count -= 8
            self._buffer.append((self._pending >> self._pending_count) & 0xFF)
            self._pending &= (1 << self._pending_count) - 1

    def finish(self) -> bytes:
        if self._pending_count:
            self._buffer.append((self._pending << (8 - self._pending_count)) & 0xFF)
            self._pending = 0
            self._pending_count = 0
        return bytes(self._buffer)


class BitReader:
    __slots__ = ('_data', 'bit_length', 'position')

    def __init__(self, data: bytes, bit_length: int):
        if bit_length < 0 or len(data) != (bit_length + 7) // 8:
            raise ValueError('압축 비트 길이와 페이로드 크기가 일치하지 않습니다.')
        if bit_length % 8 and data and (data[-1] & ((1 << (8 - bit_length % 8)) - 1)):
            raise ValueError('압축 데이터의 패딩 비트가 0이 아닙니다.')
        self._data = data
        self.bit_length = bit_length
        self.position = 0

    def read_bit(self) -> int:
        if self.position >= self.bit_length:
            raise ValueError('압축 비트스트림이 중간에 끝났습니다.')
        pos = self.position
        self.position += 1
        return (self._data[pos >> 3] >> (7 - (pos & 7))) & 1

    def read(self, width: int) -> int:
        value = 0
        for _ in range(width):
            value = (value << 1) | self.read_bit()
        return value

    def remaining(self) -> int:
        return self.bit_length - self.position
