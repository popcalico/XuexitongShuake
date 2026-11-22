# -*- coding: utf-8 -*-
"""MeoW 消息推送（api.chuckfang.com）—— v3 纯文本版。

接口规范来自 fkccoding/meow-push-skill 与在线 API 文档，改动前务必遵守：

- `POST /{nickname}` + JSON Body `{"title","msg","category"}`；`msgType` 必须在查询串。
- **只发 `msgType=text`**：用户真机确认 HTML/表格在 App 内正常，但系统通知栏把标签
  当纯文本显示、排版很乱，因此全面放弃 html 与 htmlHeight。别改回去。
- 消息统一「首行高信息密度摘要 + 后续明细行」：系统通知栏通常只显示第一行。
- HTTP 200 不代表成功，必须解析 JSON 校验 `status == 200 && data === true`。
- 推送不幂等：超时 / 断连 / 500 / 429 / 非 JSON 一律**不自动重试**。
- 限流 3 秒 1 条 / 每分钟 15 / 每小时 60 / 每天 1000；本地先挡。
- 后台线程 + 有界队列，失败只写 applog，绝不影响刷课主流程。
- 设备 URL 是凭据：只从本机配置读取，绝不硬编码、绝不写进 example / 日志包。

用法：
    from core.notifier import Notifier, format_message
    Notifier.instance().configure(config.load_account())
    Notifier.instance().notify('course_finish',
                               format_message(headline, lines), title='课程结束 · 高等数学')
"""
from __future__ import annotations

import queue
import re
import threading
import time
from urllib.parse import urlsplit

from core import applog

try:
    import requests
except ImportError:  # 源码环境未装依赖时也不能让主流程崩
    requests = None

API_HOST = 'api.chuckfang.com'
MIN_INTERVAL = 3.0
WINDOW_LIMITS = ((60.0, 15), (3600.0, 60), (86400.0, 1000))
MAX_QUEUE = 200
TIMEOUT = 5
DEDUP_WINDOW = 60.0
MAX_MSG = 2000
MAX_LINE = 40
CATEGORY_LIMIT = 10

# 不再有 start 事件（用户要求去掉「开始学习」推送）
EVENT_LABELS = {
    'stop': '手动结束',
    'account_finish': '账号结束',
    'all_finish': '全部结束',
    'course_finish': '课程结束',
    'course_abnormal': '课程异常结束',
    'error': '运行错误',
    'sign_start': '签到开始',
    'sign_stop': '签到停止',
    'export_done': '日志导出完成',
    'test': '测试推送',
}

EVENT_CATEGORY = {
    'stop': '学习进度',
    'account_finish': '学习进度',
    'all_finish': '学习进度',
    'course_finish': '学习进度',
    'course_abnormal': '异常告警',
    'error': '异常告警',
    'sign_start': '签到',
    'sign_stop': '签到',
    'export_done': '工具',
    'test': '工具',
}

BIZ_CODES = {
    400: '标题或消息缺失/无效（不重试）',
    403: '内容被策略拒绝（不重试）',
    404: '接收者昵称未注册（请核对设备 URL）',
    429: '发送或 IP 频率超限（不自动重试）',
    500: '服务器错误，送达结果不确定（不自动重试）',
}

_HTML_TAG_RE = re.compile(r'<[A-Za-z/][^>]*>')


def sanitize_text(text: str) -> str:
    """发送前脱敏：复用日志导出的规则，再额外打码设备 URL / 昵称。"""
    value = text or ''
    try:
        from core.log_export import sanitize
        value = sanitize(value)
    except Exception:
        pass
    value = re.sub(r'(api\.chuckfang\.com/)[A-Za-z0-9_\-]{4,}',
                   r'\1***', value, flags=re.I)
    return value


def format_message(headline, lines=None) -> str:
    """拼一条纯文本通知：首行摘要（通知栏可见）+ 明细行（每行 ≤40 字）。

    不使用 HTML 标签、表格或空格对齐——系统通知栏会把它们显示得很乱。
    """
    first = re.sub(r'\s+', ' ', str(headline or '').strip())
    parts = [first[:MAX_LINE * 2]] if first else []
    for line in (lines or []):
        item = re.sub(r'\s+', ' ', str(line or '').strip())
        if item:
            parts.append(item[:MAX_LINE])
    return '\n'.join(parts)


def is_plain_text(text: str) -> bool:
    """给纯桩测试用：输出里不能残留任何 HTML 标签。"""
    return not bool(_HTML_TAG_RE.search(text or ''))


def valid_category(value) -> str:
    """分类名 ≤10 个 Unicode 字符、无控制字符；超长返回空串（服务端会返回 400）。"""
    cat = str(value or '').strip()
    if not cat or len(cat) > CATEGORY_LIMIT:
        return ''
    if any(ord(ch) < 32 for ch in cat):
        return ''
    return cat


class Notifier:
    """单例推送器；notify() 只入队，不代表已送达。"""

    _instance = None
    _singleton_lock = threading.Lock()

    def __init__(self, autostart: bool = True):
        self._lock = threading.Lock()
        self.enabled = False
        self.url = ''
        self.category_enabled = True
        self.events = {}
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._sent = []
        self._recent = {}
        self._started = False
        self._autostart = autostart
        self._error_day = ''
        self._error_counts = {}

    @classmethod
    def instance(cls) -> 'Notifier':
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ------------------------------------------------------------ 配置
    def configure(self, cfg: dict) -> None:
        """从扁平配置（global + account）读取；可在 GUI 保存设置后重复调用。"""
        with self._lock:
            self.enabled = bool(cfg.get('meow_enabled', 0))
            self.url = str(cfg.get('meow_url') or '').strip().rstrip('/')
            self.category_enabled = bool(cfg.get('meow_category_enabled', 1))
            events = cfg.get('meow_events') or {}
            self.events = dict(events) if isinstance(events, dict) else {}
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if not self._autostart:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._worker, daemon=True, name='meow-notifier').start()

    # ------------------------------------------------------------ 错误聚合
    def error_count(self, tag: str) -> int:
        """返回某个异常 tag 今日累计次数（每次调用 +1），供错误首行显示。"""
        day = time.strftime('%Y-%m-%d')
        with self._lock:
            if self._error_day != day:
                self._error_day = day
                self._error_counts = {}
            key = str(tag or '未知错误')[:80]
            self._error_counts[key] = self._error_counts.get(key, 0) + 1
            return self._error_counts[key]

    # ------------------------------------------------------------ 对外入口
    def notify(self, event: str, msg: str, detail: str = '', level: str = 'info',
               category=None, title=None) -> bool:
        """入队一条通知。返回是否入队（**不代表送达**）。失败静默，绝不抛给主流程。"""
        try:
            with self._lock:
                if not self.enabled or not self.url:
                    return False
                if self.events.get(event) is False:
                    return False
                url = self.url
                use_category = self.category_enabled

            body = sanitize_text(str(msg or '').strip())
            if detail:
                body += '\n' + sanitize_text(str(detail).strip())
            body = body[:MAX_MSG]
            if not body.strip():
                return False
            if not is_plain_text(body):
                body = _HTML_TAG_RE.sub('', body)

            key = (str(event), body[:80])
            now = time.time()
            with self._lock:
                if now - self._recent.get(key, 0) < DEDUP_WINDOW:
                    return False
                self._recent[key] = now

            cat = valid_category(category if category is not None
                                 else EVENT_CATEGORY.get(event)) if use_category else ''
            head = str(title).strip() if title else (
                EVENT_LABELS.get(event, str(event)) + ' · 课程学习助手')

            item = {
                'url': url,
                'title': head,
                'msg': body,
                'category': cat,
                'event': event,
            }
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                self._queue.put_nowait(item)
            self._ensure_worker()
            return True
        except Exception as e:
            try:
                applog.warn('MeoW 通知入队失败（忽略）：%r' % (e,))
            except Exception:
                pass
            return False

    def send_test(self, msg: str = '这是一条测试推送') -> dict:
        """界面「测试推送」按钮：同步发一次，返回结果供弹窗展示。不自动重试。"""
        with self._lock:
            if not self.url:
                return {'ok': False, 'status': None, 'msg': '未填写设备 URL'}
            url = self.url
            use_category = self.category_enabled
        cat = valid_category(EVENT_CATEGORY.get('test')) if use_category else ''
        return self._post_once(url, '测试推送 · 课程学习助手',
                               sanitize_text(msg)[:MAX_MSG], cat)

    # ------------------------------------------------------------ 内部
    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                self._process(item)
            except Exception as e:
                try:
                    applog.warn('MeoW 推送线程异常（忽略）：%r' % (e,))
                except Exception:
                    pass

    def _allow(self, now: float = None) -> bool:
        """四档滑动窗口限流；3 秒最小间隔 + 15/min + 60/h + 1000/day。"""
        now = now or time.time()
        with self._lock:
            self._sent = [t for t in self._sent if now - t < 86400]
            times = self._sent
            if times and now - times[-1] < MIN_INTERVAL:
                return False
            for window, limit in WINDOW_LIMITS:
                if sum(1 for t in times if now - t < window) >= limit:
                    return False
            return True

    def _process(self, item: dict) -> None:
        now = time.time()
        if not self._allow(now):
            try:
                applog.warn('MeoW 推送被本地限流拦下：%s' % item.get('event'))
            except Exception:
                pass
            return
        with self._lock:
            self._sent.append(now)   # 发出即计入，结果不确定也不重试
        result = self._post_once(item['url'], item['title'], item['msg'],
                                 item.get('category') or '')
        if result.get('ok'):
            try:
                applog.info('MeoW 推送成功：event=%s' % item.get('event'))
            except Exception:
                pass
        else:
            try:
                applog.warn('MeoW 推送失败（不重试）：event=%s status=%s msg=%s'
                            % (item.get('event'), result.get('status'), result.get('msg')))
            except Exception:
                pass

    @staticmethod
    def _post_once(url: str, title: str, msg: str, category: str = '') -> dict:
        """单次 POST，msgType 固定 text；任何失败都返回结果 dict，绝不重试/抛异常。"""
        if requests is None:
            return {'ok': False, 'status': None, 'msg': 'requests 未安装'}
        try:
            parts = urlsplit(url)
        except Exception:
            return {'ok': False, 'status': None, 'msg': '设备 URL 格式错误'}
        if parts.scheme != 'https' or (parts.hostname or '').lower() != API_HOST:
            return {'ok': False, 'status': None,
                    'msg': '设备 URL 必须是 api.chuckfang.com 的 HTTPS 地址'}
        body = {'title': title, 'msg': msg}
        cat = valid_category(category)
        if cat:
            body['category'] = cat
        try:
            resp = requests.post(url, params={'msgType': 'text'},
                                 json=body, timeout=TIMEOUT)
        except Exception as e:
            return {'ok': False, 'status': None,
                    'msg': '请求异常/超时（送达结果不确定，不重试）：%r' % (e,)}
        try:
            data = resp.json()
        except Exception:
            return {'ok': False, 'status': getattr(resp, 'status_code', None),
                    'msg': '响应不是 JSON（送达结果不确定，不重试）'}
        if not isinstance(data, dict):
            return {'ok': False, 'status': getattr(resp, 'status_code', None),
                    'msg': '响应格式异常（不重试）'}
        status = data.get('status')
        ok = (status == 200 and data.get('data') is True)
        return {
            'ok': bool(ok),
            'status': status,
            'data': data.get('data'),
            'msg': data.get('msg') or BIZ_CODES.get(status, ''),
        }

    @staticmethod
    def sanitize(text: str) -> str:
        return sanitize_text(text)
