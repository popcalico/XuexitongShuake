# -*- coding: utf-8 -*-
"""V5.1 第28节：长时间运行资源上限（短压力，输出基线/峰值/结束值）。

纯桩：计时记录上限、GUI 日志队列背压与 1000/10000 行基准、
有界线程池、等待可取消、多账号重复启停后资源归零。不联网、不用真实账号。
真实 tkinter.Text 基准见既有 test_gui_log_performance.py（V5.0）。

用法：C:/Python315/python.exe tests/test_long_running_resource_bounds.py
"""
import os
import queue
import statistics
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_resbounds_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import bounded_executor as be
from core import performance as perf
from core import task_control as tc
from core import runner as R

FAILS = []
METRICS = {}


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def pct(values, p):
    s = sorted(values)
    return round(s[min(len(s) - 1, int((len(s) - 1) * p))], 6)


def queue_bench(n, cap=2000, batch=500):
    """模拟 GUI 日志队列：带背压的生产 + 一批消费。"""
    q = queue.Queue(maxsize=cap)
    puts = []
    t0 = time.perf_counter()
    for i in range(n):
        s = time.perf_counter()
        if q.qsize() >= cap:
            try:
                q.get_nowait()
            except Exception:
                pass
        q.put(('accA', 'line %d\n' % i, None))
        puts.append(time.perf_counter() - s)
    total = time.perf_counter() - t0
    t1 = time.perf_counter()
    drained = 0
    for _ in range(batch):
        try:
            q.get_nowait()
            drained += 1
        except Exception:
            break
    flush = time.perf_counter() - t1
    return {
        'rows': n, 'put_total_s': round(total, 4),
        'put_p50_s': pct(puts, 0.5), 'put_p95_s': pct(puts, 0.95),
        'put_max_s': round(max(puts), 6),
        'batch_drain_s': round(flush, 6), 'drained': drained,
        'queue_left': q.qsize(),
    }


class FakeConfig:
    def write_account_runtime(self, aid):
        return tempfile.mkdtemp(prefix='acc_')

    def get_account_by_id(self, aid):
        return {'alias': aid}


class Out:
    def readline(self):
        return b''

    def close(self):
        pass


class P:
    def __init__(self, *a, **k):
        self.pid = id(self) % 100000
        self.stdout = Out()
        self.terminated = False

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True


def main():
    print('=== V5.1 长时间运行资源上限（短压力） ===')
    base_threads = threading.active_count()
    METRICS['baseline'] = {'active_threads': base_threads}

    # 1) 计时记录有上限，不会无限增长
    perf.reset()
    for _ in range(3000):
        with perf.stage('压测阶段'):
            pass
    rep = perf.report()
    check('单阶段记录不超过 _MAX_RECORDS',
          rep['压测阶段']['count'] == perf._MAX_RECORDS,
          rep['压测阶段']['count'])
    check('P50/P95/max 统计可用',
          all(k in rep['压测阶段'] for k in ('p50', 'p95', 'max', 'avg')),
          rep['压测阶段'])

    # 2) GUI 日志队列 1000 / 10000 行
    b1 = queue_bench(1000)
    b10 = queue_bench(10000)
    METRICS['gui_log'] = {'rows_1000': b1, 'rows_10000': b10}
    check('1000 行队列有界（不超过 cap）', b1['queue_left'] <= 2000, b1['queue_left'])
    check('10000 行后队列仍不超上限', b10['queue_left'] <= 2000, b10['queue_left'])
    check('10000 行背压不阻塞（总耗时 < 2s）', b10['put_total_s'] < 2.0,
          b10['put_total_s'])
    check('批量消费一批只做一次', b10['drained'] == 500, b10['drained'])
    print('[资源] GUI日志 1000行:', b1)
    print('[资源] GUI日志 10000行:', b10)

    # 3) 等待可取消（不会拖到 timeout 才返回）
    ev = threading.Event()
    ev.set()
    st, detail, elapsed = tc.bounded_wait(
        '可取消等待', lambda: False, timeout=5, stop_event=ev, poll_interval=0.1)
    check('停止事件立即取消等待',
          st == tc.TaskStatus.CANCELLED and elapsed < 0.5, (st, elapsed))
    check('取消也记入阶段统计', '可取消等待' in perf.report())

    # 4) 线程池上限
    be.reset_stats()
    def _burst():
        import asyncio
        async def run():
            be.install()
            return await asyncio.gather(*(
                asyncio.to_thread(time.sleep, 0.01) for _ in range(40)))
        asyncio.run(run())
    _burst()
    st = be.stats()
    check('线程池峰值不超上限', st['peak_threads'] <= st['max_workers'], st)
    check('资源判定 bounded', st['bounded'] is True, st)

    # 5) 同一 Runner 20 轮多账号启停后资源归零
    # 注意：上面创建的有界线程池是进程级常驻复用（8 线程，非泄漏），
    # 因此这里以「线程池建立之后」为基线，只衡量 Runner 是否遗留线程。
    runner_base = threading.active_count()
    r = R.Runner(FakeConfig(), lambda *a: None, lambda *a: None,
                 max_concurrency=2)
    r._popen_factory = P
    r._terminator = lambda p: p.terminate()
    peak_threads = runner_base
    for i in range(20):
        r.start(['a%d' % i, 'b%d' % i])
        r.stop_all()
        r.wait_all(1)
        peak_threads = max(peak_threads, threading.active_count())
    end_threads = threading.active_count()
    check('20 轮后信号量归零（回到满额）',
          r._sem._value == r.max_concurrency, r._sem._value)
    check('20 轮后无残留进程/启动集合', not r._procs and not r._starting,
          (len(r._procs), len(r._starting)))
    check('Runner 线程数回落到基线（<= 基线+2）',
          end_threads <= runner_base + 2,
          (runner_base, peak_threads, end_threads))
    METRICS['runner'] = {
        'baseline_threads': runner_base, 'peak_threads': peak_threads,
        'end_threads': end_threads, 'sem_value': r._sem._value,
        'procs_left': len(r._procs),
        'note': '线程池 8 线程为进程级有界复用，已计入基线，非泄漏',
    }
    print('[资源] Runner:', METRICS['runner'])

    # 6) 源码级：GUI 队列上限/背压/批量/关闭停回调
    start_src = open(os.path.join(ROOT, 'start.py'), encoding='utf-8').read()
    check('GUI 队列有上限', 'LOG_QUEUE_MAX = 2000' in start_src)
    check('GUI 队列有背压', 'q.qsize() >= self.LOG_QUEUE_MAX' in start_src)
    check('GUI 批量刷新 100ms', 'LOG_FLUSH_MS = 100' in start_src)
    check('窗口关闭停刷新回调', '_log_closed' in start_src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
