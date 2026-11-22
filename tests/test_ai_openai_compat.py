# -*- coding: utf-8 -*-
"""OpenAI 兼容 AI 接口回归测试（纯桩，绝不联网、不开浏览器）。

覆盖：
  1. 默认配置仍指向 DeepSeek —— 老配置不填新字段时行为不变；
  2. 自定义 base_url / model / vision / timeout 能生效；
  3. API Key 不再要求 `sk-` 前缀，任意非空字符串可用；哨兵与空值拒绝；
  4. DeepSeekAsk / DeepSeekAskImage 用配置的模型与地址发请求，失败不抛异常；
  5. 配置贯通：config_manager / runtime / setting_panel / selftest 清单都带新键；
  6. 日志导出对任意格式 Key 打码。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import ai_config, runtime  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- 1) 默认兼容
runtime.reset()
cfg = ai_config.resolve()
check('默认 base_url 仍是 DeepSeek',
      cfg['ai_base_url'] == 'https://api.deepseek.com', cfg['ai_base_url'])
check('默认模型仍是 deepseek-v4-flash',
      ai_config.model_for(cfg) == 'deepseek-v4-flash', ai_config.model_for(cfg))
check('视觉模型留空时回落到文本模型',
      ai_config.model_for(cfg, vision=True) == 'deepseek-v4-flash')
check('默认不显式设超时（沿用 SDK 默认）', ai_config.timeout_for(cfg) is None)

# ---------------------------------------------------------------- 2) 自定义配置
custom = {
    'ai_base_url': 'http://localhost:11434/v1/',
    'ai_model': 'qwen-plus',
    'ai_vision_model': 'qwen-vl-max',
    'ai_timeout': 30,
}
c2 = ai_config.resolve(custom)
check('自定义地址去尾部斜杠',
      c2['ai_base_url'] == 'http://localhost:11434/v1', c2['ai_base_url'])
check('自定义文本模型生效', ai_config.model_for(c2) == 'qwen-plus')
check('自定义视觉模型生效', ai_config.model_for(c2, vision=True) == 'qwen-vl-max')
check('自定义超时生效', ai_config.timeout_for(c2) == 30)

# runtime 快照装载（子进程路径：account_info.json -> runtime.configure）
runtime.reset()
runtime.configure(custom)
c3 = ai_config.resolve()
check('runtime 快照里的配置被子进程读到',
      c3['ai_base_url'] == 'http://localhost:11434/v1' and ai_config.model_for(c3) == 'qwen-plus',
      c3)
check('显式 overrides 优先于 runtime',
      ai_config.resolve({'ai_model': 'gpt-4o-mini'})['ai_model'] == 'gpt-4o-mini')

# 空值不覆盖
c4 = ai_config.resolve({'ai_base_url': '', 'ai_model': None, 'ai_timeout': ''})
check('空字符串/None 不覆盖 runtime 值',
      c4['ai_base_url'] == 'http://localhost:11434/v1', c4['ai_base_url'])
check('地址规范化：空白回落默认',
      ai_config.resolve({'ai_base_url': '   '})['ai_base_url'] != '   ')
check('超时非法值回落 0', ai_config.resolve({'ai_timeout': 'abc'})['ai_timeout'] == 0)
runtime.reset()

# ---------------------------------------------------------------- 3) Key 有效性
check('sk- 开头的 Key 有效', ai_config.is_key_valid('sk-abc123') is True)
check('不以 sk- 开头的 Key 有效（本次核心）',
      ai_config.is_key_valid('my-custom-token-9527') is True)
check('自建服务占位 Key 有效', ai_config.is_key_valid('ollama') is True)
check('空 Key 无效', ai_config.is_key_valid('') is False)
check('None 无效', ai_config.is_key_valid(None) is False)
check('纯空白无效', ai_config.is_key_valid('   ') is False)
check('历史哨兵 sk-open_deepseek 无效',
      ai_config.is_key_valid('sk-open_deepseek') is False)

# ---------------------------------------------------------------- 4) 客户端构造
captured = {}


class _FakeCompletions:
    def create(self, **kwargs):
        captured['create'] = kwargs

        class _Msg:
            content = '["A"]'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _FakeClient:
    def __init__(self, **kwargs):
        captured['client'] = kwargs
        self.chat = type('Chat', (), {'completions': _FakeCompletions()})()


import openai  # noqa: E402
_orig_openai = openai.OpenAI
openai.OpenAI = _FakeClient
try:
    client = ai_config.build_client('token-without-sk', cfg=ai_config.resolve({
        'ai_base_url': 'https://api.example.com/v1',
        'ai_model': 'gpt-4o-mini',
        'ai_timeout': 12,
    }))
    check('客户端用自定义 base_url',
          captured['client']['base_url'] == 'https://api.example.com/v1',
          captured['client'])
    check('客户端用自定义 Key（不限前缀）',
          captured['client']['api_key'] == 'token-without-sk')
    check('客户端用自定义超时', captured['client'].get('timeout') == 12,
          captured['client'])
    ai_config.build_client('x', cfg=ai_config.resolve({'ai_timeout': 0}))
    check('超时为 0 时不传 timeout 参数', 'timeout' not in captured['client'])

    # ---- DeepSeekAsk 走配置里的模型
    from task.tool import DeepSeekAsk as ds
    runtime.reset()
    runtime.configure({'ai_base_url': 'https://api.example.com/v1',
                       'ai_model': 'qwen-plus', 'ai_timeout': 20})
    ds.quota.reset()
    captured.clear()
    answer = ds.DeepSeekAsk('my-token', '【单选题】1+1=? A.1 B.2', '单选题')
    check('DeepSeekAsk 返回解析后的答案', answer == '["A"]', answer)
    check('DeepSeekAsk 用配置的模型名',
          captured.get('create', {}).get('model') == 'qwen-plus', captured.get('create'))
    check('DeepSeekAsk 用配置的超时',
          captured.get('create', {}).get('timeout') == 20, captured.get('create'))

    # ---- 图片题用独立视觉模型
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.write(b'\x89PNG\r\n\x1a\n' + b'0' * 40)
    tmp.close()
    runtime.configure({'ai_vision_model': 'qwen-vl-max'})
    ds.quota.reset()
    captured.clear()
    img = ds.DeepSeekAskImage('my-token', '识别这道题', [tmp.name])
    check('图片题返回模型原文', img == '["A"]', img)
    check('图片题用独立视觉模型',
          captured.get('create', {}).get('model') == 'qwen-vl-max', captured.get('create'))
    os.unlink(tmp.name)

    # ---- 请求失败不抛异常
    def _boom(**kwargs):
        raise RuntimeError('模拟 401')

    _FakeCompletions.create = staticmethod(_boom)
    ds.quota.reset()
    check('请求异常时 DeepSeekAsk 返回 [] 不抛',
          ds.DeepSeekAsk('my-token', '题目', '单选题') == '[]')
    ds.quota.reset()
    check('请求异常时 DeepSeekAskImage 返回空串不抛',
          ds.DeepSeekAskImage('my-token', '题目', [__file__]) == '')
finally:
    openai.OpenAI = _orig_openai
    runtime.reset()

# ---------------------------------------------------------------- 5) 配置贯通
import re  # noqa: E402
cfg_src = open('core/config_manager.py', encoding='utf-8').read()
rt_src = open('core/runtime.py', encoding='utf-8').read()
ui_src = open('gui/setting_panel.py', encoding='utf-8').read()
sel_src = open('start.py', encoding='utf-8').read()
for key in ('ai_base_url', 'ai_model', 'ai_vision_model', 'ai_timeout'):
    check('config_manager 含 %s' % key, key in cfg_src)
    check('runtime.DEFAULTS 含 %s' % key, key in rt_src)
    check('设置页表单含 %s' % key, key in ui_src)
check('selftest 清单含 core.ai_config', "'core.ai_config'" in sel_src)
check('设置页有测试连接按钮', '_test_ai' in ui_src and '测试连接' in ui_src)
# API Key 与地址/模型放在同一个子面板，且模式切换不再隐藏它（2026-11-10 22:xx）
check('API Key 与 AI 地址由同一 box 创建',
      "self.API_entry = ctk.CTkEntry(box" in ui_src
      and "self.ai_base_url_entry = ctk.CTkEntry(" in ui_src)
check('模式切换不再 grid/grid_forget API 控件',
      not re.search(r'(API_label|API_entry|show_api_btn)\.grid_forget\(\)', ui_src))
check('shua_ti_choice 不再因随机答题隐藏 Key',
      'API Key 常驻「AI 接口」子面板' in ui_src)

# 源码里不再有硬编码的 DeepSeek 地址/模型（默认值除外）
ds_src = open('task/tool/DeepSeekAsk.py', encoding='utf-8').read()
check('DeepSeekAsk 不再 import OpenAI 硬编码地址',
      'base_url="https://api.deepseek.com"' not in ds_src)
check('DeepSeekAsk 不再用 sk- 前缀闸门',
      re.search(r"if\s+'sk-'\s+in", ds_src) is None)
check('DeepSeekAsk 保留旧函数名与 MODEL 常量',
      'def DeepSeekAsk(' in ds_src and 'def DeepSeekAskImage(' in ds_src
      and re.search(r'^MODEL\s*=', ds_src, re.M) is not None)

# ---------------------------------------------------------------- 6) 日志脱敏
from core import log_export  # noqa: E402
ai_config._EXTRA_SECRETS.clear()
ai_config.register_secret('my-custom-token-9527')
masked = log_export.sanitize('日志里出现了 my-custom-token-9527 这个 Key')
check('任意格式 Key 在导出时被精确打码',
      'my-custom-token-9527' not in masked and '***' in masked, masked)
check('打码不误伤普通文本',
      log_export.sanitize('今天完成了 3 门课程') == '今天完成了 3 门课程')
ai_config._EXTRA_SECRETS.clear()

print('---')
if FAILS:
    print('FAILED=%d %s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
