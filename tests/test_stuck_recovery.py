# -*- coding: utf-8 -*-
"""2026-11-14 真机日志回归：1.9 章节卡死、自动退出、40 分钟空转、日志膨胀。

日志包：`_AI归档/04_运行日志/_导出日志_20261114_224502/`（另一台电脑 26.11.12.2151）
  · 22:02~22:42 一直在同一个 chapterId 上「PPT 无任务点→跳过→下一页」空转；
  · 22:40 起 1.9 章节是 downloadfile 附件页 / 空白节，被判「无法识别」11 次；
  · 22:42:48 `连续 10 页都无法识别，结束本课程` → 22:43:35 `回不到课程页，结束本课程`
    → 子进程退出（用户看到的"直接自动退出"）；
  · gui.log 每次字体解密都 INFO 级 dump 十几张字体表，持续写盘。

用法：C:/Python315/python.exe tests/test_stuck_recovery.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_stuck_')

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class WaitDriver:
    """wait_page_message 兜底所需的最小 driver 桩。"""
    def __init__(self, url, ready, frames):
        self.current_url = url
        self._ready = ready
        self._frames = frames

    def execute_script(self, script, *args):
        return self._ready

    def find_elements(self, by, selector):
        return self._frames if by == By.TAG_NAME else []


class NoButtonDriver:
    """永远找不到「下一页」按钮的 driver 桩。"""
    def __init__(self):
        self.switch_to = SimpleNamespace(
            default_content=lambda: None, frame=lambda _x: None)
        self.window_handles = []

    def find_elements(self, by, selector):
        return []

    def find_element(self, *args):
        raise NoSuchElementException('no button')

    def execute_script(self, *args):
        return None


def main():
    src = Path('main.py').read_text(encoding='utf-8')
    start_src = Path('start.py').read_text(encoding='utf-8')
    study = 'https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1209433885&courseId=2'

    print('=== 1. 附件下载页 / 空白节不再被当成异常页 ===')
    check('CONTENT_TYPES 收录 downloadfile 附件页',
          "'附件', By.CSS_SELECTOR, '[src*=\"/ananas/modules/downloadfile/index\"]'" in src)

    with patch.object(core, 'page_message', return_value={}), \
         patch('time.sleep', lambda *a: None):
        empty = core.wait_page_message(WaitDriver(study, 'complete', []), tries=3, interval=2)
    check('学习页 + 加载完成 + 无 iframe → 判为「无任务点」',
          empty.get('无任务点') == 'empty-section', empty)

    with patch.object(core, 'page_message', return_value={}), \
         patch('time.sleep', lambda *a: None):
        has_frame = core.wait_page_message(WaitDriver(study, 'complete', [object()]),
                                          tries=3, interval=2)
    check('仍有 iframe 时不误判为空节（保留漏识别的诊断能力）',
          has_frame == {}, has_frame)

    with patch.object(core, 'page_message', return_value={}), \
         patch('time.sleep', lambda *a: None):
        not_ready = core.wait_page_message(WaitDriver(study, 'loading', []),
                                          tries=3, interval=2)
    check('页面未加载完成时不兜底（避免漏掉还在加载的任务点）',
          not_ready == {}, not_ready)

    with patch.object(core, 'page_message', return_value={}), \
         patch('time.sleep', lambda *a: None):
        other = core.wait_page_message(WaitDriver('https://i.chaoxing.com/base', 'complete', []),
                                       tries=3, interval=2)
    check('非学习页不兜底', other == {}, other)

    # dispatch_page 只处理已知 key，附件/无任务点页会被安全忽略、直接翻页
    try:
        core.dispatch_page(None, {'附件': 'downloadfile'}, '课程', '答题', '',
                           False, False, '', '跳过讨论', '', False, 1)
        dispatched = True
    except Exception as exc:
        dispatched = False
        print('   dispatch 异常:', exc)
    check('附件页被 dispatch 安全忽略（不误跑任何处理器）', dispatched)

    print('=== 2. 刷新后不再误判「换页成功」（40 分钟空转） ===')
    check('刷新后的 changed 只认 chapterId 等稳定身份',
          'changed = bool(new_identity and new_identity != identity)' in src)
    check('不再用 signature 抖动当换页凭据',
          'or new_signature != signature)' not in src)
    check('连续无法识别时诊断截图降频', 'unknown_count % 3 == 0' in src)

    print('=== 3. 找不到「下一页」不再把课程判成「已完结」 ===')
    check('run() 每轮清空全局 error_num 与放弃标记',
          'global error_num, _NEXT_GAVE_UP' in src
          and 'error_num = 0\n    _NEXT_GAVE_UP = False\n    unknown_count = 0' in src)

    with patch.object(core, '_find_next_button', return_value=None), \
         patch('time.sleep', lambda *a: None):
        core.error_num = 0
        core._NEXT_GAVE_UP = False
        results = [core.click_next_page(NoButtonDriver(), 0) for _ in range(8)]
    check('前 7 次找不到按钮只是等待', results[:7] == [True] * 7, results)
    check('第 8 次放弃返回 False', results[7] is False)
    check('放弃时打标记（区别于课程真的完结）', core._NEXT_GAVE_UP is True)
    check('run() 收到放弃信号返回 lost 而非 done',
          "return 'lost' if gave_up else 'done'" in src)

    # 课程真的完结时仍应返回 done（不能把正常完结改坏）
    core.error_num = 0
    core._NEXT_GAVE_UP = False
    # 2026-11-21：原来的「🎉 撒花」分支是**死代码**（_try_click 自 P0-5 起
    # 只会返回 clicked/none，永远不会有 'finished'），所以那条断言一直在
    # 给已被删除的行为背书。现在改为断言真正生效的完结判据。
    check('课程完结路径未改动',
          '❌ 连续多次找不到「下一页」，回课程页重新定位' in src
          and '本页任务点已全部完成，且没有下一页' in src)

    print('=== 4. 回课程页失败先重试，不立刻放弃 ===')
    check('重进失败会刷新重试', '刷新后重试' in src and 'continue' in src)

    print('=== 5. 日志噪音降噪（越用越慢） ===')
    check('fontTools / httpx 的 INFO 日志被压到 WARNING',
          "('fontTools', 'fontTools.ttLib', 'httpx', 'httpcore')" in start_src
          and 'setLevel(logging.WARNING)' in start_src)

    print('---')
    if FAILS:
        print('FAILED=%d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
