# -*- coding: utf-8 -*-
"""「无法处理的题」策略回归测试（纯桩，不联网、不开浏览器）。

锁住用户确认的默认行为：
- 默认「AI 尽力完成」：放行主观题/填空题，走 AI 作答并填写；
- 处理不了（数不出空位 / 答案不足 / 写不进去 / AI 失败）自动降级为截图跳过，不卡住；
- 切成「截图跳过」时恢复旧行为，现有 test_quota 断言不被破坏。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runtime  # noqa: E402
from task import quiz_ai  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeDriver:
    def __init__(self):
        self.switch_to = self

    def execute_script(self, *a, **k):
        return None

    def frame(self, *a, **k):
        return None

    def parent_frame(self):
        return None


def bare_answer():
    a = object.__new__(quiz_ai.Answer)
    a.skipped_questions = set()
    a.needs_ai_questions = set()
    a.image_questions = set()
    a.image_unanswered = set()
    a.num_answer_dit = {}
    a.all_title_dit = {}
    a.questionType_list = []
    a.only_title_text = []
    a.num_option_dit = {}
    a.all_optionWebElementList = []
    a.option_text_list = []
    a.questionList0 = []
    a.API_KEY = 'sk-test'
    a.times = 0
    a.work_choice = None
    a.driver = FakeDriver()
    a._ask_one = lambda key: []
    a._write_answer = lambda *args: True
    a._count_blanks = lambda key: 2
    a._save_image_question = lambda *args: 'shot.png'
    a._relocate = lambda key: object()
    return a


# ---------------------------------------------------------------- runtime
runtime.reset()
check('默认策略是 AI 尽力完成', runtime.ai_effort_enabled() is True)
check('默认直接用 DeepSeek、不抄回复', runtime.copy_existing_reply() is False)
check('默认讨论范围是任务点内全部讨论',
      runtime.get('discussion_scope') == '任务点内全部讨论')
runtime.configure({'unanswerable_policy': '截图跳过', 'discussion_copy_reply': 1,
                   'discussion_scope': '全部讨论（需真机校准）'})
check('configure 能切到截图跳过', runtime.ai_effort_enabled() is False)
check('configure 能开启抄回复与全课程范围',
      runtime.copy_existing_reply() is True
      and runtime.get('discussion_scope') == '全部讨论（需真机校准）')
check('configure 忽略未知键', runtime.get('not_a_key') is None)
runtime.reset()
check('reset 恢复默认值',
      runtime.ai_effort_enabled() is True and runtime.copy_existing_reply() is False)

# ---------------------------------------------------------------- 答案规范化
check('列表答案合并成文本',
      quiz_ai.Answer._answer_to_text(['甲', '乙']) == '甲\n乙')
check('字符串答案原样返回', quiz_ai.Answer._answer_to_text('答案') == '答案')
check('空答案返回空串', quiz_ai.Answer._answer_to_text([]) == '')

# ---------------------------------------------------------------- 放行 / 降级
runtime.reset()
a = bare_answer()
a.all_title_dit = {0: '【填空题】题干'}
a.questionType_list = ['填空题']
a.num_option_dit = {0: ['']}
a.needs_ai_questions = {0}
a._ask_one = lambda key: ['甲', '乙']
a.use_ai_wen_da()
check('放行模式：AI 答案写进 num_answer_dit',
      a.num_answer_dit.get(0) == ['甲', '乙'], a.num_answer_dit)
check('放行模式：该题不进 skipped_questions', 0 not in a.skipped_questions)

a = bare_answer()
a.all_title_dit = {0: '【填空题】题干'}
a.questionType_list = ['填空题']
a.num_option_dit = {0: ['']}
a.needs_ai_questions = {0}
a._ask_one = lambda key: []          # AI 没答出来
a.use_ai_wen_da()
check('AI 失败自动降级为截图跳过',
      a.num_answer_dit.get(0) == [] and 0 in a.skipped_questions
      and 0 not in a.needs_ai_questions)

runtime.configure({'unanswerable_policy': '截图跳过'})
a = bare_answer()
a.all_title_dit = {0: '【填空题】题干'}
a.questionType_list = ['填空题']
a.num_option_dit = {0: ['']}
a.use_ai_wen_da()
check('截图跳过模式：填空题仍不去联网',
      a.num_answer_dit.get(0) == [] and 0 not in a.needs_ai_questions)
runtime.reset()

# ---------------------------------------------------------------- 填空填写
def fill_case(answer, blanks=2, write_ok=True, policy='AI 尽力完成'):
    runtime.configure({'unanswerable_policy': policy})
    a = bare_answer()
    a.questionType_list = ['填空题']
    a.num_answer_dit = {0: answer}
    a.questionList0 = [object()]
    a._count_blanks = lambda key: blanks
    a._blank_elements = lambda key: [object()] * blanks
    a._blank_frames = lambda key: []
    written = []
    a._fill_blank = lambda el, text, frames, idx: (
        written.append((idx, text)), write_ok)[1]
    saved = []
    a._save_image_question = lambda *args: saved.append(args) or 'shot.png'
    result = a.finish_title(0)
    runtime.reset()
    return result, saved, written


r, saved, written = fill_case(['甲', '乙'])
check('空位数与答案数一致时填写成功', r is True and not saved, (r, saved))
r, saved, written = fill_case(['甲', '乙', '丙'])
check('答案多于空位时仍填写成功（多余忽略）', r is True and not saved, (r, saved))
r, saved, written = fill_case(['甲'])
check('答案不足时降级截图跳过', r is False and len(saved) == 1, (r, saved))
r, saved, written = fill_case(['甲', '乙'], blanks=0)
check('数不出空位时降级截图跳过', r is False and len(saved) == 1, (r, saved))
r, saved, written = fill_case(['甲', '乙'], write_ok=False)
check('所有空位都写不进去时降级截图跳过', r is False and len(saved) == 1, (r, saved))
r, saved, written = fill_case(['甲,乙'])
check('AI 把多个答案塞一个字符串时能拆开填写', r is True and not saved, (r, saved))

# 2026-11-06 真机截图回归：第3题「生我者父母，知我者鲍子也。」被误拆 + 答案串空
r, saved, written = fill_case(['生我者父母，知我者鲍子也。'], blanks=1)
check('单答案含中文逗号且只有 1 个空时完整写入，不被截断',
      r is True and written == [(0, '生我者父母，知我者鲍子也。')],
      (r, written))
r, saved, written = fill_case(['12', '10', '8', '30', '70'], blanks=5)
check('5 个空分别写入 5 个答案，索引一一对应',
      r is True and [i for i, _ in written] == [0, 1, 2, 3, 4]
      and [t for _, t in written] == ['12', '10', '8', '30', '70'], written)
r, saved, written = fill_case(['甲，乙'], blanks=2)
check('拆分后数量等于空位数才采用',
      written == [(0, '甲'), (1, '乙')], written)
r, saved, written = fill_case(['甲，乙，丙'], blanks=2)
check('拆分数量不匹配时不硬填，降级截图跳过',
      r is False and written == [] and len(saved) == 1, (r, written))

# ---------------------------------------------------------------- 简答填写
runtime.reset()
a = bare_answer()
a.questionType_list = ['简答题']
a.num_answer_dit = {0: ['这是一段回答']}
a.questionList0 = [object()]
a.finish_title(0)
a.questionType_list = ['简答题']
a.num_answer_dit = {0: []}
a.questionList0 = [object()]
a._save_image_question = lambda *args: 'shot.png'
check('简答题无答案时返回 False（不抛异常）', a.finish_title(0) is False)

# ---------------------------------------------------------------- 早退行为
a = bare_answer()
a.questionType_list = ['填空题']
a.skipped_questions = {0}
check('已在 skipped_questions 的题直接跳过', a.finish_title(0) is False)

# ---------------------------------------------------------------- 源码约束
src = open(os.path.join(ROOT, 'task', 'tool', 'ai_wen_da.py'), encoding='utf-8').read()
check('题库对填空/简答的跳过已受策略开关控制',
      "not runtime.ai_effort_enabled()" in src)
qsrc = open(os.path.join(ROOT, 'task', 'quiz_ai.py'), encoding='utf-8').read()
check('填写分支支持多编辑器探测', '_write_answer' in qsrc
      and '[contenteditable=\"true\"]' in qsrc)
check('填空题 prompt 会带上空位数量', '共有 {blanks} 个空' in qsrc)

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), ', '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
sys.exit(0)
