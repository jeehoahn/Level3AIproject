"""GUI 없는 환경에서도 압축 코드를 검증할 수 있는 명령행 실행기."""
import argparse
from pathlib import Path
from core.engine import compress_file, compress_and_verify, decompress_file, benchmark, export_csv
from algorithms.registry import CODECS


def main():
    parser = argparse.ArgumentParser(description='허프만 비교 실험 CLI')
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('compress')
    c.add_argument('source'); c.add_argument('target')
    c.add_argument('--algorithm', choices=CODECS, default='static')
    v = sub.add_parser('verify')
    v.add_argument('source'); v.add_argument('target')
    v.add_argument('--algorithm', choices=CODECS, default='static')
    d = sub.add_parser('decompress')
    d.add_argument('source'); d.add_argument('target')
    b = sub.add_parser('bench')
    b.add_argument('files', nargs='+')
    b.add_argument('--output', default='results')
    b.add_argument('--algorithms', nargs='+', choices=CODECS, default=list(CODECS))
    args = parser.parse_args()
    if args.command == 'compress':
        record = compress_file(args.source, args.target, args.algorithm)
    elif args.command == 'verify':
        src = Path(args.source)
        restored = str(Path(args.target).with_name(Path(args.target).stem + '_restored' + (src.suffix or '.bin')))
        record = compress_and_verify(args.source, args.target, restored, args.algorithm)
    elif args.command == 'decompress':
        record, _ = decompress_file(args.source, args.target)
    else:
        records, summary = benchmark(args.files, args.algorithms, args.output,
                                     lambda f, m: print(f'\r{f*100:5.1f}% {m}', end='', flush=True))
        print()
        path = Path(args.output) / 'experiment.csv'
        export_csv(records, str(path))
        print('CSV:', path)
        for s in summary:
            print(s['algorithm'], '평균 절감률:', f"{s['saving_pct']:.3f}%",
                  '복원 성공:', f"{s['pass_count']}/{s['count']}")
        return
    for key in ('algorithm', 'original_bytes', 'compressed_bytes', 'saving_pct',
                'entropy', 'avg_payload_bits', 'compression_sec', 'decompression_sec',
                'mismatched_bytes', 'loss_pct', 'verified'):
        print(key, '=', record[key])


if __name__ == '__main__':
    main()
