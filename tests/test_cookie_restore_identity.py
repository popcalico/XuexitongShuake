# -*- coding: utf-8 -*-
"""P0：Cookie 自动恢复只恢复当前目标账号（2026-11-16）纯桩测试。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_cookierestore_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import login_identity as li

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class D:
    def __init__(self, cookies=None, html='', title=''):
        self.cookies = list(cookies or [])
        self.html = html
        self.title = title
        self.deleted = []
        self.refreshed = False
    def get_cookies(self): return list(self.cookies)
    def delete_cookie(self, name, **kw):
        self.deleted.append(name)
        self.cookies = [c for c in self.cookies if c.get('name') != name]
    def add_cookie(self, c): self.cookies.append(c)
    def execute_script(self, js, *a):
        return self.html if 'documentElement' in js else ''
    def refresh(self): self.refreshed = True
    def get(self, url): pass


def main():
    print('=== P0 Cookie恢复身份校验 ===')

    # 1) 恢复前清理站点旧 Cookie（叠加两个账号认证Cookie是串号直接原因）
    d = D(cookies=[
        {'name': 'uid', 'value': 'A', 'domain': '.chaoxing.com'},
        {'name': 'other', 'value': 'keep', 'domain': '.other.com'},
    ])
    cleared = li.clear_site_cookies(d)
    check('恢复前清理站点Cookie', 'uid' in cleared and 'other' not in cleared, cleared)
    check('其它站点不受影响', any(c['name'] == 'other' for c in d.cookies))

    # 2) 恢复出的身份必须与目标一致
    acc = {'uid': '555555', 'phone_number': '13700137000'}
    ok, reason, _ = li.verify_identity(
        D(cookies=[{'name': 'uid', 'value': '555555', 'domain': '.chaoxing.com'}]), acc)
    check('恢复A的Cookie→A身份通过', ok, reason)

    # 3) A目录误放B的Cookie → 身份不匹配，拒绝
    ok, reason, _ = li.verify_identity(
        D(cookies=[{'name': 'uid', 'value': '666666', 'domain': '.chaoxing.com'}]), acc)
    check('A目录放B Cookie被拒', not ok and 'UID 不匹配' in reason, reason)

    # 4) 无法验证身份（只有无关cookie）→ 拒绝
    ok, reason, _ = li.verify_identity(
        D(cookies=[{'name': 'x', 'value': '1', 'domain': '.chaoxing.com'}]), acc)
    check('无法验证身份被拒', not ok, reason)

    # 5) 多个来源冲突时，UID优先且严格比对
    ok, reason, _ = li.verify_identity(
        D(cookies=[
            {'name': 'uid', 'value': '555555', 'domain': '.chaoxing.com'},
            {'name': 'phone', 'value': '13900139000', 'domain': '.chaoxing.com'},
        ]), acc)
    check('UID与手机号冲突仍拒绝', not ok, reason)

    # 6) 源码级：恢复失败时清理本次注入的 Cookie
    face = open(os.path.join(ROOT, 'task/tool/face.py'), encoding='utf-8').read()
    idx = face.find('身份校验失败')
    seg = face[idx:idx+400] if idx >= 0 else ''
    check('身份不匹配时清理Cookie', 'clear_site_cookies(driver)' in seg, seg[:160])
    check('恢复状态机标记MISMATCH',
          'LoginState.IDENTITY_MISMATCH' in face)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
