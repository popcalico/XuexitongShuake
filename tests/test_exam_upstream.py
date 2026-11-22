# -*- coding: utf-8 -*-
"""开源实现整合回归测试（纯桩，不联网、不开浏览器）。

覆盖 2026-11-10 从 ocsjs / chaoxing-toolkit / chaoxing-script 借鉴的算法层：
  1. 题干归一化；
  2. 判断题词表扩充 + isJudgementOptions（恰好两个对错选项才判判断）；
  3. 多选严格方向匹配（TCP 不误选 TCP/IP协议）；
  4. 相似候选消歧；
  5. 文本题答案按空位数对齐、中文句子不被误拆；
  6. AI 按题型定制 prompt；
  7. do_exam 接线：默认路径/交卷逻辑未被改动、兜底常量有来源标注。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task import exam_answer_kb as kb  # noqa: E402
from task import do_exam as ex  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def q(type_name='单选题', stem='题干', options=None):
    return ex.ExamQuestion('q1', stem, type_name, options or [], {})


def opt(letter, text, kind='single'):
    return {'real_letter': letter, 'display_letter': letter, 'text': text, 'kind': kind}


# ---------------------------------------------------------------- 1) 归一化
check('去圆括号/方括号/书名号注释',
      kb.normalize_question('1+1=（  A  ）【单选题】[2分]') == '1+1=',
      kb.normalize_question('1+1=（  A  ）【单选题】[2分]'))
check('去空白与下划线',
      kb.normalize_question(' 甲 _ 乙 \n 丙 ') == '甲乙丙',
      kb.normalize_question(' 甲 _ 乙 \n 丙 '))
check('None 归一化为空串', kb.normalize_question(None) == '')

# ---------------------------------------------------------------- 2) 判断题
for word in ('正确', '对', '是', '√', 'TRUE', 'yes', '1', '对的', '确定'):
    check('判为「对」: %s' % word, kb.judge_side(word) == 'true', kb.judge_side(word))
for word in ('错误', '错', '否', '×', 'False', 'no', '0', '不正确', '不对', '不是'):
    check('判为「错」: %s' % word, kb.judge_side(word) == 'false', kb.judge_side(word))
check('普通文本不判对错', kb.judge_side('TCP/IP协议') == '')
check('两个对错选项识别为判断题',
      kb.is_judgement_options(['正确', '错误']) is True)
check('三个选项不误判为判断题',
      kb.is_judgement_options(['A', 'B', 'C']) is False)
check('两个普通选项不误判为判断题',
      kb.is_judgement_options(['甲', '乙']) is False)
check('ExamQuestion.is_judge 用选项文本兜底',
      q('单选题', options=[opt('A', '正确'), opt('B', '错误')]).is_judge is True)

# ---------------------------------------------------------------- 3) 严格方向匹配
# 借鉴 ocsjs：答案 "TCP" 不能命中选项 "TCP/IP协议"
check('答案⊇选项才命中（同值）', kb.strict_option_match('TCP', 'TCP') is True)
check('答案⊇选项才命中（答案更长）', kb.strict_option_match('TCP协议详解', 'TCP') is True)
check('选项⊇答案不命中（避免 TCP 误选 TCP/IP）',
      kb.strict_option_match('TCP', 'TCP/IP协议') is False)
options = ['TCP', 'TCP/IP协议', 'UDP', 'HTTP']
check('严格匹配只选 TCP',
      kb.resolve_by_text(['TCP'], options) == [0], kb.resolve_by_text(['TCP'], options))
check('纯字母答案仍按字母映射',
      kb.resolve_by_text(['A,C'], options) == [0, 2], kb.resolve_by_text(['A,C'], options))

# ---------------------------------------------------------------- 4) 消歧
check('相似候选只保留最高分',
      kb.disambiguate_similar([(0, 0.95, 'x'), (1, 0.80, 'x'), (2, 0.7, '完全不同')]) == [0, 2],
      kb.disambiguate_similar([(0, 0.95, 'x'), (1, 0.80, 'x'), (2, 0.7, '完全不同')]))

# ---------------------------------------------------------------- 5) 文本答案对齐
check('| 分隔按空位数解析', kb.as_text_answers('12|10|8', expected=3) == ['12', '10', '8'])
check('列表字面量可解析', kb.as_text_answers('["甲","乙"]', expected=2) == ['甲', '乙'])
check('中文逗号按空位数拆分', kb.as_text_answers('甲，乙', expected=2) == ['甲', '乙'])
check('含中文逗号的自然句不误拆（填空题错位教训）',
      kb.as_text_answers('生我者父母，知我者鲍子也。', expected=1)
      == ['生我者父母，知我者鲍子也。'],
      kb.as_text_answers('生我者父母，知我者鲍子也。', expected=1))
check('答案数与空位数不符返回空（不猜位置）',
      kb.as_text_answers('12|10', expected=5) == [])
check('单空文本原样保留',
      kb.as_text_answers('鸦片战争', expected=1) == ['鸦片战争'])
check('空内容返回空', kb.as_text_answers('', expected=1) == [])

# ---------------------------------------------------------------- 6) AI prompt
p_multi = kb.exam_ai_prompt('多选题', '以下哪些是协议', ['TCP', 'UDP'])
check('多选 prompt 要求返回字母串', '多选题' in p_multi and 'AB' in p_multi)
p_judge = kb.exam_ai_prompt('判断题', '地球是圆的', [])
check('判断 prompt 要求只回正确/错误', '正确' in p_judge and '错误' in p_judge)
p_blank = kb.exam_ai_prompt('填空题', '共有几空', [], blank_count=3)
check('填空 prompt 带空位数并要求 | 分隔', '3' in p_blank and '|' in p_blank)
p_single = kb.exam_ai_prompt('单选题', '1+1', ['1', '2'])
check('单选 prompt 只要求一个字母', 'A/B/C/D' in p_single)

# ---------------------------------------------------------------- 7) do_exam 接线
src = open('task/do_exam.py', encoding='utf-8').read()
check('do_exam 引入 exam_answer_kb', 'from task import exam_answer_kb as kb' in src)
check('文本题型分派已接入 run()', 'q.is_text_answer' in src)
check('兜底常量标注未经真机验证', '未经' in src and '真机' in src)
check('兜底翻页命中后不重复点（无连点两次）',
      'if not clicked:' in src and 'self._go_next_by_text()' in src)
check('交卷逻辑未被改动（仍默认关闭）',
      "if not int(self.cfg.get('exam_auto_submit', 0) or 0)" in src)
check('仍按 span[data] 真实字母定位', 'span[data="%s"][qid="%s"]' in src)
check('文本题读不到编辑器时跳过',
      '文本题未找到可填写的编辑器' in src)
check('AnswerResolver 用严格匹配',
      'kb.resolve_by_text' in src)
check('AnswerSource 用按题型 prompt',
      'kb.exam_ai_prompt' in src)

# ---------------------------------------------------------------- 8) 兼容旧行为
check('BANK_TYPES 保持原样',
      ex.BANK_TYPES == {'单选题': 0, '多选题': 1, '填空题': 2, '判断题': 3,
                        '简答题': 9, '论述题': 9, '名词解释': 9})
check('ExamQuestion 默认 blank_count=0', q().blank_count == 0)
check('默认题不是文本题型', q().is_text_answer is False)
check('乱序字母映射仍按真实字母',
      ex.AnswerResolver().resolve(
          q('单选题', options=[opt('C', '甲'), opt('A', '乙')]), ['C']) == ['C'])
check('未知题型回落为单选', q('未知题型').type_name == '未知题型')

# ---------------------------------------------------------------- 9) 11-12 真机 bug：单选被选多个字母
def make(type_name):
    opts = [{'real_letter': l, 'display_letter': l, 'text': t, 'kind': 'single'}
            for l, t in (('A', 'A. stability'), ('B', 'B. reconcile'),
                         ('C', 'C. xxx'), ('D', 'D. yyy'))]
    return ex.ExamQuestion('q1', '题干', type_name, opts, {})

r = ex.AnswerResolver()
check('单选题脏数据 A,B 只取一个字母', r.resolve(make('单选题'), ['A,B']) == ['A'],
      r.resolve(make('单选题'), ['A,B']))
check('单选题多元素答案只取一个',
      r.resolve(make('单选题'), ['A', 'B']) == ['A'],
      r.resolve(make('单选题'), ['A', 'B']))
check('单选题正常单字母不变', r.resolve(make('单选题'), ['C']) == ['C'])
check('单选题选项文本仍可映射',
      r.resolve(make('单选题'), ['stability']) == ['A'],
      r.resolve(make('单选题'), ['stability']))
check('多选题仍允许多个字母',
      r.resolve(make('多选题'), ['A,B']) == ['A', 'B'],
      r.resolve(make('多选题'), ['A,B']))
check('判断题脏数据只取一个',
      r.resolve(make('判断题'), ['A,B']) == ['A'])

src2 = open('task/do_exam.py', encoding='utf-8').read()
check('按题型限制答案数量', 'max_letters = 4 if multi else 1' in src2)
check('翻页按钮文本改为下一题/下一步',
      "('下一题', '下一步')" in src2)
check('CSS 兜底必须校验文本含下一', "'下一' not in (el.text or '')" in src2)
check('题号重复时等待重试而非立刻结束',
      'repeat_qid_count' in src2 and '等待重试' in src2)
check('题库答案有诊断日志', '题库返回=' in src2)

print('---')
if FAILS:
    print('FAILED=%d %s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
