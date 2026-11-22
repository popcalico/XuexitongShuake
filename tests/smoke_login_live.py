# -*- coding: utf-8 -*-
"""真实浏览器冒烟：登录页整条链路（不提交、不碰账号）。

背景（2026-11-20）：连续两次发版把登录打崩——一次调用了未定义的
`_login_form_ready`，一次清了 localStorage 把用户登出。单测全绿却没有任何
用例真正打开过登录页。这个脚本就是补那一环：

  启动浏览器（与生产同一套参数/扩展）→ 打开学习通首页
  → 等登录表单 → 检查 #phone/#pwd/#loginBtn
  → 清空输入框 → 输入假手机号/假密码 → **回读校验**
  → 关闭浏览器（绝不点「登录」）

用法（会弹出真实 Edge 窗口，约 20~40 秒）：
    C:/Python315/python.exe tests/smoke_login_live.py

退出码 0 = 全部通过。
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_smoke_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core
from core import navigation as nav

FAILS = []
FAKE_PHONE = '13800138000'
FAKE_PWD = 'smoke-test-pwd'


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 登录页真机冒烟（不会提交）===')
    driver = None
    try:
        driver = core.start_browser('edge', '', '1')
        check('浏览器启动成功', driver is not None)

        ok_nav, status, detail = nav.navigate(
            driver, 'https://i.chaoxing.com/', name='冒烟-打开首页')
        check('打开学习通首页', ok_nav, '%s / %s' % (status, detail))
        print('  当前 URL=%s' % (driver.current_url or '')[:120])
        print('  当前标题=%s' % (driver.title or '')[:80])

        ready = core._login_form_ready(driver, timeout=12)
        check('_login_form_ready 返回 True（函数存在且能工作）', ready)
        if not ready:
            print('  页面文本片段=%r' % (
                driver.execute_script(
                    "return (document.body&&document.body.innerText||'').slice(0,300);") or ''))
            return 1

        for sel in ('phone', 'pwd', 'loginBtn'):
            n = len(driver.find_elements('id', sel))
            check('元素 #%s 存在' % sel, n >= 1, n)

        phone = driver.find_element('id', 'phone')
        pwd = driver.find_element('id', 'pwd')
        before = core._input_value(phone)
        print('  输入框清空前的值=%r（浏览器自动填充就会非空）' % before[:30])

        core._clear_input(driver, phone)
        core._clear_input(driver, pwd)
        check('清空后手机号框为空', core._input_value(phone) == '',
              repr(core._input_value(phone)[:30]))

        phone.send_keys(FAKE_PHONE)
        time.sleep(0.3)
        pwd.send_keys(FAKE_PWD)
        typed = core._input_value(phone)
        check('输入后回读与目标一致', typed == FAKE_PHONE, repr(typed[:30]))
        check('密码框回读非空', bool(core._input_value(pwd).strip()))

        # 再清一次，确认不会残留（下一轮真实运行要用干净输入框）
        core._clear_input(driver, phone)
        check('再次清空有效', core._input_value(phone) == '',
              repr(core._input_value(phone)[:30]))

        check('已登录判定不误报（当前是登录页）',
              core._page_looks_logged_in(driver) is False)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check('冒烟过程无异常', False, '%s: %s' % (type(e).__name__, e))
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS（未提交登录，浏览器已关闭）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
