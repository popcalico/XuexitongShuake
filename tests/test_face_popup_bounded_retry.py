# -*- coding: utf-8 -*-
"""V5.1 第19节：人脸弹窗处理有最大尝试次数，绝不递归自调用。

场景：无弹窗、一次成功、强制删除成功、多次失败、等待超时、用户停止、
driver 失效、最大次数边界。全部用 fake driver，不联网、不用真实 Cookie。

用法：C:/Python315/python.exe tests/test_face_popup_bounded_retry.py
"""
import io
import os
import sys
import tempfile
import threading
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_facepopup_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import task.tool.face as face
from core import task_control as tc

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeHelper:
    instances = []

    def __init__(self, driver):
        self.driver = driver
        FakeHelper.instances.append(self)

    def wait_for_face_popup(self, condition):
        cfg = self.driver.cfg
        if cfg.get('detect_error'):
            raise cfg['detect_error']
        return cfg.get('present', True)

    def run(self):
        return self.driver.cfg.get('run_ok', False)


class FakeWait:
    """替换 WebDriverWait，避免真实 10 秒等待。"""
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, condition):
        cfg = self.driver.cfg
        if cfg.get('remove_ok'):
            self.driver.removed += 1
            return object()
        if cfg.get('wait_timeout'):
            raise Exception('TimeoutException')
        raise Exception('element not found')


class D:
    def __init__(self, **cfg):
        self.cfg = cfg
        self.removed = 0
        self.scripts = []
        self.title = '视频页'
        self.current_url = 'https://mooc1.chaoxing.com/v'

    def execute_script(self, js, *a):
        self.scripts.append(js)

    def find_elements(self, by, value):
        return []


def run_case(**cfg):
    d = D(**cfg)
    FakeHelper.instances = []
    calls = {'check': 0}
    saved = (face.ChaoxingFaceHelper, face.WebDriverWait,
             face.check_face, face.time.sleep)
    face.ChaoxingFaceHelper = FakeHelper
    face.WebDriverWait = FakeWait
    face.check_face = lambda *a, **k: calls.__setitem__('check', calls['check'] + 1) or True
    face.time.sleep = lambda _s: None
    ev = threading.Event()
    if cfg.get('stopped'):
        ev.set()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            status = face.delete_face_popup(d, True, stop_event=ev)
    finally:
        (face.ChaoxingFaceHelper, face.WebDriverWait,
         face.check_face, face.time.sleep) = saved
    return status, d, buf.getvalue(), calls


def main():
    print('=== V5.1 人脸弹窗有界重试 ===')

    # 1) 弹窗不存在是正常情况
    st, d, out, calls = run_case(present=False)
    check('无弹窗 → NO_TASK', st == tc.TaskStatus.NO_TASK, st)
    check('无弹窗只检测一次', len(FakeHelper.instances) == 1,
          len(FakeHelper.instances))
    check('无弹窗不当作严重异常 / 不重试', '已达最大尝试次数' not in out)

    # 2) 自动处理一次成功
    st, d, out, calls = run_case(present=True, run_ok=True)
    check('自动处理成功 → SUCCESS', st == tc.TaskStatus.SUCCESS, st)
    check('成功不再重试', len(FakeHelper.instances) == 1,
          len(FakeHelper.instances))

    # 3) 自动处理失败但强制删除成功
    st, d, out, calls = run_case(present=True, run_ok=False, remove_ok=True)
    check('强制删除成功 → SUCCESS', st == tc.TaskStatus.SUCCESS, st)
    check('确实执行了删除脚本', d.removed == 1, d.removed)

    # 4) 多次失败：达到最大次数返回 FAILED，绝不无限递归
    st, d, out, calls = run_case(present=True, run_ok=False, remove_ok=False)
    check('连续失败 → FAILED', st == tc.TaskStatus.FAILED, st)
    check('尝试次数有硬上限',
          len(FakeHelper.instances) == face.FACE_POPUP_MAX_ATTEMPTS,
          len(FakeHelper.instances))
    check('日志说明已达最大次数', '已达最大尝试次数' in out)

    # 5) 等待超时也不崩、也不无限重试
    st, d, out, calls = run_case(present=True, run_ok=False, wait_timeout=True)
    check('等待超时 → 有界失败', st == tc.TaskStatus.FAILED, st)
    check('超时后尝试次数不超上限',
          len(FakeHelper.instances) <= face.FACE_POPUP_MAX_ATTEMPTS,
          len(FakeHelper.instances))

    # 6) 用户停止
    st, d, out, calls = run_case(present=True, stopped=True)
    check('停止事件 → CANCELLED', st == tc.TaskStatus.CANCELLED, st)
    check('停止时不做任何弹窗检测', len(FakeHelper.instances) == 0,
          len(FakeHelper.instances))

    # 7) WebDriver 失效
    class _Session(Exception):
        pass
    _Session.__name__ = 'InvalidSessionIdException'
    st, d, out, calls = run_case(present=True, detect_error=_Session('gone'))
    check('driver 失效 → DRIVER_ERROR', st == tc.TaskStatus.DRIVER_ERROR, st)

    # 8) 源码级：无递归自调用，有最大次数常量与停止检查
    src = open(os.path.join(ROOT, 'task', 'tool', 'face.py'), encoding='utf-8').read()
    start = src.index('def delete_face_popup(')
    body = src[src.index('\n', start):]   # 跳过 def 行本身，只看函数体
    check('弹窗函数不再递归自调用',
          'delete_face_popup(driver,pass_face,' not in body
          and 'delete_face_popup(driver, pass_face,' not in body)
    check('有最大尝试次数常量',
          'FACE_POPUP_MAX_ATTEMPTS' in src and face.FACE_POPUP_MAX_ATTEMPTS == 3)
    check('循环内检查停止事件', 'tc.is_stopped(ev)' in body)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
