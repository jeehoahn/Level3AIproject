"""공통 알고리즘 성능 측정. 개별 Codec 구현은 변경하지 않습니다.

RSS는 psutil로 10ms마다 샘플링한 **프로세스 전체**의 최대 관측값입니다.
tracemalloc은 측정 구간에서 Python이 추적한 할당 피크이며 RSS와 동일하지 않습니다.
동시에 여러 작업을 프로파일링하지 않아야 합니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread, Lock
import time
import tracemalloc
from typing import Callable, TypeVar

try:
    import psutil
except ImportError:  # RSS를 측정할 수 없을 때도 다른 지표는 기록한다.
    psutil = None

T = TypeVar('T')
_PROFILE_LOCK = Lock()
MIB = 1024 ** 2
SAMPLE_INTERVAL = 0.01


@dataclass(frozen=True)
class Profile:
    seconds: float
    trace_peak_mib: float | None
    rss_start_mib: float | None
    rss_peak_mib: float | None
    rss_increase_mib: float | None


def profile_call(action: Callable[[], T], *, measure_memory: bool = True) -> tuple[T, Profile]:
    """순수 알고리즘 호출의 시간·피크 메모리 측정.

    실패 예외는 상위 엔진으로 그대로 전달합니다. 메모리 미측정 모드에서는
    tracemalloc 오버헤드가 없으므로 시간 비교 시 이 모드를 권장합니다.
    """
    if not measure_memory:
        start = time.perf_counter()
        result = action()
        return result, Profile(time.perf_counter()-start, None, None, None, None)

    if psutil is None:
        raise RuntimeError('메모리 측정에 psutil이 필요합니다. pip install -r requirements.txt')

    if not _PROFILE_LOCK.acquire(blocking=False):
        raise RuntimeError('다른 메모리 측정이 진행 중입니다. 동시에 실행할 수 없습니다.')

    proc = psutil.Process()
    rss_start = proc.memory_info().rss
    peak_rss = rss_start
    stop = Event()

    def sampler():
        nonlocal peak_rss
        while not stop.wait(SAMPLE_INTERVAL):
            try:
                peak_rss = max(peak_rss, proc.memory_info().rss)
            except (psutil.Error, OSError):
                break

    thread = Thread(target=sampler, name='rss-sampler', daemon=True)
    own_trace = not tracemalloc.is_tracing()
    try:
        if own_trace:
            tracemalloc.start()
        trace_start = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        thread.start()
        start = time.perf_counter()
        try:
            result = action()
        finally:
            seconds = time.perf_counter() - start
            stop.set()
            thread.join()
            peak_rss = max(peak_rss, proc.memory_info().rss)
            trace_peak = tracemalloc.get_traced_memory()[1]
            if own_trace:
                tracemalloc.stop()
        return result, Profile(
            seconds=seconds,
            trace_peak_mib=max(0, trace_peak-trace_start)/MIB,
            rss_start_mib=rss_start/MIB,
            rss_peak_mib=peak_rss/MIB,
            rss_increase_mib=max(0, peak_rss-rss_start)/MIB,
        )
    finally:
        _PROFILE_LOCK.release()
