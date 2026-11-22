# -*- coding: utf-8 -*-
"""日志行时间戳格式化（2026-11-19 新增，供界面日志与测试共用）。

放在独立模块里而不是写在 `start.py`：`start.py` 在模块级解析命令行并可能直接
拉起界面，测试无法安全导入；这里只做纯字符串处理，零副作用。
"""
from __future__ import annotations

import time

GUI_FMT = '%H:%M:%S'
FILE_FMT = '%Y-%m-%d %H:%M:%S'


def stamp(text: str, now: float | None = None, fmt: str = GUI_FMT) -> str:
    """给多行文本的每一行加 `[时间] ` 前缀，保留原有换行结构。

    - 空行不加前缀（避免日志里出现大量 `[12:00:00] ` 的空壳）；
    - 原文以换行结尾时，结果也以换行结尾，方便直接插入 tkinter Text。
    """
    raw = str(text or '')
    prefix = '[%s] ' % time.strftime(fmt, time.localtime(now))
    lines = raw.splitlines()
    if not lines:
        return raw
    out = '\n'.join((prefix + line) if line.strip() else line for line in lines)
    return out + ('\n' if raw.endswith('\n') else '')


def file_stamp(now: float | None = None) -> str:
    """磁盘日志用的带日期时间戳（精确到秒）。"""
    return time.strftime(FILE_FMT, time.localtime(now))
