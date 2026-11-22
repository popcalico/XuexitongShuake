# -*- coding: utf-8 -*-
"""独立「开始考试」入口回归测试（纯桩，不联网、不开浏览器）。

覆盖用户 2026-11-11 的要求：
  1. 单独的开始考试选项（GUI 按钮 + --exam 子进程入口）；
  2. 打开浏览器自动登录，不进课程循环、不点考试入口；
  3. 用户自己选考试，程序只读识别「考试页面链」；
  4. **确认开始考试页必须能识别到**（不只是答题页）；
  5. 识别到答题页才开始作答；
  6. 答完停在页面由用户手动提交，绝不交卷。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task import exam_standalone as es  # noqa: E402
from task import do_exam as ex  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeElement:
    def __init__(self, text=''):
        self.text = text

    def find_element(self, *a, **k):
        return FakeElement('我已阅读并同意 进入考试 考试须知')


class _FakeSwitchTo:
    def __init__(self, driver):
        self._driver = driver

    def window(self, handle):
        self._driver.switch_to_window(handle)

    def default_content(self):
        pass


class FakeDriver:
    """只读桩：可配置 CDP targets / 窗口 URL / body 文本。"""

    def __init__(self, targets=None, windows=None, body='', cdp_ok=True):
        self._targets = targets
        self._windows = windows or []
        self._body = body
        self._cdp_ok = cdp_ok
        self._cur = 0
        self.current_window_handle = 'w0'
        self.switch_to = _FakeSwitchTo(self)

    @property
    def current_url(self):
        if self._windows:
            return self._windows[min(self._cur, len(self._windows) - 1)]
        return 'https://i.chaoxing.com/base'

    @property
    def window_handles(self):
        return ['w%d' % i for i in range(len(self._windows))]

    def switch_to_window(self, handle):
        self._cur = int(str(handle).replace('w', '') or 0)

    def execute_cdp_cmd(self, cmd, args):
        if not self._cdp_ok:
            raise RuntimeError('CDP 不可用')
        return {'targetInfos': [{'type': 'page', 'url': u} for u in (self._targets or [])]
                + [{'type': 'iframe', 'url': u} for u in (self._iframe_urls or [])]}

    _iframe_urls = []

    def find_element(self, *a, **k):
        if a and a[0] == 'tag name':
            return FakeElement(self._body)
        return FakeElement(self._body)


NOTES_URL = 'https://mooc1.chaoxing.com/exam-ans/exam/test/examcode/examnotes?examId=1'
ANSWER_URL = 'https://mooc1.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?id=1'
LIST_URL = 'https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list'
PREVIEW_URL = 'https://mooc1.chaoxing.com/exam-ans/mooc2/exam/preview'
LOOK_URL = 'https://mooc1.chaoxing.com/exam-ans/exam/test/look'

# ---------------------------------------------------------------- 1) URL 分类
check('确认开始考试页 URL 能识别（用户重点要求）',
      es.classify_url(NOTES_URL) == 'notes', es.classify_url(NOTES_URL))
check('答题页 URL 能识别', es.classify_url(ANSWER_URL) == 'answer')
check('考试列表页 URL 能识别', es.classify_url(LIST_URL) == 'list')
check('整卷预览页 URL 能识别', es.classify_url(PREVIEW_URL) == 'preview')
check('考试查看页 URL 能识别', es.classify_url(LOOK_URL) == 'look')
check('普通首页不误判', es.classify_url('https://i.chaoxing.com/base') is None)
check('URL 带查询串也能识别',
      es.classify_url(NOTES_URL + '&foo=bar') == 'notes')

# ---------------------------------------------------------------- 2) 页面链扫描（CDP）
d = FakeDriver(targets=[NOTES_URL])
check('CDP 扫描识别确认页', es.scan_pages(d)[0] == 'notes', es.scan_pages(d)[0])
d = FakeDriver(targets=[ANSWER_URL, NOTES_URL])
check('答题页优先于确认页', es.scan_pages(d)[0] == 'answer', es.scan_pages(d)[0])
d = FakeDriver(targets=[LIST_URL])
check('CDP 扫描识别列表页', es.scan_pages(d)[0] == 'list')
d = FakeDriver(targets=[], body='我已阅读并同意 进入考试')
d.find_element = lambda *a, **k: FakeElement('我已阅读并同意 进入考试')
check('DOM 兜底识别确认页（#startBtn 特征）',
      es.classify_document(d) == 'notes', es.classify_document(d))
d = FakeDriver(targets=['https://i.chaoxing.com/base'])
check('CDP 可用但无考试页时返回 None', es.scan_pages(d)[0] is None)

# ---------------------------------------------------------------- 3) CDP 降级
d = FakeDriver(windows=[NOTES_URL], cdp_ok=False)
check('CDP 不可用时降级识别确认页', es.scan_pages(d)[0] == 'notes',
      es.scan_pages(d)[0])
d = FakeDriver(windows=[NOTES_URL, ANSWER_URL], cdp_ok=False)
check('降级时答题页优先', es.scan_pages(d)[0] == 'answer', es.scan_pages(d)[0])
d = FakeDriver(windows=['https://i.chaoxing.com/base'], cdp_ok=False)
check('降级时普通页不误判', es.scan_pages(d)[0] is None)

# ---------------------------------------------------------------- 4) iframe 列表页
d = FakeDriver(targets=['https://i.chaoxing.com/base'])
d._iframe_urls = [LIST_URL]
check('考试列表嵌在 iframe 也能识别', es.scan_pages(d)[0] == 'list',
      es.scan_pages(d)[0])

# ---------------------------------------------------------------- 5) 强制不交卷
src = open('task/exam_standalone.py', encoding='utf-8').read()
check('会话里强制关闭自动交卷', "session.cfg['exam_auto_submit'] = 0" in src)
check('答完明确提示手动提交', '手动提交' in src)
check('不代点进入考试（红线）', '绝不代点' in src and '绝不交卷' in src)
check('用 already_on_page 跳过 180 秒等待', 'already_on_page=True' in src)
check('do_exam.run 支持 already_on_page 参数',
      'already_on_page' in open('task/do_exam.py', encoding='utf-8').read())
check('确认页提示由用户自己点',
      '请自行勾选同意并点「进入考试」' in src)
check('等待离开答题页避免重复作答', '_wait_leave_answer' in src)

# ---------------------------------------------------------------- 6) 入口接线
start_src = open('start.py', encoding='utf-8').read()
main_src = open('main.py', encoding='utf-8').read()
spec_src = open('start.spec', encoding='utf-8').read()
check('主页有独立开始考试按钮', 'start_exam_button' in start_src
      and '开始考试' in start_src)
check('start_exam 用 --exam 入口', "entry_args = ('--exam',)" in start_src)
check('start.py 有 --exam 分支', '"--exam" in sys.argv' in start_src)
check('--exam 调 run_exam_only', 'core.run_exam_only()' in start_src)
check('main.py 有 run_exam_only', 'def run_exam_only' in main_src)
check('run_exam_only 自动登录', 'login_study(' in main_src
      and 'def run_exam_only' in main_src)
check('run_exam_only 不进课程循环（不调 main(）',
      'run_exam_standalone' in main_src)
check('selftest 清单含 exam_standalone', "'task.exam_standalone'" in start_src)
check('spec hiddenimports 含 exam_standalone',
      'task.exam_standalone' in spec_src)
check('普通学习入口恢复 --core', "entry_args = ('--core',)" in start_src)

# ---------------------------------------------------------------- 7) 旧考试模式未被破坏
check('旧 ExamSession.run 默认仍等待进入答题页',
      'already_on_page=False' in open('task/do_exam.py', encoding='utf-8').read())
check('旧交卷逻辑仍在（由配置控制）',
      "self.cfg.get('exam_auto_submit', 0)" in open('task/do_exam.py', encoding='utf-8').read())
check('独立会话复用 ExamSession', 'exam.ExamSession' in src)

# ---------------------------------------------------------------- 8) 12:53 真机 bug 回归
# 现象：CDP 识别到答题页 URL，但 Selenium 焦点没切到那个窗口，
# read_current_question 在首页返回 None → 2 秒就误判「答题完成（0题）」。
class _WindowDriver(FakeDriver):
    def __init__(self, urls):
        super().__init__(windows=urls, cdp_ok=False)
        self.focused = None

    def switch_to_window(self, handle):
        self._cur = int(str(handle).replace('w', '') or 0)
        self.focused = handle


d = _WindowDriver(['https://i.chaoxing.com/base', ANSWER_URL])
check('能把焦点切到答题页窗口', es.focus_answer_window(d) is True)
check('切窗口后当前 URL 是答题页',
      es.PAGE_ANSWER in d.current_url, d.current_url)
d2 = _WindowDriver(['https://i.chaoxing.com/base', NOTES_URL])
check('没有答题页窗口时返回 False（不误判完成）',
      es.focus_answer_window(d2) is False)

stand = open('task/exam_standalone.py', encoding='utf-8').read()
check('答题分支先切窗口再启动会话',
      'focus_answer_window(driver)' in stand)
check('切不到窗口时继续等待、不判定完成',
      '继续等待，绝不把「读不到题」误判为答题完成' in stand)
check('0 题不算答题完成',
      "if session.stat['total'] == 0:" in stand
      and '不判定为完成' in stand)
check('0 题时不进入长等待',
      '继续监听，等页面加载或你重新进入答题页' in stand)

exam_src = open('task/do_exam.py', encoding='utf-8').read()
check('第一题读不到时等待重试（不立刻判完成）',
      'first_question_deadline' in exam_src and '题目容器尚未渲染' in exam_src)
check('读不到题时有诊断日志', '_log_no_question_diagnostic' in exam_src)
check('读题有 iframe 兜底', '_locate_question_box' in exam_src)
check('宽松选择器兜底', "div.singleQuesId[data]" in exam_src)

print('---')
if FAILS:
    print('FAILED=%d %s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
