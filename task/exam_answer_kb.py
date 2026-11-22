# -*- coding: utf-8 -*-
"""考试答案解析工具（2026-11-10 借鉴开源实现重写，纯函数、无浏览器依赖）。

来源与许可（只借鉴算法思路与页面事实，代码为本工程自行实现）：
  * ocsjs/ocsjs (MIT) — packages/core/src/core/worker/resolvers/judgement.ts
    判断题正/误词表、isJudgementOptions：仅当恰好 2 个选项且分别含对/错词才判判断。
    https://github.com/ocsjs/ocsjs
  * ocsjs/ocsjs (MIT) — resolvers/multiple.ts
    多选题「答案⊇选项」的严格匹配方向，避免 TCP 误选 TCP/IP协议；相似选项消歧。
  * pang-qin/chaoxing-toolkit（无 LICENSE，仅参考思路）— tiku/app/matching.py
    normalize_question：去括号/【】/空白/下划线；文本+选项加权评分。
  * fysh1010/chaoxing-script (MIT) / pang-qin/chaoxing-toolkit — 学习通脚本.js
    判断题按 :radio[value=true]、填空题多空按 | 分隔、AI 按题型定制输出。

注意：这些开源脚本面向「旧版整页多题」页面；本工程真机采集的是
「新版逐题页」（span[data] 乱序 + hidden #answer<qid>）。因此本模块只做
**答案解析层**，DOM 兜底常量在 task/do_exam.py 里且标注未经真机验证。
"""
from __future__ import annotations

import ast
import re
from difflib import SequenceMatcher

# ---------------------------------------------------------------- 判断题词表
# 借鉴 ocsjs judgement.ts（MIT），按中文考试常见写法补充
JUDGE_TRUE_WORDS = frozenset({
    '是', '对', '正确', '确定', '√', '对的', '是的', '正确的',
    'true', 't', 'yes', 'y', '1', 'a', '对', '正确',
})
JUDGE_FALSE_WORDS = frozenset({
    '非', '否', '错', '错误', '×', 'x', 'X', '错的', '不对',
    '不正确的', '不正确', '不是', '不是的', 'false', 'f', 'no', 'n', '0', 'b',
})

_BRACKET_RE = re.compile(r'\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】')
_SPACE_RE = re.compile(r'\s+')


def normalize_question(text) -> str:
    """题干/选项归一化：去括号注释、去空白与下划线，便于比对。

    借鉴 pang-qin/chaoxing-toolkit 的 matching.py（仅参考思路）。
    """
    if text is None:
        return ''
    s = str(text)
    s = _BRACKET_RE.sub('', s)
    s = s.replace('_', '').replace('　', '')
    return _SPACE_RE.sub('', s).strip()


def _clear(text) -> str:
    """去掉标点与常见修饰，保留用于判断题词匹配的核心字符。"""
    s = normalize_question(text).lower()
    return re.sub(r'[.,;:!?、，。；：！？\s\'"]', '', s)


def judge_side(text) -> str:
    """返回 'true' / 'false' / ''：判断一段文本表达的是对还是错。"""
    s = _clear(text)
    if not s:
        return ''
    # 先判否定词，避免「不正确」被「正确」抢先命中
    if s in JUDGE_FALSE_WORDS or any(w in s for w in ('不正确', '不对', '不是', '错误', '错的')):
        return 'false'
    if s in JUDGE_TRUE_WORDS or any(w in s for w in ('正确', '对的', '是的')):
        return 'true'
    return ''


def is_judgement_options(option_texts) -> bool:
    """恰好 2 个选项、且分别含对/错语义时才认定为判断题。

    借鉴 ocsjs isJudgementOptions（MIT）。比只看 judgeoption class 更鲁棒。
    """
    texts = [t for t in (option_texts or []) if isinstance(t, str) and t.strip()]
    if len(texts) != 2:
        return False
    sides = {judge_side(t) for t in texts}
    return sides == {'true', 'false'}


def strict_option_match(answer_text, option_text):
    """严格方向匹配：答案归一化后等于或包含选项才算命中。

    借鉴 ocsjs resolveMultiple 的核心约束 —— 不取「选项包含答案」方向，
    否则答案 "TCP" 会误命中选项 "TCP/IP协议"。
    """
    a = normalize_question(answer_text)
    o = normalize_question(option_text)
    if not a or not o:
        return False
    return a == o or o in a


def disambiguate_similar(matches, threshold=0.92):
    """消歧：多个候选选项文本高度相似时只保留得分最高的那个。

    matches: [(index, score, text), ...]；返回保留下来的 index 列表。
    借鉴 ocsjs 的「符号并列 / 相似选项消歧」思路。
    """
    kept = []
    for idx, score, text in sorted(matches, key=lambda x: -x[1]):
        norm = normalize_question(text)
        conflict = False
        for kept_idx, _kept_score, kept_text in kept:
            if SequenceMatcher(None, norm, normalize_question(kept_text)).ratio() >= threshold:
                conflict = True
                break
        if not conflict:
            kept.append((idx, score, text))
    return [k[0] for k in kept]


def resolve_by_text(answers, option_texts, *, strict=True, min_similarity=0.62):
    """把题库/AI 返回的答案（字母或选项文本）映射到选项下标列表。

    优先按纯字母解释；文本答案则先做严格方向匹配，失败再回落相似度匹配。
    返回下标列表（升序、去重）。
    """
    options = [str(o or '') for o in (option_texts or [])]
    if not options:
        return []
    letters = [chr(ord('A') + i) for i in range(len(options))]
    picked = set()

    for raw in answers or []:
        s = str(raw).strip()
        if not s:
            continue
        # 纯字母 / 逗号分隔字母串
        if re.fullmatch(r'[A-Za-z](?:\s*[,，、/|]\s*[A-Za-z])*', s):
            for ch in re.findall(r'[A-Za-z]', s.upper()):
                if ch in letters:
                    picked.add(letters.index(ch))
            continue

        norm_ans = normalize_question(s)
        candidates = []
        for i, opt in enumerate(options):
            if not opt:
                continue
            if strict and strict_option_match(s, opt):
                candidates.append((i, 1.0, opt))
            else:
                score = SequenceMatcher(None, norm_ans, normalize_question(opt)).ratio()
                if score >= min_similarity:
                    candidates.append((i, score, opt))
        for idx in disambiguate_similar(candidates):
            picked.add(idx)

    return sorted(picked)


# ---------------------------------------------------------------- 文本答案
TEXT_ANSWER_TYPES = frozenset({'填空题', '简答题', '名词解释', '论述题', '计算题'})
_SPLIT_RE = re.compile(r'\s*\|\s*|\s*；\s*|\s*;\s*|\s*,\s*|\s*，\s*')


def as_text_answers(raw, expected=None):
    """把 AI / 题库返回的内容解析成「每个空一个答案」的列表。

    兼容：["a","b"] 字面量、'a|b'、'a，b'、'a,b'、单段文本。
    含中文逗号或句号的完整句子**不拆**（上一版填空题错位就是被中文逗号误拆）。
    expected 给定时，若拆出的段数不等于空位数则返回 []，避免答案串空。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw if x is not None and str(x).strip()]
    else:
        text = str(raw).strip()
        if not text:
            return []
        try:
            parsed = ast.literal_eval(text)
            parts = ([str(x).strip() for x in parsed if x is not None and str(x).strip()]
                     if isinstance(parsed, (list, tuple)) else [text])
        except Exception:
            parts = [text]

    # 只有「单段 + 明确分隔符」才拆；带句读的自然语言句子保持原样
    if len(parts) == 1 and expected and expected > 1:
        candidate = parts[0]
        if not re.search(r'[。！？!?]', candidate):
            split = [p.strip() for p in _SPLIT_RE.split(candidate) if p.strip()]
            if len(split) == expected:
                parts = split
    parts = [p.strip('"\'"“”[]') for p in parts]
    parts = [p for p in parts if p]
    if expected is not None and expected > 0 and len(parts) != expected:
        # 单段文本允许作为「整题一个答案」，其余一律不猜位置
        if not (expected == 1 and len(parts) == 1):
            return []
    return parts


def exam_ai_prompt(type_name, stem, option_texts, blank_count=0):
    """按题型生成考试专用的 AI 提示（借鉴 pang-qin/chaoxing-toolkit 的思路）。

    只在考试模块内使用，不改 task/tool/DeepSeekAsk.py，章节测验行为不变。
    """
    opts = ' '.join('%s.%s' % (chr(ord('A') + i), t)
                    for i, t in enumerate(option_texts or []) if t)
    head = '【%s】%s' % (type_name, stem)
    if type_name == '多选题':
        return (head + '\n这是多选题，只返回所有正确选项的字母，如 AB 或 ACD，不要其它内容。\n选项：' + opts)
    if type_name == '判断题':
        return head + '\n这是判断题，只返回「正确」或「错误」两个字，不要其它内容。'
    if type_name == '单选题':
        return head + '\n这是单选题，只返回一个正确选项字母 A/B/C/D，不要其它内容。\n选项：' + opts
    if type_name == '填空题':
        n = blank_count or 1
        return (head + '\n这是填空题，共 %d 个空。只返回答案，多个空用 | 分隔，'
                '不要编号、不要解释。' % n)
    return head + '\n这是%s，直接给出简洁准确的答案正文，不要解释、不要编号。' % type_name
