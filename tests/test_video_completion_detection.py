# -*- coding: utf-8 -*-
"""V5.1 第21/27节：视频「未识别」绝不等于「全部完成」。

场景：真正无视频、全部完成、部分未完成、页面未加载、选择器失效、
页面跳转/stale、driver 异常、动态元素重新定位。纯桩，不联网。

用法：C:/Python315/python.exe tests/test_video_completion_detection.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_videodet_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from selenium.webdriver.common.by import By
from core import task_control as tc
import task.watch_vido as vido

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class _Switch:
    def frame(self, x):
        pass

    def default_content(self):
        pass


class El:
    def __init__(self, aria='', cls='ans-attach-ct', text='0:30'):
        self.aria = aria
        self.cls = cls
        self.text = text
        self.clicks = 0

    def get_attribute(self, name):
        if name == 'class':
            return self.cls
        if name == 'aria-label':
            return self.aria
        return ''

    def find_element(self, by, value):
        if value == '..':
            return El(cls='ans-attach-ct')
        return El()

    def click(self):
        self.clicks += 1


class D:
    def __init__(self, icons=None, landed=True, broad=False, raise_find=False):
        self.icons = list(icons or [])
        self.landed = landed
        self.broad = broad
        self.raise_find = raise_find
        self.switch_to = _Switch()
        self.title = '学生学习页面'
        self.current_url = 'https://mooc1.chaoxing.com/c'
        self.scripts = []

    def find_elements(self, by, value):
        if self.raise_find:
            raise RuntimeError('InvalidSessionIdException')
        if value == 'ans-job-icon-clear ':
            return list(self.icons)
        if value in ('.ans-job-icon', '.ans-job-icon-clear', '[class*=ans-job-icon]'):
            return [El()] if self.broad else []
        if value in ('.prev_title', '.ans-attach-ct', '.catalog_points'):
            return [El()] if self.landed else []
        return []

    def find_element(self, by, value):
        return El()

    def execute_script(self, js, *a):
        self.scripts.append(js)
        return ''


def run_case(finish_ok=True, **cfg):
    d = D(**cfg)
    saved = (vido.check_vido_finish, vido.save_record, vido.delete_face_popup,
             vido.time.sleep, vido.save_diagnostic_screenshot
             if hasattr(vido, 'save_diagnostic_screenshot') else None)
    saved_records = []
    vido.check_vido_finish = lambda *a, **k: finish_ok
    vido.save_record = lambda *a, **k: saved_records.append(a)
    vido.delete_face_popup = lambda *a, **k: None
    vido.time.sleep = lambda _s: None
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            status = vido.study_page(d, '课A', False, False, '1', False)
    finally:
        (vido.check_vido_finish, vido.save_record, vido.delete_face_popup,
         vido.time.sleep) = saved[:4]
    return status, buf.getvalue(), saved_records


def main():
    print('=== V5.1 视频未识别≠全部完成 ===')

    # 1) 真正无视频：任务区已加载、无任务点图标
    st, out, rec = run_case(landed=True, broad=False)
    check('页面确认无视频 → NO_TASK', st == tc.TaskStatus.NO_TASK, st)
    check('无视频不打印「所有视频均已完成」', '所有视频均已完成' not in out)

    # 2) 页面未加载 → UNKNOWN
    st, out, rec = run_case(landed=False)
    check('页面未加载 → UNKNOWN', st == tc.TaskStatus.UNKNOWN, st)
    check('未加载不报完成', '所有视频均已完成（' not in out and '视频诊断' in out)

    # 3) 选择器失效（有广义图标但原选择器没命中）
    st, out, rec = run_case(broad=True)
    check('选择器失效 → UNKNOWN', st == tc.TaskStatus.UNKNOWN, st)
    check('诊断说明选择器失效', '选择器失效' in out)

    # 4) driver 异常
    st, out, rec = run_case(raise_find=True)
    check('find_elements 抛异常 → DRIVER_ERROR', st == tc.TaskStatus.DRIVER_ERROR, st)
    check('异常不打印「视频已完成」', '视频已完成,点击下一节' not in out)

    # 5) 找到任务且全部已完成
    done = El(aria='任务点已完成', cls='ans-attach-ct ans-job-finished')
    st, out, rec = run_case(icons=[done])
    check('全部已完成 → ALL_DONE', st == tc.TaskStatus.ALL_DONE, st)
    check('确实打印全部完成', '所有视频均已完成' in out)

    # 6) 部分未完成、平台未确认 → FAILED，且不写记录
    pending = El(aria='任务点未完成')
    st, out, rec = run_case(finish_ok=False, icons=[pending])
    check('平台未确认 → FAILED', st == tc.TaskStatus.FAILED, st)
    check('未确认不写学习记录', rec == [], rec)
    check('未确认不报全部完成', '所有视频均已完成（' not in out)

    # 7) 部分未完成、平台确认 → ALL_DONE 且写记录
    st, out, rec = run_case(finish_ok=True, icons=[pending])
    check('平台确认 → ALL_DONE', st == tc.TaskStatus.ALL_DONE, st)
    check('确认后写一次学习记录', len(rec) == 1, len(rec))

    # 8) 源码级：有界等待、状态拆分、每轮重定位
    src = open(os.path.join(ROOT, 'task', 'watch_vido.py'), encoding='utf-8').read()
    check('两个函数不再有 while True 死循环', '\n    while True:' not in src)
    check('使用有界等待', 'tc.bounded_wait' in src)
    check('有任务区加载确认', '_confirm_video_task_area' in src)
    check('完成状态显式返回', 'return tc.TaskStatus.ALL_DONE' in src)
    check('每轮重新定位元素', src.count("find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')") >= 3)
    check('check_vido_finish 返回布尔', 'return False' in src and 'return True' in src)
    check('签名保持 8 个参数（兼容既有断言）',
          list(__import__('inspect').signature(vido.check_vido_finish).parameters)
          == ['driver', 'i', 'time_start', 'total_time', 'vido_iframe',
              'lock_screen', 'speed', 'pass_face'])
    check('study_page 签名保持 6 个参数',
          list(__import__('inspect').signature(vido.study_page).parameters)
          == ['driver', 'course_name', 'lock_screen', 'pass_vido',
              'speed', 'pass_face'])

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
