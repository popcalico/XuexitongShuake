# -*- coding: utf-8 -*-
"""任务01 考试提前交卷缺陷回归（2026-11-16）纯桩测试。

不复现真实考试：不联网、不开浏览器、不点任何页面。只驱动
`ExamSession._submit_blockers()` / `_maybe_submit()` 的判据与状态机。

覆盖任务单 13 项：
- 3/85 不按 3/3 算；整卷总数未知不交；异常退出不交；normal_completion=False 不交；
- remainTime 缺失/非数字/0 不交；安全字段缺失不交；用户停止不交；
- 撤销窗口期间条件变化不交；独立 --exam 永不交；正常开启时仍能交。

用法：C:/Python315/python.exe tests/test_exam_submit_safety.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_submit_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runtime
import task.do_exam as ex

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# 全部安全字段都在且为关闭状态（真机采集值 0/false）
def safe_hidden(remain='3000'):
    h = {k: '0' for k in ex.MONITOR_KEYS}
    h.update({'remainTime': remain, 'timeOver': 'false', 'answerTimes': '1'})
    return h


class NoClickDriver:
    """记录是否有人试图点「交卷」/确认；execute_script 返回隐藏字段。"""
    def __init__(self, hidden=None, click_submit=True):
        self.hidden = hidden if hidden is not None else safe_hidden()
        self.click_submit = click_submit
        self.clicked_submit = 0
        self.clicked_confirm = 0

    def execute_script(self, script, *a, **k):
        if 'querySelectorAll' in script and 'hidden' in script:
            return dict(self.hidden)
        if 'arguments[0].click' in script and a:
            el = a[0]
            if getattr(el, '_text', '') == '交卷':
                self.clicked_submit += 1
            elif getattr(el, '_outer', None) is self:
                self.clicked_confirm += 1
        return {}

    class _El:
        def __init__(self, text, outer):
            self._text = text
            self._outer = outer
        @property
        def text(self):
            return self._text
        def is_displayed(self):
            return True

    def find_elements(self, by, value):
        # 代码按 tag 名 ('a','button','span','div') 遍历找文本==交卷的元素
        if self.click_submit and value in ('a', 'button', 'span', 'div'):
            return [self._El('交卷', self)]
        return []

    def find_element(self, by, css):
        if self.CONFIRM_OK in css:
            return self._El('', self)
        raise Exception('not found')

    CONFIRM_OK = ex.CONFIRM_OK

    def execute_script_on(self, *a, **k):
        pass


def make_session(driver, paper_total, processed, answered, normal,
                 auto_submit=1, stop_check=None):
    s = ex.ExamSession(driver, '测试课程', '', stop_check=stop_check)
    s.cfg = dict(runtime.state())
    s.cfg['exam_auto_submit'] = auto_submit
    s.cfg['exam_submit_ratio'] = 100
    s.cfg['exam_revoke_seconds'] = 0     # 测试不等撤销窗口
    s.paper_total = paper_total
    s.normal_completion = normal
    s.stat['total'] = processed
    s.stat['answered'] = answered
    return s


def main():
    print('=== 任务01 考试交卷安全判据 ===')

    # 1) 只遍历 3 题、整卷 85 题：绝不能按 3/3 算 100%
    s = make_session(NoClickDriver(), 85, 3, 3, True)
    ok, why = s._maybe_submit(safe_hidden())
    check('3/85 不按 3/3 计算，不交卷', ok is False and '3/85' in why, why)

    # 2) 整卷总数未知（paper_total=0）：不交
    s = make_session(NoClickDriver(), 0, 85, 85, True)
    ok, why = s._maybe_submit(safe_hidden())
    check('整卷总数未知不交卷', ok is False and '未知' in why, why)

    # 3) 只答部分就异常退出：normal_completion=False → 不交
    s = make_session(NoClickDriver(), 85, 40, 40, False)
    ok, why = s._maybe_submit(safe_hidden())
    check('异常/提前退出(normal=False)不交卷', ok is False and '非正常完成' in why, why)

    # 4) 显式 normal_completion=False 判据（即使题数齐全）
    s = make_session(NoClickDriver(), 85, 85, 85, False)
    ok, why = s._maybe_submit(safe_hidden())
    check('题数齐全但 normal=False 仍不交', ok is False, why)

    # 5) remainTime 缺失
    h = safe_hidden(); del h['remainTime']
    s = make_session(NoClickDriver(), 85, 85, 85, True)
    ok, why = s._maybe_submit(h)
    check('remainTime 缺失不交卷', ok is False and '剩余时间' in why, why)

    # 6) remainTime 非数字
    s = make_session(NoClickDriver(), 85, 85, 85, True)
    ok, why = s._maybe_submit(safe_hidden('abc'))
    check('remainTime 非数字不交卷', ok is False and '非数字' in why, why)

    # 7) remainTime=0
    s = make_session(NoClickDriver(), 85, 85, 85, True)
    ok, why = s._maybe_submit(safe_hidden('0'))
    check('remainTime=0 不交卷', ok is False and 'remainTime=0' in why, why)

    # 8) 安全监控字段缺失
    h = safe_hidden(); del h[ex.MONITOR_KEYS[0]]
    s = make_session(NoClickDriver(), 85, 85, 85, True)
    ok, why = s._maybe_submit(h)
    check('安全监控字段缺失不交卷', ok is False and '无法可靠读取' in why, why)

    # 9) 安全监控字段开启（=1）
    h = safe_hidden(); h[ex.MONITOR_KEYS[0]] = '1'
    s = make_session(NoClickDriver(), 85, 85, 85, True)
    ok, why = s._maybe_submit(h)
    check('监控字段开启不交卷', ok is False, why)

    # 10) 用户停止任务
    stopped = {'v': False}
    s = make_session(NoClickDriver(), 85, 85, 85, True,
                     stop_check=lambda: stopped['v'])
    stopped['v'] = True
    ok, why = s._maybe_submit(safe_hidden())
    check('用户停止后不交卷', ok is False and '停止' in why, why)

    # 11) 撤销窗口期间条件变化（复检用新 hidden：remainTime 变 0）
    class ChangingDriver(NoClickDriver):
        def execute_script(self, script, *a, **k):
            if 'querySelectorAll' in script and 'hidden' in script:
                # 撤销后复检时重读 hidden，此时倒计时已归零
                return safe_hidden('0')
            return {}
    s = ex.ExamSession(ChangingDriver(), '测试课程', '')
    s.cfg = dict(runtime.state()); s.cfg['exam_auto_submit'] = 1
    s.cfg['exam_submit_ratio'] = 100; s.cfg['exam_revoke_seconds'] = 1
    s.paper_total = 85; s.normal_completion = True
    s.stat['total'] = 85; s.stat['answered'] = 85
    ok, why = s._maybe_submit(safe_hidden())
    check('撤销窗口后条件变化不交卷', ok is False and '复检' in why, why)

    # 12) 独立 --exam 入口：cfg 强制 auto_submit=0 → 永不交
    s = make_session(NoClickDriver(), 85, 85, 85, True, auto_submit=0)
    ok, why = s._maybe_submit(safe_hidden())
    check('独立入口(auto_submit=0)永不交卷', ok is False, why)

    # 13) 用户开启 + 全部条件正常：原有功能仍能执行
    drv = NoClickDriver()
    s = make_session(drv, 85, 85, 85, True, auto_submit=1)
    ok, why = s._maybe_submit(safe_hidden())
    check('全条件满足仍可正常交卷（功能未删）', ok is True, why)
    check('正常交卷确实点了交卷+确认',
          drv.clicked_submit == 1 and drv.clicked_confirm == 1 and s.stat['submitted'] == 1,
          'submit=%s confirm=%s submitted=%s' % (
              drv.clicked_submit, drv.clicked_confirm, s.stat['submitted']))

    # 14) 答题率阈值：整卷 85 只答 80 / 阈值 100% → 不交
    s = make_session(NoClickDriver(), 85, 85, 80, True)
    ok, why = s._maybe_submit(safe_hidden())
    check('整卷答题率未达阈值不交卷', ok is False and '答题率' in why, why)

    # 15) 阈值可调：80/85 在阈值 90% 下可交
    s = make_session(NoClickDriver(), 85, 85, 80, True)
    s.cfg['exam_submit_ratio'] = 90
    ok, why = s._maybe_submit(safe_hidden())
    check('阈值90%下 80/85 可交', ok is True, why)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS (%d 项)' % 15)
    return 0


if __name__ == '__main__':
    sys.exit(main())
