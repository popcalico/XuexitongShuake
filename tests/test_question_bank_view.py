"""守卫：题库查询页必须能显示/删除新格式（.json）的题库缓存。

背景（2026-11-22 通读时发现）：
  题库缓存从 2026-11-19 起改存 `record/cache_ques1_*.json`
  （`task/tool/ai_wen_da.py:57-64`，写入 `{'_fmt':1,'data':{expire,value}}`），
  但 `start.py` 的「题库查询」页与「删除所有缓存题目」仍只认 `.pkl`：
    · 页面永远显示"暂无缓存的题目"
    · 删除提示"已成功删除"，实际一个文件都没删
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> %s' % detail) if detail else ''))
    if not ok:
        FAILS.append(name)


def body_of(src, func):
    start = src.index('def %s(' % func)
    m = re.search(r'\n    def ', src[start + 5:])
    return src[start:start + 5 + m.start()] if m else src[start:]


def main():
    src = open('start.py', encoding='utf-8').read()

    print('=== 1. 题库查询页 ===')
    view = body_of(src, 'show_question_bank')
    check('不再只按 .pkl 列表', "endswith('.pkl')" not in view)
    check('同时认 .json 与 .pkl',
          "'.json'" in view and "'.pkl'" in view)
    check('用统一读取入口 load_json_or_pickle',
          'load_json_or_pickle' in view)
    check('仍然优先 safe_pickle（不裸 load pkl）',
          'safe_pickle' in view and 'pickle.load(' not in view)

    print()
    print('=== 2. 删除缓存 ===')
    delete = body_of(src, 'delete_huancun')
    check('删除范围含 .json', "'.json'" in delete)
    check('删除范围含 .pkl（兼容旧格式）', "'.pkl'" in delete)
    check('只删 cache_ 前缀，不误删其它文件',
          "startswith('cache_')" in delete)
    check('不再只删 .pkl', "endswith('.pkl')" not in delete)
    check('异常不再是裸 except', 'except:' not in delete)
    check('失败时打印异常类型', 'type(e).__name__' in delete)

    print()
    print('=== 3. 写入端确认（缓存确实是 json） ===')
    writer = open(os.path.join('task', 'tool', 'ai_wen_da.py'), encoding='utf-8').read()
    check("ai_wen_da 写入 cache_*.json", "cache_{key}.json" in writer)
    check('读取兼容旧 pickle', 'load_json_or_pickle' in writer)

    print()
    if FAILS:
        print('FAILED: %s' % FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
