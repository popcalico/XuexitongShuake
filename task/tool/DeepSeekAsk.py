# -*- coding: utf-8 -*-
"""AI 答题客户端（2026-11-10 改造为 OpenAI 兼容）。

历史：本模块原先写死 `base_url=https://api.deepseek.com`、`model=deepseek-v4-flash`，
并用 `'sk-' in key` 判断 Key 是否有效，只能调 DeepSeek 官方。

现在：地址 / 模型 / 超时全部由 `core.ai_config` 从配置读取，可接入任意
OpenAI 兼容服务（DeepSeek、OpenAI、通义/豆包兼容端点、自建代理、本地 Ollama 等）。
模块名、函数名 `DeepSeekAsk` / `DeepSeekAskImage`、常量 `MODEL` 均保持不变，
避免改动十余处调用方与既有纯桩测试。

默认配置仍指向 DeepSeek，因此不填新字段时行为与改造前一致。
"""
import base64

from task.tool import color
from task.tool import quota
from core import ai_config

# 兼容旧引用（tests/manual_vision_probe.py 会 import 它）
MODEL = ai_config.DEFAULT_MODEL


def _image_mime(path):
    return 'image/jpeg' if str(path).lower().endswith(('.jpg', '.jpeg')) else 'image/png'


def _log_config():
    """每次实际请求前打一行配置摘要，便于排查「配了却不生效」。绝不含 Key。"""
    try:
        print(color.yellow(ai_config.describe()), flush=True)
    except Exception:
        pass


def _chat(api_key, messages, *, vision=False, timeout_default=None, temperature=None):
    """统一的单次 chat 调用；失败一律返回 None，由调用方决定降级策略。"""
    cfg = ai_config.resolve()
    kwargs = {
        'model': ai_config.model_for(cfg, vision=vision),
        'messages': messages,
        'stream': False,
    }
    if temperature is not None:
        kwargs['temperature'] = temperature
    timeout = ai_config.timeout_for(cfg, default=timeout_default)
    if timeout:
        kwargs['timeout'] = timeout
    try:
        client = ai_config.build_client(api_key, cfg=cfg)
        response = client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or '').strip()
    except Exception as e:
        print(color.red('AI 请求失败 [%s] %s: %s'
                        % (ai_config.normalize_base_url(cfg.get('ai_base_url')),
                           kwargs['model'], str(e)[:300])), flush=True)
        return None


def DeepSeekAskImage(API_KEY, prompt, image_paths, timeout=90):
    """多模态：把题目截图（题干+选项）交给视觉模型，返回模型原文。

    图片以 base64 data URL 内联发送（OpenAI 兼容格式），不依赖公网图床。
    失败（无 key / 读文件失败 / 接口报错）时返回空串，由调用方跳过或随机。
    """
    if not ai_config.is_key_valid(API_KEY):
        return ''
    # 额度闸门：图片题每题只问 1~2 次；指纹带图片路径，避免所有图片题共用一个指纹
    if not quota.acquire(quota.question_key(prompt + '|' + '|'.join(map(str, image_paths))),
                         'deepseek-image'):
        return ''

    content = [{"type": "text", "text": prompt}]
    for path in image_paths:
        try:
            with open(path, 'rb') as f:
                blob = f.read()
        except OSError as e:
            print(color.red(f'读取题目截图失败: {e}'), flush=True)
            continue
        encoded = base64.b64encode(blob).decode('ascii')
        content.append({
            "type": "image_url",
            "image_url": {"url": 'data:%s;base64,%s' % (_image_mime(path), encoded)},
        })
    if len(content) == 1:
        return ''

    _log_config()
    answer = _chat(API_KEY, [{"role": "user", "content": content}],
                   vision=True, timeout_default=timeout, temperature=0.2)
    if answer is None:
        return ''
    print(color.yellow(f'图片题模型返回: {answer!r}'), flush=True)
    return answer


def DeepSeekAsk(API_KEY, title, _type):
    """按题型构造 prompt，返回模型原文（调用方再解析成答案列表）。

    Key 无效、被限流、请求失败都返回 '[]'，绝不抛异常中断答题流程。
    """
    if _type in ['单选题', '判断题']:
        prompt = ('不要其他话语，我仅需要这些题目的选择答案，返回的答案必须是列表格式，列表中的每个元素都是字符串,'
                  '答案必须从给你的选项中选择，并且只能是其中一个答案，不能自己编造答案,'
                  '最终你给出的答案格式应当类似于下面这样：["答案"]\n')
    elif _type == '多选题':
        prompt = ('不要其他话语，我仅仅需要这些题目的选择答案哟，返回的答案必须是列表格式，列表中的每个元素都是字符串,'
                  '答案必须从给你的选项中选择，可以选择多个答案，不能自己编造答案,'
                  '最终你给出的答案格式应当类似于下面这样：["答案1","答案2",...]\n')
    elif _type in ['简答题', '论述题']:
        prompt = ('不要其他话语，我仅仅需要这些题目的选择答案哟，返回的答案必须是列表格式，列表中的每个元素都是字符串,'
                  '只需给出文字答案即可,不要有多余的内容,答案要都在列表内，不要在列表后再加什么内容,'
                  '最终你给出的答案格式应当类似于下面这样：["答案"]\n')
    elif _type == '填空题':
        prompt = ('不要其他话语，我仅仅需要这些题目的选择答案哟，返回的答案必须是列表格式，列表中的每个元素都是字符串,'
                  '只需给出文字答案即可,不要有多余的内容,答案要都在列表内，不要在列表后再加什么内容,有多少个空，列表中就要有多少个元素,'
                  '最终你给出的答案格式应当类似于下面这样：["答案"]或["答案1","答案2",...]\n')
    else:
        prompt = '''请根据以下题目要求回答问题：
                                                  1.不要其他话语，我仅仅需要这些题目的选择答案哟

                            2.所有题目的答案必须从选项 A、B、C、D 中选择。多选题必须选多个答案，其答案紧挨着即可不用用'/'隔开

                            3.对于判断题，答案只能是 A 或 B，分别对应题目中的 A（对） 和 B（错）。

                            4.简答题只需给出文字答案即可,不要有多余的内容,论述题同样，论述题的答案字数在五十字左右,不要出现字母或题号数字

                            5.填空题的每个答案用“,”隔开

                            6.如果题目重复出现，只需回答一次，不要重复回答。

                            7.请严格按照题目编号顺序给出答案,1,2,3,4这样的题目序号顺序给出选择的答案。
                            每个题目的答案用“/”隔开,答案中不要题号，只需要答案，不用重复问题,不要给多了答案，有多少道题就给多少答案，
                            /的数量是能保持比题目数量少一个，不能多也不能少,不要有换行符

                            8.最终你给出的答案格式应当类似于下面这样：A/B,C,D/C/论述题答案/D/填空题答案1,填空题答案2,...

                            9.请你注意每道题目开头的【】里面内容判断这道题目是什么类型的题目
                                                    \n'''
    prompt += title

    if not ai_config.is_key_valid(API_KEY):
        print(color.red('未配置有效的 API Key，跳过 AI 作答'), flush=True)
        return '[]'
    # 额度闸门：超了直接返回空列表，由调用方按「没答案」处理
    if not quota.acquire(quota.question_key(title), 'deepseek'):
        return '[]'

    _log_config()
    answer = _chat(API_KEY, [{"role": "user", "content": prompt}], temperature=1.3)
    if answer is None:
        return '[]'
    import re
    match = re.search(r'\[(.*?)\]', answer)
    if match:
        answer = match.group(0)
    print(answer, flush=True)
    return answer
