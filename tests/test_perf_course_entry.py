# -*- coding: utf-8 -*-
"""课程列表 / 定位 性能回归（2026-11-19）。

背景：全局 `implicitly_wait(2)`，而课程列表探测是「预期找不到」的查找：
`_collect_course_cards` 有 10 个选择器 × 多个帧，每帧每个选择器失败都要白等 2 秒。
旧实现单轮最坏 40 秒以上，表现为「进入课程列表总是莫名卡很久」。

本测试用假 driver 精确模拟「隐式等待代价」：
- `find_elements` 未命中时，若 implicit_wait>0 就真 sleep 那么久；
- 未命中返回空列表。

断言：
1. `_wait_course_list` 在探测期间把隐式等待压到 0，耗时 < 4 秒；
2. 结束后隐式等待被精确还原；
3. `_click_text_keyword` 走 JS 快路径，一次命中时完全不做 Selenium 查找。

用法：C:/Python315/python.exe tests/test_perf_course_entry.py
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_perf_course_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core
from core import navigation as nav

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Timeouts:
    def __init__(self):
        self.implicit_wait = 2


class SwitchStub:
    def default_content(self):
        return None

    def frame(self, name):
        return None

    def window(self, h):
        return None


class SlowMissDriver:
    """未命中就按 implicit_wait 真睡，模拟 Selenium 的真实代价。"""

    def __init__(self):
        self.timeouts = Timeouts()
        self.switch_to = SwitchStub()
        self.implicit_seen = []
        self.find_calls = 0
        self.title = '课程'
        self.current_url = 'https://mooc1.chaoxing.com/'
        self.window_handles = ['h1']

    def implicitly_wait(self, seconds):
        self.timeouts.implicit_wait = seconds

    def find_elements(self, by, value):
        self.find_calls += 1
        self.implicit_seen.append(self.timeouts.implicit_wait)
        if self.timeouts.implicit_wait > 0:
            time.sleep(self.timeouts.implicit_wait)
        return []

    def find_element(self, by, value):
        self.find_calls += 1
        self.implicit_seen.append(self.timeouts.implicit_wait)
        if self.timeouts.implicit_wait > 0:
            time.sleep(self.timeouts.implicit_wait)
        raise LookupError('not found')

    def execute_script(self, *args):
        return None


def main():
    print('=== probe_mode ===')
    drv = SlowMissDriver()
    with nav.probe_mode(drv):
        check('进入探测时隐式等待被压到 0', drv.timeouts.implicit_wait == 0,
              drv.timeouts.implicit_wait)
    check('退出后精确还原为 2', drv.timeouts.implicit_wait == 2, drv.timeouts.implicit_wait)

    print()
    print('=== 课程列表探测速度 ===')
    drv = SlowMissDriver()
    t0 = time.perf_counter()
    ok = core._wait_course_list(drv, timeout=1)
    cost = time.perf_counter() - t0
    check('空列表时按超时返回 False', ok is False)
    check('探测期间隐式等待始终为 0（未命中不白等）',
          all(v == 0 for v in drv.implicit_seen), set(drv.implicit_seen))
    check('整体耗时 < 4 秒（旧实现单轮即 40 秒级）', cost < 4.0, '%.2fs' % cost)
    check('结束后隐式等待还原', drv.timeouts.implicit_wait == 2,
          drv.timeouts.implicit_wait)

    print()
    print('=== 菜单点击走 JS 快路径 ===')

    class JsDriver:
        def __init__(self):
            self.timeouts = Timeouts()
            self.switch_to = SwitchStub()
            self.find_calls = 0

        def implicitly_wait(self, seconds):
            self.timeouts.implicit_wait = seconds

        def find_elements(self, *a):
            self.find_calls += 1
            return []

        def execute_script(self, *args):
            return 'a=https://mooc1.chaoxing.com/course'

    jsd = JsDriver()
    hit = core._click_text_keyword(jsd, ('我学的课',), timeout=1.5)
    check('JS 命中即返回 True', hit is True)
    check('命中时不再做 Selenium 全量扫描', jsd.find_calls == 0, jsd.find_calls)

    print()
    print('=== 定位课程的上限 ===')
    drv = SlowMissDriver()
    t0 = time.perf_counter()
    found, names = core._locate_course(drv, '数据结构', timeout=1)
    cost = time.perf_counter() - t0
    check('找不到时按超时返回', found is False and names == [])
    check('定位耗时 < 4 秒', cost < 4.0, '%.2fs' % cost)
    check('空课程名直接拒绝', core._locate_course(drv, '  ', timeout=1) == (False, []))

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
