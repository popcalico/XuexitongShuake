# -*- coding: utf-8 -*-
"""V5.1 P1 第26节：Cookie 注入错误必须可见，不能静默忽略。

覆盖：全部成功、普通 Cookie 失败、关键认证 Cookie 失败、全部失败、
身份不匹配、注入后必须刷新并验证身份、日志只含名称/类别不含值。

用法：C:/Python315/python.exe tests/test_cookie_injection_failure.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_cookieinject_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import login_identity as li
from core import safe_pickle
import task.tool.face as face

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class D:
    """fake driver：可让指定名称的 Cookie 注入失败。"""
    def __init__(self, fail_names=(), title='用户登录'):
        self.cookies = []
        self.fail_names = set(fail_names)
        self.title = title
        self.current_url = 'https://i.chaoxing.com/'
        self.added = []
        self.deleted = []
        self.refreshed = 0
        self.navigated = []
        self.verify_calls = 0

    def get_cookies(self):
        return list(self.cookies)

    def delete_cookie(self, name, **kw):
        self.deleted.append(name)
        self.cookies = [c for c in self.cookies if c.get('name') != name]

    def add_cookie(self, c):
        if c.get('name') in self.fail_names:
            raise Exception('InvalidCookieDomainException')
        self.cookies.append(c)
        self.added.append(c.get('name'))

    def refresh(self):
        self.refreshed += 1

    def get(self, url):
        self.navigated.append(url)
        self.title = '个人中心'      # 模拟导航后进入已登录页面

    def execute_script(self, js, *a):
        return ''


COOKIES = [
    {'name': 'uid', 'value': '123456', 'domain': '.chaoxing.com'},
    {'name': 'x', 'value': 'abc', 'domain': '.chaoxing.com'},
]


class _FakeTime:
    @staticmethod
    def sleep(_s):
        pass
    @staticmethod
    def time():
        return 1800000000.0


def run(fail_names=(), identity_ok=True, cookies=None):
    """在受控桩下跑一次 auto_login_with_cookies，返回 (result, driver)。"""
    d = D(fail_names=fail_names)
    orig_load = safe_pickle.load_json_or_pickle
    orig_verify = li.verify_identity
    orig_time = face.time
    safe_pickle.load_json_or_pickle = lambda *a, **k: (list(cookies or COOKIES), None)
    li.verify_identity = lambda driver, cfg, require_identity=False: (
        (True, '身份匹配', {'uid': '123456'}) if identity_ok
        else (False, '身份不匹配', {}))
    face.time = _FakeTime
    try:
        result = face.auto_login_with_cookies(
            d, account_config={'uid': '123456', 'phone_number': '13800138000'})
    finally:
        safe_pickle.load_json_or_pickle = orig_load
        li.verify_identity = orig_verify
        face.time = orig_time
    return result, d


def main():
    print('=== V5.1 P1 Cookie 注入失败不得静默判定成功 ===')

    # 1) 全部注入成功：必须刷新 + 导航 + 身份校验
    r, d = run()
    check('全部成功 → 恢复成功', r is True, r)
    check('注入后刷新了页面', d.refreshed == 1, d.refreshed)
    check('注入后访问了登录态验证页', len(d.navigated) == 1, d.navigated)
    check('两项 Cookie 都注入成功', d.added == ['uid', 'x'], d.added)

    # 2) 关键认证 Cookie 注入失败 → 立即失败，且不继续后续流程
    r, d = run(fail_names=('uid',))
    check('关键 Cookie 失败 → 恢复失败', r is False, r)
    check('关键失败后不刷新/不导航/不进课程',
          d.refreshed == 0 and not d.navigated, (d.refreshed, d.navigated))
    check('关键失败后清理本次注入',
          all(c.get('name') not in ('uid', 'x') for c in d.cookies), d.cookies)

    # 3) 普通 Cookie 失败、关键 Cookie 成功 → 允许继续，但必须有统计与身份校验
    r, d = run(fail_names=('x',))
    check('普通 Cookie 失败不直接判死（仍靠身份校验）', r is True, r)
    check('普通失败仍继续刷新与身份校验',
          d.refreshed == 1 and len(d.navigated) == 1, (d.refreshed, d.navigated))
    check('失败的普通 Cookie 没有被注入', 'x' not in d.added, d.added)

    # 4) 全部注入失败 → 恢复失败
    r, d = run(fail_names=('uid', 'x'))
    check('0 项注入成功 → 恢复失败', r is False, r)
    check('0 项成功不进入导航', not d.navigated, d.navigated)

    # 5) 注入成功但身份不匹配 → 拒绝并清理
    r, d = run(identity_ok=False)
    check('身份不匹配 → 恢复失败', r is False, r)
    check('身份不匹配清理站点 Cookie', not d.cookies, d.cookies)

    # 6) 分类诊断
    class _DomainErr(Exception):
        pass
    _DomainErr.__name__ = 'InvalidCookieDomainException'
    check('域名错误分类正确',
          face._classify_cookie_error(_DomainErr()) == '域名不匹配')
    check('_uid 属于关键认证 Cookie', '_uid' in face.CRITICAL_COOKIE_NAMES)
    check('uid 属于关键认证 Cookie', 'uid' in face.CRITICAL_COOKIE_NAMES)

    # 7) 源码级：注入循环不再静默 pass，必须有统计
    src = open(os.path.join(ROOT, 'task', 'tool', 'face.py'), encoding='utf-8').read()
    check('注入统计存在', 'Cookie 注入统计' in src)
    check('关键 Cookie 失败有硬闸', '关键认证 Cookie 注入失败' in src)
    check('0 项成功有硬闸', 'Cookie 注入 0 项成功' in src)
    seg = src[src.index('Cookie 注入统计：'):src.index('Cookie 注入统计：') + 900]
    check('统计段不打印 Cookie 值', "['value']" not in seg and 'cookie_to_add' not in seg)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
