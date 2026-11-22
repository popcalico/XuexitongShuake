# -*- coding: utf-8 -*-
"""V5.1 第20节：音频等待有上限、可停止、可诊断，超时不误报完成。

纯桩 fake driver + 临时 HOME，不联网。真实播放时长未被跳过：
测试只把「等待上限」压缩到 0.35 秒以便快速验证。

用法：C:/Python315/python.exe tests/test_audio_wait_timeout.py
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
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_audiowait_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import task_control as tc
import task.play_audio as pa

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class _Switch:
    def __init__(self, d):
        self.d = d

    def frame(self, x):
        self.d.frames.append(x)

    def default_content(self):
        self.d.frames.append('TOP')


class _El:
    def __init__(self, text='0:30'):
        self.text = text

    def click(self):
        pass


class D:
    def __init__(self, duration='0:30', frame_error=None):
        self.duration = duration
        self.frame_error = frame_error
        self.frames = []
        self.switch_to = _Switch(self)
        self.title = '音频页'
        self.current_url = 'https://mooc1.chaoxing.com/a'

    def find_element(self, by, value):
        if value == 'vjs-duration-display':
            return _El(self.duration)
        if value == '[id="iframe"]' and self.frame_error:
            raise self.frame_error
        return _El()

    def execute_script(self, js, *a):
        return ''


def run_case(finished=False, duration='0:30', frame_error=None, stopped=False):
    d = D(duration=duration, frame_error=frame_error)
    a = object.__new__(pa.Audio)
    a.driver = d
    a.iframe = _El()
    a.course_name = '课A'
    a._platform_finished = False
    a.last_status = tc.TaskStatus.FAILED
    a.last_detail = ''
    a.last_duration = ''
    a.check_audio_finished = lambda: finished

    ev = threading.Event()
    if stopped:
        ev.set()
    tc.set_stop_event(ev)
    saved_budget, saved_sleep = pa.tc.time_budget, pa.time.sleep
    pa.tc.time_budget = lambda *a2, **k: 0.35     # 只压缩等待上限，不跳过播放判定
    pa.time.sleep = lambda _s: None                # 快进的 0.1 秒缓冲
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            a.start()
    finally:
        pa.tc.time_budget, pa.time.sleep = saved_budget, saved_sleep
        tc.set_stop_event(None)
        tc.clear_stop()
    return a, buf.getvalue()


def main():
    print('=== V5.1 音频等待有界 / 不误报完成 ===')

    # 1) 正常结束：平台打勾
    a, out = run_case(finished=True)
    check('正常完成 → SUCCESS', a.last_status == tc.TaskStatus.SUCCESS, a.last_status)
    check('平台确认后才允许写学习记录', a.verify_finished() is True)
    check('日志输出音频已确认完成', '音频已确认完成' in out)

    # 2) 完成标志永不出现 → 超时，绝不报完成
    a, out = run_case(finished=False)
    check('完成标志不出现 → TIMEOUT', a.last_status == tc.TaskStatus.TIMEOUT,
          a.last_status)
    check('超时不写学习记录', a.verify_finished() is False)
    check('超时输出诊断与不写记录', '不写学习记录' in out and '平台未更新完成标志' in out)

    # 3) 时长不可读
    a, out = run_case(finished=False, duration='')
    check('时长不可读被记录', a.last_duration == '', a.last_duration)
    check('时长不可读仍不误报完成', a.verify_finished() is False)
    check('诊断里标注时长未知', '时长=未知' in out)

    # 4) 用户停止
    a, out = run_case(finished=False, stopped=True)
    check('停止事件 → CANCELLED', a.last_status == tc.TaskStatus.CANCELLED,
          a.last_status)
    check('停止不写学习记录', a.verify_finished() is False)

    # 5) driver 失效
    a, out = run_case(frame_error=RuntimeError('InvalidSessionIdException'))
    check('driver 失效 → DRIVER_ERROR', a.last_status == tc.TaskStatus.DRIVER_ERROR,
          a.last_status)
    check('driver 失效不写学习记录', a.verify_finished() is False)

    # 6) 音频元素不存在（可区分状态，不崩溃）
    a, out = run_case(frame_error=RuntimeError('NoSuchElementException'))
    check('元素不存在 → 有界超时而非死循环',
          a.last_status == tc.TaskStatus.TIMEOUT, a.last_status)
    check('诊断区分「音频元素不存在」', '音频元素不存在' in out)

    # 7) 源码级
    src = open(os.path.join(ROOT, 'task', 'play_audio.py'), encoding='utf-8').read()
    check('不再有 while True 死循环', '\n    while True:' not in src)
    check('使用有界等待', 'tc.bounded_wait' in src)
    check('有完成校验钩子', 'def verify_finished' in src)
    check('有心跳与超时诊断', 'heartbeat=30.0' in src and 'diagnose=' in src)
    check('时长预算存在', 'tc.time_budget' in src and 'parse_duration' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
