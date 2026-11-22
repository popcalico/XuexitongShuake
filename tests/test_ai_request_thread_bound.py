# -*- coding: utf-8 -*-
"""V5.1 第25节：题库超时后后台线程/队列有界，迟到结果不污染当前题目。

不联网：用 asyncio.to_thread(time.sleep) 模拟底层同步请求；用协程模拟超时接口。

用法：C:/Python315/python.exe tests/test_ai_request_thread_bound.py
"""
import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_aithread_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import bounded_executor as be
from task.tool.ai_wen_da import AnswerAPI, AnswerResult

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def timeout_batch(n=5, work=0.12):
    """一批连续超时的底层同步任务：wait_for 取消协程，但线程仍会跑完。"""
    async def run():
        be.install()
        be.next_batch()            # 与 AnswerAPI._gather_bounded 的批次语义一致
        async def one():
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(time.sleep, work), timeout=0.02)
            except asyncio.TimeoutError:
                be.record_timeout()
                return None
        return await asyncio.gather(*(one() for _ in range(n)))
    return asyncio.run(run())


def main():
    print('=== V5.1 题库线程有界 / 迟到结果隔离 ===')
    be.reset_stats()

    # 1) 多个 asyncio.run 之间池仍可用（shutdown 是空操作）
    r1 = asyncio.run(asyncio.to_thread(lambda: 'x')) if False else None
    async def _two_loops():
        be.install()
        return await asyncio.to_thread(lambda: 'ok')
    a = asyncio.run(_two_loops())
    b = asyncio.run(_two_loops())
    check('连续两个事件循环都能复用共享池', a == 'ok' and b == 'ok', (a, b))

    # 2) 连续超时：线程数与排队长度有界
    for _ in range(10):
        timeout_batch(n=5)
    st = be.stats()
    check('活动线程数不超过上限',
          st['active_threads'] <= st['max_workers'], st)
    check('队列长度有界', st['bounded'] is True, st)
    check('峰值线程数有界', st['peak_threads'] <= st['max_workers'], st)
    check('超时数被记录', st['timeouts'] >= 50, st['timeouts'])
    check('批次数递增', st['batches'] >= 10, st['batches'])

    # 3) 超时返回空结果，顺序稳定，不串到别的接口
    api = AnswerAPI()
    api.BANK_TASK_TIMEOUT = 0.05
    async def fast(form):
        return AnswerResult(form=form, answer=['A'])
    async def slow(form):
        await asyncio.sleep(5)
        return AnswerResult(form=form, answer=['B'])
    results = asyncio.run(api._gather_bounded(
        [fast('主题库'), slow('一之题库'), fast('muketool')],
        ['主题库', '一之题库', 'muketool']))
    check('结果按 form 顺序返回',
          [r.form for r in results] == ['主题库', '一之题库', 'muketool'],
          [r.form for r in results])
    check('超时接口返回空答案', results[1].answer == [], results[1].answer)
    check('超时接口带批次标记', '单接口超时' in results[1].msg, results[1].msg)
    check('未超时接口结果未被污染',
          results[0].answer == ['A'] and results[2].answer == ['A'],
          (results[0].answer, results[2].answer))

    # 4) AnswerAPI 暴露统计接口
    st2 = AnswerAPI.bank_executor_stats()
    check('统计接口可用', isinstance(st2, dict) and 'max_workers' in st2, st2)

    # 5) reset_stats 清零
    be.reset_stats()
    check('统计可复位', be.stats()['timeouts'] == 0, be.stats()['timeouts'])

    # 6) 源码级：保留 5 处 to_thread，且明确说明取消边界
    src = open(os.path.join(ROOT, 'task', 'tool', 'ai_wen_da.py'),
               encoding='utf-8').read()
    be_src = open(os.path.join(ROOT, 'core', 'bounded_executor.py'),
                  encoding='utf-8').read()
    check('仍有 5 处 to_thread（兼容既有断言）',
          src.count('await asyncio.to_thread(') >= 5,
          src.count('await asyncio.to_thread('))
    check('答题前安装有界执行器', 'bounded_executor.install()' in src)
    check('不误称 wait_for 能终止底层线程',
          '不会终止底层线程' in src or '不终止底层同步 HTTP 线程' in be_src)
    check('shutdown 空操作以跨 asyncio.run 复用',
          'def shutdown(self, wait=True' in be_src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
