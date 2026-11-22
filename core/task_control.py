# -*- coding: utf-8 -*-
"""任务级停止信号、统一结果状态与有界等待（V5.1 P1 增补，2026-11-16）。

设计约束（不要改回去）：
* **不改变任何现有任务函数签名**。停止事件通过模块级可注入 getter 提供，
  main.py 里的 `study_page(...)` / `check_vido_finish(...)` / `play_audio(...)`
  调用点保持零改动（tests/test_video_question.py 对签名有严格断言）。
* 所有等待都有硬上限、可取消、有心跳日志、超时带诊断，绝不 `while True`。
* 结果状态统一，**「无法识别」绝不等于「已完成」**。
* 本模块不碰 WebDriver；driver 是否失效由调用方传进来的 check() 判定。

用法：
    from core import task_control as tc
    status, detail, elapsed = tc.bounded_wait(
        '音频播放完成', lambda: (self.check_audio_finished(), ''),
        timeout=tc.time_budget(seconds), poll_interval=1.0, heartbeat=30.0,
        diagnose=lambda: tc.page_snapshot(self.driver))
"""
from __future__ import annotations

import threading
import time

from core import performance as perf

# ---------------------------------------------------------------- 结果状态
class TaskStatus:
    """任务结果统一枚举（等价字符串常量，便于日志与测试断言）。"""
    SUCCESS = 'SUCCESS'            # 平台明确确认完成 / 保存成功
    SKIPPED = 'SKIPPED'            # 主动跳过（用户选择 / 无任务）
    FAILED = 'FAILED'              # 明确失败
    TIMEOUT = 'TIMEOUT'            # 等待超时
    CANCELLED = 'CANCELLED'        # 用户停止
    UNKNOWN = 'UNKNOWN'            # 无法识别任务点（≠ 全部完成）
    DRIVER_ERROR = 'DRIVER_ERROR'  # WebDriver/session 失效
    NO_TASK = 'NO_TASK'            # 页面已确认加载，确实没有任务
    ALL_DONE = 'ALL_DONE'          # 找到任务且平台确认全部完成


# ---------------------------------------------------------------- 停止信号
_STOP = threading.Event()
_EXTERNAL = None                   # 可注入任意带 is_set() 的对象
_LOCK = threading.RLock()


def set_stop_event(event):
    """注入外部停止事件（None 恢复使用进程内默认事件）。测试与 GUI 用。"""
    global _EXTERNAL
    with _LOCK:
        _EXTERNAL = event


def get_stop_event():
    with _LOCK:
        return _EXTERNAL if _EXTERNAL is not None else _STOP


def request_stop():
    """请求停止：唤醒所有用默认事件的有界等待。"""
    with _LOCK:
        ev = _EXTERNAL if _EXTERNAL is not None else _STOP
    try:
        ev.set()
    except Exception:
        pass


def clear_stop():
    with _LOCK:
        ev = _EXTERNAL if _EXTERNAL is not None else _STOP
    try:
        ev.clear()
    except Exception:
        pass


def is_stopped(event=None):
    ev = event if event is not None else get_stop_event()
    try:
        return bool(getattr(ev, 'is_set', lambda: False)())
    except Exception:
        return False


# ---------------------------------------------------------------- 时长预算
def parse_duration(text):
    """把 '1:30' / '01:02:03' / '90' / '' 解析成秒；非法返回 0.0。

    只用于计算超时预算，绝不用于缩短真实学习时间。
    """
    s = str(text or '').strip()
    if not s or s.startswith('-'):
        return 0.0
    parts = s.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 1:
            return float(parts[0])
    except (ValueError, TypeError, IndexError):
        return 0.0
    return 0.0


def time_budget(duration_seconds, factor=1.35, extra=300.0,
                floor=600.0, cap=3 * 3600):
    """按平台给出的时长生成等待上限：时长 × 系数 + 余量，绝对上限 3 小时。

    时长读不到时返回 floor（至少给 10 分钟并输出诊断），不会变成 0 秒超时。
    真实播放/观看时长由平台驱动，本函数只限制「最多等多久」。
    """
    try:
        d = float(duration_seconds or 0)
    except (ValueError, TypeError):
        d = 0.0
    if d <= 0:
        return float(floor)
    return float(min(cap, max(floor, d * factor + extra)))


# ---------------------------------------------------------------- 超时诊断
_DRIVER_FATAL = ('InvalidSessionId', 'NoSuchWindow', 'WebDriverException',
                 'SessionNotCreated', 'MaxRetry', 'ConnectionRefused',
                 'URLError', 'WebDriver')


def looks_like_driver_error(exc):
    """类型名 + 异常文本双重判断：不同 Selenium 版本的异常类名不一致。"""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(k.lower() in name or k.lower() in text for k in _DRIVER_FATAL)


def _safe(text, limit=180):
    s = str(text or '')
    # 简单脱敏：手机号、sk- 开头的 Key、Cookie 值片段
    import re
    s = re.sub(r'(?<!\d)(1\d{10})(?!\d)', lambda m: '****' + m.group(1)[-4:], s)
    s = re.sub(r'(?i)sk-[A-Za-z0-9_\-]{8,}', '***', s)
    s = re.sub(r'(?i)(cookie|token|sessionid)=[^\s&\'"]+', r'\1=***', s)
    return s[:limit]


def page_snapshot(driver):
    """超时诊断快照：只输出脱敏 URL / 标题，绝不输出 Cookie、密码、页面正文。"""
    bits = []
    for label, attr in (('URL', 'current_url'), ('标题', 'title')):
        try:
            bits.append('%s=%s' % (label, _safe(getattr(driver, attr, ''))))
        except Exception as e:
            bits.append('%s=<读取失败 %s>' % (label, type(e).__name__))
    return ' · '.join(bits)


# ---------------------------------------------------------------- 有界等待
def bounded_wait(name, check, timeout, poll_interval=1.0, stop_event=None,
                 heartbeat=30.0, diagnose=None, account_id=None):
    """有上限、可取消、有心跳、有诊断的条件等待。

    check() 返回 truthy 或 (ok, detail)；抛异常会记录但不立即退出，
    除非是 driver 失效类异常（直接返回 DRIVER_ERROR）。

    返回 (status, detail, elapsed_seconds)：
      SUCCESS / TIMEOUT / CANCELLED / DRIVER_ERROR。
    """
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout))
    poll = max(0.05, float(poll_interval or 1.0))
    hb = max(poll, float(heartbeat or 30.0))
    next_hb = started + hb
    last_detail = ''
    with perf.stage(name, account_id=account_id):
        while True:
            if is_stopped(stop_event):
                _log_wait(name, '用户停止，等待已取消', account_id)
                return TaskStatus.CANCELLED, '用户停止', time.monotonic() - started
            try:
                result = check()
                if isinstance(result, tuple):
                    ok, last_detail = bool(result[0]), _safe(result[1] if len(result) > 1 else '')
                else:
                    ok, last_detail = bool(result), ''
                if ok:
                    perf.mark_progress(account_id, name, '条件满足')
                    _log_wait(name, '条件满足，等待 %.1fs' % (time.monotonic() - started),
                              account_id)
                    return TaskStatus.SUCCESS, last_detail, time.monotonic() - started
            except Exception as e:
                last_detail = '%s: %s' % (type(e).__name__, _safe(str(e)))
                if looks_like_driver_error(e):
                    _log_wait(name, 'WebDriver 失效，停止等待 · %s' % last_detail, account_id)
                    return TaskStatus.DRIVER_ERROR, last_detail, time.monotonic() - started
                _log_wait(name, '检查异常（继续等待）· %s' % last_detail, account_id)

            now = time.monotonic()
            if now >= deadline:
                diag = ''
                if callable(diagnose):
                    try:
                        diag = _safe(diagnose())
                    except Exception as de:
                        diag = '诊断读取失败 %s' % type(de).__name__
                elapsed = now - started
                _log_wait(name, '超时 %.1fs · 最后状态=%s · %s' % (
                    elapsed, last_detail or '无', diag), account_id)
                return TaskStatus.TIMEOUT, '超时 · %s · %s' % (
                    last_detail, diag), elapsed
            if now >= next_hb:
                perf.mark_progress(account_id, name, '等待中 · %s' % last_detail)
                _log_wait(name, '已等待 %.0fs / 上限 %.0fs · 仍在等待「%s」· %s' % (
                    now - started, float(timeout), name, last_detail or '目标'), account_id)
                next_hb = now + hb
            time.sleep(min(poll, max(0.0, deadline - now)))


def _log_wait(name, message, account_id):
    """等待日志：走 applog（缓冲写），同时 print 给用户可见。"""
    line = '[等待][%s][%s] %s' % (str(account_id or '-')[:16], _safe(name), message)
    try:
        from core import applog
        applog.info(line)
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass
