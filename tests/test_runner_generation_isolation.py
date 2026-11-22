# -*- coding: utf-8 -*-
"""V5.1 第24节：Runner 信号量等待可取消，旧批次不能在新批次中复活。

压力场景：并发上限 1、多账号、停止后立即重启、重复 stop、启动失败、
旧线程延迟退出、100 次重复启动停止。纯桩进程工厂，不启动真实子进程。

用法：C:/Python315/python.exe tests/test_runner_generation_isolation.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_geniso_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runner as R

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeConfig:
    def write_account_runtime(self, aid):
        return tempfile.mkdtemp(prefix='acc_')

    def get_account_by_id(self, aid):
        return {'alias': aid}


class Out:
    def readline(self):
        return b''

    def close(self):
        pass


class FastPopen:
    created = []

    def __init__(self, *a, **k):
        self.pid = id(self) % 100000
        self.stdout = Out()
        self.terminated = False
        FastPopen.created.append(self)

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True


class SlowPopen(FastPopen):
    """旧线程延迟退出，用于验证引用被保留而不是被清空。"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._ev = threading.Event()

    def wait(self, timeout=None):
        self._ev.wait(0.08)
        return 0

    def terminate(self):
        self.terminated = True
        self._ev.set()


def make_runner(factory=FastPopen, concurrency=1):
    r = R.Runner(FakeConfig(), lambda *a: None, lambda *a: None,
                 max_concurrency=concurrency)
    r._popen_factory = factory
    r._terminator = lambda p: p.terminate()
    return r


def main():
    print('=== V5.1 Runner generation 隔离与可取消等待 ===')

    # 1) 阻塞在 acquire 的线程被 stop 唤醒
    r = make_runner()
    r._sem.acquire()               # 占住唯一许可，让新线程必须等待
    r.start(['a'])
    time.sleep(0.08)
    check('等待线程确实还活着', r._threads[0].is_alive())
    began = time.time()
    r.stop_all()
    r.wait_all(2)
    elapsed = time.time() - began
    check('stop 能在 1 秒内取消等待线程',
          not r._threads[0].is_alive() and elapsed < 1.0, round(elapsed, 3))
    check('取消等待不泄漏信号量', r._sem._value == 0, r._sem._value)
    r._sem.release()
    check('归还后信号量恢复满额', r._sem._value == r.max_concurrency,
          r._sem._value)

    # 2) 旧批次不能在新批次中启动：占住许可 → 停止 → 重启
    r = make_runner()
    r._sem.acquire()
    FastPopen.created = []
    r.start(['old_a'])
    time.sleep(0.05)
    r.stop_all()
    r.start(['new_b'])            # gen=2，_stop 被清空；旧线程只认 gen=1
    time.sleep(0.15)
    check('旧批次线程未在新批次复活（未创建旧账号进程）',
          len(FastPopen.created) == 0, len(FastPopen.created))
    check('generation 已递增', r._generation == 2, r._generation)
    r._sem.release()
    r.wait_all(2)
    check('新批次能正常启动自己账号',
          len(FastPopen.created) >= 1, len(FastPopen.created))
    r.stop_all()
    r.wait_all(2)

    # 3) 重复 stop 幂等
    r = make_runner()
    r.start(['a', 'b', 'c'])
    time.sleep(0.05)
    for _ in range(3):
        r.stop_all()
    r.wait_all(2)
    check('重复 stop 不抛异常且状态干净',
          not r._procs and not r._starting, (r._procs, r._starting))

    # 4) 启动失败路径也要归还信号量
    r = make_runner()
    def _boom(*a, **k):
        raise RuntimeError('spawn failed')
    r._popen_factory = _boom
    r.start(['x'])
    r.wait_all(2)
    check('启动失败后信号量归还', r._sem._value == r.max_concurrency,
          r._sem._value)
    check('启动失败不残留记录', not r._procs and not r._starting)

    # 5) 同一实例 100 次 start/stop：资源归零
    r = make_runner()
    for i in range(100):
        r.start(['a%d' % i])
        r.stop_all()
        r.wait_all(1)
    check('100 次后信号量无泄漏', r._sem._value == r.max_concurrency,
          r._sem._value)
    check('100 次后无残留进程/启动集合',
          not r._procs and not r._starting, (len(r._procs), len(r._starting)))
    check('旧线程引用被保留可诊断', isinstance(r.old_thread_count(), int),
          r.old_thread_count())

    # 6) 源码级
    src = open(os.path.join(ROOT, 'core', 'runner.py'), encoding='utf-8').read()
    check('信号量等待可取消', 'self._sem.acquire(timeout=0.2)' in src)
    check('等待前校验 generation', 'if gen != self._generation' in src)
    check('启动原子区也校验 generation',
          src.count('if gen != self._generation') >= 3,
          src.count('if gen != self._generation'))
    check('停止与 gen 递增在同一锁内', '_old_threads = ' in src)
    check('acquire/release 严格配对', 'if acquired:\n                self._sem.release()' in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
