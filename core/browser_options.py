# -*- coding: utf-8 -*-
"""浏览器启动选项的集中生成与审查（2026-11-16 任务04）。

把 `--disable-web-security` 这类安全削弱参数从默认启动参数里移除，
集中到本模块管理，便于纯桩测试与主工程/单文件同步。

审查结论（见 tests/test_browser_security_options.py 与交付报告）：
* `--disable-web-security`：移除。三个 crx 扩展都是 Manifest V3，跨域权限靠
  manifest 的 `<all_urls>` / `activeTab` 声明；答题扩展经 Selenium 与扩展 UI
  交互读取答案，不依赖浏览器同源策略关闭。代码里无 fetch/XHR 跨域直连。
  仍保留 `browser_disable_web_security=1` 的显式逃生开关，供确有需要的
  学校页面真机验证后手动开启（默认 0，绝不默认削弱安全）。
* `--enable-extensions`：保留（加载 crx 必需，非安全削弱）。
* `--disable-blink-features=AutomationControlled`：保留（降低自动化特征，
  属于反爬对抗；不改变同源策略，不是跨域安全削弱）。
"""
from __future__ import annotations

from core import runtime


def build_argument_list(*, speed='1', load_speed_extension=True,
                        load_answer_extension=True, disable_web_security=None):
    """返回浏览器启动参数列表（纯函数，便于测试）。

    disable_web_security=None 时读取 runtime 快照 `browser_disable_web_security`
    （默认 0）。传 True/1 才加入 `--disable-web-security`。
    """
    args = ['--disable-blink-features=AutomationControlled']
    if load_speed_extension and str(speed) != '1':
        args.append('--enable-extensions')
    if load_answer_extension:
        args.append('--enable-extensions')
    # 去重，保持顺序
    seen = set()
    args = [a for a in args if not (a in seen or seen.add(a))]
    if disable_web_security is None:
        try:
            disable_web_security = int(runtime.get('browser_disable_web_security', 0) or 0)
        except (TypeError, ValueError):
            disable_web_security = 0
    if disable_web_security:
        args.append('--disable-web-security')
    return args


def extension_flags(*, speed='1', load_speed_extension=True,
                    load_answer_extension=True):
    """返回 (是否加载倍速扩展, 是否加载答题扩展)，供 start_browser 使用。"""
    return (bool(load_speed_extension and str(speed) != '1'),
            bool(load_answer_extension))
