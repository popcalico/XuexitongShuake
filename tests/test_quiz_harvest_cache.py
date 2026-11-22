# -*- coding: utf-8 -*-
"""提交后按平台判定回填缓存（2026-11-19）纯桩测试。

## 需求

部分测验的结果页会在每题后标出对/错。答对说明我们提交的答案与平台一致，
可以放心固化进缓存；答错若也写缓存，下次同题就会一直错下去。

## 本测试锁定的安全边界

1. 只缓存「平台判为答对」的题，答错的绝不写。
2. **题块数量与题目数不一致时，整体放弃**（错位会把 A 题答案记到 B 题）。
3. 没有识别到题块（页面没有对错标记）时静默跳过，不抓瞎写。
4. 答对但没有记录到我们提交的答案时跳过。
5. `submit()` 在 `save_score()` 之后才会做回填。

用法：C:/Python315/python.exe tests/test_quiz_harvest_cache.py
"""
import os
import sys
import tempfile
from contextlib import redirect_stdout
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_harvest_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths
from task import quiz_ai
from task.tool.ai_wen_da import AnswerAPI, Question

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Switch:
    def default_content(self):
        pass

    def frame(self, name):
        pass


class ResultDriver:
    """只负责把结果题块返回给采集脚本。"""

    def __init__(self, blocks):
        self.blocks = blocks
        self.switch_to = Switch()

    def execute_script(self, script, *args):
        if 'mark_item' in script:
            return list(self.blocks)
        return ''

    def find_elements(self, by, value):
        return []


def make_answer(blocks, types, titles, options, answers):
    obj = quiz_ai.Answer.__new__(quiz_ai.Answer)
    obj.driver = ResultDriver(blocks)
    obj.questionType_list = list(types)
    obj.only_title_text = list(titles)
    obj.num_option_dit = {i: list(v) for i, v in enumerate(options)}
    obj.num_answer_dit = {i: list(v) for i, v in enumerate(answers)}
    obj.API_KEY = ''
    return obj


def cache_file_exists(question_text, options):
    api = AnswerAPI()
    q = Question(type='3', question=question_text, options=list(options), API='')
    key = api.question_hash(q)
    return os.path.exists(paths.record(f'cache_ques1_{key}.json'))


def main():
    print('=== 提交后回填缓存 ===')

    titles = ['中国特色社会主义进入新时代是我国发展新的历史方位。',
              '中国特色社会主义进入新时代，意味着科学社会主义在21世纪的中国焕发出强大生机活力。']
    options = [['对', '错'], ['对', '错']]
    answers = [['错误'], ['对']]
    types = ['判断题', '判断题']

    # 1) 一题答对、一题答错 -> 只缓存答对的那道
    blocks = [
        {'cls': 'questionLi dui', 'text': '回答正确'},
        {'cls': 'questionLi cuo', 'text': '回答错误'},
    ]
    obj = make_answer(blocks, types, titles, options, answers)
    with redirect_stdout(io.StringIO()) as buf:
        obj._harvest_correct_answers()
    log = buf.getvalue()
    check('答对写入缓存', cache_file_exists(titles[0], options[0]), log.strip()[-60:])
    check('答错不写缓存', not cache_file_exists(titles[1], options[1]))
    check('日志报告数量', '答对 1 道' in log, log.strip()[-60:])

    # 2) 题块数量对不上 -> 一条都不写
    blocks = [
        {'cls': 'dui', 'text': '回答正确'},
        {'cls': 'dui', 'text': '回答正确'},
        {'cls': 'dui', 'text': '回答正确'},
    ]
    obj = make_answer(blocks, types, titles, options, answers)
    with redirect_stdout(io.StringIO()) as buf:
        obj._harvest_correct_answers()
    log = buf.getvalue()
    check('数量不符时整体放弃', '数量对不上' in log, log.strip()[-80:])

    # 3) 没有题块 -> 静默跳过
    obj = make_answer([], types, titles, options, answers)
    with redirect_stdout(io.StringIO()) as buf:
        obj._harvest_correct_answers()
    check('无题块时不报错', '没有识别到题块' in buf.getvalue())

    # 4) 答对但没记录到我们提交的答案 -> 跳过（不缓存空答案）
    blocks = [{'cls': 'dui', 'text': '回答正确'}]
    obj = make_answer(blocks, ['判断题'], ['只有一题'], [['对', '错']], [[]])
    with redirect_stdout(io.StringIO()):
        obj._harvest_correct_answers()
    check('无提交答案时不缓存空值', not cache_file_exists('只有一题', ['对', '错']))

    # 5) 源码顺序：先 save_score 再回填
    source = open(os.path.join('task', 'quiz_ai.py'), encoding='utf-8').read()
    pos_save = source.find('self.save_score()')
    pos_harvest = source.find('self._harvest_correct_answers()')
    check('submit 在保存成绩之后回填', 0 < pos_save < pos_harvest, (pos_save, pos_harvest))
    check('回填只认答对标记', "'dui' in classes" in source)
    check('回填不写答错', "'cuo' in classes" in source)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
