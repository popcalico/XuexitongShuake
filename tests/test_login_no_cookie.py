# -*- coding: utf-8 -*-
"""登录只走账号密码、且只读本进程配置的回归（2026-11-19）。

两次真机事故：

1. **串号**：`login_study` 用 `ConfigManager().load_account()` 取目标账号，而它读的是
   主进程的 `current_account`。多账号并发时子进程拿到别的账号的手机号，日志表现为
   三个号码互不相同：
       账号 *****1520（本次任务）｜目标 *****8673（ConfigManager）｜实际 *****5267
   身份校验因此永远对不上 → 出现「明明登录成功却报身份不匹配」。
   现在只读 `HOME/tool/account_info.json`（runner 为**这个**账号写的那份）。

2. **Cookie 登录反复出问题**：用户明确要求整体移除。现在没有密码就直接失败，
   不再保存 cookies.json，也不再尝试用它恢复登录。

用法：C:/Python315/python.exe tests/test_login_no_cookie.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_loginnocookie_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths
import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def login_body(src):
    """截出 login_study 的函数体，避免误判文件里其它地方的同名调用。"""
    start = src.index('def login_study(')
    end = src.index('\ndef ', start + 10)
    return src[start:end]


class PageStub:
    """只提供 _page_looks_logged_in 用到的三个接口。"""

    def __init__(self, url='', title='', text=''):
        self.current_url = url
        self.title = title
        self._text = text

    def execute_script(self, script, *a):
        return self._text


def main():
    print('=== 登录：只用账号密码 + 只读本进程配置 ===')
    src = open('main.py', encoding='utf-8').read()
    body = login_body(src)

    # 1) 只读本进程 HOME 的配置
    check('有 _process_account_config 且读 account_info.json',
          'def _process_account_config(' in src
          and "paths.data('tool', 'account_info.json')" in src)
    check('login_study 使用 _process_account_config',
          '_process_account_config()' in body)
    check('login_study 不再用 ConfigManager().load_account()',
          'ConfigManager().load_account()' not in body)
    face_src = open(os.path.join('task', 'tool', 'face.py'), encoding='utf-8').read()
    face_code = '\n'.join(line.split('#')[0] for line in face_src.splitlines())
    check('face.py 也不再用全局 load_account',
          'ConfigManager().load_account()' not in face_code)

    # 2) Cookie 登录已整体移除
    check('login_study 不再调用 auto_login_with_cookies',
          'auto_login_with_cookies(' not in body)
    check('login_study 不再保存登录态 Cookie',
          'get_cookie(' not in body)
    check('没有密码直接失败（不再回退保存的会话）',
          'if not password:' in body)
    check('日志里标明方式=账号密码', '方式=账号密码' in src)

    # 2b) 单账号：浏览器已登录就直接复用（用户 2026-11-20 要求：
    #     「浏览器已经登录了，是 app 自己不执行」——不能把用户会话清掉重登）
    check('有单账号判断', 'def _is_single_account(' in src)
    check('有复用已登录会话的入口', 'def _reuse_existing_session(' in src)
    check('login_study 单账号会先复用会话', '_reuse_existing_session(driver, target)' in body)
    check('清 Cookie 在进入学习通域之后（否则清了个空）',
          body.index("nav.navigate(\n            driver, \"https://i.chaoxing.com/\"")
          < body.index('clear_site_cookies(driver)')
          if 'clear_site_cookies(driver)' in body else False)
    # 2026-11-20 撤回：清 localStorage 会把浏览器彻底退回未登录状态，
    # 收益（防旧 token）小于代价，所以断言「不要调用它」。
    check('不清理 localStorage（撤回，避免把用户登出）',
          'clear_site_storage(driver)' not in body)
    check('runner 会把账号数量写进子进程配置',
          "flat['account_count']" in
          open(os.path.join('core', 'config_manager.py'), encoding='utf-8').read())

    # 3) 运行时：空密码必须立刻返回 False，且不抛异常
    try:
        ok = core.login_study(None, '13800138000', '')
        raised = None
    except Exception as e:
        ok, raised = None, e
    check('空密码返回 False 且不抛异常', ok is False and raised is None, (ok, raised))

    # 4) 手机号以本次任务为准：配置里写别的号也不能影响校验目标
    tool_dir = paths.data('tool')
    os.makedirs(tool_dir, exist_ok=True)
    with open(os.path.join(tool_dir, 'account_info.json'), 'w', encoding='utf-8') as f:
        json.dump({'phone_number': '13900139000', 'password': 'x',
                   'account_id': 'acc_test'}, f)
    acc = core._process_account_config()
    check('能读到本进程 account_info.json', acc.get('account_id') == 'acc_test', acc)

    # 5) 「浏览器已经登录好、app 却不执行」的判定：看页面事实，不只信 cookie
    check('有 _page_looks_logged_in', '_page_looks_logged_in' in src)
    cases = [
        ('登录页不算已登录',
         PageStub('https://passport2.chaoxing.com/login', '用户登录'), False),
        ('个人空间算已登录',
         PageStub('https://i.chaoxing.com/base?ws=1', '个人空间'), True),
        ('我的课程算已登录',
         PageStub('https://mooc1.chaoxing.com/visit/courses', '课程'), True),
        ('页面有「个人空间」文字也算',
         PageStub('https://i.chaoxing.com/', '首页', '个人空间 退出 我的课程'), True),
        ('页面显示「用户登录」不算',
         PageStub('https://i.chaoxing.com/', '首页', '用户登录'), False),
        ('非学习通域名不算',
         PageStub('https://www.baidu.com/', '百度'), False),
    ]
    bad = [name for name, stub, want in cases
           if core._page_looks_logged_in(stub) is not want]
    check('已登录页面判定正确（6 个场景）', not bad, bad)

    # 6) 单账号的身份校验必须「只警告不阻断」
    check('单账号校验失败不 return False',
          '但本次只跑一个账号' in body)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
