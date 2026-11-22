# -*- coding: utf-8 -*-
"""章节测验提交结果判定回归（2026-11-19）纯桩测试。

## 真机问题（用户 15:39 那次小测）

```
15:40:30 3秒后提交
15:40:41 未查询到本次测试成绩
15:40:41 已保存进度
15:40:41 第1个测验平台未确认完成，不写学习记录
```

「已保存进度」是 `save_score()` 打的，说明**提交按钮确实点了、也走完了提交流程**；
但上层仍判「平台未确认完成」。

## 两个根因

1. **iframe 句柄失效**：章节测验提交后 `#iframe` 文档会整页重载，`Common.__init__`
   时存下的 `self.iframe` 立刻变成 stale。`platform_finished()` 重切了帧，
   却仍拿这个旧句柄去 `find_element('..')` → 抛 stale → 返回 False →
   上层打印「平台未确认完成」。**提交成功也会被判成没完成。**
2. **确认按钮点不到也无法区分**：旧 `_click_popup_ok()` 只试 `#popok`，
   而且异常直接吞掉，既不告诉调用方「没找到弹窗」也不说「有弹窗但点不动」。

## 本测试锁定的行为

1. 提交后旧句柄 stale 时，`check_audio_finished()` 能按「选择器 + 序号」重新定位，
   并正确读到父容器的 `ans-job-finished`。
2. `platform_finished()` 第一次读不到时等一次再读（平台打勾是异步的）。
3. `_click_popup_ok()` 三态：`clicked` / `none` / `failed`。
4. `submit()` 在确认按钮点不到时**只保存不提交**，不假装成功。

用法：C:/Python315/python.exe tests/test_quiz_submit_recovery.py
"""
import io
import os
import sys
import time
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = os.path.join(ROOT, 'tests', '_tmp_submit_recovery')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By

from task.tool import common
from task import quiz_ai

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- 桩
class Parent:
    def __init__(self, cls):
        self.cls = cls

    def get_attribute(self, name):
        return self.cls if name == 'class' else ''


class StaleIframe:
    """提交前的旧句柄：任何操作都抛 stale。"""

    def find_element(self, by, value):
        raise StaleElementReferenceException('stale after submit')

    def find_elements(self, by, value):
        raise StaleElementReferenceException('stale after submit')


class FreshIframe:
    """刷新后重新定位到的句柄。"""

    def __init__(self, cls='ans-attach-ct ans-job-finished', delay_reads=0):
        self.cls = cls
        self.reads = 0
        self.delay_reads = delay_reads

    def find_element(self, by, value):
        self.reads += 1
        if self.reads <= self.delay_reads:
            raise WebDriverException('任务点还没打勾')
        return Parent(self.cls)

    def find_elements(self, by, value):
        return []


class Switch:
    def default_content(self):
        pass

    def frame(self, name):
        pass


class Driver:
    def __init__(self, iframes):
        self.iframes = list(iframes)
        self.switch_to = Switch()

    def find_elements(self, by, value):
        return list(self.iframes) if by == By.CSS_SELECTOR else []


def make_common(iframe, iframes, index=0):
    obj = common.Common.__new__(common.Common)
    obj.course_name = '测试课'
    obj.type = '测验'
    obj.driver = Driver(iframes)
    obj.iframe = iframe
    obj.iframe_selector = 'iframe[src*="/ananas/modules/work/index.html"]'
    obj.iframe_index = index
    obj.require_mission = False
    return obj


# ---------------------------------------------------------------- 用例
def main():
    print('=== 测验提交结果判定 ===')

    # 1) 旧句柄 stale -> 重新定位后能读到已完成
    stale = StaleIframe()
    fresh = FreshIframe('ans-attach-ct ans-job-finished')
    obj = make_common(stale, [fresh])
    buf = io.StringIO()
    with redirect_stdout(buf):
        done = obj.check_audio_finished()
    log = buf.getvalue()
    check('旧句柄 stale 时能重新定位并判为已完成', done is True, log.strip()[:80])
    check('日志说明重新定位过', '重新定位' in log, log.strip()[:80])
    check('self.iframe 已换成新句柄', obj.iframe is fresh)

    # 2) 重新定位后仍然没有打勾 -> 未完成（fail-closed 不变）
    stale = StaleIframe()
    fresh = FreshIframe('ans-attach-ct')
    obj = make_common(stale, [fresh])
    with redirect_stdout(io.StringIO()):
        done = obj.check_audio_finished()
    check('刷新后仍未打勾 -> 未完成', done is False)

    # 3) 找不到可重新定位的元素 -> 未完成（不抛异常）
    obj = make_common(StaleIframe(), [])
    with redirect_stdout(io.StringIO()):
        done = obj.check_audio_finished()
    check('无法重新定位时不崩、判未完成', done is False)

    # 4) platform_finished：第一次没打勾、第二次打勾（异步）
    stale = StaleIframe()
    fresh = FreshIframe('ans-job-finished', delay_reads=1)
    obj = make_common(stale, [fresh])
    original_sleep = common.time.sleep
    common.time.sleep = lambda _s: None
    try:
        with redirect_stdout(io.StringIO()):
            done = obj.platform_finished()
    finally:
        common.time.sleep = original_sleep
    check('平台异步打勾时重试一次即成功', done is True, fresh.reads)

    # 5) _click_popup_ok 三态
    class PopDriver:
        """可配置页面上有哪些元素。"""

        def __init__(self, popok=None, container=False, click_raises=False):
            self.popok = popok
            self.container = container
            self.click_raises = click_raises
            self.clicked = 0

        def find_elements(self, by, value):
            if by == By.CSS_SELECTOR and value == '#popok' and self.popok is not None:
                return [self.popok]
            if by == By.ID and value == 'popcontent' and self.container:
                return [object()]
            return []

        def execute_script(self, script, *args):
            if args:
                # 带参数 = JS 点击兜底；按钮本身点不动时，JS 点击同样失败
                self.clicked += 1
                if self.popok is not None and self.popok.raises:
                    raise WebDriverException('js click failed')
            return ''

    class OkBtn:
        def __init__(self, raises=False):
            self.raises = raises
            self.clicks = 0

        def is_displayed(self):
            return True

        def click(self):
            self.clicks += 1
            if self.raises:
                raise WebDriverException('not interactable')

    ans = quiz_ai.Answer.__new__(quiz_ai.Answer)

    ans.driver = PopDriver(popok=OkBtn())
    check('有确认按钮 -> clicked', ans._click_popup_ok() == 'clicked',
          ans.driver.popok.clicks)

    ans.driver = PopDriver()
    check('没有弹窗 -> none', ans._click_popup_ok() == 'none')

    ans.driver = PopDriver(popok=OkBtn(raises=True), container=False)
    # 有按钮但点不动、且没有弹窗容器可兜底 -> failed
    check('有按钮点不动 -> failed', ans._click_popup_ok() == 'failed')

    ans.driver = PopDriver(popok=OkBtn(raises=True), container=True)
    check('点不动但有弹窗容器 -> 仍报 failed（未点到）',
          ans._click_popup_ok() == 'failed')

    # 6) 三态语义必须被 submit 用到：failed 时只保存
    source = open(os.path.join('task', 'quiz_ai.py'), encoding='utf-8').read()
    check('submit 检查 failed 并改为仅保存',
          "state == 'failed'" in source and '_save_only()' in source)
    check('提交后无提示也能记录确认状态', '确认按钮=' in source)

    # 7) Common 必须能重新定位（而不是只存旧句柄）
    common_src = open(os.path.join('task', 'tool', 'common.py'), encoding='utf-8').read()
    check('Common 保存了 iframe 选择器', 'self.iframe_selector = iframe_element' in common_src)
    check('Common 记录了模块序号', 'self.iframe_index = num - 1' in common_src)

    # 8) Quiz 的完成判据：平台确认提交优先于任务点打勾
    class FakeSession:
        def __init__(self, state):
            self.submit_state = state

    quiz = quiz_ai.Quiz.__new__(quiz_ai.Quiz)
    quiz.session = FakeSession('submitted')
    with redirect_stdout(io.StringIO()):
        check('平台确认提交 -> 判完成', quiz.verify_finished() is True)

    quiz.session = FakeSession('saved')
    with redirect_stdout(io.StringIO()):
        check('只保存未提交 -> 不判完成', quiz.verify_finished() is False)

    quiz.session = FakeSession('unknown')
    quiz.type = '测验'
    quiz.iframe = StaleIframe()
    quiz.iframe_selector = 'iframe[src*="work"]'
    quiz.iframe_index = 0
    quiz.driver = Driver([])
    original_sleep = common.time.sleep
    common.time.sleep = lambda _s: None
    try:
        with redirect_stdout(io.StringIO()):
            check('无平台提示 -> 回落任务点判定（未打勾=False）',
                  quiz.verify_finished() is False)
    finally:
        common.time.sleep = original_sleep

    check('Quiz.start 保存了 session 供判定使用',
          'self.session = Answer(' in open(os.path.join('task', 'quiz_ai.py'),
                                          encoding='utf-8').read())

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
