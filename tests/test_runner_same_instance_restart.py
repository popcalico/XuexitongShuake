# -*- coding: utf-8 -*-
"""P1：同一 Runner 实例快速 stop/start 竞态回归（2026-11-16）。

验证 generation 机制：旧线程退出时只清理自己那轮，绝不误删新一轮同账号记录。

用法：C:/Python315/python.exe tests/test_runner_same_instance_restart.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_samerunner_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runner as R

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeConfig:
    def write_account_runtime(self, aid): return tempfile.mkdtemp(prefix='acc_')
    def get_account_by_id(self, aid): return {'alias': aid}


class Out:
    def readline(self):
        time.sleep(0.02)
        return b''
    def close(self): pass


class SlowPopen:
    """假进程：stdout EOF 慢一点，保证 stop 后旧线程还在收尾。"""
    def __init__(self, *a, **k):
        self.pid = id(self) % 100000
        self.stdout = Out()
        self.terminated = False
        self._ev = threading.Event()
    def wait(self, timeout=None):
        self._ev.wait(min(timeout or 0, 0.05) if timeout else 0.05)
        return -1
    def terminate(self):
        self.terminated = True
        self._ev.set()
    def kill(self):
        self.terminate()


def make_runner():
    r = R.Runner(FakeConfig(), lambda *a: None, lambda *a: None, max_concurrency=1)
    r._popen_factory = SlowPopen
    r._terminator = lambda p: p.terminate()
    return r


def main():
    print('=== P1 同实例重启竞态 ===')

    # 1) generation 随 start 递增
    r = make_runner()
    r.start(['a'])
    g1 = r._generation
    r.stop_all()
    r.start(['a'])
    g2 = r._generation
    check('generation 递增', g2 == g1 + 1, (g1, g2))

    # 2) 旧线程清理不误删新轮记录
    # 手动模拟：新一轮登记一个进程，然后旧轮调用 _cleanup_own
    r2 = make_runner()
    r2.start(['x'])           # gen=1
    g_old = r2._generation
    r2.stop_all()
    r2.start(['x'])           # gen=2
    g_new = r2._generation
    # 在新轮登记一个假进程
    new_proc = SlowPopen()
    with r2._lock:
        r2._procs['x'] = R.AccountProcess('x', 'x', new_proc)
    # 旧线程退出清理（gen=g_old）
    r2._cleanup_own('x', g_old)
    check('旧线程不误删新轮进程',
          r2._procs.get('x') is not None and r2._procs['x'].popen is new_proc)
    # 新轮自己清理才生效
    r2._cleanup_own('x', g_new)
    check('新轮自己清理生效', 'x' not in r2._procs)

    # 3) acquire/release 配对：反复 stop/start 后信号量不泄漏
    r3 = make_runner()
    for i in range(8):
        r3.start(['p%d' % i])
        time.sleep(0.01)
        r3.stop_all()
        for t in list(r3._threads):
            t.join(1)
    # 8 轮各 1 个 acquire+release，信号量应恢复满额
    r3._sem.acquire()   # 能拿到说明没有泄漏（若泄漏会仍可拿，故检查计数）
    r3._sem.release()
    check('信号量未泄漏（并发上限保持）',
          r3._sem._value == r3.max_concurrency, r3._sem._value)
    check('无残留进程记录', not r3._procs and not r3._starting)

    # 4) 源码级：清理点都走 _cleanup_own
    src = open(os.path.join(ROOT, 'core/runner.py'), encoding='utf-8').read()
    check('有generation字段', 'self._generation' in src)
    check('清理只认自己轮次', 'if gen != self._generation' in src)
    check('线程携带generation',
          'args=(aid, gen)' in src)
    check('release配对', 'if acquired:\n                self._sem.release()' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
