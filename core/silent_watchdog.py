# -*- coding: utf-8 -*-
"""静默等待看门狗（2026-11-16 P1 卡顿修复）。

只**读** core.performance 里线程安全的进展缓存（mark_progress 写入），
**绝不并发调用 WebDriver** —— 这是硬红线：WebDriver 不是线程安全的，
看门狗线程碰 driver 会导致 session 错乱甚至串号。

发现长时间无进展时只输出分级诊断日志（10s 提示 / 30s 警告 / 60s 诊断），
由所属账号线程自己决定恢复动作；看门狗不刷新、不重试、不杀进程。
"""
from __future__ import annotations

import threading
import time

from core import applog, performance as perf

DEFAULT_THRESHOLDS = (10, 30, 60)   # 秒：提示 / 警告 / 诊断
_STOP = threading.Event()
_THREAD = None
_LAST_WARN = {}


def _safe(s):
    import re
    t = str(s or '')
    t = re.sub(r'(?<!\d)1\d{10}(?!\d)', '****', t)
    return t[:160]


def check_once(account_id, thresholds=DEFAULT_THRESHOLDS):
    """检查单个账号的静默时长，返回 'ok'|'info'|'warn'|'diagnose'。"""
    snap = perf.progress_snapshot(account_id)
    if not snap:
        return 'ok'
    last = float(snap.get('last_progress_at') or 0)
    if not last:
        return 'ok'
    silent = time.time() - last
    level = 'ok'
    if silent >= thresholds[2]:
        level = 'diagnose'
    elif silent >= thresholds[1]:
        level = 'warn'
    elif silent >= thresholds[0]:
        level = 'info'
    if level == 'ok':
        _LAST_WARN.pop(str(account_id), None)
        return level
    # 同一级别只告警一次，避免刷屏
    key = (str(account_id), level)
    if _LAST_WARN.get(key) == level:
        return level
    _LAST_WARN[key] = level
    prefix = {'info': '提示', 'warn': '警告', 'diagnose': '诊断'}[level]
    applog.info('[看门狗][%s][%s] 账号 %s 已静默 %.1fs ｜ 阶段=%s ｜ 操作=%s'
                '（仅诊断，看门狗不操作浏览器）' % (
                    prefix, _safe(account_id), _safe(account_id), silent,
                    _safe(snap.get('stage')), _safe(snap.get('operation'))))
    return level


def start(interval=5, thresholds=DEFAULT_THRESHOLDS):
    """启动后台看门狗线程（daemon）。重复调用不会启动多个。"""
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()

    def loop():
        while not _STOP.wait(max(1, int(interval))):
            try:
                for aid in list(perf.progress_snapshot().keys()):
                    check_once(aid, thresholds)
            except Exception:
                pass

    _THREAD = threading.Thread(target=loop, name='silent-watchdog', daemon=True)
    _THREAD.start()
    return _THREAD


def stop():
    """停止看门狗（窗口关闭/程序退出时调用）。"""
    _STOP.set()
    _LAST_WARN.clear()
