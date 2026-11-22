# -*- coding: utf-8 -*-
"""「点了菜单没反应」快速放弃回归（2026-11-19）纯桩测试。

## 真机问题

用户日志（26.11.19.1508）：

```
15:30:23 已点击「我学的课」（JS：li=我学的课|cls=|onclick=）
15:30:27 点了「我学的课」但列表没出来，换一种方式重试
15:30:33 点了「我学的课」但列表没出来，换一种方式重试
15:30:37 点了「我学的课」但列表没出来，换一种方式重试
15:30:47 菜单没点进去，改为直接打开课程列表页
15:30:49 已通过地址直接打开课程列表
```

`cls=` 与 `onclick=` 都是空 —— 点中的是一个**既没有 href、也没有 onclick、连 class
都没有的空 `<li>`**，这种节点上不可能绑定跳转事件，点多少次都不会有反应。
而旧实现每次点完还要老老实实等 4 秒 × 多次，于是 26 秒才走到 URL 兜底。

## 本测试锁定的行为

1. `_click_detail_is_dead()` 能识别这种死节点，且不误伤有 class/onclick/data-href 的。
2. `_click_course_tab()` 遇到死节点时**快速失败**（< 3.5 秒，旧实现要 12 秒以上）。
3. `_wait_course_list(accept_url=True)` 在地址已是课程列表页时立即成功。
4. `_wait_course_list(accept_url=False)` 在地址匹配但页面没有卡片时**仍然失败**
   —— 否则三个兜底 URL 会全部「假成功」，等于没验证。

用法：C:/Python315/python.exe tests/test_course_tab_dead_click.py
"""
import io
import os
import sys
import time
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = os.path.join(ROOT, 'tests', '_tmp_dead_click')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Switch:
    def default_content(self):
        pass

    def frame(self, name):
        pass

    def window(self, handle):
        pass


class DeadClickDriver:
    """页面里只有那个「死 <li>」，没有课程卡片，点完地址也不变。"""

    def __init__(self, url='https://i.chaoxing.com/base'):
        self.current_url = url
        self.title = '学习通'
        self.window_handles = ['w1']
        self.switch_to = Switch()
        self.implicitly_wait_calls = []
        self.js_calls = 0

    def implicitly_wait(self, seconds):
        self.implicitly_wait_calls.append(seconds)

    def find_element(self, by, value):
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        return []

    def execute_script(self, script, *args):
        # 注意：不能用 'location.href' 区分两个脚本 —— `_CLICK_BY_TEXT_JS` 里也有
        # `new URL(..., location.href)`。用各自独有的特征更稳。
        if 'pick(' in script:            # _CLICK_BY_TEXT_JS：返回真机日志里那条死节点记录
            self.js_calls += 1
            return 'li=我学的课|cls=|onclick=|dh='
        return ''                        # _GOTO_BY_TEXT_JS：模拟找不到可跳转链接


class ListUrlDriver(DeadClickDriver):
    """地址已经是课程列表页，但卡片选择器一个都匹配不上。"""

    def __init__(self):
        super().__init__(url='https://mooc1.chaoxing.com/visit/courses')


def main():
    print('=== 死节点点击快速失败 ===')

    # 1) 死节点判定
    check('真机日志里的空 li 判为死节点',
          core._click_detail_is_dead('li=我学的课|cls=|onclick=|dh=') is True)
    check('只有 cls 也算能点',
          core._click_detail_is_dead('li=我学的课|cls=nav-item|onclick=|dh=') is False)
    check('有 onclick 算能点',
          core._click_detail_is_dead('li=我学的课|cls=|onclick=go()|dh=') is False)
    check('有 data-href 算能点',
          core._click_detail_is_dead('li=我学的课|cls=|onclick=|dh=/visit/courses') is False)
    check('a=/inner= 永远不算死节点',
          core._click_detail_is_dead('a=https://mooc1.chaoxing.com/visit/courses') is False
          and core._click_detail_is_dead('inner=https://x/y') is False)
    check('空 detail 不算死节点', core._click_detail_is_dead('') is False)

    # 2) 遇到死节点必须快速失败（不能等到 timeout）
    drv = DeadClickDriver()
    buf = io.StringIO()
    started = time.time()
    with redirect_stdout(buf):
        result = core._click_course_tab(drv, timeout=15)
    elapsed = time.time() - started
    log = buf.getvalue()
    check('死节点返回 False', result is False)
    check('死节点 3.5 秒内放弃（旧实现 >12 秒）', elapsed < 3.5, '%.2fs' % elapsed)
    check('日志说明为什么放弃', '没有链接也没有点击事件' in log, log.strip()[-90:])
    check('没有重复点同一个死节点', drv.js_calls <= 2, drv.js_calls)
    # 3) 地址判定
    check('识别 /visit/courses',
          core._looks_like_course_list_url('https://mooc1.chaoxing.com/visit/courses') is True)
    check('识别 /mooc2-ans/visit/courses/list',
          core._looks_like_course_list_url(
              'https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courses/list') is True)
    check('首页不算课程列表',
          core._looks_like_course_list_url('https://i.chaoxing.com/base') is False)
    check('空地址不算课程列表', core._looks_like_course_list_url('') is False)

    # 4) 地址已对时 accept_url=True 立即成功
    list_drv = ListUrlDriver()
    started = time.time()
    ok = core._wait_course_list(list_drv, timeout=8, accept_url=True)
    fast = time.time() - started
    check('地址已对时立即判定成功', ok is True)
    check('不额外轮询（< 0.5 秒）', fast < 0.5, '%.2fs' % fast)

    # 5) 验证兜底 URL 时必须真的看到卡片（accept_url=False）
    ok = core._wait_course_list(ListUrlDriver(), timeout=1, accept_url=False)
    check('accept_url=False 时不认地址、只看卡片', ok is False)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
