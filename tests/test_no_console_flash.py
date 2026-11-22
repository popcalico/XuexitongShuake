# -*- coding: utf-8 -*-
"""黑窗闪屏回归（2026-11-19）纯桩测试。

## 背景

用户反馈：程序一启动就闪一大堆黑窗口，点「保存设置」又闪一大堆，而且更慢。

根因（已复核源码）：GUI 是 `--noconsole` 打包，进程没有控制台。只要调用一次
控制台程序（`icacls` / `taskkill` / `msedgedriver --version`），Windows 就为它
**新建一个控制台窗口**。而保存一次设置要把 4 个敏感字段重新加密落盘，每个字段
旧实现调 2 次 `icacls` → 一次保存闪 8 个黑窗；启动时的「凭据迁移校验」又走一遍
同样的路径（即使配置毫无变化）。

## 本测试锁定的行为

1. `core.subproc.hidden_kwargs()` 在 Windows 上同时给出
   `CREATE_NO_WINDOW` 与 `STARTF_USESHOWWINDOW` + `SW_HIDE`。
2. 全工程**没有**裸 `subprocess.run(` / `subprocess.Popen(` 调用（`subproc.py` 除外）。
3. 凭据值未变化时**不重写文件**（mtime 不变），也不触发新的 icacls。
4. `_restrict_permissions` 对同一路径只执行一次。
5. `icacls` 拿到的是**真实用户名**，不再是不会被展开的 `%USERNAME%` 字面量。
6. `_migrate_secrets` 返回三元组，且「无明文可迁」时 `changed=False`。
7. 端到端：同一配置**第二次加载（模拟重启）不再调用任何 icacls**。

用法：C:/Python315/python.exe tests/test_no_console_flash.py
"""
import glob
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_flash_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import subproc, credential_store as cs

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class _Proc:
    returncode = 0
    stdout = ''
    stderr = ''


def count_icacls():
    """临时替换 _icacls 为计数器，返回 (计数容器, 还原函数)。"""
    box = {'n': 0, 'paths': []}
    original = cs._icacls

    def fake(path):
        box['n'] += 1
        box['paths'].append(path)
        return True

    cs._icacls = fake
    return box, lambda: setattr(cs, '_icacls', original)


def main():
    print('=== 黑窗闪屏回归 ===')

    # 1) Windows 下隐藏参数齐全
    kwargs = subproc.hidden_kwargs()
    if os.name == 'nt':
        check('带 CREATE_NO_WINDOW',
              kwargs.get('creationflags', 0) & 0x08000000 == 0x08000000,
              kwargs.get('creationflags'))
        info = kwargs.get('startupinfo')
        check('带 startupinfo', info is not None)
        check('STARTF_USESHOWWINDOW 已置位',
              bool(info and (info.dwFlags & 0x00000001)),
              getattr(info, 'dwFlags', None))
        check('wShowWindow == SW_HIDE', getattr(info, 'wShowWindow', None) == 0,
              getattr(info, 'wShowWindow', None))
    else:
        check('非 Windows 不加多余参数', kwargs == {}, kwargs)

    # 2) 全工程不得有裸 subprocess 调用
    bare = []
    for path in (glob.glob('core/*.py') + glob.glob('gui/*.py')
                 + glob.glob('task/*.py') + glob.glob('task/tool/*.py')
                 + glob.glob('task/xxt/*.py') + ['main.py', 'start.py']):
        if not path or os.path.basename(path) == 'subproc.py':
            continue
        try:
            src = open(path, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        for token in ('subprocess.run(', 'subprocess.Popen(', 'os.popen(', 'os.system('):
            if token in src:
                bare.append('%s :: %s' % (path, token))
    check('无裸 subprocess / os.system 调用', not bare, bare)

    # 3) 值未变化时不重写文件
    box, restore = count_icacls()
    try:
        name = 'test.flash.value'
        cs.save_secret(name, 'same-value')
        path = cs._secret_path(name)
        first = os.stat(path).st_mtime_ns
        first_calls = box['n']
        time.sleep(0.05)
        cs.save_secret(name, 'same-value')
        again = os.stat(path).st_mtime_ns
        check('同值保存不重写文件', first == again)
        check('同值保存不再调 icacls', box['n'] == first_calls,
              (first_calls, box['n']))

        # 值真的变了必须重写
        time.sleep(0.05)
        cs.save_secret(name, 'changed-value')
        changed = os.stat(path).st_mtime_ns
        check('值变更会重写文件', changed != again)
        check('值变更只调 1 次 icacls', box['n'] == first_calls + 1, box['n'])
    finally:
        restore()

    # 4) 同一路径权限只收紧一次
    cs.reset_permission_cache()
    box, restore = count_icacls()
    try:
        target = os.path.join(tempfile.mkdtemp(prefix='xxt_perm_'), 'a.txt')
        open(target, 'w').close()
        cs._restrict_permissions(target)
        cs._restrict_permissions(target)
        cs._restrict_permissions(target)
        check('同路径只收紧一次权限', box['n'] == 1, box['n'])
        other = target + '.other'
        open(other, 'w').close()
        cs._restrict_permissions(other)
        check('不同路径各自收紧', box['n'] == 2, box['n'])
    finally:
        restore()
        cs.reset_permission_cache()

    # 5) icacls 用真实用户名（旧实现传 '%USERNAME%' 字面量，永远不会展开）
    stored = open('core/credential_store.py', encoding='utf-8').read()
    check('不再出现 %USERNAME% 字面量', "'%USERNAME%:F'" not in stored)
    check('改用真实用户名变量', "'%s:F' % user" in stored)
    check('icacls 走 subproc.run_hidden', 'subproc.run_hidden(' in stored)

    # 6) 迁移函数返回三元组，无明文时 changed=False
    from core import config_manager as cm
    mgr = cm.ConfigManager()
    data = mgr.load_accounts()
    ok, changed, note = mgr._migrate_secrets(data)
    check('_migrate_secrets 返回三元组', isinstance((ok, changed, note), tuple)
          and len((ok, changed, note)) == 3)
    check('无明文时 changed=False', changed is False, (ok, changed, note))

    # 7) 端到端：第二次加载配置不再触发 icacls（= 重启不闪窗）
    mgr.save_accounts()
    cs.reset_permission_cache()
    box, restore = count_icacls()
    try:
        mgr2 = cm.ConfigManager()          # 模拟重新启动
        mgr2.load_accounts()
        check('重启加载配置不再调 icacls', box['n'] == 0, box['n'])
    finally:
        restore()

    print()
    if FAILS:
        print('FAILED %d: %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
