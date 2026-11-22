# -*- coding: utf-8 -*-
"""V5.1 第23节：WebDriver 导航超时、阶段计时、导航≠登录失败。

纯桩 fake driver，不联网、不启动真实浏览器。

用法：C:/Python315/python.exe tests/test_webdriver_navigation_timeout.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_navtimeout_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import navigation as nav
from core import task_control as tc

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class _Timeout(Exception):
    pass


_Timeout.__name__ = 'TimeoutException'


class _Session(Exception):
    pass


_Session.__name__ = 'WebDriverException'


class D:
    def __init__(self, fail=None, alive=True, url='https://i.chaoxing.com/'):
        self.fail = fail
        self.alive = alive
        self.get_calls = []
        self.refreshes = 0
        self.scripts = []
        self.applied = {}
        self.title = '用户登录'
        self.current_url = url

    def set_page_load_timeout(self, v):
        self.applied['page_load_timeout'] = v

    def set_script_timeout(self, v):
        self.applied['script_timeout'] = v

    def get(self, url):
        self.get_calls.append(url)
        if self.fail == 'timeout':
            raise _Timeout('page load timeout')
        if self.fail == 'session':
            raise _Session('InvalidSessionIdException')
        self.title = '个人中心'

    def refresh(self):
        self.refreshes += 1
        if self.fail == 'timeout':
            raise _Timeout('refresh timeout')

    def execute_script(self, js, *a):
        self.scripts.append(js)
        if self.fail == 'script':
            raise _Session('InvalidSessionIdException')
        return 'ok'


class NoMethods:
    """没有 set_*_timeout 的替身：必须静默跳过，不能抛异常。"""


def main():
    print('=== V5.1 导航超时与身份判定分离 ===')

    # 1) 超时设置
    d = D()
    applied = nav.configure_timeouts(d)
    check('page_load 超时已设置',
          applied.get('page_load_timeout') == nav.DEFAULT_PAGE_LOAD_TIMEOUT, applied)
    check('script 超时已设置',
          applied.get('script_timeout') == nav.DEFAULT_SCRIPT_TIMEOUT, applied)
    check('fake driver 缺方法时安全降级',
          nav.configure_timeouts(NoMethods()) ==
          {'page_load_timeout': 'unsupported', 'script_timeout': 'unsupported'})

    # 2) 导航成功
    d = D()
    with redirect_stdout(io.StringIO()):
        ok, status, detail = nav.navigate(d, 'https://i.chaoxing.com/',
                                          name='打开登录入口')
    check('导航成功 → SUCCESS', ok is True and status == tc.TaskStatus.SUCCESS,
          (ok, status))
    check('确实调用了 driver.get', d.get_calls == ['https://i.chaoxing.com/'])

    # 3) 导航超时：有限重试，不无限刷新
    d = D(fail='timeout')
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok, status, detail = nav.navigate(d, 'https://i.chaoxing.com/',
                                          name='超时页', retries=1)
    check('导航超时 → TIMEOUT', ok is False and status == tc.TaskStatus.TIMEOUT,
          (ok, status))
    check('重试次数有上限（首次+1）', len(d.get_calls) == 2, len(d.get_calls))
    check('超时日志含诊断与 session 状态',
          'session=' in buf.getvalue() and '导航失败/超时' in buf.getvalue())

    # 4) 非超时类异常且 session 仍有效 → DRIVER_ERROR，不与登录失败混淆
    d = D(fail='session')
    with redirect_stdout(io.StringIO()):
        ok, status, detail = nav.navigate(d, 'https://i.chaoxing.com/',
                                          name='失效页', retries=1)
    check('driver 异常 → DRIVER_ERROR',
          ok is False and status == tc.TaskStatus.DRIVER_ERROR, (ok, status))

    # 5) refresh 计时 / 异常不静默
    d = D()
    with redirect_stdout(io.StringIO()):
        ok, status, _ = nav.refresh(d)
    check('刷新成功 → SUCCESS', ok is True and d.refreshes == 1, (ok, status))
    d = D(fail='timeout')
    with redirect_stdout(io.StringIO()):
        ok, status, _ = nav.refresh(d)
    check('刷新失败不静默', ok is False, (ok, status))

    # 6) execute_script 带计时，异常带诊断抛出
    d = D()
    with redirect_stdout(io.StringIO()):
        r = nav.eval_script(d, 'return 1;')
    check('脚本执行成功', r == 'ok', r)

    # 7) 脱敏
    d = D(url='https://x/?phone=13800138000&token=abc')
    snap = tc.page_snapshot(d)
    check('快照脱敏手机号', '****8000' in snap and '13800138000' not in snap, snap)

    # 8) 源码级：配置落地 + 导航失败与身份认证分离
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    src = open(os.path.join(ROOT, 'core', 'navigation.py'), encoding='utf-8').read()
    check('start_browser 设置超时',
          'nav.configure_timeouts(driver)' in main_src)
    check('登录入口走带计时的导航',
          'nav.navigate(\n            driver, "https://i.chaoxing.com/"' in main_src)
    check('导航失败单独 return False（不进入身份判定）',
          '登录入口导航失败（%s），已停止' in main_src)
    check('保留默认 page_load_strategy（不改成 none）',
          'page_load_strategy' in src and '不改成 `none`' in src)
    check('明确区分导航异常与登录结果', '导航超时 ≠ 登录失败' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
