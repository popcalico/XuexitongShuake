# -*- coding: utf-8 -*-
"""P1：阶段计时与 P50/P95/max 回归（2026-11-16）纯桩测试。"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_perfstg_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import performance as perf

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== P1 阶段计时 ===')
    perf.reset()

    # 1) stage 记录耗时
    with perf.stage('启动浏览器', account_id='acc_A'):
        time.sleep(0.02)
    rep = perf.report()
    check('stage 产生记录', '启动浏览器' in rep and rep['启动浏览器']['count'] == 1, rep)
    check('stage 耗时非负且合理', 0 <= rep['启动浏览器']['p50'] < 1,
          rep['启动浏览器'])

    # 2) timed_call 返回值与异常
    def add(a, b):
        return a + b
    check('timed_call 保留返回值', perf.timed_call('加法', add, 2, 3) == 5)
    try:
        perf.timed_call('异常调用', lambda: 1/0)
        raised = False
    except ZeroDivisionError:
        raised = True
    check('timed_call 异常照常抛出', raised)

    # 3) P50/P95/max 统计
    perf.reset()
    for v in (0.01, 0.02, 0.03, 0.04, 0.05):
        perf._record('样本阶段', v)
    r = perf.report()['样本阶段']
    check('P50 正确', abs(r['p50'] - 0.03) < 0.001, r)
    check('P95 有值', r['p95'] >= 0.04, r)
    check('max 正确', abs(r['max'] - 0.05) < 0.001, r)
    check('count 正确', r['count'] == 5, r)

    # 4) 记录上限（不会无限增长）
    perf.reset()
    for i in range(3000):
        perf._record('压测', 0.001)
    check('记录数有上限', perf.report()['压测']['count'] <= perf._MAX_RECORDS,
          perf.report()['压测']['count'])

    # 5) 日志脱敏
    check('手机号脱敏', '13800138000' not in perf._safe('用户 13800138000 登录'))
    check('token 脱敏', 'sk-abcdefgh12345678' not in perf._safe('key=sk-abcdefgh12345678'))

    # 6) 进展标记（看门狗读取）
    perf.mark_progress('acc_A', '登录', '提交凭据')
    snap = perf.progress_snapshot('acc_A')
    check('进展标记有阶段', snap.get('stage') == '登录', snap)
    check('进展标记有时间戳', snap.get('last_progress_at', 0) > 0, snap)

    perf.reset()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
