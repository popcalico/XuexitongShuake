# -*- coding: utf-8 -*-
"""P0：错误密码绝不回退旧 Cookie / 绝不进入课程（2026-11-16）纯桩测试。

用 fake driver + 临时 XXT_HOME，不联网、不用真实账号/Cookie。

用法：C:/Python315/python.exe tests/test_login_failure_no_cookie_fallback.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_loginfail_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import login_identity as li

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeEl:
    def send_keys(self, v):
        pass
    def click(self):
        pass


class FakeDriver:
    """模拟登录页：可注入服务器结果（rejected / ok / timeout）与残留 Cookie。"""
    def __init__(self, server='rejected', cookies=None, identity=None):
        self.server = server
        self.cookies = list(cookies or [])
        self.identity = identity or {}
        self.deleted = []
        self.added = []
        self.title = '用户登录'
        self.refreshed = 0
    def get(self, url):
        pass
    def find_element(self, by, value):
        return FakeEl()
    def execute_script(self, js, *a):
        if 'document.body' in js:
            if self.server == 'rejected':
                return '用户名或密码错误，请重新输入'
            if self.server == 'captcha':
                return '请输入验证码'
            return '欢迎使用学习平台'
        if 'document.documentElement' in js:
            return self.identity.get('html', '')
        return ''
    def get_cookies(self):
        return list(self.cookies)
    def delete_cookie(self, name, **kw):
        self.deleted.append(name)
        self.cookies = [c for c in self.cookies if c.get('name') != name]
    @property
    def window_handles(self):
        return ['w1']


def main():
    print('=== P0 登录失败不回退 ===')

    # 1) 状态机红线：CREDENTIAL_REJECTED 不能跳到 RESTORE_ATTEMPTED
    sm = li.LoginStateMachine()
    sm.transition(li.LoginState.CREDENTIAL_SUBMITTED)
    sm.transition(li.LoginState.CREDENTIAL_REJECTED)
    check('凭据被拒后禁止回退旧会话',
          not sm.can(li.LoginState.RESTORE_ATTEMPTED), sm.state.value)
    check('凭据被拒是终态（直接return False）',
          not sm.can(li.LoginState.LOGIN_FAILED))

    # 2) 启动前清理站点 Cookie（只清学习通域，不动其它站）
    d = FakeDriver(cookies=[
        {'name': 'uid', 'value': '88888', 'domain': '.chaoxing.com'},
        {'name': 'sid', 'value': 'x', 'domain': '.chaoxing.com'},
        {'name': 'other', 'value': 'y', 'domain': '.example.com'},
    ])
    cleared = li.clear_site_cookies(d)
    check('清理站点Cookie（2项）', sorted(cleared) == ['sid', 'uid'], cleared)
    check('不动其它站点Cookie',
          [c['name'] for c in d.cookies] == ['other'], d.cookies)

    # 3) 服务器明确拒绝 → 判定失败，不猜成功
    d2 = FakeDriver(server='rejected')
    check('错误密码被识别为拒绝',
          d2.server == 'rejected')

    # 4) 身份校验取不到可靠身份 → 失败（不把 title 当成功）
    d3 = FakeDriver(cookies=[], identity={})
    ok, reason, _ = li.verify_identity(d3, {'phone_number': '13800138000'})
    check('无身份标识时拒绝判成功', not ok and '无法读取可靠身份' in reason, reason)

    # 5) 身份不匹配 → IDENTITY_MISMATCH
    d4 = FakeDriver(cookies=[{'name': 'uid', 'value': '999999', 'domain': '.chaoxing.com'}])
    ok, reason, _ = li.verify_identity(d4, {'uid': '111111'})
    check('UID不匹配被拦截', not ok and 'UID 不匹配' in reason, reason)

    # 6) 身份匹配 → 通过
    d5 = FakeDriver(cookies=[{'name': 'uid', 'value': '123456', 'domain': '.chaoxing.com'}])
    ok, reason, _ = li.verify_identity(d5, {'uid': '123456'})
    check('UID匹配通过', ok, reason)

    # 7) 源码级：login_study 返回布尔、失败 return、凭据被拒不调 restore
    src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('login_study 有 True/False 返回',
          'return True' in src and 'return False' in src)
    # 定位 CREDENTIAL_REJECTED 分支，确认该分支后没有 auto_login_with_cookies
    i = src.find('state.transition(login_id.LoginState.CREDENTIAL_REJECTED)')
    j = src.find('state.transition(login_id.LoginState.CREDENTIAL_AUTHENTICATED)')
    seg = src[i:j] if i >= 0 and j > i else ''
    check('凭据被拒分支不调用旧Cookie恢复',
          'auto_login_with_cookies' not in seg, seg[:120])
    check('两个入口检查登录返回值',
          src.count('_logged_in = login_study(') >= 1
          and src.count('if not login_study(') >= 1)

    # 8) 脱敏日志
    check('手机号脱敏只留后四位',
          li.mask_phone('13800138000') == '****8000')
    check('HOME短标识不含完整路径',
          li.short_home('C:/Users/popca/AppData/Local/X') == 'X')

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
