# -*- coding: utf-8 -*-
"""P1：GUI 日志队列与批量刷新回归（2026-11-16）纯桩 + 真实 Tk Text 测试。

不启动完整 CustomTkinter 应用；用标准 tkinter.Text（CustomTkinter 底层同款）
做真实控件插入基准，验证「批量插入 + 一次 see」比「逐行 see」快且行数有界。

用法：C:/Python315/python.exe tests/test_gui_log_performance.py
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_gulog_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== P1 GUI 日志性能 ===')
    import queue
    from core import performance as perf

    # 1) 队列背压：满时丢弃最旧项（界面项），不阻塞
    q = queue.Queue(maxsize=10)
    for i in range(20):
        if q.qsize() >= 10:
            q.get_nowait()
        q.put(('acc', 'line%d' % i, None))
    check('队列不超上限', q.qsize() == 10, q.qsize())
    check('保留最新行', q.get()[1] == 'line10')

    # 2) 真实 tkinter.Text 基准（无需显示服务器时用纯桩对照）
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        box = tk.Text(root)
        lines = ['2026-11-16 21:00:00 账号输出第 %d 行内容' % i for i in range(10000)]

        t0 = time.perf_counter()
        for line in lines:
            box.insert('end', line + '\n')
            box.see('end')            # 旧行为：每行滚动
        old_ms = (time.perf_counter() - t0) * 1000

        box.delete('1.0', 'end')
        t0 = time.perf_counter()
        for i in range(0, len(lines), 500):
            batch = lines[i:i+500]
            box.insert('end', '\n'.join(batch) + '\n')
        box.see('end')                # 新行为：一批一次滚动
        new_ms = (time.perf_counter() - t0) * 1000
        root.destroy()
        has_tk = True
        print('  真实 Text：逐行see %.1fms → 批量 %.1fms' % (old_ms, new_ms))
        check('批量插入不慢于逐行', new_ms <= old_ms * 1.2,
              '%.1f vs %.1f ms' % (new_ms, old_ms))
    except Exception as e:
        has_tk = False
        print('  跳过真实Tk基准（环境无显示）：%s' % str(e)[:80])

    # 3) 源码级：批量队列 + 一批一次 see + 关闭清理
    start_src = open(os.path.join(ROOT, 'start.py'), encoding='utf-8').read()
    check('有日志队列', '_log_queue = ' in start_src or '_log_queue=' in start_src)
    check('队列有上限', 'LOG_QUEUE_MAX' in start_src)
    check('批量刷新间隔100ms', 'LOG_FLUSH_MS = 100' in start_src)
    check('一批一次see', start_src.count('box.see("end")') >= 1)
    check('不再每行after(0)', 'self.root.after(0, insert)' not in start_src)
    check('窗口关闭停回调', '_log_closed' in start_src)
    check('磁盘日志仍完整落盘',
          'applog.info(text.rstrip())' in start_src and 'applog.child' in start_src)
    check('多账号日志隔离', 'by_account.setdefault(aid' in start_src)

    # 4) 行数上限仍在（不重复加机制）
    check('保留2000行上限', 'LOG_MAX_LINES = 2000' in start_src)

    print('  真实Tk基准可用:', has_tk)
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
