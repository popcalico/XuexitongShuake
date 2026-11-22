# -*- coding: utf-8 -*-
"""任务10 性能实测（2026-11-16）。

用相同模拟场景记录原始数据，不做主观「变快了」宣称。
测量项：
1. 日志文本 1000 / 10000 行写入耗时（ListView 模拟：Python list + trim）
2. 磁盘日志落盘耗时（append_output_log）
3. digest.today_log_counts 流式读 vs 旧版全量 f.read() 耗时/内存
4. 题库模拟并发耗时（引用 task08 结论）

用法：C:/Python315/python.exe tests/test_perf_measure.py
"""
import gc
import io
import os
import sys
import tempfile
import time
import tracemalloc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_perf_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runner, digest

FAILS = []
DATA = {}


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def measure(fn, repeat=3):
    best = None
    for _ in range(repeat):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return round(best * 1000, 2)  # ms


def main():
    print('=== 任务10 性能实测（原始数据） ===')

    # 1) 日志框：1000 / 10000 行追加 + 2000 行上限裁剪（模拟 _trim_log_box 的数据操作）
    def logbox(n):
        lines = []
        MAX = 2000
        for i in range(n):
            lines.append('2026-11-16 12:00:00 账号输出第 %d 行内容...' % i)
            if len(lines) > MAX:
                del lines[:len(lines) - (MAX - 200)]
        return lines
    t1000 = measure(lambda: logbox(1000))
    t10000 = measure(lambda: logbox(10000))
    DATA['日志框1000行ms'] = t1000
    DATA['日志框10000行ms'] = t10000
    print('  日志框 1000 行: %s ms' % t1000)
    print('  日志框 10000 行: %s ms' % t10000)
    check('日志框万行写入有界（<2s）', t10000 < 2000, '%s ms' % t10000)

    # 2) 磁盘日志落盘：1000 行 append_output_log
    logp = os.path.join(os.environ['XXT_HOME'], 'perf.log')
    def write_log():
        if os.path.exists(logp):
            os.remove(logp)
        runner._ensured_dirs.clear()
        for i in range(1000):
            runner.append_output_log(logp, '账号输出第 %d 行' % i)
    tlog = measure(write_log)
    DATA['日志落盘1000行ms'] = tlog
    print('  磁盘日志 1000 行落盘: %s ms' % tlog)
    check('磁盘日志落盘有界（<15s）', tlog < 15000, '%s ms' % tlog)
    check('落盘无丢行', sum(1 for _ in open(logp, encoding='utf-8')) == 1000)

    # 3) digest：构造 5MB 日志，对比流式 vs 旧版全量读
    big = os.path.join(os.environ['XXT_HOME'], 'big.log')
    with open(big, 'w', encoding='utf-8') as f:
        for i in range(50000):
            f.write('2026-11-16 12:00:00 INFO 程序日志第 %d 行 普通内容\n' % i)
            if i % 1000 == 0:
                f.write('ERROR 该页面无法识别 测试错误\n')
    size_mb = round(os.path.getsize(big) / 1048576, 2)

    def old_read():
        with open(big, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        return sum(1 for line in text.splitlines() if 'ERROR' in line)
    def new_stream():
        return sum(1 for line in digest._iter_lines(big) if 'ERROR' in line)

    told = measure(old_read)
    tnew = measure(new_stream)
    tracemalloc.start()
    old_read()
    _, old_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    tracemalloc.start()
    new_stream()
    _, new_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    DATA['digest日志大小MB'] = size_mb
    DATA['digest旧全量读ms'] = told
    DATA['digest新流式ms'] = tnew
    DATA['digest旧峰值KB'] = round(old_peak / 1024, 1)
    DATA['digest新峰值KB'] = round(new_peak / 1024, 1)
    print('  日志大小: %s MB' % size_mb)
    print('  digest 旧全量读: %s ms, 峰值 %s KB' % (told, round(old_peak/1024,1)))
    print('  digest 新流式:   %s ms, 峰值 %s KB' % (tnew, round(new_peak/1024,1)))
    check('流式读结果一致', old_read() == new_stream())
    check('流式读内存更低', new_peak < old_peak,
          '%.0fKB < %.0fKB' % (new_peak/1024, old_peak/1024))

    # 4) digest.build 整体耗时
    tbuild = measure(lambda: digest.build('perf_test'))
    DATA['digest构建ms'] = tbuild
    print('  digest.build 整体: %s ms' % tbuild)
    check('digest 构建有界（<3s）', tbuild < 3000, '%s ms' % tbuild)

    # 5) 题库并发（引用 task08：3 接口各 0.3s，并发 ~0.3s vs 串行 ~0.9s）
    DATA['题库并发说明'] = '见 test_http_timeout.py：3×0.3s 接口并发总耗时 0.30s（串行需 0.90s）'

    # 输出原始数据 JSON 供交付
    import json
    out = os.path.join(ROOT, 'docs', '性能测试原始数据.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(DATA, f, ensure_ascii=False, indent=2)
    print('  原始数据已写入:', os.path.relpath(out, ROOT))

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
