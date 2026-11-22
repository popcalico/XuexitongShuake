# -*- coding: utf-8 -*-
"""外部命令统一入口：**绝不弹出控制台黑窗**（2026-11-19）。

## 为什么需要这个模块

本程序用 PyInstaller `--noconsole` 打包，GUI 进程**没有控制台**。
这时只要调用一次控制台程序（`icacls` / `taskkill` / `msedgedriver --version`），
Windows 就会为它**新建一个控制台窗口**——用户看到的就是「黑窗口闪一下」。

实测到的放大效应：保存一次设置要重新加密 4 个敏感字段，每个字段写盘时
调 2 次 `icacls`，于是**一次保存闪 8 个黑窗**；启动时「凭据迁移校验」再走一遍
同样的路径，所以「一启动就闪一大堆」。每个 `icacls` 还要付进程创建开销
（约 30~60ms），8 次就是几百毫秒，这就是「更慢了」的来源之一。

## 用法

```python
from core import subproc
subproc.run_hidden(['icacls', path, '/grant:r', '%s:F' % user])
subproc.run_hidden(['taskkill', '/F', '/T', '/PID', str(pid)])
```

签名与 `subprocess.run` 一致，额外保证：Windows 下带 `CREATE_NO_WINDOW`
与 `STARTF_USESHOWWINDOW`/`SW_HIDE` 双重隐藏（有些程序只认 startupinfo）。
调用方自己传了 `creationflags` / `startupinfo` 时以调用方为准，不覆盖。

非 Windows 平台直接透传，不做任何改造。
"""
from __future__ import annotations

import os
import subprocess

# SW_HIDE：给 STARTUPINFO.wShowWindow 用
SW_HIDE = 0

# 统计：便于测试与日志确认「一次操作到底起了几个进程」
_CALLS = {'count': 0, 'commands': []}


def hidden_kwargs() -> dict:
    """返回 Windows 下隐藏控制台所需的 kwargs（非 Windows 返回空字典）。"""
    if os.name != 'nt':
        return {}
    kwargs = {
        # CREATE_NO_WINDOW(0x08000000)：不为子进程新建控制台
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000),
    }
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = SW_HIDE
        kwargs['startupinfo'] = info
    except Exception:
        # 极端情况下拿不到 STARTUPINFO，仅靠 creationflags 也能挡住绝大多数
        pass
    return kwargs


def run_hidden(args, **kwargs):
    """同 `subprocess.run`，但 Windows 下完全不闪窗。"""
    for key, value in hidden_kwargs().items():
        kwargs.setdefault(key, value)
    _CALLS['count'] += 1
    try:
        _CALLS['commands'].append(str(args[0] if isinstance(args, (list, tuple)) else args)[:40])
    except Exception:
        pass
    return subprocess.run(args, **kwargs)


def popen_hidden(args, **kwargs):
    """同 `subprocess.Popen`，但 Windows 下完全不闪窗。"""
    for key, value in hidden_kwargs().items():
        kwargs.setdefault(key, value)
    _CALLS['count'] += 1
    try:
        _CALLS['commands'].append(str(args[0] if isinstance(args, (list, tuple)) else args)[:40])
    except Exception:
        pass
    return subprocess.Popen(args, **kwargs)


def stats():
    """返回已发起的子进程统计（只读快照，供测试/诊断使用）。"""
    return {'count': _CALLS['count'], 'commands': list(_CALLS['commands'])}


def reset_stats():
    _CALLS['count'] = 0
    _CALLS['commands'] = []
