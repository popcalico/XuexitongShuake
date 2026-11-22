# -*- coding: utf-8 -*-
"""P0：A/B 两账号登录身份隔离（2026-11-16）纯桩测试。

不联网、不用真实 Cookie；用 fake driver + 临时 XXT_HOME 验证：
错误密码不进课程、Cookie 恢复只恢复目标账号、A→B 不串号、登录失败终止。

用法：C:/Python315/python.exe tests/test_login_identity_isolation.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_identity_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import login_identity as li

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class D:
    def __init__(self, uid=None, phone=None, html=''):
        self.cookies = []
        if uid:
            self.cookies.append({'name': 'uid', 'value': uid, 'domain': '.chaoxing.com'})
        if phone:
            self.cookies.append({'name': 'phone', 'value': phone, 'domain': '.chaoxing.com'})
        self.html = html
        self.deleted = []
    def get_cookies(self):
        return list(self.cookies)
    def delete_cookie(self, name, **kw):
        self.deleted.append(name)
        self.cookies = [c for c in self.cookies if c.get('name') != name]
    def execute_script(self, js, *a):
        return self.html if 'documentElement' in js else ''


def main():
    print('=== P0 登录身份隔离 ===')

    # A 账号：uid=111, phone=13800138000；B 账号：uid=222, phone=13900139000
    acc_a = {'uid': '111111', 'phone_number': '13800138000'}
    acc_b = {'uid': '222222', 'phone_number': '13900139000'}

    # 1) A 密码正确：A 会话 → 校验通过
    ok, reason, _ = li.verify_identity(D(uid='111111', phone='13800138000'), acc_a)
    check('A账号密码正确→身份通过', ok, reason)

    # 2) A 密码错误但浏览器残留 B 会话 → 身份不匹配，拒绝
    ok, reason, _ = li.verify_identity(D(uid='222222', phone='13900139000'), acc_a)
    check('残留B会话不能冒充A', not ok and ('UID 不匹配' in reason or '手机号' in reason), reason)

    # 3) B 账号只接受 B 的会话
    ok, reason, _ = li.verify_identity(D(uid='222222', phone='13900139000'), acc_b)
    check('B账号身份校验通过', ok, reason)

    # 4) A 会话不能被 B 接受（反向隔离）
    ok, reason, _ = li.verify_identity(D(uid='111111', phone='13800138000'), acc_b)
    check('A会话不能登录B', not ok, reason)

    # 5) 只凭手机号后四位也能判定（UID 缺失时）
    ok, reason, _ = li.verify_identity(D(phone='13800138000'), acc_a)
    check('手机号后四位可判定身份', ok, reason)
    ok, reason, _ = li.verify_identity(D(phone='13900139000'), acc_a)
    check('手机号不匹配被拒', not ok, reason)

    # 6) 页面 JS 里的 uid 也能作为来源
    ok, reason, idt = li.verify_identity(D(html='window.uid=111111;'), acc_a)
    check('页面uid来源可用', ok and 'page:uid' in idt['sources'], idt)

    # 7) 无法验证身份 → 拒绝（不把 title 变化当成功）
    ok, reason, _ = li.verify_identity(D(), acc_a)
    check('无可靠身份拒绝', not ok, reason)

    # 8) 连续 A→B：清理后注入 B cookie，只保留 B
    d = D(uid='111111', phone='13800138000')
    li.clear_site_cookies(d)
    check('切账号前清空旧站点Cookie', d.cookies == [] and 'uid' in d.deleted, d.deleted)
    d.cookies.append({'name': 'uid', 'value': '222222', 'domain': '.chaoxing.com'})
    ok, reason, _ = li.verify_identity(d, acc_b)
    check('切换后只剩B身份', ok, reason)

    # 9) 源码级：face.py 不再用 title 判成功、有 clear_site_cookies
    face = open(os.path.join(ROOT, 'task/tool/face.py'), encoding='utf-8').read()
    check('face不再注释掉Cookie清理',
          '# driver.delete_all_cookies()' not in face)
    check('face调用站点清理', 'clear_site_cookies(driver)' in face)
    check('face调用身份校验', 'verify_identity(driver' in face)
    check('face不再用title判成功（只用title判失败）',
          "if driver.title!='用户登录'" not in face
          and '自动登录验证成功' not in face)

    # 10) 签到恢复会话校验账号归属
    login_src = open(os.path.join(ROOT, 'task/xxt/login.py'), encoding='utf-8').read()
    check('签到恢复校验账号', 'expected_phone' in login_src and '拒绝复用' in login_src)
    sign_ui = open(os.path.join(ROOT, 'gui/sign_window.py'), encoding='utf-8').read()
    check('bootstrap传账号给restore', 'expected_phone=phone' in sign_ui)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
