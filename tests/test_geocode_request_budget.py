# -*- coding: utf-8 -*-
"""任务09 签到位置修正请求预算回归（2026-11-16）纯桩测试。

不联网、不启浏览器。mock 掉 HTTP 与活动接口，只验证预算硬闸：
达到 MAX_TOTAL_REQUESTS 后不再发请求、stop_event 取消、总时间预算、
验证码/人脸立即停止。

用法：C:/Python315/python.exe tests/test_geocode_request_budget.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_budget_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task.xxt import sign, constants, storage

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# --- 桩：让 _resolve_candidates 返回多个候选，submit_once 永远返回"距离未达标"，
#     从而驱动爬山循环尽可能多发请求 ---
def fake_candidates(client, act, content):
    return [(30.0, 120.0, '测试起点')]


def make_far_result(distance=500.0):
    r = sign.SignResult(False, '距教师指定签到地点 500 米')
    r.distance = distance
    return r


def stub_deps():
    orig = {}
    orig['candidates'] = sign._resolve_candidates
    orig['submit'] = sign.submit_once
    orig['fetch'] = sign.activity_mod.fetch_activity_info
    orig['content'] = sign.activity_mod.activity_content
    orig['address'] = sign.activity_mod.content_address
    orig['cached'] = storage.save_cached_coord
    orig['log'] = storage.log
    orig['interval'] = constants.REQUEST_INTERVAL
    sign._resolve_candidates = fake_candidates
    sign.activity_mod.fetch_activity_info = lambda c, a: {}
    sign.activity_mod.activity_content = lambda c, a, cached=None: {}
    sign.activity_mod.content_address = lambda content: '测试地址'
    storage.save_cached_coord = lambda *a: None
    storage.log = lambda *a, **k: None
    constants.REQUEST_INTERVAL = 0     # 测试不等间隔
    return orig


def restore(orig):
    sign._resolve_candidates = orig['candidates']
    sign.submit_once = orig['submit']
    sign.activity_mod.fetch_activity_info = orig['fetch']
    sign.activity_mod.activity_content = orig['content']
    sign.activity_mod.content_address = orig['address']
    storage.save_cached_coord = orig['cached']
    storage.log = orig['log']
    constants.REQUEST_INTERVAL = orig['interval']


class Dummy:
    aid = 'act1'
    course = type('C', (), {'title': '测试课程'})()
    sign_type_text = '位置签到'
    name = '测试活动'
    other_id = constants.SIGN_LOCATION


def main():
    print('=== 任务09 请求预算 ===')
    orig = stub_deps()
    try:
        # 1) 请求数硬闸：爬山永远不成功，请求数不得超过 max_requests
        calls = []
        def far_submit(client, act, la, lo, address):
            calls.append((la, lo))
            return make_far_result()
        sign.submit_once = far_submit
        r = sign.location_sign(Dummy(), Dummy(), info={},
                               max_requests=8, time_budget=30)
        check('请求数不超过预算', len(calls) == 8, len(calls))
        check('超预算返回失败且注明原因',
              not r.success and '停止' in r.message, r.message)

        # 2) stop_event 在过程中置位：立即停止
        calls.clear()
        ev = threading.Event()
        def stop_after3(client, act, la, lo, address):
            calls.append(1)
            if len(calls) == 3:
                ev.set()
            return make_far_result()
        sign.submit_once = stop_after3
        r = sign.location_sign(Dummy(), Dummy(), info={}, stop_event=ev,
                               max_requests=50, time_budget=30)
        check('stop_event 生效', len(calls) == 3, len(calls))
        check('停止原因正确', '用户停止' in r.message, r.message)

        # 3) 总时间预算极小：发一次后超时停止
        calls.clear()
        sign.submit_once = lambda *a: (calls.append(1), make_far_result())[1]
        # 用 submit 里的 sleep 真正耗尽时间预算
        def slow_submit(client, act, la, lo, address):
            calls.append(1)
            time.sleep(0.01)
            return make_far_result()
        sign.submit_once = slow_submit
        r = sign.location_sign(Dummy(), Dummy(), info={},
                               max_requests=200, time_budget=0.05)
        check('时间预算生效（提前停止）',
              0 < len(calls) < 200 and '停止' in r.message,
              'requests=%d msg=%s' % (len(calls), r.message))

        # 4) 验证码/人脸立即停止，不再继续爬山
        calls.clear()
        def captcha(client, act, la, lo, address):
            calls.append(1)
            rr = sign.SignResult(False, '需要验证码')
            rr.need_captcha = True
            return rr
        sign.submit_once = captcha
        r = sign.location_sign(Dummy(), Dummy(), info={},
                               max_requests=50, time_budget=30)
        check('验证码立即停止', len(calls) == 1, len(calls))
        check('验证码结果透传', r.need_captcha, r.message)

        # 5) 已有 MAX_ROUNDS/MAX_WALK 未被改动
        check('MAX_ROUNDS 保留', sign.MAX_ROUNDS == 10, sign.MAX_ROUNDS)
        check('MAX_WALK 保留', sign.MAX_WALK == 100, sign.MAX_WALK)
        check('独立预算常量存在',
              constants.LOCATION_MAX_TOTAL_REQUESTS == 60
              and constants.LOCATION_TIME_BUDGET == 90)

        # 6) _RequestBudget 单元：次数/时间/停止三闸
        b = sign._RequestBudget(max_requests=2, time_budget=100)
        check('预算内可 spend', b.can_spend() and b.can_spend())
        b.spend(); b.spend()
        check('次数用尽拒绝', not b.can_spend() and '上限' in b.reason, b.reason)
        ev2 = threading.Event(); ev2.set()
        b2 = sign._RequestBudget(stop_event=ev2)
        check('停止事件拒绝', not b2.can_spend() and b2.reason == '用户停止', b2.reason)
        b3 = sign._RequestBudget(time_budget=-1)
        check('时间耗尽拒绝', not b3.can_spend() and '预算' in b3.reason, b3.reason)
    finally:
        restore(orig)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
