# -*- coding: utf-8 -*-
"""缓存脏答案与判断题真假判定回归（2026-11-19）纯桩测试。

## 真机问题（用户 15:53 那次小测）

```
题目类型: 3 | 题目:中国特色社会主义进入新时代，意味着科学社会主义在21世纪…
📦 缓存查询结果: 找到
🎯 使用缓存答案
['科学社会主义']              ← 判断题的缓存里存着一段名词
😔 所有来源均未找到答案
未找到答案
（前面四道题查完后）正在使用deepseek搜题…
```

用户看到的现象是「缓存明明命中了，却还是去问 DeepSeek」。真实链路是：

1. 缓存**存了脏答案**：判断题（选项只有「对/错」）里存着 `['科学社会主义']`。
2. 旧逻辑只要「缓存非空」就当成命中，**完全跳过网络查询**；
3. 拿去匹配判断题必然失败 → 判「所有来源均未找到答案」；
4. 于是这道题被塞进 `no_answer_dit`，最后一并交给 AI 批量提问。

也就是说：**缓存不但没帮上忙，还挡掉了本该进行的题库查询**。

## 本测试锁定的行为

1. `is_true` / `is_false` 否定词优先，不再因为含一个「是」「对」就判真。
2. 脏缓存（答案与题型不匹配）必须被判为「不可用」。
3. `drop_cache_answer()` 能把脏缓存文件删掉。
4. 源码层面：缓存必须先验证再采用，且失败会回落网络查询。

用法：C:/Python315/python.exe tests/test_cache_dirty_answer.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_cache_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths
from task.tool.ai_wen_da import (AnswerAPI, AnswerMatcher, Question, _JUDGE_FALSE_WORDS)

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 缓存脏答案与判断题判定 ===')

    matcher = AnswerMatcher()

    # 1) 真假判定：否定词优先，长文本不认零散的「是/对」
    true_cases = ['正确', '对', 'A', 'true', '√', '是']
    false_cases = ['错误', '不正确', '否', 'B', 'false', '×',
                   '科学社会主义', '这是错误的', '不是这样',
                   '是国家的、民族的，也是每一个中国人的']
    bad = [(t, matcher.is_true(t)) for t in true_cases if matcher.is_true(t) is not True]
    check('应当判「对」的答案', not bad, bad)
    bad = [(t, matcher.is_true(t)) for t in false_cases if matcher.is_true(t) is not False]
    check('不应判「对」的答案（含脏数据/长文本）', not bad, bad)

    for text in ('错误', '不正确', '错', 'B', 'false'):
        check('is_false 认 %s' % text, matcher.is_false(text) is True)
    for text in ('正确', 'A', '科学社会主义'):
        check('is_false 不误判 %s' % text, matcher.is_false(text) is False)

    check('否定词表含「不正确」', '不正确' in _JUDGE_FALSE_WORDS)

    # 2) 脏缓存必须匹配不上（这才是触发重查的依据）
    judgement = Question(type='3',
                         question='中国特色社会主义进入新时代是我国发展新的历史方位。',
                         options=['对', '错'], API='')
    dirty = AnswerAPI.__new__(AnswerAPI)
    from task.tool.ai_wen_da import AnswerResult
    dirty_result = AnswerResult(form='本地缓存', answer=['科学社会主义'], duration=10)
    probe = matcher.api_answer_match([dirty_result], judgement)
    check('判断题 + 名词答案 -> 判为无答案（脏缓存）', probe.get('haveAnswer') is False)

    clean_result = AnswerResult(form='本地缓存', answer=['错误'], duration=10)
    probe = matcher.api_answer_match([clean_result], judgement)
    check('判断题 + 「错误」 -> 正常命中', probe.get('haveAnswer') is True)

    # 3) drop_cache_answer 真的删掉文件
    api = AnswerAPI()
    api.cache_answer(judgement, ['科学社会主义'])
    key = api.question_hash(judgement)
    cache_file = paths.record(f'cache_ques1_{key}.json')
    check('脏答案确实写进了缓存', os.path.exists(cache_file), cache_file)
    api.drop_cache_answer(judgement)
    check('drop_cache_answer 删除缓存文件', not os.path.exists(cache_file))

    # 4) 源码层面：先验证再采用，失败回落网络
    source = open(os.path.join('task', 'tool', 'ai_wen_da.py'), encoding='utf-8').read()
    check('缓存先做匹配校验', 'cache_usable = bool(probe.get(\'haveAnswer\'))' in source)
    check('校验失败会删缓存', 'self.api.drop_cache_answer(question)' in source)
    check('未命中才走网络查询', 'if not cache_usable:' in source)
    check('日志会说明脏缓存', '与本题型不匹配（脏数据）' in source)

    # 5) 批量问 AI 时要说明是为哪几道题问的（避免误解成「有缓存还问 AI」）
    quiz = open(os.path.join('task', 'quiz_ai.py'), encoding='utf-8').read()
    check('AI 提问日志列出题目', '题库未命中的' in quiz)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
