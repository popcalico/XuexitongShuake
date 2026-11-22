# -*- coding: utf-8 -*-
"""V5.1 第22节：作业结果状态真实、失败不误报完成。

纯桩：用 object.__new__ 绕过重量级 Answer.__init__，注入 fake driver。

用法：C:/Python315/python.exe tests/test_homework_result_status.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_hwstatus_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from selenium.webdriver.common.by import By
from core import task_control as tc
from task import do_work as hw

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class El:
    def __init__(self, text='', aria='', links=None, items=None):
        self.text = text
        self.aria = aria
        self.links = links or []
        self.items = items or []
        self.clicks = 0

    def get_attribute(self, name):
        if name == 'aria-label':
            return self.aria
        if name == 'typename':
            return '单选题'
        return ''

    def find_elements(self, by, value):
        if value == 'a':
            return self.links
        if value == 'li':
            return self.items
        return []

    def find_element(self, by, value):
        return El()

    def click(self):
        self.clicks += 1


class D:
    def __init__(self, radios=None, items=None, save_links=None,
                 no_save_area=False):
        self.radios = list(radios or [])
        self.items = list(items or [])
        self.save_links = list(save_links or [])
        self.no_save_area = no_save_area
        self.closed = 0
        self.scripts = []
        self.title = '作业作答'
        self.current_url = 'https://mooc1.chaoxing.com/work'

    def find_elements(self, by, value):
        if value == '[name="group-radio"]':
            return list(self.radios)
        return []

    def find_element(self, by, value):
        if value == 'submitFocus':
            if self.no_save_area:
                raise RuntimeError('NoSuchElementException')
            return El(links=self.save_links)
        if value == 'bottomList':
            return El(items=self.items)
        return El()

    def execute_script(self, js, *a):
        self.scripts.append(js)

    def close(self):
        self.closed += 1


def make(save_links=None, no_save_area=False, radios=None, items=None,
         answer_ok=True):
    d = D(radios=radios, items=items, save_links=save_links,
          no_save_area=no_save_area)
    w = object.__new__(hw.do_work)
    w.driver = d
    w.course_name = '课A'
    w.homework = '自动选择'
    w.API_KEY = 'sk-test-key'
    w.result_status = tc.TaskStatus.FAILED
    w.result_detail = ''
    w.turn_page = lambda *a, **k: True
    w.use_ai_wen_da = lambda *a, **k: None
    w.use_deepseek = lambda *a, **k: None
    w.finish_title = lambda *a, **k: True
    w._register_unanswerable = lambda *a, **k: None
    w.get_answer_list = lambda: answer_ok
    return w, d


def main():
    print('=== V5.1 作业结果状态真实 ===')
    orig_sleep = hw.time.sleep
    hw.time.sleep = lambda _s: None   # 保存失败分支的 15 秒缓冲，测试里压缩掉
    try:
        return _run(orig_sleep)
    finally:
        hw.time.sleep = orig_sleep


def _run(orig_sleep):
    # 1) get_answer_list：点击到「暂时保存」
    save = El(text='暂时保存')
    w, d = make(save_links=[save])
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = hw.do_work.get_answer_list(w)
    check('点击暂时保存 → True', r is True, r)
    check('保存按钮确实被点击', save.clicks == 1, save.clicks)

    # 2) 无保存区 → False
    w, d = make(no_save_area=True)
    with redirect_stdout(io.StringIO()):
        r = hw.do_work.get_answer_list(w)
    check('无保存区 → False', r is False, r)

    # 3) 有保存区但没有「暂时保存」按钮 → False
    w, d = make(save_links=[El(text='提交')])
    with redirect_stdout(io.StringIO()):
        r = hw.do_work.get_answer_list(w)
    check('未确认保存 → False', r is False, r)

    # 4) 无法识别未完成项 → FAILED（调用者必须能检查）
    w, d = make(radios=[El(aria='全部')])
    with redirect_stdout(io.StringIO()):
        r = w.auto_choice_homework_question()
    check('无法识别未完成项 → FAILED', r == tc.TaskStatus.FAILED, r)

    # 5) 正常选中 + 保存成功 → SUCCESS
    radio = El(aria='未完成作业')
    item = El(aria='作业1')
    w, d = make(radios=[radio], items=[item], answer_ok=True)
    with redirect_stdout(io.StringIO()):
        r = w.auto_choice_homework_question()
    check('选中并保存成功 → SUCCESS', r == tc.TaskStatus.SUCCESS, r)
    check('作业窗口被关闭', d.closed == 1, d.closed)

    # 6) 保存失败 → FAILED，不误报完成
    w, d = make(radios=[radio], items=[item], answer_ok=False)
    with redirect_stdout(io.StringIO()):
        r = w.auto_choice_homework_question()
    check('保存失败 → FAILED', r == tc.TaskStatus.FAILED, r)

    # 7) 源码级：__init__ 检查返回值，绝不无条件报完成
    src = open(os.path.join(ROOT, 'task', 'do_work.py'), encoding='utf-8').read()
    check('结果状态显式化', 'self.result_status' in src)
    check('自动选择返回值被检查',
          'status = self.auto_choice_homework_question()' in src)
    check('只有 SUCCESS 才输出完成提示',
          'if self.result_status == tc.TaskStatus.SUCCESS:' in src)
    check('措辞明确未提交', '未提交，需人工确认后再提交' in src)
    check('不再有裸「已完成作业」',
          "'已完成作业,15秒后自动关闭窗口'" not in src)
    check('翻页后重新定位元素', 'fresh = element.find_elements' in src)
    check('作业项身份校验', '作业项身份变化' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
