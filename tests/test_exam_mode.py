# -*- coding: utf-8 -*-
"""课程考试模式（2026-11-08）纯桩回归测试。

考试一旦开始倒计时不停止、提交不可逆，所以这里全部用桩，不联网、不开浏览器、
不点任何真实页面。覆盖：
1. 配置白名单 / 默认值 / runtime 快照（交卷默认关闭）；
2. 安全守卫：人脸/抓拍/切屏、timeOver、剩余时间、作答次数归零；
3. 答案映射：`randomOptions=true` 乱序时必须按 span[data] 真实字母，
   题库返回选项文本时要按文本相似度反查，判断题对/错映射；
4. 自动交卷：默认关、阈值未达、守卫拒绝、找不到交卷按钮时都绝不点确认；
5. 接线检查：main.py 有考试分支且不照抄作业模式的 return，
   设置页 radio=3 不再弹「开发中」，selftest 清单含 task.do_exam。

用法：C:/Python315/python.exe tests/test_exam_mode.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_exam_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import config_manager as cm
from core import runtime
import task.do_exam as ex

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Q:
    """模拟一道乱序单选题：data=C 的选项显示在第二个位置（页面显示 B）。"""
    def __init__(self, judge=False):
        self.is_judge = judge
        self.options = [
            {'real_letter': 'A', 'display_letter': 'A', 'text': '2-3'},
            {'real_letter': 'C', 'display_letter': 'B', 'text': '5-9'},
            {'real_letter': 'B', 'display_letter': 'C', 'text': '4-10'},
            {'real_letter': 'D', 'display_letter': 'D', 'text': '8-12'},
        ]

    @property
    def letters(self):
        return [o['real_letter'] for o in self.options]

    @property
    def option_texts(self):
        return [o['text'] for o in self.options]


class JudgeQ:
    """真实判断题只有两个选项，data 顺序就是 A / B。"""
    is_judge = True

    def __init__(self):
        self.options = [
            {'real_letter': 'A', 'display_letter': 'A', 'text': '对'},
            {'real_letter': 'B', 'display_letter': 'B', 'text': '错'},
        ]

    @property
    def letters(self):
        return [o['real_letter'] for o in self.options]

    @property
    def option_texts(self):
        return [o['text'] for o in self.options]


class FakeDriver:
    def __init__(self):
        self.clicks = 0
        # 2026-11-16 任务01：新判据要求安全监控字段齐全才允许交卷，桩里补齐
        self.hidden = {'remainTime': '5355', 'timeOver': 'false', 'answerTimes': '1'}
        for _k in ex.MONITOR_KEYS:
            self.hidden.setdefault(_k, '0')

    def execute_script(self, js, *args):
        if 'forEach' in js or 'getElementById' in js:
            return dict(self.hidden)
        self.clicks += 1
        return None

    def find_elements(self, by, value):
        return []

    @property
    def window_handles(self):
        return []

    @property
    def current_url(self):
        return ''


def main():
    print('=== 1. 配置与运行时快照 ===')
    keys = ('exam_answer_mode', 'exam_auto_submit', 'exam_submit_ratio',
            'exam_revoke_seconds', 'exam_min_remain_minutes', 'exam_max_questions')
    check('ACCOUNT_KEYS 含全部考试键', all(k in cm.ACCOUNT_KEYS for k in keys))
    check('DEFAULT_ACCOUNT 含全部考试键', all(k in cm.DEFAULT_ACCOUNT for k in keys))
    check('runtime.DEFAULTS 含全部考试键', all(k in runtime.DEFAULTS for k in keys))
    check('默认绝不自动交卷',
          cm.DEFAULT_ACCOUNT['exam_auto_submit'] == 0 and runtime.get('exam_auto_submit') == 0)
    runtime.reset()
    runtime.configure({'exam_auto_submit': 1, 'exam_submit_ratio': 90,
                        'exam_min_remain_minutes': 7})
    check('runtime.configure 能装载考试配置',
          runtime.get('exam_auto_submit') == 1
          and runtime.get('exam_submit_ratio') == 90
          and runtime.get('exam_min_remain_minutes') == 7)
    runtime.reset()
    check('runtime.reset 恢复默认交卷关闭', runtime.get('exam_auto_submit') == 0)

    print('=== 2. 安全守卫 ===')
    g = ex.ExamGuard()
    check('正常页面放行',
          g.reasons({'remainTime': '5355', 'timeOver': 'false', 'answerTimes': '1'}) == [])
    for key in ('needFaceRecognition', 'webNeedFaceRecognition', 'snapshotMonitor',
                'switchScreenControl', 'webNeedScreenShot'):
        check('监控字段 %s 开启即拦截' % key,
              bool(g.reasons({'remainTime': '5355', key: '1'})))
    check('timeOver=true 拦截', bool(g.reasons({'remainTime': '5355', 'timeOver': 'true'})))
    check('answerTimes=0 拦截', bool(g.reasons({'remainTime': '5355', 'answerTimes': '0'})))
    check('剩余时间不足拦截', bool(g.reasons({'remainTime': '30'})))
    check('answerTimes=2 放行（剩余次数，不是异常）',
          g.reasons({'remainTime': '5355', 'answerTimes': '2'}) == [])
    check('缺少 remainTime 时不误判',
          g.reasons({'timeOver': 'false'}) == [])

    print('=== 3. 答案映射（乱序） ===')
    r = ex.AnswerResolver()
    q = Q()
    check('题库字母 C -> 真实 data=C', r.resolve(q, ['C']) == ['C'])
    check('题库文本「4-10」-> 真实 data=B', r.resolve(q, ['4-10']) == ['B'])
    check('题库文本「5-9」-> 真实 data=C（显示是 B）',
          r.resolve(q, ['5-9']) == ['C'])
    check('选项文本含字母不被误判成字母答案',
          r.resolve(q, ['A型']) == ['A'] or r.resolve(q, ['A型']) == [])
    check('多选题 A,C -> [A,C]', r.resolve(q, ['A,C']) == ['A', 'C'])
    check('无答案 -> 空', r.resolve(q, []) == [])
    check('正常字母 A -> A', r.resolve(Q(), ['A']) == ['A'])
    jq = JudgeQ()
    check('判断题「对」-> A', r.resolve(jq, ['对']) == ['A'])
    check('判断题「错」-> B', r.resolve(jq, ['错']) == ['B'])
    check('判断题文本「正确」-> A', r.resolve(jq, ['正确']) == ['A'])
    check('只识别不作答模式不联网',
          (runtime.configure({'exam_answer_mode': '只识别不作答'}),
           ex.AnswerSource('sk-fake').get(q))[1] == [])
    runtime.reset()

    print('=== 4. 自动交卷闸门 ===')
    runtime.reset()
    d = FakeDriver()
    s = ex.ExamSession(d, '测试课程')
    s.stat = {'total': 10, 'answered': 10, 'skipped': 0, 'failed': 0,
              'submitted': 0, 'reasons': []}
    # 任务01：构造「正常完成整卷」前置状态，否则新判据会提前拦截，
    # 测不到本测试要验证的「阈值/守卫/找不到按钮」深层分支。
    s.paper_total = 10
    s.normal_completion = True
    ok, msg = s._maybe_submit(d.hidden)
    check('默认关闭时不交卷', ok is False and '默认不交卷' in msg, msg)
    check('默认关闭时零点击', d.clicks == 0, d.clicks)

    s.cfg = dict(s.cfg, exam_auto_submit=1, exam_submit_ratio=100,
                 exam_revoke_seconds=0)
    s.stat['answered'] = 5
    ok, msg = s._maybe_submit(d.hidden)
    check('答题率未达阈值不交卷', ok is False and '未达阈值' in msg, msg)

    s.stat['answered'] = 10
    low_hidden = dict(d.hidden); low_hidden['remainTime'] = '30'
    ok, msg = s._maybe_submit(low_hidden)
    check('剩余时间不足不交卷', ok is False and '保护阈值' in msg, msg)

    ok, msg = s._maybe_submit(d.hidden)
    check('找不到交卷按钮时不瞎点、不确认', ok is False and '没找到' in msg, msg)
    check('未调用任何点击', d.clicks == 0, d.clicks)
    check('桩环境下 run_exam 不外抛且快速退出',
          ex.run_exam(FakeDriver(), '测试课程', wait_seconds=0) in
          ('wait_timeout', 'exam_error'))

    print('=== 5. 接线检查（源码级） ===')
    main_src = Path(ROOT, 'main.py').read_text(encoding='utf-8')
    ui_src = Path(ROOT, 'gui', 'setting_panel.py').read_text(encoding='utf-8')
    start_src = Path(ROOT, 'start.py').read_text(encoding='utf-8')
    exam_src = Path(ROOT, 'task', 'do_exam.py').read_text(encoding='utf-8')
    check("main.py 有 task_type == '考试' 分支", "task_type == '考试'" in main_src)
    check('考试分支用 continue 不终止课程循环',
          "continue" in main_src.split("task_type == '考试'")[1][:1400])
    check('考试分支调用 run_exam', 'run_exam(driver, course_name, API)' in main_src)
    check('设置页 radio=3 走 _show_exam_mode',
          'self.radio_var.get() == 3' in ui_src and 'self._show_exam_mode()' in ui_src)
    check('设置页不再弹「开发中」', '该功能还在开发中' not in ui_src)
    check('task_type 映射到考试', "3: '考试'" in ui_src)
    check('selftest 清单含 task.do_exam', "'task.do_exam'" in start_src)
    check('do_exam 默认不交卷且禁止递归重做',
          'exam_auto_submit' in exam_src and '绝不递归重试提交' in exam_src)
    check('do_exam 按 span[data] 定位', 'span[data="' in exam_src)
    check('考试汇总写入记录', '_save_record' in exam_src)

    print('---')
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
