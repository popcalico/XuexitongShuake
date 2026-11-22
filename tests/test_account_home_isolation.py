# -*- coding: utf-8 -*-
"""P0：账号 HOME / XXT_HOME 隔离与登录失败终止（2026-11-16）纯桩测试。"""
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_homeiso_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths, login_identity as li

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== P0 账号HOME隔离 ===')

    # 1) 每个账号的 HOME 唯一且不互相覆盖
    h_a = paths.account_home('acc_A')
    h_b = paths.account_home('acc_B')
    check('不同账号HOME不同', h_a != h_b, '%s vs %s' % (h_a, h_b))
    check('HOME含account id', 'acc_A' in h_a and 'acc_B' in h_b)

    # 2) runner 把 XXT_HOME 指向账号目录（源码级）
    runner_src = open(os.path.join(ROOT, 'core/runner.py'), encoding='utf-8').read()
    check('runner设置XXT_HOME=账号home',
          "env['XXT_HOME'] = home" in runner_src)
    check('runner按account写运行时',
          'write_account_runtime(account_id)' in runner_src)

    # 3) 数据路径跟随 HOME（不是全局固定）
    old_home = paths.HOME
    tmp = tempfile.mkdtemp(prefix='xxt_switch_')
    try:
        paths.HOME = tmp
        p1 = paths.data('tool', 'accounts.json')
        check('路径跟随切换后的HOME', p1.startswith(tmp), p1)
    finally:
        paths.HOME = old_home

    # 4) 登录失败不进课程（源码级：两个入口都检查返回值）
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('main()登录失败return',
          '_logged_in = login_study(driver, phone_number, password)' in main_src)
    check('run_exam_only登录失败return',
          main_src.count('if not login_study(') >= 1)

    # 5) 课程调用在登录成功之后（顺序检查）
    i_login = main_src.find('_logged_in = login_study(driver, phone_number, password)')
    i_course = main_src.find('for course_name in course_lst:')
    check('课程循环在登录检查之后', 0 <= i_login < i_course, (i_login, i_course))

    # 6) 签到：A账号目录误放B会话 → 拒绝（源码级）
    xlogin = open(os.path.join(ROOT, 'task/xxt/login.py'), encoding='utf-8').read()
    check('签到恢复比对账号',
          'saved != want' in xlogin and 'expected_phone' in xlogin)
    check('拒绝时记录脱敏日志',
          'mask_phone(saved)' in xlogin)

    # 7) 日志脱敏
    check('手机号只留后四位', li.mask_phone('13900139000') == '****9000')

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
