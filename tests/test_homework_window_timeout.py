# -*- coding: utf-8 -*-
"""V5.1 第22节：作业新窗口等待有超时、可取消、可诊断。

纯桩 fake driver，不联网、不用真实账号。

用法：C:/Python315/python.exe tests/test_homework_window_timeout.py
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
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_hwwindow_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import task_control as tc
from task import do_work as hw

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class D:
    def __init__(self, handles=None, fail_handles=False):
        self.handles = list(handles or ['w1'])
        self.fail_handles = fail_handles
        self.title = '作业列表'
        self.current_url = 'https://mooc1.chaoxing.com/work'

    @property
    def window_handles(self):
        if self.fail_handles:
            raise RuntimeError('InvalidSessionIdException')
        return list(self.handles)


def call(d, timeout=0.35, stopped=False):
    ev = threading.Event()
    if stopped:
        ev.set()
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = hw.wait_for_homework_window(
            d, 1, timeout=timeout, stop_event=ev, poll_interval=0.1)
    return result, buf.getvalue()


def main():
    print('=== V5.1 作业窗口等待有界 ===')

    # 1) 新窗口出现
    d = D(['w1', 'w2'])
    (st, detail), out = call(d)
    check('新窗口出现 → SUCCESS', st == tc.TaskStatus.SUCCESS, st)
    check('详情含窗口数', '窗口数 2' in detail, detail)

    # 2) 窗口一直不出现 → 超时而非无限等待
    d = D(['w1'])
    (st, detail), out = call(d)
    check('窗口未出现 → TIMEOUT', st == tc.TaskStatus.TIMEOUT, st)
    check('超时明确提示不判定为完成', '不判定为完成' in out)

    # 3) 用户停止
    d = D(['w1'])
    (st, detail), out = call(d, stopped=True)
    check('停止事件 → CANCELLED', st == tc.TaskStatus.CANCELLED, st)
    check('停止时放弃等待', '放弃等待作业窗口' in out)

    # 4) driver 失效
    d = D(['w1'], fail_handles=True)
    (st, detail), out = call(d)
    check('driver 失效 → DRIVER_ERROR', st == tc.TaskStatus.DRIVER_ERROR, st)

    # 5) 源码级：不再有无限窗口等待循环
    src = open(os.path.join(ROOT, 'task', 'do_work.py'), encoding='utf-8').read()
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('有独立的有界等待函数', 'def wait_for_homework_window' in src)
    check('不再无限 while 等窗口',
          'while len(driver.window_handles) == now_window_handles' not in src)
    check('等待有超时常量', 'HOMEWORK_WINDOW_TIMEOUT' in src)
    check('等待检查停止事件', 'stop_event=stop_event' in src)
    check('main.py 检查作业结果状态',
          '_work_ok' in main_src and 'result_status' in main_src)
    check('只有成功才计入完成课程', 'if _work_ok:' in main_src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
