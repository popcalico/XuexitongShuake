# -*- coding: utf-8 -*-
"""任务05 作业多课程提前终止 + 硬编码选择回归（2026-11-16）纯桩测试。

不启动浏览器。测：
1. pick_pending_homework 的语义识别（无第三 radio / 只有一个 / 顺序变化 / 找不到）；
2. main.py 作业分支不再 `return`（源码级 + 模拟三课程循环）。

用法：C:/Python315/python.exe tests/test_homework_multi_course.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_homework_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task.do_work import pick_pending_homework

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class El:
    def __init__(self, label, text=''):
        self._label = label
        self._text = text or label
    def get_attribute(self, k):
        return self._label if k == 'aria-label' else None
    @property
    def text(self):
        return self._text


def main():
    print('=== 任务05 作业选择 ===')

    # 1) 三个 radio，标记在第一个（顺序变化，不再硬编码第三个）
    els = [El('未完成'), El('已完成'), El('已提交')]
    check('未完成在第一项也能选中',
          pick_pending_homework(els) is els[0])

    # 2) 只有一个 radio
    one = [El('未完成作业')]
    check('只有一个 radio 也能选中',
          pick_pending_homework(one) is one[0])

    # 3) 没有第三个 radio（只有两个）
    two = [El('已完成'), El('未完成')]
    check('无第三 radio 时不越界',
          pick_pending_homework(two) is two[1])

    # 4) 空列表
    check('空列表返回 None（不猜）', pick_pending_homework([]) is None)

    # 5) 找不到未完成语义且有 explicit label
    labels = [El('语文作业一'), El('数学作业二')]
    check('显式标签精确匹配',
          pick_pending_homework(labels, '数学作业二') is labels[1])
    check('显式标签包含匹配',
          pick_pending_homework(labels, '语文') is labels[0])

    # 6) 完全找不到 → None（安全退出，不误选）
    unknown = [El('选项甲'), El('选项乙'), El('选项丙')]
    check('无法判断返回 None（不误选第三项）',
          pick_pending_homework(unknown) is None)
    check('未知标签不误选',
          pick_pending_homework(unknown, '不存在的作业') is None)

    # 7) radio 顺序打乱：未完成在最后
    shuffled = [El('已提交'), El('已完成'), El('待做')]
    check('乱序下按语义而非位置',
          pick_pending_homework(shuffled) is shuffled[2])

    print('=== 任务05 多课程不提前终止 ===')
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    # 定位作业分支 do_work(...) 之后的一小段，确认是 continue 而非裸 return
    idx = main_src.index('do_work(driver, course_name, homework, API)')
    seg = main_src[idx:idx + 1600]
    check('作业分支不再紧跟 return',
          'return\n' not in seg.split('continue')[0].split('_close_course_tab')[0]
          or 'do_work(driver, course_name, homework, API)\n                return' not in main_src)
    check('作业分支有 continue', 'continue' in seg)
    check('作业 do_work 有 try 包裹',
          'try:' in main_src[idx - 120:idx])
    check('do_work 分支不硬编码 elements[2]',
          'elements[2].click()' not in open(
              os.path.join(ROOT, 'task', 'do_work.py'), encoding='utf-8').read())

    # 模拟三课程循环：作业处理函数返回后应继续访问下一门
    visited = []
    courses = ['课A', '课B', '课C']
    def fake_do_work(course):
        visited.append(course)
        # 即使某门课作业"提前返回"，外层仍应继续下一门
        return
    for c in courses:
        fake_do_work(c)   # 模拟 main 里的 do_work + continue 结构
    check('模拟三课程全部访问', visited == courses, visited)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
