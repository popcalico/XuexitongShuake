# -*- coding: utf-8 -*-
"""login_study 四条真实路径的端到端桩测试（2026-11-20）。

为什么必须有这个文件：过去两天连续两次把登录打崩，都是因为**没有任何用例
真正走完 login_study** —— `_login_form_ready` 未定义、清 localStorage 把用户登出，
单测全绿也照样发出去。这里用桩 driver 把四条路径逐条跑通：

  1. 单账号 + 浏览器已登录        → 直接复用，返回 True，不清 Cookie、不填表
  2. 单账号 + 未登录 + 身份对不上  → 只警告，继续执行，返回 True
  3. 多账号 + 未登录 + 身份对不上  → 停止，返回 False（多账号必须防串号）
  4. 未登录 + 没配密码            → 停止，返回 False

用法：C:/Python315/python.exe tests/test_login_flow_paths.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_loginflow_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core
from core import login_identity as li
from core import navigation as nav

FAILS = []
CALLS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Node:
    """输入框桩：真实保存 value，让 _input_value / _clear_input 有意义。"""

    def __init__(self, name, driver=None):
        self.name = name
        self.driver = driver
        self.value = ''
        self.keys = []

    def clear(self):
        self.value = ''

    def send_keys(self, *args):
        self.keys.append(args)
        if len(args) == 1 and isinstance(args[0], str):
            self.value += args[0]

    def get_attribute(self, name):
        return self.value if name == 'value' else ''

    def click(self):
        CALLS.append('click:' + self.name)
        # 点「登录」后服务端通过 → 标题不再是「用户登录」
        # （不模拟这一步，login_study 会真等 20 秒结果轮询）
        if self.name == 'loginBtn' and self.driver is not None:
            self.driver.title = '个人空间'


class Driver:
    def __init__(self, logged_in=False):
        self.logged_in = logged_in
        self.current_url = ('https://i.chaoxing.com/base' if logged_in
                            else 'https://passport2.chaoxing.com/login')
        self.title = '个人空间' if logged_in else '用户登录'
        self.fields = {'phone': Node('phone', self), 'pwd': Node('pwd', self),
                       'loginBtn': Node('loginBtn', self)}

    def find_element(self, by, value):
        return self.fields[value]

    def find_elements(self, by, value):
        if value in self.fields:
            return [self.fields[value]]
        return []

    def get_cookies(self):
        return ([{'name': 'UID', 'domain': '.chaoxing.com', 'value': 'x'}]
                if self.logged_in else [])

    def execute_script(self, script, *a):
        if 'innerText' in script:
            return '个人空间 退出 我的课程' if self.logged_in else '用户登录'
        return ''

    def implicitly_wait(self, t):
        pass


def run_login(logged_in, single, password, identity_ok):
    """按给定条件跑一次 login_study，返回 (结果, 调用记录, driver)。"""
    CALLS.clear()
    d = Driver(logged_in=logged_in)
    saved = {
        'navigate': nav.navigate,
        'clear_site_cookies': li.clear_site_cookies,
        'verify_identity': li.verify_identity,
        'extract_identity': li.extract_identity,
        'turn_page': core.turn_page,
        'record_dom': core.record_dom,
        'is_single': core._is_single_account,
    }
    old_env = os.environ.get('XXT_ACCOUNT_COUNT')
    try:
        nav.navigate = lambda *a, **k: (True, 'SUCCESS', 'ok')
        li.clear_site_cookies = lambda drv: (CALLS.append('clear_cookie') or ['UID'])
        li.extract_identity = lambda drv, cfg=None: (
            {'uid': 'abcdef12', 'phone_tail': '', 'sources': ['cookie:UID']}
            if logged_in else {'uid': '', 'phone_tail': '', 'sources': []})
        li.verify_identity = lambda drv, cfg=None, require_identity=True: (
            (True, '身份校验通过', {}) if identity_ok
            else (False, '手机号后四位不匹配（实际 ****4384 / 目标 ****1520）', {}))
        core.turn_page = lambda *a, **k: True
        core.record_dom = lambda *a, **k: ''
        core._is_single_account = lambda acc: single
        os.environ['XXT_ACCOUNT_COUNT'] = '1' if single else '3'
        os.environ.setdefault('XXT_HOME', tempfile.mkdtemp(prefix='xxt_lf_'))
        # 让配置里有一个账号（手机号/密码）
        import json
        from core import paths
        os.makedirs(paths.data('tool'), exist_ok=True)
        with open(paths.data('tool', 'account_info.json'), 'w', encoding='utf-8') as f:
            json.dump({'phone_number': '13800131520' if password else '',
                       'password': password, 'account_id': 'acc_test'}, f)
        result = core.login_study(d, '13800131520', password)
    finally:
        nav.navigate = saved['navigate']
        li.clear_site_cookies = saved['clear_site_cookies']
        li.verify_identity = saved['verify_identity']
        li.extract_identity = saved['extract_identity']
        core.turn_page = saved['turn_page']
        core.record_dom = saved['record_dom']
        core._is_single_account = saved['is_single']
        if old_env is None:
            os.environ.pop('XXT_ACCOUNT_COUNT', None)
        else:
            os.environ['XXT_ACCOUNT_COUNT'] = old_env
    return result, list(CALLS), d


def main():
    print('=== login_study 四条路径 ===')
    _sleep = core.time.sleep
    core.time.sleep = lambda *a, **k: None      # 不等 20 秒结果轮询

    try:
        # 1) 单账号 + 已登录：直接复用，绝不能清 Cookie、不能填表
        result, calls, d = run_login(logged_in=True, single=True,
                                     password='pw', identity_ok=True)
        check('① 单账号已登录 → 返回 True', result is True, result)
        check('① 不清 Cookie（不把用户踢下线）', 'clear_cookie' not in calls, calls)
        check('① 不往输入框写东西（复用现成会话）',
              d.fields['phone'].value == '' and not d.fields['phone'].keys,
              d.fields['phone'].keys)

        # 2) 单账号 + 未登录 + 身份对不上 → 警告但继续（用户现场）
        result, calls, d = run_login(logged_in=False, single=True,
                                     password='pw', identity_ok=False)
        check('② 单账号身份对不上 → 仍然 True（不阻断）', result is True, result)
        check('② 确实填了手机号', d.fields['phone'].value == '13800131520',
              d.fields['phone'].value)
        check('② 确实填了密码', bool(d.fields['pwd'].value))
        check('② 登录前清了站点 Cookie（防残留）', 'clear_cookie' in calls, calls)

        # 3) 多账号 + 未登录 + 身份对不上 → 必须停止
        result, calls, d = run_login(logged_in=False, single=False,
                                     password='pw', identity_ok=False)
        check('③ 多账号身份对不上 → False（严格防串号）', result is False, result)

        # 4) 未登录 + 没密码 → 直接失败
        result, calls, d = run_login(logged_in=False, single=True,
                                     password='', identity_ok=True)
        check('④ 没密码 → False', result is False, result)
        check('④ 没密码时不填表', not d.fields['phone'].keys)
    finally:
        core.time.sleep = _sleep

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
