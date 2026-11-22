# -*- coding: utf-8 -*-
"""任务02 多账号停止竞态回归（2026-11-16）纯桩测试。

用假 Popen / 线程屏障 / 事件模拟场景 A~E，绝不启动真实浏览器或 Python 子进程。
并发停止测试重复 100 次，验证无遗留假进程、无死锁、无未处理异常。

用法：C:/Python315/python.exe tests/test_runner_stop_race.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_race_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runner as R

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeConfig:
    def write_account_runtime(self, aid):
        return tempfile.mkdtemp(prefix='xxt_acc_')

    def get_account_by_id(self, aid):
        return {'alias': '账号' + str(aid)}


class FakeStdout:
    def readline(self):
        return b''
    def close(self):
        pass


class FakePopen:
    """假子进程：记录 pid / 是否被终止；stdout 立即 EOF 让 _pump 快速结束。"""
    _seq = 0
    _lock = threading.Lock()

    def __init__(self, *a, **k):
        with FakePopen._lock:
            FakePopen._seq += 1
            self.pid = 10000 + FakePopen._seq
        self.terminated = False
        self.stdout = FakeStdout()
        self._exit = threading.Event()

    def wait(self, timeout=None):
        # 若被终止则立即返回，否则最多等很短时间（避免测试卡住）
        self._exit.wait(min(timeout or 0, 0.05) if timeout else 0.05)
        return -1

    def terminate(self):
        self.terminated = True
        self._exit.set()

    def kill(self):
        self.terminate()


def make_runner(factory=None, terminator=None, max_conc=3):
    r = R.Runner(FakeConfig(), lambda *a: None, lambda *a: None, max_concurrency=max_conc)
    if factory is not None:
        r._popen_factory = factory
    if terminator is not None:
        r._terminator = terminator
    # 避免 _pump 写真实日志文件到临时目录之外：output_log_path 用 XXT_HOME，OK
    return r


def wait_threads(r, timeout=5):
    for t in list(r._threads):
        t.join(timeout)


def main():
    print('=== 任务02 停止竞态 ===')

    # 场景 A：线程检查 stop=False 准备启动，用户停止后才 Popen
    # 用 factory 在被调用时先 set stop，模拟「stop 发生在 Popen 之前」
    created = []
    killed = []

    def factory_a(*a, **k):
        # 到这一步说明线程已越过前两次 stop 检查——但真实原子区在锁内再次检查，
        # 这里无法区分是否被锁内检查拦住；用一个 stop-before 变体单独测（见下）。
        p = FakePopen(*a, **k)
        created.append(p)
        return p

    # 更精确的场景 A：在持锁前 set stop，线程应在锁内二次检查处退出，不 Popen
    r = make_runner(factory_a, lambda p: killed.append(p))
    stop_before = threading.Event()

    def factory_abort(*a, **k):
        # 一旦走到 factory，说明二次检查没拦住（不应发生在此变体）
        r._stop.set()
        p = FakePopen(*a, **k)
        created.append(p)
        return p

    # 场景 A 真变体：start 之后立刻 stop，绝大多数线程应在锁内二次检查退出
    created.clear(); killed.clear()
    def term_a(p):
        killed.append(p)
        p.terminate()

    r = make_runner(factory_abort, term_a, max_conc=1)
    r.start(['a1', 'a2', 'a3', 'a4', 'a5'])
    r.stop_all()
    wait_threads(r)
    # 已 Popen 的进程必须全部被终止（不论是在锁内 kill_now 还是 stop_all）
    check('场景A 已创建进程全部被终止',
          all(p.terminated for p in created),
          'created=%d killed=%d' % (len(created), len(killed)))

    # 场景 B：Popen 成功但登记前 stop —— 用 terminator 记录，验证无遗留
    created.clear(); killed.clear()
    def term_b(p):
        killed.append(p)
        p.terminate()   # 桩也要真正标记终止，否则无法判定残留

    r = make_runner(lambda *a, **k: (created.append(FakePopen(*a, **k)) or created[-1]),
                    term_b, max_conc=1)
    r.start(['b1', 'b2', 'b3'])
    # 让线程进入 Popen 阶段后再 stop
    time.sleep(0.02)
    r.stop_all()
    wait_threads(r)
    check('场景B 无遗留未终止进程',
          all(p.terminated for p in created),
          'created=%d terminated=%d' % (
              len(created), sum(1 for p in created if p.terminated)))

    # 场景 C：3 个账号排队，停止后排队账号不得继续启动
    created.clear()
    r = make_runner(lambda *a, **k: (created.append(FakePopen(*a, **k)) or created[-1]),
                    lambda p: None, max_conc=1)
    r.start(['c1', 'c2', 'c3'])
    r.stop_all()
    wait_threads(r)
    check('场景C 停止后排队账号不再启动', len(created) <= 1,
          '实际启动数=%d' % len(created))

    # 场景 D：连续两次停止，无重复终止异常；每个进程只终止一次
    killed.clear()
    r = make_runner(lambda *a, **k: FakePopen(*a, **k),
                    lambda p: killed.append(p), max_conc=2)
    r.start(['d1', 'd2'])
    time.sleep(0.02)
    try:
        r.stop_all()
        r.stop_all()
        no_exc = True
    except Exception as e:
        no_exc = False
        print('  stop_all 抛异常:', e)
    wait_threads(r)
    check('场景D 双次停止无异常', no_exc)
    check('场景D 每个进程只终止一次', len(killed) == len(set(id(p) for p in killed)),
          'kill调用=%d 去重=%d' % (len(killed), len(set(id(p) for p in killed))))

    # 场景 E：停止后重新开始，新 Runner 正常工作
    r = make_runner(lambda *a, **k: FakePopen(*a, **k), max_conc=1)
    r.start(['e0']); r.stop_all(); wait_threads(r)
    created.clear()
    r2 = make_runner(lambda *a, **k: (created.append(FakePopen(*a, **k)) or created[-1]),
                     max_conc=1)
    r2.start(['e1'])
    wait_threads(r2)
    check('场景E 停止后新Runner可启动', len(created) >= 1 and not r2._stop.is_set(),
          'created=%d stop=%s' % (len(created), r2._stop.is_set()))

    # 100 次并发停止压力测试
    print('--- 100 次并发停止压力测试 ---')
    leaks = 0
    deadlocks = 0
    for i in range(100):
        created.clear()
        r = make_runner(lambda *a, **k: (created.append(FakePopen(*a, **k)) or created[-1]),
                        lambda p: p.terminate(), max_conc=3)
        r.start(['x1', 'x2', 'x3', 'x4'])
        # 起一个并发停止线程，主线程也停止（双次并发）
        t = threading.Thread(target=r.stop_all)
        t.start()
        r.stop_all()
        t.join(2)
        wait_threads(r, timeout=2)
        if any(not p.terminated for p in created):
            leaks += 1
        if any(t.is_alive() for t in r._threads):
            deadlocks += 1
    check('压测100次无遗留进程', leaks == 0, 'leaks=%d' % leaks)
    check('压测100次无死锁', deadlocks == 0, 'deadlocks=%d' % deadlocks)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
