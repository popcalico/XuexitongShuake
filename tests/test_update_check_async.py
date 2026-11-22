# -*- coding: utf-8 -*-
"""更新检查不得冻结 GUI 主线程（2026-11-19）。

旧实现：`root.after(800, check_update)` → 主线程直接 `requests.get(timeout=10)`。
网络慢时启动后主线程被冻结最多 10 秒，用户感知为「启动卡住、点不动」。

现在拆成 `_fetch()`（纯网络，绝不碰 tkinter）+ `_apply()`（主线程弹窗）
+ `check_async(on_done)`（后台线程跑网络，`root.after(0, ...)` 回主线程）。

本测试用桩验证：网络请求发生在非主线程、主线程只收到一次 after 回调、
`_fetch` 不触碰任何控件、异常路径不崩溃。
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from gui import update_checker as uc

FAILS = []
MAIN_THREAD = threading.current_thread().name


def check(name, cond, detail=''):
    if cond:
        print('PASS %s' % name)
    else:
        print('FAIL %s  %s' % (name, detail))
        FAILS.append(name)


class FakeRoot:
    """只记录 after 调用；绝不创建真实 tk 控件。"""

    def __init__(self):
        self.after_calls = []
        self.touched_threads = []

    def after(self, ms, fn):
        self.touched_threads.append(threading.current_thread().name)
        self.after_calls.append((ms, fn))

    def tk_touch(self):
        """任何一个 tkinter 调用都会走这里 —— 用来证明 _fetch 没碰 UI。"""
        raise AssertionError('_fetch 不允许触碰 tkinter')


class FakeResp:
    status_code = 200

    def __init__(self, name='start-26.12.01.exe'):
        self._name = name

    def json(self):
        return {'body': '公告：测试', 'assets': [{'name': self._name}]}


class FakeRequests:
    def __init__(self, resp=None, boom=None):
        self.threads = []
        self._resp = resp or FakeResp()
        self._boom = boom

    def get(self, url, headers=None, timeout=None):
        self.threads.append(threading.current_thread().name)
        if self._boom:
            raise self._boom
        return self._resp


def make_checker(root, version='26.11.19.0001'):
    c = uc.UpdateChecker(root, lambda: version, lambda: {'primary': '#000'})
    # 弹窗绝不能在测试里真建：只记录被调用
    c.dialog_calls = []
    c._show_selection = lambda: (c.dialog_calls.append(1), '公告')[1]
    return c


def main():
    print('=== 更新检查异步化 ===')

    # 0) 发布地址必须指向真实存在的上游仓库
    #    历史值 `Mortal004/CourseStudyHelper` 是 404（仓库不存在），导致「检查更新」
    #    永远提示失败；这里用断言把正确地址钉住。
    check('发布地址指向 Xuexitong_shuake',
          uc.UpdateChecker.RELEASE_URL ==
          'https://api.github.com/repos/Mortal004/Xuexitong_shuake/releases/latest',
          uc.UpdateChecker.RELEASE_URL)
    check('不再指向不存在的 CourseStudyHelper',
          'CourseStudyHelper' not in uc.UpdateChecker.RELEASE_URL)
    # 上游真实资产名（2026-11-18 发布）能否被版本解析正确比较
    check('上游 26.11.18 视为旧版', uc.UpdateChecker._is_latest(
        '26.11.19.1421', 'xuexitong_shuake26.11.18.zip') is True)
    check('上游 26.11.20 视为新版', uc.UpdateChecker._is_latest(
        '26.11.19.1421', 'xuexitong_shuake26.11.20.zip') is False)

    # 1) _fetch 只做网络，不碰 tkinter
    root = FakeRoot()
    c = make_checker(root)
    fake = FakeRequests()
    orig_requests = uc.requests
    uc.requests = fake
    try:
        payload = c._fetch()
    finally:
        uc.requests = orig_requests
    check('_fetch 返回结构化结果', payload.get('ok') is True and payload.get('latest') is False,
          payload)
    check('_fetch 未触发任何 after / UI 调用', root.after_calls == [] and root.touched_threads == [])
    check('_fetch 请求落在当前调用线程', fake.threads == [threading.current_thread().name],
          fake.threads)

    # 2) check_async：网络在后台线程，UI 回调通过 after 回主线程
    root2 = FakeRoot()
    c2 = make_checker(root2)
    fake2 = FakeRequests()
    got = []
    uc.requests = fake2
    try:
        t0 = time.time()
        th = c2.check_async(got.append)
        elapsed = time.time() - t0
        check('check_async 立即返回（不阻塞调用方）', elapsed < 0.5, '%.3fs' % elapsed)
        th.join(5)
    finally:
        uc.requests = orig_requests
    check('网络请求发生在后台线程', fake2.threads and fake2.threads[0] != MAIN_THREAD,
          fake2.threads)
    check('后台线程没有直接调用 on_done', got == [], got)
    check('结果通过 root.after(0, ...) 交回', len(root2.after_calls) == 1, root2.after_calls)
    if root2.after_calls:
        ms, fn = root2.after_calls[0]
        check('after 延迟为 0（尽快回主线程）', ms == 0, ms)
        fn()   # 模拟主线程执行
    check('主线程回调拿到文案', bool(got) and '新版本' in got[0], got)
    check('有新版本时弹窗被调用一次', c2.dialog_calls == [1], c2.dialog_calls)

    # 3) 已是最新：不弹窗
    root3 = FakeRoot()
    c3 = make_checker(root3, version='26.12.01.0001')
    uc.requests = FakeRequests()
    text = c3.check()
    uc.requests = orig_requests
    check('已是最新时返回「无需更新」', '无需更新' in text, text)
    check('已是最新时不弹窗', c3.dialog_calls == [], c3.dialog_calls)

    # 4) 网络异常：不崩溃、给出失败文案
    root4 = FakeRoot()
    c4 = make_checker(root4)
    uc.requests = FakeRequests(boom=RuntimeError('network down'))
    text4 = c4.check()
    uc.requests = orig_requests
    check('网络异常时返回失败文案', '检查更新失败' in text4, text4)

    # 5) 窗口已销毁（after 抛异常）：后台线程不得把异常抛出来
    class DeadRoot(FakeRoot):
        def after(self, ms, fn):
            raise RuntimeError('application has been destroyed')

    root5 = DeadRoot()
    c5 = make_checker(root5)
    uc.requests = FakeRequests()
    try:
        th = c5.check_async(lambda t: None)
        th.join(5)
        ok = True
        err = ''
    except Exception as e:
        ok = False
        err = repr(e)
    finally:
        uc.requests = orig_requests
    check('窗口已销毁时不抛异常', ok, err)

    print()
    if FAILS:
        print('FAILED %d: %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
