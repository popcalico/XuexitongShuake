# -*- coding: utf-8 -*-
"""P1：条件等待的超时/取消/异常/成功 回归（2026-11-16）纯桩测试。"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_smartwait_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import performance as perf

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== P1 智能等待 ===')
    perf.reset()

    # 1) 成功
    ok, detail = perf.wait_until('成功等待', lambda: True, timeout=2,
                                 poll_interval=0.05, account_id='acc_A')
    check('条件满足返回True', ok is True, detail)

    # 2) 超时（有界，不无限）
    t0 = time.perf_counter()
    ok, detail = perf.wait_until('超时等待', lambda: False, timeout=0.3,
                                 poll_interval=0.05, account_id='acc_A')
    elapsed = time.perf_counter() - t0
    check('超时返回False', ok is False and detail == 'timeout', detail)
    check('超时有界（<1s）', elapsed < 1.0, '%.2fs' % elapsed)

    # 3) 取消
    ev = threading.Event()
    ev.set()
    ok, detail = perf.wait_until('取消等待', lambda: False, timeout=5,
                                 poll_interval=0.05, stop_event=ev,
                                 account_id='acc_A')
    check('stop_event取消', ok is False and detail == 'cancelled', detail)

    # 4) 条件异常不外抛、继续等待直到超时
    def boom():
        raise RuntimeError('fake')
    t0 = time.perf_counter()
    ok, detail = perf.wait_until('异常等待', boom, timeout=0.3,
                                 poll_interval=0.05, account_id='acc_A')
    check('条件异常不外抛', ok is False, detail)
    check('异常后仍按时超时', time.perf_counter() - t0 < 1.0)

    # 5) (ok, detail) 元组返回
    ok, detail = perf.wait_until('元组等待', lambda: (True, '页面已就绪'),
                                 timeout=2, poll_interval=0.05)
    check('元组返回被解析', ok and detail == '页面已就绪', detail)

    # 6) 延迟满足：轮询次数受 poll_interval 控制
    calls = [0]
    def later():
        calls[0] += 1
        return calls[0] >= 3
    ok, _ = perf.wait_until('延迟满足', later, timeout=2,
                            poll_interval=0.02)
    check('延迟满足条件满足', ok and calls[0] == 3, calls[0])

    # 7) 超时记录进 report（可解释）
    rep = perf.report()
    check('超时阶段有统计', '超时等待' in rep and rep['超时等待']['count'] >= 1,
          rep.get('超时等待'))

    # 8) 源码级：登录等待已改为有界（不含无限 while title）
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('登录不再无限sleep等待', 'while driver.title==' not in main_src)
    check('登录等待有deadline', 'deadline = time.time() + 20' in main_src)

    perf.reset()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
