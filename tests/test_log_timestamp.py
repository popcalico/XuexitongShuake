# -*- coding: utf-8 -*-
"""日志时间戳（2026-11-19 新增功能）回归。

覆盖：
- 界面日志每行 `[HH:MM:SS]` 前缀（core.logfmt.stamp）
- 磁盘运行日志 `YYYY-MM-DD HH:MM:SS` 前缀（core.runner.append_output_log）

纯字符串 + 临时目录，不联网、不开浏览器。

用法：C:/Python315/python.exe tests/test_log_timestamp.py
"""
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_logstamp_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import logfmt
from core import runner

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 日志时间戳 ===')

    # 固定一个时间点，避免跨秒抖动
    ts = time.mktime((2026, 11, 19, 13, 45, 7, 0, 0, -1))
    hhmmss = time.strftime('%H:%M:%S', time.localtime(ts))
    ymdhms = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

    got = logfmt.stamp('开始播放\n已保存进度\n', now=ts)
    check('多行逐行加前缀',
          got == '[%s] 开始播放\n[%s] 已保存进度\n' % (hhmmss, hhmmss), repr(got))
    check('保留结尾换行', got.endswith('\n'))

    single = logfmt.stamp('单行', now=ts)
    check('单行不带多余换行', single == '[%s] 单行' % hhmmss, repr(single))

    mixed = logfmt.stamp('第一行\n\n第三行', now=ts)
    check('空行不加前缀', mixed == '[%s] 第一行\n\n[%s] 第三行' % (hhmmss, hhmmss), repr(mixed))

    check('空文本原样返回', logfmt.stamp('') == '')

    check('磁盘时间戳精确到秒且含日期',
          logfmt.file_stamp(ts) == ymdhms, logfmt.file_stamp(ts))

    # 落盘验证：追加两行，必须每行都有完整日期时间
    log_path = os.path.join(tempfile.mkdtemp(prefix='xxt_runlog_'), '运行日志.log')
    runner.append_output_log(log_path, '子进程输出一\n子进程输出二\n')
    lines = [ln for ln in open(log_path, encoding='utf-8').read().splitlines() if ln.strip()]
    check('落盘行数正确', len(lines) == 2, lines)
    pattern = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} 子进程输出[一二]$')
    check('每行都是 YYYY-MM-DD HH:MM:SS 前缀',
          all(pattern.match(ln) for ln in lines), lines)

    # 同一行不得出现两个时间戳（避免上层已带前缀时重复）
    runner.append_output_log(log_path, 'x', stamp='2099-01-01 00:00:00')
    last = open(log_path, encoding='utf-8').read().splitlines()[-1]
    check('支持外部指定时间戳', last == '2099-01-01 00:00:00 x', last)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
