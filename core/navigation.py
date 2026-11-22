# -*- coding: utf-8 -*-
"""WebDriver 导航超时与阶段计时（V5.1 P1 第 23 节，2026-11-16）。

工程原先只有 `driver.implicitly_wait(2)`，没有 page_load / script 超时：
`driver.get()` 在国内网络/平台慢时可能长时间阻塞且没有任何日志。

本模块提供：
* `configure_timeouts(driver)`：创建 driver 后**首次导航前**设置超时；
  只调用 driver 真实存在的方法，fake driver 缺方法时静默跳过。
* `navigate()` / `refresh()` / `eval_script()`：带阶段计时的导航封装，
  记录开始/完成/超时/异常，超时后读取脱敏 URL/标题并检查 session 是否仍有效。
* **导航超时 ≠ 登录失败**：`navigate()` 只返回导航结果，身份认证由
  `main.login_study()` 的状态机单独判定，两者不混为一谈。

保留默认 `page_load_strategy`（normal）：不改成 `none`，因为工程大量
代码依赖「get 返回时页面已就绪」，改成 eager/none 会需要全链路补就绪条件。
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from core import performance as perf
from core.task_control import TaskStatus, _safe

DEFAULT_PAGE_LOAD_TIMEOUT = 45
DEFAULT_SCRIPT_TIMEOUT = 30
DEFAULT_NAV_RETRIES = 1          # 首次 + 1 次有限重试，绝不无限刷新


@contextmanager
def probe_mode(driver):
    """把隐式等待临时压到 0，用于「探测型」查找（2026-11-19 性能修复）。

    背景：工程全局 `implicitly_wait(2)`。凡是**预期可能找不到**的查找
    （遍历 5 个 iframe 找课程卡片、按文字找菜单、轮询等元素），
    每次失败都要白等 2 秒；`_collect_course_cards` 有 10 个选择器 × 5 个帧，
    最坏一轮就是 100 秒，这就是「进入课程列表总是莫名其妙卡很久」的主因。

    探测场景本来就自己带轮询与超时，不需要隐式等待。退出时精确还原原值。
    """
    saved = None
    try:
        saved = driver.timeouts.implicit_wait
    except Exception:
        saved = None
    try:
        driver.implicitly_wait(0)
    except Exception:
        pass
    try:
        yield
    finally:
        if saved is not None:
            try:
                driver.implicitly_wait(saved)
            except Exception:
                pass


def configure_timeouts(driver, page_load=None, script=None):
    """设置页面加载/脚本执行超时；返回实际生效的设置字典。"""
    applied = {}
    for label, method, value in (
        ('page_load_timeout', 'set_page_load_timeout',
         DEFAULT_PAGE_LOAD_TIMEOUT if page_load is None else page_load),
        ('script_timeout', 'set_script_timeout',
         DEFAULT_SCRIPT_TIMEOUT if script is None else script),
    ):
        fn = getattr(driver, method, None)
        if not callable(fn):
            applied[label] = 'unsupported'
            continue
        try:
            fn(value)
            applied[label] = value
        except Exception as e:
            applied[label] = 'failed:%s' % type(e).__name__
    return applied


def session_alive(driver):
    """轻量检查 session 是否仍有效（不刷新、不导航）。"""
    try:
        _ = driver.title
        _ = driver.current_url
        return True
    except Exception:
        return False


def _snapshot(driver):
    try:
        from core.task_control import page_snapshot
        return page_snapshot(driver)
    except Exception:
        return '<快照不可用>'


def navigate(driver, url, name='页面导航', account_id=None, retries=None,
             before=None):
    """带超时/有限重试/诊断的 driver.get()。

    返回 (ok: bool, status: str, detail: str)。
    超时后先检查 URL/标题与 session 有效性，再决定是否有限重试；
    绝不在超时后盲目继续执行任务识别。
    """
    limit = DEFAULT_NAV_RETRIES if retries is None else max(0, int(retries))
    attempts = limit + 1
    last = ''
    for attempt in range(1, attempts + 1):
        label = '%s#%d' % (name, attempt) if attempts > 1 else name
        start = time.perf_counter()
        try:
            with perf.stage(label, account_id=account_id,
                            details='目标=%s' % _safe(url, 120)):
                if callable(before):
                    before()
                driver.get(url)
            dur = time.perf_counter() - start
            return True, TaskStatus.SUCCESS, '导航完成 %.2fs' % dur
        except Exception as e:
            dur = time.perf_counter() - start
            kind = type(e).__name__
            last = '%s: %s' % (kind, _safe(str(e)))
            alive = session_alive(driver)
            detail = ('耗时 %.2fs · %s · session=%s · %s'
                      % (dur, last, '有效' if alive else '失效', _snapshot(driver)))
            _warn(name, '导航失败/超时（第 %d/%d 次）· %s'
                  % (attempt, attempts, detail), account_id)
            if 'Timeout' not in kind and 'Timeout' not in last and alive:
                # 非超时类异常且 session 仍好：不重试，直接交由上层决定
                return False, TaskStatus.DRIVER_ERROR, detail
            if not alive and attempt >= attempts:
                return False, TaskStatus.DRIVER_ERROR, detail
    return False, TaskStatus.TIMEOUT, '导航超时，已用尽 %d 次尝试 · %s' % (attempts, last)


def refresh(driver, name='页面刷新', account_id=None):
    """带计时的 refresh；异常返回 False + 状态，不吞掉错误。"""
    try:
        with perf.stage(name, account_id=account_id):
            driver.refresh()
        return True, TaskStatus.SUCCESS, '刷新完成'
    except Exception as e:
        alive = session_alive(driver)
        detail = '%s: %s · session=%s · %s' % (
            type(e).__name__, _safe(str(e)),
            '有效' if alive else '失效', _snapshot(driver))
        _warn(name, '刷新失败 · ' + detail, account_id)
        return False, (TaskStatus.DRIVER_ERROR if not alive
                       else TaskStatus.FAILED), detail


def eval_script(driver, script, *args, name='脚本执行', account_id=None):
    """带计时的 execute_script；异常带诊断返回，不静默 pass。"""
    try:
        with perf.stage(name, account_id=account_id):
            return driver.execute_script(script, *args)
    except Exception as e:
        detail = '%s: %s · session=%s' % (
            type(e).__name__, _safe(str(e)), '有效' if session_alive(driver) else '失效')
        _warn(name, '脚本执行失败 · ' + detail, account_id)
        raise


def _warn(name, message, account_id):
    line = '[导航][%s][%s] %s' % (str(account_id or '-')[:16], _safe(name), message)
    try:
        from core import applog
        applog.warn(line)
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass
