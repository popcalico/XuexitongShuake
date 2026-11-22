# -*- coding: utf-8 -*-
"""P1：静默等待看门狗回归（2026-11-16）纯桩测试。

关键验收：看门狗**不调用 WebDriver**（源码级 + 桩验证），只输出分级诊断。
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_watchdog_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import performance as perf
from core import silent_watchdog as wd

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== P1 静默看门狗 ===')
    perf.reset()

    # 1) 无进展记录 → ok
    check('无记录不告警', wd.check_once('acc_none') == 'ok')

    # 2) 刚更新进展 → ok
    perf.mark_progress('acc_A', '登录', '提交凭据')
    check('刚有进展不告警', wd.check_once('acc_A') == 'ok')

    # 3) 模拟静默 12s → info；35s → warn；70s → diagnose
    snap = perf.progress_snapshot('acc_A')
    perf._EVENTS['acc_A']['last_progress_at'] = time.time() - 12
    check('静默12s提示', wd.check_once('acc_A', (10, 30, 60)) == 'info')
    perf._EVENTS['acc_A']['last_progress_at'] = time.time() - 35
    check('静默35s警告', wd.check_once('acc_A', (10, 30, 60)) == 'warn')
    perf._EVENTS['acc_A']['last_progress_at'] = time.time() - 70
    check('静默70s诊断', wd.check_once('acc_A', (10, 30, 60)) == 'diagnose')

    # 4) 进展更新后重置告警状态
    perf.mark_progress('acc_A', '课程页', '已识别任务点')
    check('恢复进展后不告警', wd.check_once('acc_A', (10, 30, 60)) == 'ok')

    # 5) 硬红线：看门狗源码不得调用 WebDriver
    src = open(os.path.join(ROOT, 'core/silent_watchdog.py'), encoding='utf-8').read()
    check('看门狗不import webdriver',
          'webdriver' not in src.lower() or '绝不并发调用 WebDriver' in src)
    check('看门狗不调用driver方法',
          'driver.' not in src and 'find_element' not in src)
    check('看门狗只读进展缓存',
          'perf.progress_snapshot' in src)

    # 6) start/stop 幂等，不残留线程
    t = wd.start(interval=60)
    t2 = wd.start(interval=60)
    check('重复start不建多线程', t is t2)
    wd.stop()
    check('stop后事件置位', wd._STOP.is_set())

    # 7) 脱敏
    check('日志脱敏手机号', '13800138000' not in wd._safe('账号 13800138000 静默'))

    perf.reset()
    wd._STOP.clear()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
