# -*- coding: utf-8 -*-
"""讨论自动完成回归测试（纯桩，不联网、不开浏览器）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runtime  # noqa: E402
from task import finish_dicussion as disc  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeTextarea:
    def __init__(self, value=''):
        self.value = value

    def get_attribute(self, key):
        return self.value if key == 'value' else ''


def bare(replies=None, ai='AI 生成的简短回复内容', copy=False):
    d = object.__new__(disc.Discussion)
    d.question = '如何看待本章讨论的主题'
    d.answer = ''
    d.API = 'sk-test'
    d.copy_reply = copy
    d.stats = {'total': 0, 'ok': 0, 'skipped': 0, 'failed': 0}
    d._existing_replies = lambda: replies or []
    d._ai_answer = lambda topic: ai
    return d


# ---------------------------------------------------------------- 文本规范化
check('列表答案合并成一段', disc._as_text(['第一段', '第二段']) == '第一段 第二段')
check('字符串形式的列表能解析', disc._as_text('["这是一段简短回复"]') == '这是一段简短回复')
check('普通字符串原样保留', disc._as_text('直接给的答案') == '直接给的答案')
check('回复不超过 80 字', len(disc._as_text('很' * 200)) == disc.MAX_REPLY)
check('多余空白被压平', disc._as_text('  第一行\n\n  第二行  ') == '第一行 第二行')

# ---------------------------------------------------------------- 默认 DeepSeek
runtime.reset()
check('默认不抄已有回复', runtime.copy_existing_reply() is False)
d = bare(replies=['别人写的一条回复'], ai='这是 DeepSeek 生成的简短回答')
d.get_answer()
check('默认即使有回复也直接用 DeepSeek', d.answer == '这是 DeepSeek 生成的简短回答', d.answer)

# ---------------------------------------------------------------- 抄回复开关
d = bare(replies=['别人写的一条回复'], ai='AI 答案', copy=True)
d.get_answer()
check('打开开关后抄用已有回复', d.answer == '别人写的一条回复', d.answer)

# ---------------------------------------------------------------- AI 失败兜底
d = bare(replies=['现成回复内容'], ai='')
d.get_answer()
check('AI 未答出时兜底抄已有回复', d.answer == '现成回复内容', d.answer)
d = bare(replies=[], ai='')
d.get_answer()
check('AI 未答出且无回复时答案为空（由 start 记失败跳过）', d.answer == '')

# ---------------------------------------------------------------- brief prompt
check('prompt 明确要求 30~50 字简短回复', '30~50 字' in disc.SHORT_REPLY_PROMPT)
check('prompt 禁止客套与列表', '不要列表' in disc.SHORT_REPLY_PROMPT
      and '作为AI' in disc.SHORT_REPLY_PROMPT)

# ---------------------------------------------------------------- 提交判定
d = bare()
check('输入框清空视为提交成功', d._submitted(FakeTextarea('')) is True)
check('输入框仍有内容且无回复视为未确认',
      d._submitted(FakeTextarea('还没提交')) is False)
d2 = bare(replies=['已有回复'])
check('输入框未清空但有回复列表时视为已提交',
      d2._submitted(FakeTextarea('未清空')) is True)

# ---------------------------------------------------------------- 源码约束
src = open(os.path.join(ROOT, 'task', 'finish_dicussion.py'), encoding='utf-8').read()
check('放宽 require_mission，避免整批讨论被跳过', 'require_mission=False' in src)
check('不再无条件 driver.close()',
      'len(handles) > len(before_handles)' in src and 'self.driver.close()' in src)
check('异常不再静默吞掉', "self.stats['failed'] += 1" in src)
check('汇总统计存在', "'total': 0" in src and '汇总：共' in src)
check('全课程模式明确标注需真机校准', '需真机确认页面结构' in src)

# ---------------------------------------------------------------- 配置串联
runtime.configure({'discussion_copy_reply': 1,
                   'discussion_scope': '全部讨论（需真机校准）'})
check('配置能开启抄回复', runtime.copy_existing_reply() is True)
check('配置能切到全课程开关',
      runtime.get('discussion_scope') == '全部讨论（需真机校准）')
runtime.reset()

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), ', '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
sys.exit(0)
