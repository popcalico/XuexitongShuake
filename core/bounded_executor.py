# -*- coding: utf-8 -*-
"""题库请求专用的**共享有界线程池**（V5.1 P1 第 25 节，2026-11-16）。

背景：`asyncio.to_thread()` 使用事件循环的默认执行器。工程里每题一次
`asyncio.run()`，默认执行器是「每次循环按需创建、无显式上限」的线程池；
外层 `asyncio.wait_for()` 超时**并不会终止底层同步 HTTP 线程**，连续超时时
线程会堆积。这里把默认执行器换成一个进程内共享、有 max_workers 上限的池。

关键实现点（不要改回去）：
* `asyncio.run()` 结束时会调用 `loop.shutdown_default_executor()`，进而
  `executor.shutdown(wait=True)`；若直接把普通 ThreadPoolExecutor 设为默认，
  第一题结束池就被关掉，后续题目报错。所以 `_BoundedPool.shutdown()` 是
  空操作，池由模块单例跨题复用，进程退出时由解释器清理。
* `loop.set_default_executor()` 在 Python 3.8+ 要求实例必须是
  `ThreadPoolExecutor` 子类，因此用继承而非鸭子类型代理。
* **不误称 wait_for 能取消底层线程**：真实取消边界是 requests 层的
  connect/read timeout；本模块限制的是「最多允许多少个底层线程/排队任务」。
* 迟到响应不会污染当前题目：`_gather_bounded` 按 form 顺序 gather，
  超时的协程返回空 AnswerResult，被取消的 Future 结果无处写入。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

# 与 AnswerAPI.BANK_MAX_CONCURRENCY=3 配套：并发 3 + 超时残留余量
DEFAULT_MAX_WORKERS = 8

_LOCK = threading.RLock()
_STATS = {'requests': 0, 'timeouts': 0, 'peak_threads': 0, 'peak_queue': 0}
_BATCH = 0


def next_batch():
    """每批题库请求分配唯一序号：用于日志与「迟到结果不污染当前题目」的核对。"""
    global _BATCH
    with _LOCK:
        _BATCH += 1
        return _BATCH


class _BoundedPool(ThreadPoolExecutor):
    """跨 asyncio.run 复用的有界线程池。"""

    def __init__(self, max_workers):
        super().__init__(max_workers=max_workers,
                         thread_name_prefix='xxt-bank')
        self.max_workers = max_workers

    def shutdown(self, wait=True, *, cancel_futures=False):
        # 故意不关闭：asyncio.run() 结束会调用 shutdown_default_executor()。
        # 池是进程级单例，下一题还要用；只有显式 shutdown_now() 才真关。
        pass

    def shutdown_now(self):
        try:
            super().shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


_POOL = None


def get_pool():
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = _BoundedPool(DEFAULT_MAX_WORKERS)
        return _POOL


def install(loop=None):
    """把当前/指定事件循环的默认执行器设为共享有界池。返回池对象。"""
    import asyncio
    if loop is None:
        loop = asyncio.get_running_loop()
    pool = get_pool()
    try:
        loop.set_default_executor(pool)
    except Exception:
        # 老版本/替身循环不支持时静默降级，不影响答题主流程
        pass
    return pool


def record_request():
    _bump('requests')


def record_timeout():
    _bump('timeouts')


def _bump(key):
    with _LOCK:
        _STATS[key] += 1
        pool = get_pool()
        try:
            threads = len(pool._threads)
            queued = pool._work_queue.qsize()
        except Exception:
            threads, queued = 0, 0
        _STATS['peak_threads'] = max(_STATS['peak_threads'], threads)
        _STATS['peak_queue'] = max(_STATS['peak_queue'], queued)


def stats():
    """返回可复核的资源上限证据（不涉及真实账号/网络）。"""
    pool = get_pool()
    with _LOCK:
        try:
            threads = len(pool._threads)
            queued = pool._work_queue.qsize()
        except Exception:
            threads, queued = 0, 0
        return {
            'max_workers': pool.max_workers,
            'active_threads': threads,
            'queued_tasks': queued,
            'requests': _STATS['requests'],
            'timeouts': _STATS['timeouts'],
            'peak_threads': max(_STATS['peak_threads'], threads),
            'peak_queue': max(_STATS['peak_queue'], queued),
            'batches': _BATCH,
            'bounded': threads <= pool.max_workers and queued <= pool.max_workers * 4,
        }


def reset_stats():
    global _BATCH
    with _LOCK:
        for k in _STATS:
            _STATS[k] = 0
        _BATCH = 0
