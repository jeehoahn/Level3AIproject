"""B 담당 예시: FGK(Faller-Gallager-Knuth) 적응형 허프만 코딩.

송수신측이 같은 NYT 초기 트리와 업데이트 규칙을 사용해 트리를 동기화합니다.
처음 보는 바이트는 NYT 코드 + 8비트 원문 값으로 보내고, 기존 심볼은
현재 시점의 허프만 경로를 전송합니다.
복원용 정적 테이블은 없습니다(헤더의 'fgk' 알고리즘 ID와 버전이 규칙을 결정).
"""
from __future__ import annotations
from .base import Codec, PackedData, DecodedData, Progress
from core.bitstream import BitReader, BitWriter


class Node:
    __slots__ = ('weight', 'order', 'symbol', 'parent', 'left', 'right', 'nyt')

    def __init__(self, weight: int, order: int, symbol: int | None = None,
                 parent: 'Node | None' = None, nyt: bool = False):
        self.weight = weight
        self.order = order
        self.symbol = symbol
        self.parent = parent
        self.left: Node | None = None
        self.right: Node | None = None
        self.nyt = nyt


class FGKTree:
    def __init__(self):
        self.root = Node(0, 512, nyt=True)
        self.nyt = self.root
        self.leaves: dict[int, Node] = {}
        self.by_weight: dict[int, set[Node]] = {0: {self.root}}

    def _remove_weight(self, node: Node):
        bucket = self.by_weight[node.weight]
        bucket.remove(node)
        if not bucket:
            del self.by_weight[node.weight]

    def _increment(self, node: Node):
        self._remove_weight(node)
        node.weight += 1
        self.by_weight.setdefault(node.weight, set()).add(node)

    @staticmethod
    def _ancestor(ancestor: Node, node: Node) -> bool:
        curr = node.parent
        while curr is not None:
            if curr is ancestor:
                return True
            curr = curr.parent
        return False

    def _swap(self, a: Node, b: Node) -> None:
        pa, pb = a.parent, b.parent
        if pa is None or pb is None:
            raise ValueError('트리의 루트는 교환할 수 없습니다.')
        a_left = pa.left is a
        b_left = pb.left is b
        if a_left:
            pa.left = b
        else:
            pa.right = b
        if b_left:
            pb.left = a
        else:
            pb.right = a
        a.parent, b.parent = pb, pa
        a.order, b.order = b.order, a.order

    def _update_upward(self, node: Node | None):
        while node is not None:
            # 같은 가중치 블록의 가장 높은 순서 번호를 택함.
            # 조상/자손 간 스왑은 트리를 깨뜨리므로 배제함.
            candidates = self.by_weight[node.weight]
            leader = node
            for other in candidates:
                if other.order > leader.order and not self._ancestor(node, other) \
                   and not self._ancestor(other, node):
                    leader = other
            if leader is not node:
                self._swap(node, leader)
            self._increment(node)
            node = node.parent

    def add_new(self, symbol: int) -> None:
        if symbol in self.leaves or len(self.leaves) >= 256:
            raise ValueError('NYT 심볼 삽입에 실패했습니다.')
        previous = self.nyt
        if previous.order < 2:
            raise ValueError('FGK 트리의 순서 번호가 부족합니다.')
        new_nyt = Node(0, previous.order - 2, parent=previous, nyt=True)
        leaf = Node(1, previous.order - 1, symbol=symbol, parent=previous)
        previous.nyt = False
        previous.left = new_nyt
        previous.right = leaf
        self.nyt = new_nyt
        self.leaves[symbol] = leaf
        self.by_weight[0].add(new_nyt)
        self.by_weight.setdefault(1, set()).add(leaf)
        self._increment(previous)
        self._update_upward(previous.parent)

    def touch(self, symbol: int):
        self._update_upward(self.leaves[symbol])

    @staticmethod
    def path(node: Node) -> tuple[int, int]:
        directions = []
        while node.parent is not None:
            directions.append(1 if node.parent.right is node else 0)
            node = node.parent
        value = 0
        for direction in reversed(directions):
            value = (value << 1) | direction
        return value, len(directions)

    def codes(self) -> dict[int, str]:
        output = {}
        for symbol, node in self.leaves.items():
            code, length = self.path(node)
            output[symbol] = format(code, f'0{length}b') if length else ''
        return output


class AdaptiveFGK(Codec):
    id = 'fgk'
    name = 'B · FGK 적응형 허프만'
    description = '심볼을 읽을 때마다 트리를 갱신하며 NYT + 8비트 신규 바이트를 사용'
    NOTE = '적응형 코드표: 표시된 코드는 처리 완료 시점의 최종 코드이며, 실제 전송 코드는 시점마다 달라집니다.'

    def encode(self, data: bytes, progress: Progress = None) -> PackedData:
        tree = FGKTree()
        writer = BitWriter()
        interval = max(1024, len(data) // 100)
        for i, byte in enumerate(data):
            if byte in tree.leaves:
                code, length = tree.path(tree.leaves[byte])
                writer.write(code, length)
                tree.touch(byte)
            else:
                code, length = tree.path(tree.nyt)
                writer.write(code, length)
                writer.write(byte, 8)
                tree.add_new(byte)
            if progress and i % interval == 0:
                progress(i / max(1, len(data)))
        if progress:
            progress(1.0)
        return PackedData(writer.finish(), writer.bit_length, b'', tree.codes(), self.NOTE)

    def decode(self, payload: bytes, bit_length: int, metadata: bytes,
               original_size: int, progress: Progress = None) -> DecodedData:
        if metadata:
            raise ValueError('FGK 헤더에는 정적 코드표를 넣지 않습니다.')
        reader = BitReader(payload, bit_length)
        tree = FGKTree()
        restored = bytearray()
        interval = max(1024, original_size // 100)
        for i in range(original_size):
            curr = tree.root
            while curr.left is not None:
                curr = curr.right if reader.read_bit() else curr.left
            if curr.nyt:
                symbol = reader.read(8)
                if symbol in tree.leaves:
                    raise ValueError('NYT가 이미 등장한 바이트를 가리킵니다.')
                restored.append(symbol)
                tree.add_new(symbol)
            else:
                symbol = curr.symbol
                if symbol is None or symbol not in tree.leaves:
                    raise ValueError('FGK 트리가 손상되었습니다.')
                restored.append(symbol)
                tree.touch(symbol)
            if progress and i % interval == 0:
                progress(i / max(1, original_size))
        if reader.remaining():
            raise ValueError('원본 길이까지 복원했지만 비트가 남아 있습니다.')
        if progress:
            progress(1.0)
        return DecodedData(bytes(restored), tree.codes(), self.NOTE)
