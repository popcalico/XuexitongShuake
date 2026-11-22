# -*- coding: utf-8 -*-
"""完成判据 / 重复处理 / 误判完结 的回归（2026-11-19 审计 P0 修复）。

两部分：
1. 行为验证：`Common.verify_finished()` 默认必须按平台任务点打勾判定，
   不能再无条件返回 True（PPT/阅读/讨论的假完成根因）。
2. 源码契约：视频不再「二次到底即完成」、阅读不再固定 sleep(10)、
   同一页不再重复 dispatch、按钮不可交互不再判课程完结、定时窗口 × = 取消。

纯桩，不联网、不开浏览器。

用法：C:/Python315/python.exe tests/test_completion_gate.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_gate_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task.tool.common import Common

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class FakeSwitch:
    def __init__(self):
        self.calls = []

    def default_content(self):
        self.calls.append('default')

    def frame(self, name):
        self.calls.append(('frame', name))


class FakeParent:
    def __init__(self, cls):
        self.cls = cls

    def get_attribute(self, name):
        return self.cls if name == 'class' else ''


class FakeIframe:
    def __init__(self, cls):
        self.cls = cls

    def find_element(self, by, value):
        return FakeParent(self.cls)


class FakeDriver:
    def __init__(self):
        self.switch_to = FakeSwitch()


class Probe(Common):
    """绕过 __init__（不碰真浏览器），只测 verify_finished 的判定。"""

    def __init__(self, parent_class):
        self.driver = FakeDriver()
        self.iframe = FakeIframe(parent_class)
        self.type = 'PPT'


def main():
    print('=== 完成判据 ===')
    done = Probe('ans-attach-ct ans-job-finished')
    check('平台已打勾 -> verify_finished True', done.verify_finished() is True)
    check('校验前先切回外层帧',
          done.driver.switch_to.calls[:2] == ['default', ('frame', 'iframe')],
          done.driver.switch_to.calls)

    todo = Probe('ans-attach-ct')
    check('平台未打勾 -> verify_finished False', todo.verify_finished() is False)

    dead = Probe('ans-attach-ct ans-job-finished')
    dead.iframe = None            # 取父节点必失败
    check('元素失效 -> False（不误报完成）', dead.verify_finished() is False)

    print()
    print('=== 源码契约 ===')
    common_src = read('task/tool/common.py')
    check('默认实现改为按平台打勾判定',
          'self.platform_finished()' in common_src)
    check('取不到状态时 fail-closed（返回 False）',
          'return bool(self.platform_finished())' in common_src)
    check('turn_page 有界等待且返回 bool',
          'def turn_page(driver,page_name,timeout=' in common_src
          and "return 'True'" not in common_src)

    for name, path in (('PPT', 'task/watch_ppt.py'),
                       ('阅读', 'task/reading.py'),
                       ('讨论', 'task/finish_dicussion.py')):
        check('%s 不再依赖默认放行（未定义自己的 verify_finished 也没关系，'
              '默认已改严格）' % name, 'def verify_finished' not in read(path))

    vido = read('task/watch_vido.py')
    check('视频不再「二次到底即完成」',
          '二次到底（按既有策略结束）' not in vido and '继续等待' in vido)

    reading = read('task/reading.py')
    check('阅读不再固定 sleep(10)', 'time.sleep(10)' not in reading)
    check('阅读按平台打勾有界等待', 'READ_WAIT_SECONDS' in reading
          and 'check_audio_finished' in reading)

    main_src = read('main.py')
    check('重复页不再 dispatch', 'repeat_page' in main_src
          and '跳过重复处理' in main_src)
    check('按钮不可交互不再返回 finished', "return 'finished'" not in main_src)
    check('多端检测前先切帧', 'prev_title' in main_src
          and "driver.switch_to.frame('iframe')" in main_src)
    check('空课程名被拒绝', '课程名为空，已跳过选课' in main_src)
    check('DOM 已接入课程循环', 'record_dom(' in main_src)

    ident = read('core/login_identity.py')
    check('目标身份为空时拒绝放行', '目标账号未配置 uid/手机号' in ident)

    exam = read('task/do_exam.py')
    check('确认框不可见时不记已交卷', '确认按钮不可见' in exam)
    check('submitted 只在点击确认后置位',
          exam.index("self.stat['submitted'] = 1") > exam.index('确认按钮不可见'))

    quiz = read('task/quiz_ai.py')
    check('填空写入有回读', '_editor_has_text' in quiz)
    check('少写一个空也算未完成', 'filled < blanks' in quiz)
    check('有编辑器但题型未识别也登记', 'force=True' in quiz)

    sign = read('task/xxt/sign.py')
    check('签到成功判定排除否定词', 'negative' in sign and '未成功' in sign)

    timer = read('gui/timer_window.py')
    check('定时窗口关闭 = 取消',
          'WM_DELETE_WINDOW", lambda: self._cancel(win)' in timer
          and 'WM_DELETE_WINDOW", lambda :self._set' not in timer)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
