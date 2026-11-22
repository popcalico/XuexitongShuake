"""多账号并发调度。

每个账号一个独立子进程，通过环境变量 XXT_HOME 指向自己的工作目录，
从而隔离 Cookie、学习记录、课程进度；主进程只负责拉起、读输出、限流、终止。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

from core import paths
from core import applog
from core import logfmt
from core import subproc

# colorama 的 Fore 码 -> tkinter 颜色名
ANSI_COLORS = {
    '\x1b[31m': 'red', '\x1b[33m': 'yellow', '\x1b[34m': 'blue',
    '\x1b[32m': 'green', '\x1b[35m': 'magenta', '\x1b[36m': 'cyan',
}
ANSI_RESET = '\x1b[0m'

# 子进程输出落盘（界面日志框关掉就没了，排查问题时全靠这个文件）
OUTPUT_LOG_NAME = '运行日志.log'
OUTPUT_LOG_LIMIT = 3 * 1024 * 1024     # 超过 3MB 轮转成 .old
# 与 core.applog 同理：逐行 makedirs + exists/getsize 是渐进卡顿的固定开销，
# 目录只建一次，轮转检查合并成一次 stat。
_ensured_dirs = set()


def output_log_path(account_id) -> str:
    return os.path.join(paths.account_home(account_id), OUTPUT_LOG_NAME)


def append_output_log(path: str, text: str, stamp: str = None) -> None:
    """把子进程输出按时间戳追加到日志文件；失败静默（不能因为写日志影响主流程）。

    2026-11-19：时间戳由原来的 `HH:MM:SS` 改为 `YYYY-MM-DD HH:MM:SS`（精确到秒）。
    跨天连跑时旧格式分不清是哪一天，导出日志后无法和程序日志/报错时间对齐。
    """
    stamp = stamp or logfmt.file_stamp()
    folder = os.path.dirname(path)
    try:
        if folder and folder not in _ensured_dirs:
            os.makedirs(folder, exist_ok=True)
            _ensured_dirs.add(folder)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > OUTPUT_LOG_LIMIT:
            try:
                os.replace(path, path + '.old')
            except OSError:
                pass
        with open(path, 'a', encoding='utf-8') as f:
            for line in (text or '').splitlines():
                if line.strip():
                    f.write(f'{stamp} {line}\n')
    except OSError:
        _ensured_dirs.discard(folder)

MAX_CONCURRENCY = 5


class AccountProcess:
    """一个账号对应的一条子进程。"""

    def __init__(self, account_id, alias, popen):
        self.account_id = account_id
        self.alias = alias
        self.popen = popen
        self.terminated = False   # stop_all 幂等标记：避免连续点两次停止重复 taskkill


class Runner:
    """按并发上限逐个拉起账号，输出通过回调送回 UI。"""

    def __init__(self, config, on_output, on_finish, max_concurrency=2,
                 entry_args=('--core',)):
        self.config = config
        self.on_output = on_output          # (account_id, text, color_tag) -> None
        self.on_finish = on_finish          # (account_id, exit_code) -> None
        self.entry_args = tuple(entry_args or ('--core',))
        self.max_concurrency = max(1, min(MAX_CONCURRENCY, int(max_concurrency or 1)))
        self._sem = threading.Semaphore(self.max_concurrency)
        # 用可重入锁：启动、登记、终止共用一个锁，避免竞态窗口。
        #  scenes A/B（检查 stop 后、Popen 前/登记前被 stop_all 抢跑）由
        #  _starting 集合 + 锁内二次检查 + 锁内登记共同消除（2026-11-16 任务02）。
        self._lock = threading.RLock()
        self._procs = {}
        self._pending = []
        self._starting = set()   # 正在「检查/Popen/登记」关键区的账号 id
        self._stop = threading.Event()
        self._threads = []
        # V5.1 第24节：新一轮开始时保留上一轮尚未退出的线程引用，
        # 绝不直接清空后假装它们不存在。
        self._old_threads = []
        # 同实例快速 stop/start 竞态（2026-11-16）：每轮运行有唯一 generation，
        # 旧线程退出时只清理自己那轮的记录，绝不误删新一轮同账号进程。
        self._generation = 0
        # 可注入的进程工厂 / 终止器，纯桩竞态测试用；生产保持 None 走真实实现。
        self._popen_factory = None
        self._terminator = None

    # ------------------------------------------------------------ 生命周期
    def set_concurrency(self, n):
        """运行前调整并发上限（1~5）。"""
        self.max_concurrency = max(1, min(MAX_CONCURRENCY, int(n or 1)))
        self._sem = threading.Semaphore(self.max_concurrency)

    def active_count(self):
        with self._lock:
            return len(self._procs)

    def running_ids(self):
        """当前真正在跑的账号 id（已经起了子进程的）。"""
        with self._lock:
            return list(self._procs.keys())

    def pending_ids(self):
        """还在排队的账号 id（已提交但还没轮到）。"""
        with self._lock:
            return [aid for aid in self._pending if aid not in self._procs]

    @property
    def running(self):
        with self._lock:
            return dict(self._procs)

    def start(self, account_ids):
        # 场景 E：停止后重新开始必须清掉旧停止状态与旧集合，不继承。
        # V5.1 第24节：generation 递增与 _stop.clear() 放进同一把锁内，
        # 避免旧批次线程看到「stop 被清空」就误以为可以继续；旧线程靠 gen 校验退出。
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._old_threads = [t for t in self._threads if t.is_alive()]
            self._threads = []
            self._starting = set()
            self._procs = {}
            self._pending = list(account_ids)
            # 本次批次真正要跑几个账号（≠ 配置里一共有几个）：
            # 只跑 1 个时子进程可以复用浏览器已有登录态，不必清 Cookie 重登
            self._batch_size = len(account_ids)
            self._stop.clear()
        for aid in account_ids:
            t = threading.Thread(target=self._run_one, args=(aid, gen), daemon=True)
            t.start()
            self._threads.append(t)
        return len(self._threads)

    def _discard_pending(self, account_id):
        with self._lock:
            if account_id in self._pending:
                self._pending.remove(account_id)

    def stop_all(self):
        """停止所有账号——覆盖排队中、启动中、已运行的进程，且幂等。

        场景 A/B：_run_one 把「二次 stop 检查 → Popen → 登记 _procs」放在同一把锁里，
        stop_all 一旦拿到锁，要么进程还没创建（线程二次检查会发现 stop 而放弃），
        要么进程已登记（下面能拿到并终止），不存在漏网窗口。
        场景 C：先 set 事件并清空 pending，排队线程被唤醒后二次检查会直接退出。
        场景 D：AccountProcess.terminated 标记保证同一进程只终止一次。
        """
        self._stop.set()
        # V5.1 第24节：等待信号量的账号线程用 0.2 秒轮询检查 _stop，
        # 这里不需要 release 信号量（那会破坏并发上限），停止信号足以唤醒它们。
        try:
            applog.info('已发出停止信号，等待中的账号任务将立即退出')
        except Exception:
            pass
        with self._lock:
            self._pending = []
            to_kill = []
            for item in list(self._procs.values()):
                if not item.terminated:
                    item.terminated = True
                    to_kill.append(item.popen)
        for process in to_kill:
            self._terminate(process)

    def _terminate(self, process):
        try:
            if self._terminator is not None:
                self._terminator(process)
            elif os.name == 'nt':
                # 保留 Windows taskkill /T 进程树终止能力
                # 走 subproc：taskkill 是控制台程序，直接调会闪黑窗
                subproc.run_hidden(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                   capture_output=True)
            else:
                process.terminate()
        except Exception:
            pass

    # ------------------------------------------------------------ 单账号执行
    def _command(self):
        if getattr(sys, 'frozen', False):
            return [sys.executable, *self.entry_args]
        if self.entry_args == ('--core',):
            return [sys.executable, os.path.join(paths.app_root(), 'main.py')]
        return [sys.executable, os.path.join(paths.app_root(), 'start.py'), *self.entry_args]

    def _cleanup_own(self, account_id, gen):
        """只清理属于 generation gen 的记录；新一轮同账号不受影响。"""
        with self._lock:
            if gen != self._generation:
                return
            self._starting.discard(account_id)
            self._procs.pop(account_id, None)
            if account_id in self._pending:
                self._pending.remove(account_id)

    def _run_one(self, account_id, gen=None):
        if gen is None:
            gen = self._generation
        acquired = False
        # V5.1 第24节：阻塞式 _sem.acquire() 改为短间隔可取消等待。
        # 旧批次线程在新一轮 start() 后绝不「复活」：start() 会 clear _stop，
        # 所以仅靠停止标志不够，必须逐次校验 generation。
        while True:
            if gen != self._generation:
                return                      # 旧批次：不动任何新一轮状态
            if self._stop.is_set():
                self._discard_pending(account_id)
                return
            if self._sem.acquire(timeout=0.2):
                acquired = True
                break
        try:
            # 拿到信号量后再校验一次 gen（等待期间可能刚好发生了新一轮 start）
            with self._lock:
                if gen != self._generation:
                    return                  # finally 归还信号量，且不清新记录
            # 场景 C：排队线程拿到信号量后先检查停止
            if self._stop.is_set():
                self._discard_pending(account_id)
                return
            home = self.config.write_account_runtime(account_id)
            if not home:
                self.on_output(account_id, f'[调度] 账号 {account_id} 配置无效，跳过\n', 'red')
                applog.warn('账号 %s 配置无效，未启动子进程' % account_id)
                self._discard_pending(account_id)
                return

            env = os.environ.copy()
            env['XXT_HOME'] = home
            env['PYTHONIOENCODING'] = 'utf-8'
            # 本次批次账号数：子进程据此决定「能否复用浏览器已有登录态」
            env['XXT_ACCOUNT_COUNT'] = str(getattr(self, '_batch_size', 1))

            # ---- 启动原子区（2026-11-16 任务02）----
            # 二次 stop 检查、Popen、登记 _procs 三步在同一把锁内完成。
            # 这样 stop_all 拿锁时看到的要么是「进程还没建」（本线程二次检查会放弃），
            # 要么是「进程已登记」（stop_all 能终止它），消除场景 A/B 的漏网窗口。
            with self._lock:
                if gen != self._generation:
                    return                  # 旧批次不得启动任何进程
                if self._stop.is_set():
                    self._discard_pending(account_id)
                    return
                self._starting.add(account_id)
                try:
                    # 默认走 subproc.popen_hidden：除 CREATE_NO_WINDOW 外还带
                    # STARTF_USESHOWWINDOW/SW_HIDE，双重保证子进程不弹控制台
                    factory = self._popen_factory or subproc.popen_hidden
                    process = factory(
                        self._command(),
                        cwd=paths.app_root(),
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                except Exception as e:
                    self.on_output(account_id, f'[调度] 启动失败: {e}\n', 'red')
                    applog.exception('账号 %s 子进程启动失败' % account_id)
                    self._starting.discard(account_id)
                    self._discard_pending(account_id)
                    return
                alias = self.config.get_account_by_id(account_id).get('alias', account_id)
                # Popen 成功后立即登记，并再次确认停止状态（场景 B：Popen 后、登记前停止）
                self._procs[account_id] = AccountProcess(account_id, alias, process)
                self._starting.discard(account_id)
                if account_id in self._pending:
                    self._pending.remove(account_id)
                if self._stop.is_set():
                    # 停标志在 Popen 期间被置上：标记终止，交给 stop_all/这里立即杀
                    item = self._procs[account_id]
                    if not item.terminated:
                        item.terminated = True
                        kill_now = process
                    else:
                        kill_now = None
                else:
                    kill_now = None
            if kill_now is not None:
                self._terminate(kill_now)
                applog.info('启动窗口内收到停止，已终止账号 %s' % account_id)

            applog.info('子进程启动：账号 %s(%s) pid=%s' % (alias, account_id, process.pid))
            self._pump(process, account_id)

            try:
                code = process.wait(timeout=10)
            except Exception:
                code = -1
            self._cleanup_own(account_id, gen)
            applog.info('子进程退出：账号 %s(%s) code=%s' % (alias, account_id, code))
            self.on_finish(account_id, code)
        finally:
            # 异常/停止路径兜底清理；只清自己那轮（场景 E：同实例重启干净）
            self._cleanup_own(account_id, gen)
            if acquired:
                self._sem.release()

    def _pump(self, process, account_id):
        """逐行读子进程输出，解析 ANSI 颜色后回调，并同时落盘到 运行日志.log。"""
        tag = None
        log_path = output_log_path(account_id)
        append_output_log(log_path, f'=== 子进程启动 pid={process.pid} ===')
        while True:
            try:
                raw = process.stdout.readline()
            except Exception:
                break
            if not raw:
                break
            text = self._decode(raw)
            text, tag = self._apply_color(text, tag)
            if text:
                append_output_log(log_path, text)
                self.on_output(account_id, text, tag)
        append_output_log(log_path, '=== 子进程输出结束 ===')
        try:
            process.stdout.close()
        except Exception:
            pass

    @staticmethod
    def _decode(raw: bytes) -> str:
        for enc in ('utf-8', 'gbk'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='ignore')

    @staticmethod
    def _apply_color(text, tag):
        for code, name in ANSI_COLORS.items():
            if code in text:
                tag = name
                text = text.replace(code, '')
        if ANSI_RESET in text:
            text = text.replace(ANSI_RESET, '')
            tag = None
        # 兜底：清掉残余的 CSI 序列
        text = re.sub(r'\x1b\[[0-9;]*m', '', text)
        text = text.replace('\x1b', '')
        return text, tag

    def old_thread_count(self):
        """上一轮尚未退出的线程数（供诊断/回归，绝不清空引用）。"""
        with self._lock:
            return len([t for t in self._old_threads if t.is_alive()])

    def wait_all(self, timeout=None):
        # 旧一轮线程也做一次有限 join：它们靠 generation 自行退出，不会无限阻塞。
        for t in list(self._old_threads) + list(self._threads):
            try:
                t.join(timeout)
            except Exception:
                pass
