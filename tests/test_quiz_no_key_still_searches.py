# -*- coding: utf-8 -*-
"""未配置 AI Key 时，题库查询必须照常执行（2026-11-19 审计 P1）。

旧实现：
    if self.API_KEY == 'sk-open_deepseek':
        self.no_answer_dit = {全部题: 题面}      # 直接认为「都没答案」
    else:
        self.use_ai_wen_da()                     # 只有配了 Key 才查题库

后果：没填 Key 的用户整场测验/作业一次题库都不查，必然空答。
本测试锁住三点：
  1. `use_ai_wen_da()` 在未配 Key 时仍然被调用（题库照查）；
  2. `use_ai_wen_da()` 的 AI 分支要求 Key 有效，未配时不会每题白跑一次失败请求；
  3. quiz_ai / do_work 都不再存在「哨兵 Key 直接建成全部无答案」的分支。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from contextlib import redirect_stdout

from core import ai_config
from task.quiz_ai import Answer

FAILS = []


def check(name, cond, detail=''):
    if cond:
        print('PASS %s' % name)
    else:
        print('FAIL %s  %s' % (name, detail))
        FAILS.append(name)


def make_answer(api_key):
    """用 __new__ 造最小对象：只喂 use_ai_wen_da 真正会读的属性。"""
    a = object.__new__(Answer)
    a.API_KEY = api_key
    a.image_questions = set()
    a.needs_ai_questions = set()
    a.skipped_questions = set()
    a.questionType_list = ['单选题']
    a.all_title_dit = {0: '1. 下列哪个是 Python 关键字？'}
    a.num_option_dit = {0: ['A. def', 'B. func']}
    a.num_answer_dit = {}
    a.times = 0
    a.work_choice = '自动答题'
    return a


def main():
    print('=== 未配置 Key 仍查题库 ===')

    # 1) 哨兵 Key：use_ai_wen_da 仍然走到题库搜索
    a = make_answer('sk-open_deepseek')
    searched = []
    a._search_bank = lambda i: (searched.append(i), ['A'])[1]
    with redirect_stdout(io.StringIO()):
        a.use_ai_wen_da()
    check('哨兵 Key 下仍执行题库搜索', searched == [0], searched)
    check('哨兵 Key 下题库答案被采用', a.num_answer_dit.get(0) == ['A'],
          a.num_answer_dit)

    # 2) 空 Key 同样要查题库
    b = make_answer('')
    searched2 = []
    b._search_bank = lambda i: (searched2.append(i), ['B'])[1]
    with redirect_stdout(io.StringIO()):
        b.use_ai_wen_da()
    check('空 Key 下仍执行题库搜索', searched2 == [0], searched2)

    # 3) 有效 Key + 需要 AI 的题：才会进 AI 分支
    c = make_answer('sk-real-key-for-test')
    c.needs_ai_questions = {0}
    ai_called = []
    c._ask_one = lambda i: (ai_called.append(i), ['C'])[1]
    c._search_bank = lambda i: []
    orig = ai_config.is_key_valid
    try:
        ai_config.is_key_valid = lambda k: True
        with redirect_stdout(io.StringIO()):
            c.use_ai_wen_da()
    finally:
        ai_config.is_key_valid = orig
    check('有效 Key 且需 AI 时走 AI 分支', ai_called == [0], ai_called)

    # 4) 未配 Key + 需要 AI 的题：不得进 AI 分支（否则每题白跑一次失败请求）
    d = make_answer('sk-open_deepseek')
    d.needs_ai_questions = {0}
    ai_called2 = []
    d._ask_one = lambda i: (ai_called2.append(i), ['D'])[1]
    bank_called = []
    d._search_bank = lambda i: (bank_called.append(i), [])[1]
    orig = ai_config.is_key_valid
    try:
        ai_config.is_key_valid = lambda k: False
        with redirect_stdout(io.StringIO()):
            d.use_ai_wen_da()
    finally:
        ai_config.is_key_valid = orig
    check('未配 Key 时不进 AI 分支', ai_called2 == [], ai_called2)
    check('未配 Key 时仍回落题库', bank_called == [0], bank_called)

    # 5) 源码级：不再有「哨兵 Key 直接建成全部无答案」的分支
    for name in ('task/quiz_ai.py', 'task/do_work.py'):
        src = open(os.path.join(ROOT, name), encoding='utf-8').read()
        check('%s 用 is_key_valid 判断 Key' % name,
              'ai_config.is_key_valid(self.API_KEY)' in src)
        check('%s 不再有哨兵直通分支' % name,
              "if self.API_KEY=='sk-open_deepseek':" not in src
              and 'if self.API_KEY == \'sk-open_deepseek\':' not in src)

    print()
    if FAILS:
        print('FAILED %d: %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
