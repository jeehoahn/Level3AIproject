"""실험용 50,000/100,000/500,000자 UTF-8 텍스트 각 4종 생성.

텍스트 길이는 파이썬 Unicode code point 기준입니다(개별 결합문자 제외).
ChatGPT가 작성한 문장 템플릿을 활용한 재현 가능한 합성 데이터입니다.
"""
from __future__ import annotations
from pathlib import Path
import random
import string
import argparse

SEED = 20261113
SIZES = (50_000, 100_000, 500_000)
KOREAN_SENTENCES = [
    '허프만 코딩은 각 기호의 등장 확률에 따라 서로 다른 길이의 코드를 할당합니다. ',
    '자료구조와 알고리즘을 비교할 때는 실행 시간과 압축률을 함께 측정합니다. ',
    '실험 환경이 바뀌면 동일한 프로그램에서도 실행 시간이 달라질 수 있습니다. ',
    '압축된 데이터를 복원한 뒤 원본과 바이트 단위로 정확히 비교합니다. ',
    '문자의 분포가 치우쳐 있을수록 짧은 코드를 자주 사용하는 효과가 커집니다. ',
    '파일의 헤더에는 압축 해제에 필요한 정보를 담아야 합니다. ',
    '한글은 UTF-8에서 여러 바이트로 표현되므로 바이트 기준 분석과 문자 기준 분석이 다릅니다. ',
    '균등한 빈도의 심볼은 편향된 빈도의 심볼보다 정보 엔트로피가 높아질 수 있습니다. ',
]


def make_text(style: str, size: int, seed: int) -> str:
    rng = random.Random(seed)
    if style == 'uniform_ascii':
        alphabet = string.ascii_letters + string.digits + string.punctuation + ' '
        return ''.join(rng.choices(alphabet, k=size))
    if style == 'skewed_ascii':
        # A 비율 87%, 나머지는 불균등하지만 다양한 문자 사용
        options = ['A', 'B', 'C', 'D', 'E', 'F', '\n']
        weights = [87, 5, 3, 2, 1, 1, 1]
        return ''.join(rng.choices(options, weights=weights, k=size))
    if style == 'repeated_pattern':
        template = ('ABRACADABRA! 허프만 코딩 테스트. ' * 3) + '\n'
        return (template * (size // len(template) + 1))[:size]
    if style == 'korean_prose':
        # 자연어 문장 조각의 빈도를 일부러 편향시킨 AI 작성 템플릿 조합
        weights = [5, 3, 2, 3, 2, 1, 3, 1]
        out = []
        length = 0
        while length < size:
            sentence = rng.choices(KOREAN_SENTENCES, weights=weights)[0]
            out.append(sentence)
            length += len(sentence)
        return ''.join(out)[:size]
    raise ValueError('알 수 없는 분포')


def generate(output_dir: Path, sizes=SIZES):
    output_dir.mkdir(parents=True, exist_ok=True)
    styles = ('uniform_ascii', 'skewed_ascii', 'repeated_pattern', 'korean_prose')
    for size in sizes:
        for i, style in enumerate(styles):
            text = make_text(style, size, SEED + i + size)
            assert len(text) == size
            target = output_dir / f'{size}_{style}.txt'
            target.write_text(text, encoding='utf-8')
            print(f'{target.name:37} {size:,}자 / {target.stat().st_size:,} bytes')
    print(f'총 {len(sizes)*len(styles)}개 파일 생성 완료: {output_dir}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='test_corpus')
    args = parser.parse_args()
    generate(Path(args.output))
