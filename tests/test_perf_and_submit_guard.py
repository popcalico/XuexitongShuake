# -*- coding: utf-8 -*-
"""两项修复的纯桩回归（不开浏览器、不联网、不进事件循环）：

1. 卡顿修复：`Start._trim_log_box()` 行数上限、批量删旧行、只在贴底时跟随滚动；
2. 提交防护：简答题/填空题没答上时，`Answer.submit()` 绝不自动提交，改为仅保存。

用法：C:/Python315/python.exe tests/test_perf_and_submit_guard.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_guard_test_')

from start import Start                     # noqa: E402
import task.quiz_ai as qa                    # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeBox:
    """最小 CTkTextbox 桩：index/delete/yview/insert/see/tag_config。"""
    def __init__(self, lines=0, yview=(0.0, 1.0)):
        self._lines = lines
        self._yview = yview
        self.deletes = []
        self.inserts = []
        self.sees = []
        self.tags = []

    def index(self, which):
        return '%d.0' % (self._lines + 1)

    def delete(self, start, end):
        self.deletes.append((start, end))
        cut = int(float(end)) - 1
        self._lines = max(0, self._lines - cut)

    def yview(self):
        return self._yview

    def insert(self, *args):
        self.inserts.append(args)
        self._lines += 1

    def see(self, *args):
        self.sees.append(args)

    def tag_config(self, *args, **kwargs):
        self.tags.append((args, kwargs))


def main():
    print('=== 1. 日志框行数上限（渐进卡顿修复） ===')
    fake_self = SimpleNamespace(LOG_MAX_LINES=Start.LOG_MAX_LINES,
                                LOG_TRIM_CHUNK=Start.LOG_TRIM_CHUNK)
    check('常量合理', Start.LOG_MAX_LINES >= 1000 and Start.LOG_TRIM_CHUNK > 0,
          (Start.LOG_MAX_LINES, Start.LOG_TRIM_CHUNK))

    small = FakeBox(lines=100)
    Start._trim_log_box(fake_self, small)
    check('未超上限不删除', small.deletes == [], small.deletes)

    big = FakeBox(lines=2501)
    Start._trim_log_box(fake_self, big)
    check('超上限触发裁剪', len(big.deletes) == 1, big.deletes)
    check('裁剪是批量一次删（不是每行删）', len(big.deletes) == 1)
    check('裁剪后行数回落', big._lines <= Start.LOG_MAX_LINES, big._lines)
    check('裁剪留出余量（不会立刻再触发）',
          big._lines <= Start.LOG_MAX_LINES - Start.LOG_TRIM_CHUNK + 1,
          big._lines)

    broken = FakeBox(lines=10)
    broken.index = lambda _: 'not-an-index'
    Start._trim_log_box(fake_self, broken)
    check('index 异常不抛、不删', broken.deletes == [])

    print('=== 2. 只在贴底时跟随滚动 ===')
    class FakeCombo:
        def get(self):
            return 'acc'

    import queue as _queue

    class FakeRoot:
        def __init__(self):
            self.after_calls = 0
        def after(self, _ms, cb):
            # 批量刷新会再次 after；测试里只执行有限次，避免递归
            self.after_calls += 1
            if self.after_calls <= 3:
                cb()

    def make_start(box):
        s = Start.__new__(Start)
        s._log_tags = set()
        s.text_box_map = {}
        s._log_index = {'acc': 'acc'}
        s.log_select = FakeCombo()
        s.root = FakeRoot()
        s._log_queue = _queue.Queue(maxsize=100)
        s._log_closed = False
        s._ensure_log_box = lambda aid: box
        s._alias_map = {'acc': '账号'}
        return s

    def emit(s, aid, text, tag=None):
        s._on_account_output(aid, text, tag)
        s._root_flush = getattr(s, '_root_flush', None)

    at_bottom = FakeBox(lines=0, yview=(0.0, 1.0))
    s = make_start(at_bottom)
    s._on_account_output('acc', '第一行\n', None)
    # 走真实批量刷新逻辑：先入队，再由主线程批量插入
    s.root.after_calls = 0
    s._flush_log_queue()
    check('贴底时插入并跟随', len(at_bottom.inserts) == 1 and len(at_bottom.sees) == 1,
          (len(at_bottom.inserts), len(at_bottom.sees)))

    scrolled_up = FakeBox(lines=0, yview=(0.0, 0.42))
    s2 = make_start(scrolled_up)
    s2._on_account_output('acc', '第一行\n', None)
    s2.root.after_calls = 0
    s2._flush_log_queue()
    check('回看历史时不强制拉到底', len(scrolled_up.inserts) == 1
          and len(scrolled_up.sees) == 0, len(scrolled_up.sees))

    tagged = FakeBox(lines=0, yview=(0.0, 1.0))
    s3 = make_start(tagged)
    s3._on_account_output('acc', 'a\n', 'red')
    s3._on_account_output('acc', 'b\n', 'red')
    s3.root.after_calls = 0
    s3._flush_log_queue()   # 一批插入两行，tag 只配置一次
    check('同一 tag 不重复 tag_config', len(tagged.tags) == 1, len(tagged.tags))

    print('=== 3. 简答题未作答时绝不自动提交 ===')
    def make_answer(blocked, skipped, types, ans_rate=0.95, after=0.9, choice='DeepSeek AI'):
        a = qa.Answer.__new__(qa.Answer)
        a.unanswered_subjective = set(blocked)
        a.skipped_questions = set(skipped)
        a.questionType_list = list(types)
        a.ans_rate = ans_rate
        a.after_finish_question = after
        a.work_choice = choice
        a.driver = SimpleNamespace(
            switch_to=SimpleNamespace(default_content=lambda: None),
            find_element=lambda *x, **k: (_ for _ in ()).throw(Exception('no popup')))
        return a

    a = make_answer({2}, set(), ['单选题', '单选题', '简答题', '单选题'])
    check('写入失败的主观题被识别', a._unanswered_subjective_numbers() == [2],
          a._unanswered_subjective_numbers())

    b = make_answer(set(), {3}, ['单选题', '单选题', '单选题', '简答题'])
    check('被跳过的主观题也被识别', b._unanswered_subjective_numbers() == [3],
          b._unanswered_subjective_numbers())

    c = make_answer(set(), {0}, ['计算题', '单选题'])
    check('计算题等本就答不了的题不误拦', c._unanswered_subjective_numbers() == [])

    # 阈值达标（95% ≥ 90%）且有空简答题 → 必须改为保存，绝不点提交按钮
    calls = []
    d = make_answer({2}, set(), ['单选题'] * 3 + ['简答题'])
    d._save_only = lambda: calls.append('save') or True
    d._work_button = lambda sel: calls.append(sel) or True
    with patch.object(qa, 'image_viewer_is_open', return_value=False), \
         patch.object(qa.time, 'sleep', lambda *x: None):
        result = d.submit()
    check('有空简答题时走保存', 'save' in calls, calls)
    check('有空简答题时未点提交按钮',
          not any('btnSubmit' in str(x) for x in calls), calls)
    check('submit 返回成功（不重答整场）', result is True)

    # 「随机答题」原本无条件提交，也要被拦住
    calls2 = []
    e = make_answer({1}, set(), ['单选题', '简答题'], choice='随机答题')
    e._save_only = lambda: calls2.append('save') or True
    e._work_button = lambda sel: calls2.append(sel) or True
    with patch.object(qa, 'image_viewer_is_open', return_value=False), \
         patch.object(qa.time, 'sleep', lambda *x: None):
        e.submit()
    check('随机答题模式也被拦住', 'save' in calls2
          and not any('btnSubmit' in str(x) for x in calls2), calls2)

    # 没有空主观题时，阈值达标仍应正常提交（不能把提交功能改坏）
    calls3 = []
    f = make_answer(set(), set(), ['单选题'] * 4)
    f._work_button = lambda sel: calls3.append(sel) or True
    f._click_popup_ok = lambda: None
    f.save_score = lambda: None
    with patch.object(qa, 'image_viewer_is_open', return_value=False), \
         patch.object(qa.time, 'sleep', lambda *x: None):
        f.submit()
    check('无空主观题时仍正常提交',
          any('btnSubmit' in str(x) for x in calls3), calls3)

    # ans_rate 为 None 不应崩溃（旧代码 "{:.2%}".format(None) 会 TypeError）
    calls4 = []
    g = make_answer(set(), set(), ['单选题'], ans_rate=None)
    g._save_only = lambda: calls4.append('save') or True
    g._work_button = lambda sel: calls4.append(sel) or True
    with patch.object(qa, 'image_viewer_is_open', return_value=False), \
         patch.object(qa.time, 'sleep', lambda *x: None):
        g.submit()
    check('ans_rate 为 None 不崩溃', True)

    print('=== 3b. 失败路径兜底登记（旧桩兼容 / 答题异常） ===')
    # 旧套件用 object.__new__ 绕过 __init__：登记绝不能抛 AttributeError，
    # 否则 finish_title 会掉进 except、连降级截图都留不下（21:05 那版的回归）。
    old = qa.Answer.__new__(qa.Answer)
    old.questionType_list = ['简答题']
    old._mark_subjective_unanswered(0)
    check('旧桩缺属性时登记不抛异常且能写入',
          0 in (getattr(old, 'unanswered_subjective', None) or set()),
          getattr(old, 'unanswered_subjective', None))

    objective = qa.Answer.__new__(qa.Answer)
    objective.questionType_list = ['单选题']
    objective._mark_subjective_unanswered(0)
    check('客观题失败不误拦自动提交',
          getattr(objective, 'unanswered_subjective', None) is None)

    # 答题过程抛异常同样会留下空答案，必须登记，否则 submit() 会把它交上去
    boom = make_answer(set(), set(), ['简答题'])
    boom.image_questions = set()
    boom.questionType_list = ['简答题']
    boom.num_answer_dit = {0: ['一段答案']}
    boom.questionList0 = [object()]
    boom._relocate = lambda _k: object()

    def _raise(*_a, **_k):
        raise RuntimeError('作答区写入炸了')

    boom._write_answer = _raise
    boom._save_image_question = lambda *_a: 'shot.png'
    check('答题异常时 finish_title 返回 False', boom.finish_title(0) is False)
    check('答题异常也登记空主观题（原实现漏掉）',
          boom._unanswered_subjective_numbers() == [0],
          boom._unanswered_subjective_numbers())

    print('=== 3c. 全简答题交白卷（2026-11-12 20:49 真机日志回归） ===')
    # 真机日志：7 道全是简答题，AI 给了 7 个答案，日志显示 7/7=100% 并「3秒后提交」，
    # 但作答区一个字都没写进去 —— 旧 _write_answer 无条件 return True。
    def submit_calls(a):
        seen = []
        a._save_only = lambda: seen.append('save') or True
        a._work_button = lambda sel: seen.append(sel) or True
        a._click_popup_ok = lambda: None
        a.save_score = lambda: None
        with patch.object(qa, 'image_viewer_is_open', return_value=False), \
             patch.object(qa.time, 'sleep', lambda *x: None):
            a.submit()
        return seen

    blank = make_answer(set(), set(), ['简答题'] * 7, ans_rate=1.0)
    c_blank = submit_calls(blank)
    check('7 道简答题全未确认写入时绝不提交',
          'save' in c_blank and not any('btnSubmit' in str(x) for x in c_blank),
          c_blank)
    check('白名单硬闸拦住「强制自动提交」',
          'save' in submit_calls(make_answer(set(), set(), ['简答题'] * 7,
                                             ans_rate=1.0, after='强制自动提交')))
    check('白名单硬闸拦住「随机答题」',
          'save' in submit_calls(make_answer(set(), set(), ['简答题'] * 7,
                                             ans_rate=1.0, choice='随机答题')))

    mixed = make_answer(set(), set(), ['单选题'] * 3 + ['简答题'], ans_rate=1.0)
    c_mix = submit_calls(mixed)
    check('混合卷里仅 1 道主观题未确认也拦住',
          'save' in c_mix and not any('btnSubmit' in str(x) for x in c_mix), c_mix)

    ok_all = make_answer(set(), set(), ['简答题'] * 2, ans_rate=1.0)
    ok_all.subjective_written = {0, 1}
    c_ok = submit_calls(ok_all)
    check('每道主观题都回读确认后才按阈值正常提交',
          any('btnSubmit' in str(x) for x in c_ok), c_ok)

    print('=== 3d. 写入回读（防 send_keys 假阳性） ===')
    class FakeEl:
        def __init__(self, write_ok):
            self.value = ''
            self._ok = write_ok

        def click(self):
            pass

        def send_keys(self, v):
            if self._ok:
                self.value = v

        def get_attribute(self, name):
            return self.value if name == 'value' else None

    class FakeQ:
        def __init__(self, el):
            self.el = el

        def find_elements(self, by, sel):
            return [self.el] if sel == 'textarea' else []

    class FakeWriteDriver:
        def execute_script(self, script, *a):
            if a and 'innerText' in str(script):
                return a[0].value
            return None

    w = qa.Answer.__new__(qa.Answer)
    w.driver = FakeWriteDriver()
    check('写入后回读到答案才返回 True',
          w._write_answer(FakeQ(FakeEl(True)), 0, '一段答案') is True)
    check('send_keys 没写进去绝不返回 True',
          w._write_answer(FakeQ(FakeEl(False)), 0, '一段答案') is False)
    check('空答案直接判失败',
          w._write_answer(FakeQ(FakeEl(True)), 0, '  ') is False)

    print('=== 4. 源码红线 ===')
    start_src = Path(ROOT, 'start.py').read_text(encoding='utf-8')
    quiz_src = Path(ROOT, 'task', 'quiz_ai.py').read_text(encoding='utf-8')
    check('start.py 有行数上限', 'LOG_MAX_LINES' in start_src)
    check('start.py 有贴底判断', 'at_bottom' in start_src)
    check('quiz_ai 有主观题拦截', '_unanswered_subjective_numbers' in quiz_src)
    check('quiz_ai 有明确原因文案', '提交后无法修改' in quiz_src)
    check('quiz_ai 有反向白名单硬闸',
          'subjective_written' in quiz_src and 'unverified' in quiz_src)
    check('写入后触发编辑器同步',
          '_sync_editor' in quiz_src and 'UE.instants' in quiz_src)
    check('iframe「有文本就算已答」的假阳性已移除',
          quiz_src.count('if looks_written(read_back(') == 2,
          quiz_src.count('if looks_written(read_back('))
    check('简答/填空失败会登记（含异常与循环兜底）',
          quiz_src.count('self._mark_subjective_unanswered(title_num)') >= 6,
          quiz_src.count('self._mark_subjective_unanswered(title_num)'))

    print('=== 5. 每行磁盘 IO 止血（诊断 P1） ===')
    import core.applog as applog
    import core.runner as runner

    home = os.environ['XXT_HOME']
    d = os.path.join(home, 'io_probe')
    with patch('os.makedirs', wraps=os.makedirs) as mk:
        applog._ensure_dir(d)
        applog._ensure_dir(d)
        check('applog 同一目录只创建一次', mk.call_count == 1, mk.call_count)

    run_log = os.path.join(home, 'io_probe_run', '运行日志.log')
    with patch('os.makedirs', wraps=os.makedirs) as mk:
        runner.append_output_log(run_log, '第一行')
        runner.append_output_log(run_log, '第二行')
        check('runner 同一目录只创建一次', mk.call_count == 1, mk.call_count)
    body = Path(run_log).read_text(encoding='utf-8')
    check('降频后日志不丢行', '第一行' in body and '第二行' in body, body)

    old_limit = runner.OUTPUT_LOG_LIMIT
    runner.OUTPUT_LOG_LIMIT = 10
    try:
        runner.append_output_log(run_log, 'x' * 100)
    finally:
        runner.OUTPUT_LOG_LIMIT = old_limit
    check('runner 超限仍轮转成 .old', os.path.exists(run_log + '.old'))

    print('---')
    if FAILS:
        print('FAILED=%d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
