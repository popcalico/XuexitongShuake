# -*- coding: utf-8 -*-
"""UnboundLocalError / 课程到底误判 的回归（2026-11-21 真机）。

两个真机问题：

1. `UnboundLocalError: cannot access local variable 'runtime'`（main.py:2304）
   `main()` 内部有 `from core import runtime, digest`（考试/作业分支里），
   Python 因此把这两个名字当成**函数局部变量**；普通学习路径没走到那个分支，
   复习判定 `runtime.review_enabled()` 就崩了，整门课直接结束。
   修法：两个名字全部走模块级导入，函数内不许再出现同名局部 import。

2. 「回不到课程页」连续 3 次丢课
   页面停在学习页（`studentstudy?chapterId=...`）时，旧 `_reenter_course` 只会
   `driver.refresh()`，刷新后还是同一页；随后 `find_mission` 在学习页上找
   「章节」标签，白等 15 秒判失败 → 3 次后结束课程。
   修法：不在课程详情页就回「我学的课」重新进入课程。

3. 最后一页被误判成「页面丢了」
   本页任务点全部打勾、`下一页` 点不动时，应该判「本课程已学完」，
   而不是 return 'lost' 回去重找。

用法：C:/Python315/python.exe tests/test_unbound_runtime.py
"""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_unbound_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def func_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class IconStub:
    """任务点图标桩：aria-label + 父节点 class。"""

    def __init__(self, label, parent_class):
        self.label = label
        self.parent_class = parent_class

    def get_attribute(self, name):
        return self.label if name == 'aria-label' else ''

    def find_element(self, by, value):
        outer = self

        class Parent:
            def get_attribute(self, n):
                return outer.parent_class
        return Parent()


class Switch:
    def default_content(self):
        pass

    def frame(self, arg):
        pass


class StudyDriver:
    def __init__(self, icons):
        self.icons = icons
        self.switch_to = Switch()

    def find_elements(self, by, value):
        return self.icons


def main_test():
    print('=== UnboundLocalError + 课程到底判定 ===')
    src = open('main.py', encoding='utf-8').read()
    tree = ast.parse(src)

    # 1) main() 里不许有 runtime / digest 的局部 import
    main_fn = func_node(tree, 'main')
    check('main() 存在', main_fn is not None)
    bad_imports = []
    if main_fn is not None:
        for node in ast.walk(main_fn):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in ('runtime', 'digest'):
                        bad_imports.append(alias.name)
    check('main() 内不再局部 import runtime/digest', not bad_imports, bad_imports)
    check('runtime 与 digest 都在模块级导入',
          'from core import runtime' in src and 'from core import digest' in src)

    # 2) _reenter_course 必须能真正回课程页
    check('有 _reenter_course', 'def _reenter_course(' in src)
    reenter = src[src.index('def _reenter_course('):]
    reenter = reenter[:reenter.index('\ndef ')]
    check('不在课程详情页时会回课程列表重进（不再只刷新）',
          reenter.count('choice_course(') >= 2, reenter.count('choice_course('))
    check('保留了「刷新救不了学习页」的说明', '刷新无效' in reenter)

    # 3) 课程到底判定
    check('有 _page_all_tasks_done', 'def _page_all_tasks_done(' in src)
    stuck_mark = '刷新后仍停在同一页'
    check('run() 在「刷新后仍停在同页 → lost」之前先判「本页已完成」',
          '_page_all_tasks_done(driver)' in src
          and src.index('_page_all_tasks_done(driver)') < src.index(stuck_mark))

    cases = [
        ('全部打勾 → True',
         [IconStub('任务点已完成', 'ans-attach-ct'), IconStub('任务点已完成', 'x')], True),
        ('有未完成 → False',
         [IconStub('任务点已完成', 'x'), IconStub('任务点未完成', 'ans-attach-ct')], False),
        ('父容器标 finished 也算完成',
         [IconStub('', 'ans-job-finished')], True),
        ('没有任务点 → False（不能瞎判学完）', [], False),
    ]
    bad = []
    for name, icons, want in cases:
        got = core._page_all_tasks_done(StudyDriver(icons))
        if got is not want:
            bad.append('%s（得到 %s）' % (name, got))
    check('任务点完成判定 4 个场景', not bad, bad)

    # 4) 保存学习记录不能再整条吞掉
    common_src = open(os.path.join('task', 'tool', 'common.py'), encoding='utf-8').read()
    seg = common_src[common_src.index('def save_record('):]
    seg = seg[:seg.index('\ndef ')]
    seg_code = '\n'.join(line.split('#')[0] for line in seg.splitlines())
    check('save_record 不再用裸 except', 'except:' not in seg_code, '仍有裸 except')
    check('save_record 失败时打印异常类型', 'type(e).__name__' in seg_code)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main_test())
