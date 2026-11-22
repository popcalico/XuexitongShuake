# -*- coding: utf-8 -*-
"""静态守卫：`_xxx()` 被调用但从来没定义过（2026-11-20 事故）。

事故：`login_study` 里调用了 `_login_form_ready(driver)`，但函数**根本没写**。
上线后子进程直接 `NameError: name '_login_form_ready' is not defined`，
整个登录流程被打死，浏览器停在被清空会话后的登录页 ——
用户看到的是「现在浏览器都不登录了」。

69 个单元测试全绿也没抓到它，因为没有一个用例真的走完登录流程。
这个测试用 AST 静态扫描整个工程：凡是以 `_` 开头、作为函数被调用的名字，
必须在同一文件里出现过（函数/类/赋值/import/参数/except 变量），
否则直接失败。这类错误零成本可查，不该再流到用户手里。

用法：C:/Python315/python.exe tests/test_no_undefined_calls.py
"""
import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def undefined_calls(path):
    """返回该文件里「以 _ 开头被调用但从未定义/导入」的名字。"""
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    defined = set()
    star_import = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            # 装饰器里也可能出现名字，但那是别的检查范围
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == '*':
                    star_import = True
                defined.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.Global):
            defined.update(node.names)
    if star_import:
        return []          # `from x import *` 无法静态判定，跳过
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id.startswith('_')}
    return sorted(n for n in called
                  if n not in defined and not hasattr(builtins, n))


def targets():
    files = ['main.py', 'start.py']
    for folder in ('core', 'task', 'gui', os.path.join('task', 'tool'),
                   os.path.join('task', 'xxt')):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith('.py'):
                files.append(os.path.join(folder, name))
    return files


def main():
    print('=== 未定义函数调用守卫 ===')
    bad = {}
    checked = 0
    for path in targets():
        if not os.path.isfile(path):
            continue
        checked += 1
        missing = undefined_calls(path)
        if missing:
            bad[path] = missing
    print('扫描 %d 个文件' % checked)
    check('没有「调用了但没定义」的下划线函数', not bad, bad)

    # 反向自检：守卫本身要能抓到问题，否则它会永远绿
    probe = os.path.join(os.environ.get('TEMP', '.'), '_xxt_guard_probe.py')
    with open(probe, 'w', encoding='utf-8') as f:
        f.write('def ok():\n    _missing_func(1)\n')
    try:
        got = undefined_calls(probe)
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    check('守卫能识别出未定义调用（自检）', got == ['_missing_func'], got)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
