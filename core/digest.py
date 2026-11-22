# -*- coding: utf-8 -*-
"""通知用的轻量统计聚合（v3）。

只服务于「把单条事件通知写详细」，**不生成每日汇总、不发定时快照**。
所有读取都是 best-effort：任何失败都返回 0 / 空，绝不抛给刷课主流程。
"""
from __future__ import annotations

import os
import time

from core import paths, runtime


def _iter_lines(path: str, max_bytes=10 * 1024 * 1024):
    """流式逐行读日志（任务10）：避免 f.read() 一次性载入几 MB 全文。

    超大文件（> max_bytes）只读末尾 max_bytes，统计足够用且不拖慢通知。
    """
    try:
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()   # 丢弃可能被切断的第一行
            for line in f:
                yield line
    except OSError:
        return


def today_log_counts() -> dict:
    """统计当天主日志 / 运行日志里的错误与未识别页次数（按行内关键字计数）。"""
    out = {'errors': 0, 'unknown_pages': 0, 'quiz_lines': 0}
    candidates = [
        paths.root_data('logs', '程序日志.log'),
        paths.root_data('error.log'),
    ]
    try:
        for name in os.listdir(paths.DATA_ROOT):
            p = os.path.join(paths.DATA_ROOT, name)
            if os.path.isdir(p):
                candidates.append(os.path.join(p, '运行日志.log'))
    except OSError:
        pass
    for path in candidates:
        for line in _iter_lines(path):
            if ('ERROR' in line or 'Exception' in line or '错误' in line
                    or '处理出错' in line):
                out['errors'] += 1
            if '该页面无法识别' in line or '无作答选项' in line:
                out['unknown_pages'] += 1
            if '正在回答第' in line:
                out['quiz_lines'] += 1
    return out


_config_cache = None


def _config():
    """任务10：复用同一个 ConfigManager，避免每次通知都重新 load+凭据解密。"""
    global _config_cache
    if _config_cache is None:
        from core.config_manager import ConfigManager
        _config_cache = ConfigManager()
    return _config_cache


def video_count() -> int:
    try:
        return int(_config().count_video_records() or 0)
    except Exception:
        return 0


def course_process() -> dict:
    try:
        return _config().get_course_process() or {}
    except Exception:
        return {}


def elapsed_text(seconds) -> str:
    """秒 → 「1小时08分」/「34分12秒」。"""
    try:
        s = max(0, int(seconds or 0))
    except Exception:
        return '?'
    if s >= 3600:
        return '%d小时%02d分' % (s // 3600, (s % 3600) // 60)
    if s >= 60:
        return '%d分%02d秒' % (s // 60, s % 60)
    return '%d秒' % s


def build(event: str, account_id: str = None, course_name: str = None) -> dict:
    """返回一个扁平 dict 供埋点拼消息；字段缺失时用 0 / '?'。"""
    try:
        counts = today_log_counts()
    except Exception:
        counts = {'errors': 0, 'unknown_pages': 0}
    result = {
        'event': event,
        'account_id': account_id or '',
        'course_name': course_name or '',
        'videos': video_count(),
        'errors': counts.get('errors', 0),
        'unknown_pages': counts.get('unknown_pages', 0),
        # 没在进程内埋点时，用当天日志里「正在回答第N题」的行数做近似值
        'quiz_answered': counts.get('quiz_lines', 0),
        'quiz_skipped': counts.get('unknown_pages', 0),
        'discussion_ok': 0,
        'discussion_failed': 0,
        'elapsed': 0,
        'elapsed_text': '?',
    }
    try:
        st = runtime.stats()
        for key in ('quiz_answered', 'quiz_skipped', 'discussion_ok',
                    'discussion_failed', 'videos_done', 'courses_done'):
            value = int(st.get(key, 0) or 0)
            if value:
                result[key] = value
        started = float(st.get('started_at', 0) or 0)
        if started:
            result['elapsed'] = int(time.time() - started)
            result['elapsed_text'] = elapsed_text(result['elapsed'])
    except Exception:
        pass
    try:
        result['course_progress'] = course_process()
    except Exception:
        result['course_progress'] = {}
    return result
