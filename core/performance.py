# -*- coding: utf-8 -*-
"""统一性能诊断（2026-11-16 P1 启动慢/静默卡顿修复）。

提供：
* stage(name, account_id, details)：上下文计时，记录开始/完成/失败与耗时；
* timed_call(name, func, ...)：函数调用计时；
* wait_until(name, condition, timeout, poll_interval, stop_event)：条件等待，
  成功/超时/取消/异常都有日志，绝不无限循环；
* snapshot() / report()：每阶段的 P50 / P95 / max 与调用次数。

设计约束：
* 计时器只在进程内存里累积，**不每次同步刷盘**（不拖慢主流程）；
* 日志走 core.applog（缓冲写），且**自动脱敏**：手机号只留后四位，
  绝不输出密码 / Cookie / token；
* 线程安全（多账号子进程各自独立，同进程内多线程也安全）。
"""
from __future__ import annotations

import statistics
import threading
import time
from contextlib import contextmanager

from core import applog

_LOCK = threading.RLock()
_RECORDS = {}          # name -> list[duration_seconds]
_EVENTS = {}           # account_id -> {'stage', 'operation', 'last_progress_at'}
_MAX_RECORDS = 2000

# 日志脱敏（与 login_identity 保持一致，但避免循环 import）
import re
_PHONE_RE = re.compile(r'(?<!\d)(1\d{10})(?!\d)')
_TOKEN_RE = re.compile(r'(?i)(sk-[A-Za-z0-9_\-]{8,}|token=)[^\s&"\']*')


def _safe(text):
    s = str(text or '')
    s = _PHONE_RE.sub(lambda m: '****' + m.group(1)[-4:], s)
    s = _TOKEN_RE.sub('***', s)
    return s[:300]


def _tag(account_id=None):
    return str(account_id or '-')[:16]


def _log(level, name, account_id, message):
    line = '[性能][%s][%s] %s' % (_tag(account_id), _safe(name), _safe(message))
    try:
        if level == 'warn':
            applog.warn(line)
        else:
            applog.info(line)
    except Exception:
        pass


def _record(name, duration):
    with _LOCK:
        bucket = _RECORDS.setdefault(name, [])
        bucket.append(duration)
        if len(bucket) > _MAX_RECORDS:
            del bucket[:len(bucket) - _MAX_RECORDS]


@contextmanager
def stage(name, account_id=None, details=None):
    """阶段计时上下文：自动记录开始、完成/异常与耗时。"""
    start = time.perf_counter()
    _log('info', name, account_id, '开始' + (' · %s' % details if details else ''))
    ok = False
    try:
        yield
        ok = True
    except Exception as e:
        _log('warn', name, account_id, '异常：%s（耗时 %.2fs）' % (
            type(e).__name__, time.perf_counter() - start))
        raise
    finally:
        dur = time.perf_counter() - start
        _record(name, dur)
        _log('info', name, account_id,
             '%s，耗时 %.2fs' % ('完成' if ok else '结束', dur))


def timed_call(name, func, *args, account_id=None, **kwargs):
    """带计时的函数调用；异常照常抛出但会记录。"""
    start = time.perf_counter()
    _log('info', name, account_id, '调用开始')
    try:
        return func(*args, **kwargs)
    except Exception:
        _log('warn', name, account_id,
             '调用异常（耗时 %.2fs）' % (time.perf_counter() - start))
        raise
    finally:
        dur = time.perf_counter() - start
        _record(name, dur)
        _log('info', name, account_id, '调用结束，耗时 %.2fs' % dur)


def wait_until(name, condition, timeout, poll_interval=0.5,
               stop_event=None, account_id=None, url=None, title=None):
    """有超时/取消/日志的条件等待。

    condition() 返回 truthy 即成功；返回 (ok, detail) 时也支持。
    返回 (ok: bool, detail: str)。绝不无限循环。
    """
    deadline = time.perf_counter() + max(0.01, float(timeout))
    poll = max(0.05, float(poll_interval or 0.5))
    last_detail = ''
    next_log = deadline - min(float(timeout) * 0.5, 10.0)
    _log('info', name, account_id, '等待开始（超时 %.1fs，间隔 %.1fs）' % (timeout, poll))
    while True:
        if stop_event is not None and getattr(stop_event, 'is_set', lambda: False)():
            _log('warn', name, account_id, '用户取消等待')
            return False, 'cancelled'
        try:
            r = condition()
            if isinstance(r, tuple):
                ok, last_detail = r
            else:
                ok, last_detail = bool(r), ''
            if ok:
                dur = float(timeout) - max(0.0, deadline - time.perf_counter())
                _record(name, dur)
                _log('info', name, account_id,
                     '条件满足，等待 %.2fs%s' % (
                         dur, (' · %s' % last_detail) if last_detail else ''))
                return True, last_detail
        except Exception as e:
            last_detail = '%s: %s' % (type(e).__name__, e)
            _log('warn', name, account_id, '条件检查异常：%s' % last_detail)
        if time.perf_counter() >= deadline:
            _record(name, float(timeout))
            _log('warn', name, account_id,
                 '超时 %.1fs%s%s%s' % (
                     timeout,
                     (' · 当前URL=%s' % _safe(url)) if url else '',
                     (' · 标题=%s' % _safe(title)) if title else '',
                     (' · %s' % _safe(last_detail)) if last_detail else ''))
            return False, 'timeout'
        if time.perf_counter() >= next_log:
            _log('warn', name, account_id,
                 '已等待 %.1fs，仍未满足条件%s' % (
                     float(timeout) - (deadline - time.perf_counter()),
                     (' · %s' % _safe(last_detail)) if last_detail else ''))
            next_log += poll * 4
        time.sleep(poll)


def mark_progress(account_id, stage_name, operation=''):
    """更新账号最近进展时间（看门狗读取，不直接操作 WebDriver）。"""
    with _LOCK:
        _EVENTS[str(account_id or '-')] = {
            'stage': _safe(stage_name),
            'operation': _safe(operation),
            'last_progress_at': time.time(),
        }


def progress_snapshot(account_id=None):
    with _LOCK:
        if account_id is not None:
            return dict(_EVENTS.get(str(account_id), {}))
        return {k: dict(v) for k, v in _EVENTS.items()}


def _percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return round(s[idx], 4)


def report():
    """返回 {阶段: {'count','p50','p95','max','avg'}}，单位秒。"""
    with _LOCK:
        out = {}
        for name, vals in _RECORDS.items():
            out[name] = {
                'count': len(vals),
                'p50': _percentile(vals, 0.50),
                'p95': _percentile(vals, 0.95),
                'max': round(max(vals), 4),
                'avg': round(statistics.fmean(vals), 4),
            }
        return out


def reset():
    """测试用：清空统计。"""
    with _LOCK:
        _RECORDS.clear()
        _EVENTS.clear()
