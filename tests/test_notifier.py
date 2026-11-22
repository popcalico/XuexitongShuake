# -*- coding: utf-8 -*-
"""MeoW 推送 v3 纯文本版回归测试（不联网、不写日志、不启动后台线程）。

锁住：仅 text / 首行摘要 / category 校验 / 单次发送不重试 /
四档限流 / 状态校验 / 脱敏 / 错误聚合 / 去掉 start 事件。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import notifier  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, bad_json=False):
        self.payload = payload
        self.status_code = status_code
        self.bad_json = bad_json

    def json(self):
        if self.bad_json:
            raise ValueError('not json')
        return self.payload


class FakeRequests:
    def __init__(self):
        self.calls = []
        self.payload = {'status': 200, 'data': True, 'msg': '发送成功'}
        self.bad_json = False
        self.raise_post = False

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({'url': url, 'params': params, 'json': json, 'timeout': timeout})
        if self.raise_post:
            raise TimeoutError('timed out')
        return FakeResponse(self.payload, bad_json=self.bad_json)


class FakeAppLog:
    def warn(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


notifier.applog = FakeAppLog()
fake = FakeRequests()
notifier.requests = fake

URL = 'https://api.chuckfang.com/98ff9b41/'


def fresh(cfg=None):
    n = notifier.Notifier(autostart=False)
    n.configure(cfg or {
        'meow_enabled': 1,
        'meow_url': URL,
        'meow_category_enabled': 1,
        'meow_events': {},
    })
    fake.calls = []
    fake.payload = {'status': 200, 'data': True, 'msg': '发送成功'}
    fake.bad_json = False
    fake.raise_post = False
    return n


# ---------------------------------------------------------------- v3 定稿约束
check('EVENT_LABELS 已删除 start 事件', 'start' not in notifier.EVENT_LABELS)
check('事件分类表存在且不含 start', 'start' not in notifier.EVENT_CATEGORY
      and notifier.EVENT_CATEGORY.get('error') == '异常告警')
check('正文上限提到 2000', notifier.MAX_MSG == 2000)
check('明细行上限 40 字', notifier.MAX_LINE == 40)

# ---------------------------------------------------------------- 配置
n = fresh()
check('设备 URL 尾斜杠被去掉', n.url == URL.rstrip('/'), n.url)
check('默认启用分类', n.category_enabled is True)
n = fresh({'meow_enabled': 1, 'meow_url': URL, 'meow_category_enabled': 0})
check('可关闭分类', n.category_enabled is False)

# ---------------------------------------------------------------- 纯文本模板
msg = notifier.format_message('《高等数学》已结束 · done · 34分12秒 · 6视频/12题',
                              ['视频/PPT：6/2 ｜ 答题：12成功 1跳过',
                               '累计进度：第3/8章 ｜ 当日视频：21'])
check('首行摘要保留完整', msg.splitlines()[0].startswith('《高等数学》已结束'), msg.splitlines()[0])
check('明细行不超过 40 字', all(len(x) <= 40 for x in msg.splitlines()[1:]))
check('超长明细被截断到 40 字',
      len(notifier.format_message('摘要', ['很' * 90]).splitlines()[1]) == 40)
check('输出是纯文本、无 HTML 标签', notifier.is_plain_text(msg))
check('含标签时判为不纯', notifier.is_plain_text('<table><tr><td>x</td></tr></table>') is False)
check('分类名校验：正常', notifier.valid_category('学习进度') == '学习进度')
check('分类名校验：超 10 字', notifier.valid_category('一二三四五六七八九十十一') == '')
check('分类名校验：空白', notifier.valid_category('   ') == '')
check('分类名校验：控制字符', notifier.valid_category('告警\n换行') == '')

# ---------------------------------------------------------------- 请求构造
n = fresh()
r = n._post_once(n.url, '课程结束 · 高等数学', '摘要\n明细', '学习进度')
c = fake.calls[-1]
check('POST 到去掉尾斜杠的设备 URL', c['url'] == URL.rstrip('/'), c['url'])
check('msgType 固定 text 且在查询串', c['params'] == {'msgType': 'text'}, c['params'])
check('不再发送 htmlHeight', 'htmlHeight' not in c['params'])
check('Body 带 title/msg/category',
      set(c['json'].keys()) == {'title', 'msg', 'category'}, c['json'])
check('请求带超时', c['timeout'] == notifier.TIMEOUT, c['timeout'])
check('status=200 且 data=true 才算成功', r['ok'] is True, r)

n = fresh()
r = n._post_once(n.url, '标题', '内容', '一二三四五六七八九十十一')
c = fake.calls[-1]
check('超长分类不会进 Body（避免业务 400）', 'category' not in c['json'], c['json'])

n = fresh()
r = n._post_once(n.url, '标题', '内容', '')
check('空分类不进 Body', 'category' not in fake.calls[-1]['json'])

# ---------------------------------------------------------------- 响应 / 不重试
for payload, label in [
    ({'status': 200, 'data': False, 'msg': '未送达'}, 'data=false'),
    ({'status': 400, 'data': False, 'msg': '参数缺失'}, '业务 400'),
    ({'status': 403, 'data': False, 'msg': '被拒绝'}, '业务 403'),
    ({'status': 404, 'data': False, 'msg': '昵称不存在'}, '业务 404'),
    ({'status': 429, 'data': False, 'msg': '频率超限'}, '业务 429'),
    ({'status': 500, 'data': False, 'msg': '服务器错误'}, '业务 500'),
]:
    n = fresh()
    fake.payload = payload
    r = n._post_once(n.url, '标题', '内容')
    check('%s 判失败且只调用一次' % label,
          r['ok'] is False and len(fake.calls) == 1, (r, len(fake.calls)))

n = fresh()
fake.bad_json = True
r = n._post_once(n.url, '标题', '内容')
check('非 JSON 判失败且只调用一次', r['ok'] is False and len(fake.calls) == 1)

n = fresh()
fake.raise_post = True
r = n._post_once(n.url, '标题', '内容')
check('超时判失败且只调用一次（不重试）',
      r['ok'] is False and len(fake.calls) == 1 and '不重试' in r['msg'], r)

n = fresh()
for bad in ('http://api.chuckfang.com/98ff9b41', 'https://evil.example.com/98ff9b41', 'not a url'):
    fake.calls = []
    r = n._post_once(bad, '标题', '内容')
    check('非 HTTPS/非官方 host 不发请求: %s' % bad[:30],
          r['ok'] is False and len(fake.calls) == 0)

# ---------------------------------------------------------------- 限流
n = fresh()
now = 10000.0
n._sent = [now - 1.0]
check('3 秒内不重复发送', n._allow(now) is False)
n._sent = [now - 3.1]
check('超过 3 秒允许发送', n._allow(now) is True)
n._sent = [now - 10 - i for i in range(15)]
check('每分钟 15 条上限', n._allow(now) is False)
n._sent = [now - 100 - i for i in range(60)]
check('每小时 60 条上限', n._allow(now) is False)
n._sent = [now - 4000 - i for i in range(1000)]
check('每天 1000 条上限', n._allow(now) is False)
n._sent = []
check('空窗口允许发送', n._allow(now) is True)

# ---------------------------------------------------------------- 脱敏
masked = notifier.sanitize_text('账号 13800001475 使用 sk-abcdefghijklmnop，'
                                '设备 https://api.chuckfang.com/98ff9b41/push')
check('手机号只留后 4 位', '13800001475' not in masked and '1475' in masked, masked)
check('API Key 被打码', 'sk-abcdefghijklmnop' not in masked)
check('设备 URL/昵称被打码', '98ff9b41' not in masked)

# ---------------------------------------------------------------- 错误聚合
n = fresh()
check('同类异常第 1 次', n.error_count('TypeError:main') == 1)
check('同类异常第 2 次', n.error_count('TypeError:main') == 2)
check('不同异常单独计数', n.error_count('ValueError:quiz') == 1)

# ---------------------------------------------------------------- notify 入队
n = fresh({'meow_enabled': 0, 'meow_url': URL})
check('未启用时不入队', n.notify('stop', '结束') is False and n._queue.qsize() == 0)
n = fresh({'meow_enabled': 1, 'meow_url': ''})
check('无设备 URL 时不入队', n.notify('stop', '结束') is False)
n = fresh({'meow_enabled': 1, 'meow_url': URL,
           'meow_events': {'sign_start': False}})
check('事件被关闭时不入队', n.notify('sign_start', '签到') is False)
check('未显式关闭的事件可入队', n.notify('stop', '结束') is True and n._queue.qsize() == 1)

n = fresh()
check('相同事件+内容 60 秒内去重',
      n.notify('error', '同样的错误 13800001475') is True
      and n.notify('error', '同样的错误 13800001475') is False)
item = n._queue.get_nowait()
check('入队消息已脱敏', '13800001475' not in item['msg'] and '1475' in item['msg'])
check('入队带事件分类', item['category'] == '异常告警', item['category'])
check('入队标题按事件定制', item['title'] == '运行错误 · 课程学习助手', item['title'])

n = fresh()
check('可覆盖自定义标题',
      n.notify('course_finish', '摘要', title='课程结束 · 高等数学') is True
      and n._queue.get_nowait()['title'] == '课程结束 · 高等数学')

n = fresh()
check('HTML 标签会被剥离',
      n.notify('error', '<b>摘要</b>\n明细') is True
      and notifier.is_plain_text(n._queue.get_nowait()['msg']))

n = fresh({'meow_enabled': 1, 'meow_url': URL, 'meow_category_enabled': 0})
n.notify('error', '摘要')
check('关闭分类后 category 为空', n._queue.get_nowait()['category'] == '')

n = fresh()
for i in range(notifier.MAX_QUEUE + 5):
    n.notify('error', '错误 %d' % i)
check('队列不超过上限', n._queue.qsize() == notifier.MAX_QUEUE)

# ---------------------------------------------------------------- send_test / 失败不外抛
n = fresh({'meow_enabled': 1, 'meow_url': ''})
check('无 URL 的测试推送返回明确错误', n.send_test()['ok'] is False)
n = fresh()
r = n.send_test('测试')
check('测试推送同步返回且只发一次', r['ok'] is True and len(fake.calls) == 1, r)
check('测试推送也是 text', fake.calls[-1]['params'] == {'msgType': 'text'})

n = fresh()
fake.raise_post = True
try:
    n._process({'url': n.url, 'title': 'T', 'msg': 'M', 'category': '', 'event': 'error'})
    check('_process 失败不抛异常', True)
except Exception as e:
    check('_process 失败不抛异常', False, repr(e))

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), ', '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
sys.exit(0)
