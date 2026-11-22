# -*- coding: utf-8 -*-
"""问卷功能回归（2026-11-19 从上游 26.11.18 移植）。

上游 `finish_vote.py` 有三个必然崩的问题，本测试专门钉住它们：
  1. `confirm = find_elements(...)` 之后 `confirm.click()` —— list 没有 click；
  2. 提交后不校验平台是否真的完成（假完成）；
  3. 收尾不保证切回 default_content（异常时后续任务全找不到元素）。

另外验证接入点：页面识别表里有问卷选择器、dispatch_page 有分支且受 `vote_enabled` 控制。

纯桩，不联网、不开浏览器。

用法：C:/Python315/python.exe tests/test_vote.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_vote_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task import finish_vote as fv

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------------ 假 driver
class FakeElement:
    def __init__(self, tag='el', on_click=None):
        self.tag = tag
        self.clicks = 0
        self._on_click = on_click
        self.sent = []

    def click(self):
        self.clicks += 1
        if self._on_click:
            return self._on_click()
        return None

    def send_keys(self, text):
        self.sent.append(text)

    def find_elements(self, by, selector):
        return []


class FakeDriver:
    def __init__(self, confirms=None, scripts=None):
        self.confirms = confirms or []
        self.scripts = []
        self._scripts = scripts

    def find_elements(self, by, selector):
        if 'dialog_affrim' in str(selector):
            return list(self.confirms)
        return []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if self._scripts:
            return self._scripts(script, *args)
        return None


def make_vote(driver):
    """绕过 Common.__init__（它会真的切 frame），只造需要的属性。"""
    v = object.__new__(fv.Vote)
    v.driver = driver
    v.type = '问卷'
    v.course_name = '测试课'
    return v


# ------------------------------------------------------------------ 用例
def main():
    print('=== 问卷移植回归 ===')

    # 1) 上游 bug ①：确认框是 list，不能直接 .click()
    confirm = FakeElement('confirm')
    driver = FakeDriver(confirms=[confirm])
    vote = make_vote(driver)
    with redirect_stdout(io.StringIO()):
        ok = vote._confirm_submit()
    check('有确认框时取第一个元素点击（不崩）', ok is True and confirm.clicks == 1,
          (ok, confirm.clicks))

    # 有多个确认框也只点第一个，绝不把 list 当元素
    c1, c2 = FakeElement('c1'), FakeElement('c2')
    vote2 = make_vote(FakeDriver(confirms=[c1, c2]))
    with redirect_stdout(io.StringIO()):
        vote2._confirm_submit()
    check('多个确认框只点第一个', c1.clicks == 1 and c2.clicks == 0, (c1.clicks, c2.clicks))

    # 没有确认框时返回 False 且不抛
    vote3 = make_vote(FakeDriver(confirms=[]))
    with redirect_stdout(io.StringIO()):
        try:
            r = vote3._confirm_submit()
            no_raise = True
        except Exception as e:
            r, no_raise = None, False
    check('无确认框时不抛异常', no_raise and r is False, r)

    # 2) 上游 bug ②：提交后必须走平台完成校验
    source = open(os.path.join(ROOT, 'task/finish_vote.py'), encoding='utf-8').read()
    check('verify_finished 走平台打勾判定', 'platform_finished()' in source)
    check('没有裸 return True 冒充完成',
          'return True' not in source.split('def verify_finished')[1].split('def ')[0])

    vote4 = object.__new__(fv.Vote)
    vote4.platform_finished = lambda: True
    check('平台打勾 -> 判完成', vote4.verify_finished() is True)

    vote5 = object.__new__(fv.Vote)

    def boom():
        raise RuntimeError('读不到任务点')

    vote5.platform_finished = boom
    check('读取异常 -> 判未完成（fail-closed）', vote5.verify_finished() is False)

    # 3) 上游 bug ③：收尾必须切回 default_content
    check('start() 有 finally 收尾', 'finally:' in source)
    check('收尾切回 default_content', 'switch_to.default_content()' in source)

    # 4) 作答逻辑
    radio_a, radio_b = FakeElement('radio-a'), FakeElement('radio-b')
    q = FakeElement('question')
    q.find_elements = lambda by, sel: (
        [radio_a, radio_b] if 'radio' in str(sel) else [])
    vote6 = make_vote(FakeDriver())
    with redirect_stdout(io.StringIO()):
        vote6._answer_one(q)
    check('单选题随机点一个选项', (radio_a.clicks + radio_b.clicks) == 1,
          (radio_a.clicks, radio_b.clicks))

    textarea = FakeElement('textarea')
    q2 = FakeElement('question2')
    q2.find_elements = lambda by, sel: (
        [textarea] if ('textarea' in str(sel) or 'contenteditable' in str(sel)) else [])
    vote7 = make_vote(FakeDriver())
    with redirect_stdout(io.StringIO()):
        vote7._answer_one(q2)
    check('填空题写入「无」', textarea.sent == ['无'], textarea.sent)

    q3 = FakeElement('question3')
    q3.find_elements = lambda by, sel: []
    vote8 = make_vote(FakeDriver())
    try:
        with redirect_stdout(io.StringIO()):
            vote8._answer_one(q3)
        raised = False
    except RuntimeError:
        raised = True
    check('既无选项也无输入框时抛错（由上层跳过该题）', raised)

    # 5) 接入点
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('识别表含问卷选择器',
          '/ananas/modules/zt/vote/index' in main_src)
    check('dispatch_page 有问卷分支', "if '问卷' in message:" in main_src)
    check('问卷受 vote_enabled 控制', 'runtime.vote_enabled()' in main_src)
    check('关闭时仍识别并跳过（不累计未识别）',
          '已关闭「自动问卷」，跳过该问卷' in main_src)

    print()
    if FAILS:
        print('FAILED %d: %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
