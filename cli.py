"""GUI 없이 압축/복원/실험/용량 사전검사를 수행하는 명령행 실행기."""
import argparse
from pathlib import Path

from core.engine import (compress_file, compress_and_verify, decompress_file,
                         benchmark, export_csv)
from core.preflight import check_input_file, check_input_size
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
    p = sub.add_parser('preflight', help='파일 전체를 읽지 않고 크기/가용 RAM/1GB 스트리밍 사전 점검')
    p.add_argument('source', nargs='?', help='실제 파일 경로; 생략하면 1GB 가정')
    p.add_argument('--size-bytes', type=int, help='가상의 원본 크기(B)')
    for cmd in (c, v, d, b):
        cmd.add_argument('--memory', action='store_true', help='Python 추적 피크와 RSS를 계측 (기본: 꺼짐)')
        cmd.add_argument('--no-memory', action='store_true', help='이전 버전 호환: 메모리 측정 비활성화')
    args = parser.parse_args()

    if args.command == 'preflight':
        if args.source and args.size_bytes is not None:
            parser.error('preflight에서는 파일 경로나 --size-bytes 중 하나만 사용하세요.')
        report = (check_input_file(args.source) if args.source else
                  check_input_size(args.size_bytes if args.size_bytes is not None else 1_000_000_000))
        print('사전 점검:', '통과(성공 보장 아님)' if report.can_attempt else '차단/미지원')
        print('파일 크기:', f'{report.input_bytes:,} B')
        if report.available_ram_bytes is not None:
            print('시스템 여유 RAM:', f'{report.available_ram_bytes / 1048576:,.1f} MiB')
        print('사유:', report.explanation)
        return

    try:
        if args.command == 'compress':
            record = compress_file(args.source, args.target, args.algorithm,
                                   measure_memory=args.memory and not args.no_memory)
        elif args.command == 'verify':
            src = Path(args.source)
            restored = str(Path(args.target).with_name(Path(args.target).stem + '_restored' +
                                                        (src.suffix or '.bin')))
            record = compress_and_verify(args.source, args.target, restored, args.algorithm,
                                         measure_memory=args.memory and not args.no_memory)
        elif args.command == 'decompress':
            record, _ = decompress_file(args.source, args.target,
                                        measure_memory=args.memory and not args.no_memory)
        else:
            records, summary = benchmark(args.files, args.algorithms, args.output,
                                         lambda f, m: print(f'\r{f*100:5.1f}% {m}', end='', flush=True),
                                         measure_memory=args.memory and not args.no_memory)
            print()
            path = Path(args.output) / 'experiment.csv'
            path.parent.mkdir(parents=True, exist_ok=True)
            export_csv(records, str(path))
            print('CSV:', path)
            for s in summary:
                avg = s['saving_pct']
                print(s['algorithm'], '평균 절감률:',
                      f'{avg:.3f}%' if avg is not None else 'N/A',
                      '검증 성공:', f"{s['pass_count']}/{s['count']}",
                      '실패:', s['fail_count'])
            for r in records:
                if r.get('status') == '실패':
                    print('실패:', r['source'], r['algorithm'], r['failure_stage'], r['error_message'])
            return
        for key in ('status', 'failure_stage', 'error_message', 'algorithm',
                    'original_bytes', 'compressed_bytes', 'saving_pct',
                    'entropy', 'avg_payload_bits', 'compression_sec',
                    'compression_algorithm_sec', 'compression_trace_peak_mib',
                    'compression_rss_peak_mib', 'compression_rss_increase_mib',
                    'decompression_sec', 'decompression_algorithm_sec',
                    'decompression_trace_peak_mib', 'decompression_rss_peak_mib',
                    'decompression_rss_increase_mib', 'mismatched_bytes', 'loss_pct', 'verified'):
            print(key, '=', record.get(key))
    except (OSError, ValueError, RuntimeError, MemoryError) as exc:
        parser.exit(1, f'작업 실패 ({type(exc).__name__}): {exc}\n')


if __name__ == '__main__':
    main()
