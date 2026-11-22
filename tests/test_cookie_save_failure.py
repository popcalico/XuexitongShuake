# -*- coding: utf-8 -*-
"""V5.1 P0 第18节：Cookie 保存失败不得返回成功（纯桩 / 临时 HOME / 不联网）。

覆盖：正常保存、读取失败、空 Cookie、序列化失败、原子替换失败、
目录不可写、已有有效文件保护、face_url 写入失败、调用者检查返回值。

用法：C:/Python315/python.exe tests/test_cookie_save_failure.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_cookiesave_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths
import task.tool.face as face

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class D:
    """fake driver：可注入读取失败、空 Cookie、URL 读取失败。"""
    def __init__(self, cookies=None, fail_get=False, fail_url=False):
        self.cookies = list(cookies if cookies is not None else
                            [{'name': 'uid', 'value': '123456',
                              'domain': '.chaoxing.com'}])
        self.fail_get = fail_get
        self.fail_url = fail_url
        self.title = '个人中心'
        self._url = 'https://i.chaoxing.com/base'

    def get_cookies(self):
        if self.fail_get:
            raise RuntimeError('session gone')
        return list(self.cookies)

    @property
    def current_url(self):
        if self.fail_url:
            raise RuntimeError('no url')
        return self._url


COOKIE_FILE = paths.data('tool', 'cookies.json')


def raw_bytes():
    with open(COOKIE_FILE, 'rb') as f:
        return f.read()


def main():
    print('=== V5.1 P0 Cookie 保存失败不得返回成功 ===')

    # 1) 正常保存
    ok = face.get_cookie(D())
    check('正常保存返回 True', ok is True, ok)
    check('目标文件确实生成', os.path.exists(COOKIE_FILE))
    with open(COOKIE_FILE, encoding='utf-8') as f:
        saved = json.load(f)
    check('写入内容含 1 项且格式版本正确',
          len(saved.get('value', [])) == 1 and saved.get('_fmt') == 1, saved)
    baseline = raw_bytes()

    # 2) 读取 Cookie 失败
    check('get_cookies 异常返回 False',
          face.get_cookie(D(fail_get=True)) is False)
    check('读取失败不覆盖已有文件', raw_bytes() == baseline)

    # 3) 返回空 Cookie（不能把空列表写成成功）
    check('空 Cookie 返回 False', face.get_cookie(D(cookies=[])) is False)
    check('空 Cookie 不覆盖已有文件为空内容', raw_bytes() == baseline)

    # 4) 序列化失败（不可 JSON 序列化对象）
    check('序列化失败返回 False',
          face.get_cookie(D(cookies=[{'name': 'x', 'value': {1, 2}}])) is False)
    check('序列化失败不覆盖已有文件', raw_bytes() == baseline)

    # 5) 原子替换失败（模拟磁盘满 / 目录不可写）
    original_replace = face.os.replace
    def _boom(*a, **k):
        raise OSError('disk full')
    face.os.replace = _boom
    try:
        r = face.get_cookie(D())
    finally:
        face.os.replace = original_replace
    check('写入/替换失败返回 False', r is False, r)
    check('替换失败不破坏已有有效文件', raw_bytes() == baseline)

    # 6) face_cookies 的 URL 保存失败也要整体判失败
    original_save = face._save_face_url
    face._save_face_url = lambda *a, **k: False
    try:
        r = face.get_cookie(D(), file_name='face_cookies', course_name='课A')
    finally:
        face._save_face_url = original_save
    check('face_url 保存失败 → get_cookie 返回 False', r is False, r)
    check('URL 映射未被写入', not os.path.exists(paths.data('tool', 'face_url.json')))

    # 7) 读取当前 URL 失败
    check('current_url 读取失败返回 False',
          face.get_cookie(D(fail_url=True), file_name='face_cookies',
                          course_name='课B') is False)

    # 8) face_cookies 正常路径：原子写 URL 映射
    r = face.get_cookie(D(), file_name='face_cookies', course_name='课C')
    check('face_cookies 正常返回 True', r is True, r)
    with open(paths.data('tool', 'face_url.json'), encoding='utf-8') as f:
        url_map = json.load(f)
    check('face_url 原子写入成功', url_map.get('课C') == 'https://i.chaoxing.com/base',
          url_map)

    # 9) 源码级：get_cookie 只有一条成功路径，异常分支绝不 return True
    src = open(os.path.join(ROOT, 'task', 'tool', 'face.py'), encoding='utf-8').read()
    start = src.index('def get_cookie(')
    nxt = src.find('\ndef ', start + 5)
    body = src[start:nxt if nxt > 0 else len(src)]
    check('get_cookie 只有一处 return True（末尾成功）',
          body.count('return True') == 1, body.count('return True'))
    check('get_cookie 有多处 return False（失败分支）',
          body.count('return False') >= 5, body.count('return False'))
    check('写入用 os.replace 原子替换', 'os.replace(_tmp, _json_path)' in body)
    check('日志不输出 Cookie 值', "print(color.green(f'已保存 %d 项 Cookie" in src
          or '日志不含 Cookie 值' in src)

    # 10) 调用者必须检查返回值
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    # 2026-11-19：登录流程已彻底移除 Cookie/保存登录态（用户要求），
    # login_study 不再调用 get_cookie / auto_login_with_cookies。
    # 这里改成反向断言，防止哪天又把这套逻辑加回登录路径。
    check('login_study 不再保存登录态 Cookie',
          'if get_cookie(driver):' not in main_src
          or main_src.count('if get_cookie(driver):') == 0,
          '仍是旧写法')
    check('login_study 不再用 Cookie 恢复登录',
          'auto_login_with_cookies(driver, account_config=' not in main_src)
    check('check_face 检查 get_cookie 返回值',
          'if not get_cookie(driver,file_name=' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
