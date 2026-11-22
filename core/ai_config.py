# -*- coding: utf-8 -*-
"""OpenAI 兼容 AI 接口的统一配置与客户端构造（2026-11-10 新增）。

背景：原先 `task/tool/DeepSeekAsk.py` 把 base_url 写死成
`https://api.deepseek.com`、把模型写死成 `deepseek-v4-flash`，并且到处用
`'sk-' in key` 判断 API Key 是否有效。这导致：

  * 只能调 DeepSeek 官方，无法接任意 OpenAI 兼容服务；
  * 很多兼容服务（自建代理 / 本地 Ollama / 部分厂商）的 key 不以 `sk-` 开头，
    会被这些闸门直接拦掉，界面上看起来「配了却完全没生效」。

这里把三件事收敛到一处：
  1. 配置解析（API 地址 / 模型 / 视觉模型 / 超时），带向后兼容默认值；
  2. API Key 有效性判断（不再要求 `sk-` 前缀）；
  3. OpenAI 客户端构造与一次极小连通性测试。

配置优先级：**显式 overrides > 子进程运行时快照(runtime) > 模块默认值**。
默认值仍然指向 DeepSeek，因此老配置不填新字段时行为与改造前完全一致。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ---------------------------------------------------------------- 默认值
DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-v4-flash'

DEFAULTS: Dict[str, Any] = {
    # 任意 OpenAI 兼容服务的基地址；SDK 会自行拼 /chat/completions
    'ai_base_url': DEFAULT_BASE_URL,
    # 文本答题 / 讨论 / 题库兜底使用的模型
    'ai_model': DEFAULT_MODEL,
    # 图片题（多模态）模型；留空表示复用 ai_model
    'ai_vision_model': '',
    # 单次请求超时秒数；0 = 不显式设置（沿用改造前行为）
    'ai_timeout': 0,
}

# 历史遗留哨兵：旧版本用它表示「强制只用 AI、跳过题库」。它不是真实 key，
# 因此有效性判断里必须拒绝，避免拿着假 key 去请求任意服务商。
SENTINEL_KEYS = frozenset({'sk-open_deepseek'})

_KEYS = tuple(DEFAULTS)


def _clean_str(value, default='') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def normalize_base_url(url) -> str:
    """只做最小规范化：去空白、去尾部斜杠；不擅自补协议或 /v1。

    用户可能填 `https://api.openai.com/v1`、`http://localhost:11434/v1`、
    `https://api.deepseek.com` 等，都按原样交给 SDK。
    """
    text = _clean_str(url, DEFAULT_BASE_URL)
    return text.rstrip('/') or DEFAULT_BASE_URL


def is_key_valid(key) -> bool:
    """API Key 是否有效。**不再要求 `sk-` 前缀**。

    有效：任意非空字符串（含自建服务随意填的占位 token）。
    无效：None / 空 / 纯空白 / 历史哨兵 `sk-open_deepseek`。
    """
    if key is None:
        return False
    text = str(key).strip()
    if not text or text in SENTINEL_KEYS:
        return False
    return True


def resolve(overrides: Optional[dict] = None) -> Dict[str, Any]:
    """解析出一份完整配置。overrides 里值为 None / 空串的项不覆盖。"""
    cfg = dict(DEFAULTS)
    try:
        from core import runtime
        for key in _KEYS:
            value = runtime.get(key)
            if value is not None and str(value).strip() != '':
                cfg[key] = value
    except Exception:
        pass
    if isinstance(overrides, dict):
        for key in _KEYS:
            if key in overrides:
                value = overrides[key]
                if value is not None and str(value).strip() != '':
                    cfg[key] = value
    cfg['ai_base_url'] = normalize_base_url(cfg['ai_base_url'])
    cfg['ai_model'] = _clean_str(cfg['ai_model'], DEFAULT_MODEL)
    cfg['ai_vision_model'] = _clean_str(cfg['ai_vision_model'], '')
    try:
        cfg['ai_timeout'] = max(0, int(float(cfg['ai_timeout']) or 0))
    except (TypeError, ValueError):
        cfg['ai_timeout'] = 0
    return cfg


def model_for(cfg: Optional[dict] = None, vision: bool = False) -> str:
    """取文本或视觉模型名；视觉留空时回落到文本模型。"""
    conf = cfg or resolve()
    if vision:
        return _clean_str(conf.get('ai_vision_model'), '') or _clean_str(
            conf.get('ai_model'), DEFAULT_MODEL)
    return _clean_str(conf.get('ai_model'), DEFAULT_MODEL)


def timeout_for(cfg: Optional[dict] = None, default=None):
    """配置里没填超时时返回 default（文本/图片各自的原默认行为）。"""
    conf = cfg or resolve()
    value = conf.get('ai_timeout') or 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else default


def build_client(api_key, cfg: Optional[dict] = None, overrides: Optional[dict] = None):
    """构造 OpenAI 兼容客户端。api_key 必填；base_url / timeout 来自配置。"""
    from openai import OpenAI

    conf = overrides if (overrides and cfg is None) else (cfg or resolve(overrides))
    kwargs = {
        'api_key': _clean_str(api_key),
        'base_url': normalize_base_url(conf.get('ai_base_url')),
    }
    timeout = timeout_for(conf)
    if timeout:
        kwargs['timeout'] = timeout
    return OpenAI(**kwargs)


def describe(cfg: Optional[dict] = None) -> str:
    """给日志用的一行配置摘要，绝不包含 API Key。"""
    conf = cfg or resolve()
    vision = model_for(conf, vision=True)
    return ('AI 接口: %s | 模型: %s | 视觉模型: %s | 超时: %s'
            % (normalize_base_url(conf.get('ai_base_url')),
               model_for(conf),
               vision,
               ('%ss' % conf.get('ai_timeout')) if conf.get('ai_timeout') else '默认'))


def test_connection(api_key, overrides: Optional[dict] = None, max_tokens=1) -> dict:
    """发一个极小的 chat 请求验证配置，返回 {ok, msg, reply, config}。

    只有用户在界面主动点「测试连接」时才会调用；失败不重试、不抛异常。
    """
    out = {'ok': False, 'msg': '', 'reply': '', 'config': ''}
    if not is_key_valid(api_key):
        out['msg'] = '请先填写 API Key'
        return out
    conf = resolve(overrides)
    out['config'] = describe(conf)
    try:
        client = build_client(api_key, cfg=conf)
        kwargs = {
            'model': model_for(conf),
            'messages': [{'role': 'user', 'content': 'hi'}],
            'stream': False,
        }
        try:
            kwargs['max_tokens'] = max_tokens
        except Exception:
            pass
        timeout = timeout_for(conf, default=15)
        if timeout:
            kwargs['timeout'] = min(timeout, 20) if conf.get('ai_timeout') else 15
        response = client.chat.completions.create(**kwargs)
        reply = ''
        try:
            reply = (response.choices[0].message.content or '').strip()
        except Exception:
            pass
        out['ok'] = True
        out['reply'] = reply[:80]
        out['msg'] = '连接成功（模型 %s）' % model_for(conf)
    except Exception as e:
        out['msg'] = '%s: %s' % (type(e).__name__, str(e)[:260])
    return out


# 给日志脱敏用：任意 key 都可能不以 sk- 开头，导出前把当前 key 注册进来
_EXTRA_SECRETS = set()


def register_secret(value) -> None:
    """注册一个需要额外打码的明文（例如当前配置的 API Key）。"""
    if value is None:
        return
    text = str(value).strip()
    if len(text) >= 8:
        _EXTRA_SECRETS.add(text)


def extra_secrets():
    return tuple(_EXTRA_SECRETS)


def secret_pattern(value):
    """返回用于精确替换的正则（转义后的字面量），None 表示不可用。"""
    if not value:
        return None
    return re.compile(re.escape(str(value)))
