"""선택한 파일들의 경로와 내용을 하나의 HUF 파일로 압축하고 폴더로 복원한다."""
from __future__ import annotations

import ntpath
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import tempfile
from zipfile import ZIP_STORED, ZipFile

from core.engine import Progress, StageError, compress_and_verify, decompress_file, emit
from core.failures import failed_record
from core.file_stream import CHUNK_SIZE
from core.metrics import averages
from core.preflight import require_input_ready

BUNDLE_SUFFIX = '.hufbundle'
_is_reserved = getattr(ntpath, 'isreserved', lambda part: PureWindowsPath(part).is_reserved())


def _entry_parts(name: str) -> tuple[str, ...]:
    parts = tuple(name.split('/'))
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(ord(c) < 32 or c in '\\:<>|"*?' for c in part)
                or _is_reserved(part)):
            raise ValueError(f'묶음에 사용할 수 없는 파일 경로입니다: {name!r}')
    return parts


def _pack_files(paths: list[str], target: Path, progress: Progress) -> None:
    files = sorted({Path(path).resolve(strict=True) for path in paths})
    try:
        common = Path(os.path.commonpath([p.parent for p in files]))
    except ValueError:
        # 드라이브가 다르면 같은 이름도 보존할 수 있도록 번호 폴더를 만든다.
        common = None
    with ZipFile(target, 'w', compression=ZIP_STORED, strict_timestamps=False) as archive:
        for index, path in enumerate(files, 1):
            require_input_ready(path)
            name = path.relative_to(common).as_posix() if common is not None else f'{index}/{path.name}'
            _entry_parts(name)
            emit(progress, .05 * (index - 1) / len(files), f'묶음 준비 중: {path.name}')
            before = path.stat()
            archive.write(path, name)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError(f'묶음을 만드는 동안 원본이 바뀌었습니다: {path}')


def _extract_files(source: Path, folder: Path) -> None:
    # folder는 이번 작업이 새로 만든 임시 폴더다.
    folder.mkdir()
    with ZipFile(source) as archive:
        members = archive.infolist()
        if not members:
            raise ValueError('묶음에 복원할 파일이 없습니다.')
        for entry in members:
            parts = _entry_parts(entry.orig_filename)
            file_type = stat.S_IFMT(entry.external_attr >> 16)
            if entry.compress_type != ZIP_STORED or file_type not in (0, stat.S_IFREG):
                raise ValueError(f'묶음에 일반 파일만 저장할 수 있습니다: {entry.filename!r}')
            path = folder.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as data, path.open('xb') as output:
                shutil.copyfileobj(data, output, CHUNK_SIZE)


def restore_bundle(source: str, destination: str, progress: Progress = None, *,
                   measure_memory: bool = False) -> dict:
    folder = Path(destination)
    if os.path.lexists(folder):
        raise FileExistsError(f'복원 폴더가 이미 있습니다: {folder}')
    with tempfile.TemporaryDirectory(prefix='.huf_restore_', dir=folder.parent) as temp:
        scratch = Path(temp)
        restored_zip = scratch / 'restored.zip'
        result, _ = decompress_file(
            source, str(restored_zip),
            lambda f, msg: emit(progress, f * .9, msg), measure_memory=measure_memory,
        )
        emit(progress, .92, '묶음에서 개별 파일을 복원하는 중')
        unpacked = scratch / 'files'
        _extract_files(restored_zip, unpacked)
        if os.path.lexists(folder):
            raise FileExistsError(f'복원 폴더가 이미 있습니다: {folder}')
        unpacked.rename(folder)
    result.update(source=source, restored_path=destination)
    emit(progress, 1, '묶음 폴더 복원 완료')
    return result


def benchmark_bundle(paths: list[str], codec_ids: list[str], output_dir: str,
                     progress: Progress = None, *, measure_memory: bool = False
                     ) -> tuple[list[dict], list[dict]]:
    if not paths or not codec_ids:
        raise ValueError('실험 파일과 알고리즘을 모두 선택해야 합니다.')
    parent = Path(output_dir)
    parent.mkdir(parents=True, exist_ok=True)
    records = []
    count = len(codec_ids)
    with tempfile.TemporaryDirectory(prefix='.huf_bundle_', dir=parent) as temp:
        scratch = Path(temp)
        original = scratch / 'original.zip'
        try:
            _pack_files(paths, original, progress)
        except Exception as exc:
            raise StageError('묶음 준비', exc) from exc
        original_size = original.stat().st_size
        run = Path(tempfile.mkdtemp(prefix='파일묶음-', dir=parent))
        for index, codec_id in enumerate(codec_ids):
            suffix = codec_id if all(c.isascii() and (c.isalnum() or c in '-_')
                                    for c in codec_id) else 'codec'
            output = run / f'{index + 1:02d}-{suffix}'
            with tempfile.TemporaryDirectory(dir=scratch) as trial:
                stage = Path(trial)
                ready = stage / 'ready'
                ready.mkdir()
                archive = ready / ('files' + BUNDLE_SUFFIX)
                restored_zip = stage / 'restored.zip'
                try:
                    result = compress_and_verify(
                        str(original), str(archive), str(restored_zip), codec_id,
                        lambda f, message: emit(progress, .05 + .95 * (index + .9 * f) / count,
                                                f'[{index + 1}/{count}] {codec_id} · {message}'),
                        measure_memory=measure_memory,
                    )
                    if result['verified']:
                        emit(progress, .05 + .95 * (index + .9) / count, '개별 파일 복원 중')
                        _extract_files(restored_zip, ready / 'restored')
                        ready.rename(output)
                    result.update(source=str(run),
                                  archive_path=str(output / archive.name) if result['verified'] else '',
                                  restored_path=str(output / 'restored') if result['verified'] else '')
                except Exception as exc:
                    cause = exc.original if isinstance(exc, StageError) else exc
                    step = exc.stage if isinstance(exc, StageError) else '묶음 저장·복원'
                    result = failed_record(str(run), '', '', codec_id, step, cause)
                    result['original_bytes'] = original_size
                records.append(result)
                emit(progress, .05 + .95 * (index + 1) / count, f'[{index + 1}/{count}] 완료')
    return records, averages(records)
