# -*- coding: utf-8 -*-
"""任务07 网络超时 + 任务08 题库真实并发 回归（2026-11-16）纯桩测试。

用 Mock 替换 requests.post/get，不联网、不启浏览器。

用法：C:/Python315/python.exe tests/test_http_timeout.py
"""
import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_http_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import task.tool.ai_wen_da as aw
from task.tool.ai_wen_da import AnswerAPI, AnswerResult

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Q:
    question = '测试题'
    options = ['A', 'B']
    type = '0'
    work_type = '0'
    page_type = '0'
    html = ''


class Resp:
    def __init__(self, code=1, data='答案A#答案B', status=200):
        self.status_code = status
        self._code, self._data = code, data
    def json(self):
        return {'code': self._code, 'data': self._data}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception('HTTP %d' % self.status_code)


def main():
    print('=== 任务07 超时 ===')
    src = open(os.path.join(ROOT, 'task', 'tool', 'ai_wen_da.py'), encoding='utf-8').read()
    check('get_answer4 无裸 requests.post',
          'response = requests.post(url, headers=headers, data=data)' not in src)
    check('get_answer4 有显式 timeout', 'timeout=(5, 15)' in src)
    check('所有题库请求走 to_thread', src.count('await asyncio.to_thread(') >= 5)

    # Mock：断言每次调用都带 timeout（连接/读取超时）
    called = {}
    def fake_post(url, **kw):
        called['timeout'] = kw.get('timeout')
        return Resp()
    old_post = aw.requests.post
    aw.requests.post = fake_post
    try:
        api = AnswerAPI()
        r = asyncio.run(api.get_answer1(Q()))
        check('get_answer1 携带 timeout', called.get('timeout') is not None,
              called.get('timeout'))
        check('get_answer1 返回结构正确', isinstance(r, AnswerResult) and r.answer)
    finally:
        aw.requests.post = old_post

    # 连接超时 / 读取超时：requests 抛 Timeout -> 归类 msg=timeout
    def timeout_post(url, **kw):
        raise aw.requests.exceptions.Timeout('connect timeout')
    aw.requests.post = timeout_post
    try:
        r = asyncio.run(AnswerAPI().get_answer1(Q()))
        check('连接超时单独记录', r.msg == 'timeout', r.msg)
    finally:
        aw.requests.post = old_post

    # HTTP 500：分类为请求失败（不外抛）
    def http500(url, **kw):
        return Resp(status=500)
    aw.requests.post = http500
    try:
        r = asyncio.run(AnswerAPI().get_answer1(Q()))
        check('HTTP500 不外抛', isinstance(r, AnswerResult), r)
    finally:
        aw.requests.post = old_post

    # 非法 JSON：不外抛
    class BadJson(Resp):
        def json(self):
            raise ValueError('bad json')
    aw.requests.post = lambda url, **kw: BadJson()
    try:
        r = asyncio.run(AnswerAPI().get_answer1(Q()))
        check('非法JSON不外抛', isinstance(r, AnswerResult), r)
    finally:
        aw.requests.post = old_post

    # 通知服务不重试（源码级：notify 只有一次 requests.post）
    notifier = open(os.path.join(ROOT, 'core', 'notifier.py'), encoding='utf-8').read()
    check('通知请求有 timeout', 'timeout=TIMEOUT' in notifier)
    check('通知无重试循环', 'for retry' not in notifier and 'while retry' not in notifier)

    print('=== 任务08 真实并发 ===')
    # 模拟三个接口各延迟 0.3s：真实并发总耗时 ~0.3s，串行 ~0.9s
    calls = []
    def slow_post(url, **kw):
        calls.append(url)
        time.sleep(0.3)
        return Resp()
    aw.requests.post = slow_post
    aw.requests.get = slow_post
    try:
        api = AnswerAPI()
        t0 = time.time()
        results = asyncio.run(api.get_answers_free(Q()))
        elapsed = time.time() - t0
        check('三接口真实并发(总耗时<0.7s)', elapsed < 0.7, '%.2fs' % elapsed)
        check('三接口都被调用', len(calls) == 3, len(calls))
        check('结果保持传入顺序', [r.form for r in results] ==
              ['爱问答题库', '一之题库', 'muketool'], [r.form for r in results])
    finally:
        aw.requests.post = old_post

    # 单接口异常不阻塞其它（异常隔离：一个接口抛错，其余照常返回）
    # 注：asyncio.to_thread 里的同步 sleep 无法被 wait_for 取消（线程会跑完），
    # 故用「抛异常」验证隔离；真正的超时取消由 _gather_bounded 的 wait_for 保证。
    def one_fail(url, **kw):
        if 'icodef' in url:
            raise Exception('模拟单接口失败')
        return Resp()
    aw.requests.post = one_fail
    try:
        api = AnswerAPI()
        results = asyncio.run(api.get_answers_free(Q()))
        check('单接口异常被隔离', len(results) == 3 and
              all(isinstance(r, AnswerResult) for r in results),
              [r.msg for r in results])
        # 只有 icodef(一之题库) 失败；muketool 等其它接口仍应正常返回答案
        check('异常接口不污染其它结果',
              len(results[2].answer) > 0 and not results[1].answer,
              [r.answer for r in results])
    finally:
        aw.requests.post = old_post

    # wait_for 超时机制本身：用一个真 async 慢任务验证会被标记超时
    async def slow():
        await asyncio.sleep(1.0)
        return AnswerResult(form='x', answer=['should-not-appear'])
    async def check_waitfor():
        api = AnswerAPI()
        old = api.BANK_TASK_TIMEOUT
        api.BANK_TASK_TIMEOUT = 0.05
        try:
            out = await api._gather_bounded([slow()], ['x'])
            return out[0]
        finally:
            api.BANK_TASK_TIMEOUT = old
    r = asyncio.run(check_waitfor())
    check('wait_for 单接口超时生效', '超时' in (r.msg or '') and not r.answer, r.msg)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
