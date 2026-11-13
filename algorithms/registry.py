"""알고리즘을 추가하는 곳. 팀원은 각자 파일을 만들고 한 줄 등록합니다."""
from .static_huffman import StaticHuffman
from .adaptive_fgk import AdaptiveFGK

CODECS = {codec.id: codec for codec in (StaticHuffman(), AdaptiveFGK())}


def get_codec(codec_id: str):
    try:
        return CODECS[codec_id]
    except KeyError:
        raise ValueError(f'지원하지 않는 압축 알고리즘: {codec_id!r}') from None
