# -*- coding: utf-8 -*-
"""课程考试适配器（2026-11-08 新增）。

与「章节测验」根本不同，不能用 Quiz / Answer 那套整页多题的逻辑：
  * 考试页是顶层文档、**逐题显示**（一次只有一道题，靠「下一步」推进）；
  * `randomOptions=true` 选项乱序，`span[data]` 才是真实字母，
    页面上显示的 A/B/C/D 是打乱后的位置（实测 data="C" 显示 "B"）；
  * 答案落在每题的 hidden input `#answer<qid>`，题型在 `#typeName<qid>`；
  * 提交走 `form#submitTest` + `#submitConfirmPop` 二次确认，提交不可逆；
  * 90 分钟倒计时，离开界面不停止计时，截止自动提交，禁止粘贴 / 多端。

因此这里写成独立的「读当前题 → 取答案 → 按真实字母点选项 → 校验写入 → 下一步」循环。

安全红线（不要改回去）：
  1. **默认绝不交卷**。只有用户在设置里显式打开 `exam_auto_submit`（打开时有二次确认弹窗）、
     且达到阈值、剩余时间充足、监控字段全关、撤销窗口走完，才会点交卷。
  2. 绝不自动确认 `#submitConfirmPop`、绝不递归重试提交、绝不刷新答题页。
  3. 检测到人脸 / 抓拍 / 切屏监控任一开启，或 timeOver / 剩余时间不足，立即停下。
  4. 不替用户决定进哪一场考试：导航只负责打开考试列表，检测到答题页才接管；
     考试说明页的「进入考试」按钮绝不自动点（那会启动倒计时）。

选择器常量内嵌在本模块：打包成单文件 exe 后，工作区的 exam_probe/ 采集目录并不存在。
事实来源：exam_probe/exam_selectors.json（2026-11-07 真机只读采集）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import traceback
from datetime import datetime
from difflib import SequenceMatcher

from selenium.webdriver.common.by import By

from core import paths, runtime, ai_config
from task.tool import color
from task.tool.common import save_diagnostic_screenshot
from task.tool.ai_wen_da import main as bank_main
from task.tool.DeepSeekAsk import DeepSeekAsk
from task.quiz_ai import _as_answer_list
from task import exam_answer_kb as kb

# ---------------------------------------------------------------- 页面常量
EXAM_URL_MARK = '/exam-ans/exam/test/reVersionTestStartNew'
EXAM_NOTES_MARK = '/exam-ans/exam/test/examcode/examnotes'
EXAM_LIST_MARK = '/exam-ans/mooc2/exam/exam-list'

Q_BOX = 'div.whiteDiv.questionLi.singleQuesId[data]'
Q_STEM = 'h3.mark_name'
Q_TYPE_LABEL = 'span.colorShallow[aria-label]'
OPT_BOX = 'div.answerBg'
OPT_SPAN = 'span[data][qid]'
OPT_TEXT = 'div.answer_p'
CONFIRM_DIALOG = '#submitConfirmPop'
CONFIRM_OK = '#submitConfirmPop a.jb_btn.confirm'

# 课程页「考试」入口的候选选择器（不同学校版式不同，找不到就等用户手动点）
ENTRY_CSS = [
    'li[data-nav-name="考试"]', 'a[title="考试"]',
    'a[data-name="考试"]', 'li[title="考试"]',
]
ENTRY_XPATH = (
    '//a[normalize-space(text())="考试"] | '
    '//li[normalize-space(text())="考试"] | '
    '//span[normalize-space(text())="考试"]/..'
)

MONITOR_KEYS = (
    'needFaceRecognition', 'webNeedFaceRecognition', 'webNeedScreenShot',
    'snapshotMonitor', 'webSnapshotMonitor', 'electronSnapshotMonitor',
    'switchScreenControl', 'liveMonitor', 'monitorLock',
)
# 答题卡：展开后含整卷所有题号（采集事实：题号 1..85）。
# 这是「整卷总题数」的可靠来源；绝不用「本轮遍历到的题数」当分母。
ANSWER_CARD_CSS = 'a.unfoldBtn'
PAPER_TOTAL_JS = (
    # 返回整卷题数：优先答题卡题号最大值；其次页面分组「共 N 题」之和。
    # 读不到返回 0（= 未知），调用方据此禁止自动交卷。
    "var best=0;"
    "document.querySelectorAll('a.unfoldBtn').forEach(function(a){"
    "  try{a.click();}catch(e){}"
    "});"
    "document.querySelectorAll('[class*=answerCard] [class*=num],"
    " [class*=answer-card] [class*=num],"
    " [class*=marking] [class*=num],"
    " .mark_gr .num, .answerCardNum, .tabNum').forEach(function(e){"
    "  var n=parseInt((e.textContent||'').trim(),10);"
    "  if(n>best && n<10000) best=n;"
    "});"
    "return best;"
)


def read_paper_total(driver):
    """只读探测整卷题数：答题卡题号最大值。读不到/异常返回 0（未知）。

    2026-11-16 任务01：交卷分母必须是整卷题数，不能是本轮遍历题数。
    配置 `exam_paper_total`（人工填写）优先级最高；其次 DOM 探测；
    两者都没有就是 0 —— 调用方必须据此禁止自动交卷。
    仅点「答题卡展开」这种无害 UI，绝不点交卷/确认/下一题。
    """
    cfg_total = 0
    try:
        cfg_total = int(runtime.get('exam_paper_total', 0) or 0)
    except (TypeError, ValueError):
        cfg_total = 0
    if cfg_total > 0:
        return cfg_total
    if driver is None:
        return 0
    try:
        n = int(driver.execute_script(PAPER_TOTAL_JS) or 0)
        return n if 0 < n < 10000 else 0
    except Exception:
        return 0
BANK_TYPES = {'单选题': 0, '多选题': 1, '填空题': 2, '判断题': 3,
              '简答题': 9, '论述题': 9, '名词解释': 9}
# 判断题词表与语义判定统一放到 exam_answer_kb（借鉴 ocsjs，MIT）；
# 保留旧名字，避免破坏既有测试与调用方
JUDGE_TRUE = kb.JUDGE_TRUE_WORDS
JUDGE_FALSE = kb.JUDGE_FALSE_WORDS
TEXT_ANSWER_TYPES = kb.TEXT_ANSWER_TYPES

READ_HIDDEN_JS = (
    "var o={};document.querySelectorAll('input[type=hidden][id]')"
    ".forEach(function(e){o[e.id]=e.value;});return o;"
)

# ---------------------------------------------------------------- 兜底 DOM 常量
# 来源：fysh1010/chaoxing-script (MIT) 与 pang-qin/chaoxing-toolkit 的油猴脚本。
# 它们面向「旧版整页多题」页面；本校真机采集为「新版逐题页」，
# 因此这些只作为现有选择器读不到时的兜底探测，**未经真机验证**。
FALLBACK_Q_BOX = '.mark_table, div.TiMu, div.questionLi'
FALLBACK_STEM = '.mark_name.colorDeep, h3.mark_name, .Py-m1-title'
FALLBACK_NEXT_CSS = '.nextDiv .jb_btn, .nextDiv a, .nextDiv button'
FALLBACK_TEXT_INPUT = 'textarea, [contenteditable="true"]'
FALLBACK_OPT_INPUT = 'ul:eq(0) li :radio, ul:eq(0) li :checkbox'
WRITE_UE_JS = (
    "var name=arguments[0], value=arguments[1];"
    "try { if (window.UE && UE.getEditor) {"
    "  UE.getEditor(name).ready(function(){ this.setContent(value); });"
    "  return 'ue'; } } catch(e) {}"
    "return '';"
)


# ---------------------------------------------------------------- 题目结构
class ExamQuestion:
    def __init__(self, qid, stem, type_name, options, hidden, blank_count=0):
        self.qid = qid
        self.stem = stem
        self.type_name = type_name
        self.options = options          # [{real_letter, display_letter, text}]
        self.hidden = hidden
        # 文本题型的可填写编辑器数量；0 表示页面上没读到，后面按「跳过」处理
        self.blank_count = int(blank_count or 0)

    @property
    def letters(self):
        return [o['real_letter'] for o in self.options if o.get('real_letter')]

    @property
    def option_texts(self):
        return [o['text'] for o in self.options]

    @property
    def is_judge(self):
        # 除题型名/class 外，再用「恰好两个对错选项」判定（借鉴 ocsjs isJudgementOptions）
        return (self.type_name == '判断题'
                or any(o.get('kind') == 'judge' for o in self.options)
                or kb.is_judgement_options(self.option_texts))

    @property
    def is_text_answer(self):
        return self.type_name in TEXT_ANSWER_TYPES

    @property
    def shuffled(self):
        return any(o['real_letter'] and o['display_letter']
                   and o['real_letter'] != o['display_letter']
                   for o in self.options)


def _locate_question_box(driver):
    """在当前上下文找题目容器：先精确选择器，再宽松兜底。"""
    for css in (Q_BOX, FALLBACK_Q_BOX + '[data]', 'div.singleQuesId[data]',
                'div.questionLi[data]'):
        try:
            found = driver.find_elements(By.CSS_SELECTOR, css)
        except Exception:
            continue
        if found:
            return css, found[0]
    return None, None


def read_current_question(driver):
    """读取当前显示的这一道题；读不到返回 None（不抛异常给主循环）。

    2026-11-12 修复：独立考试会话此前因 Selenium 焦点停在错误窗口，
    在首页上找题目返回 None，2 秒就误判「答题完成」。这里补上
    iframe 兜底与宽松选择器，并让调用方在「第一题读不到」时等待重试。
    """
    try:
        driver.switch_to.default_content()
        css, box = _locate_question_box(driver)
        if box is None:
            # 采集显示答题页是顶层文档、无 iframe；但不同学校版式可能把它嵌进帧里，
            # 顶层找不到时再遍历前 4 个 iframe 试一次（只读，不点击）。
            try:
                frames = driver.find_elements(By.TAG_NAME, 'iframe')[:4]
            except Exception:
                frames = []
            for idx in range(len(frames)):
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(
                        driver.find_elements(By.TAG_NAME, 'iframe')[idx])
                    css, box = _locate_question_box(driver)
                    if box is not None:
                        break
                except Exception:
                    continue
            else:
                driver.switch_to.default_content()
                return None
        if box is None:
            return None
        qid = (box.get_attribute('data') or '').strip()
        if not qid:
            return None

        hidden = {}
        try:
            hidden = driver.execute_script(READ_HIDDEN_JS) or {}
        except Exception:
            pass

        stem = ''
        try:
            stem = box.find_element(By.CSS_SELECTOR, Q_STEM).text or ''
        except Exception:
            pass
        if not stem:
            # 兜底：旧版整页多题页面的题干选择器（开源脚本事实，未经本校真机验证）
            try:
                stem = box.find_element(By.CSS_SELECTOR, FALLBACK_STEM).text or ''
            except Exception:
                pass
        stem = re.sub(r'^\s*\d+\s*[.、]\s*[\(（][^)）]*[)）]\s*', '', stem)
        stem = re.sub(r'\s+', ' ', stem).strip()

        type_name = str(hidden.get('typeName' + qid, '') or '').strip()
        if not type_name:
            try:
                label = box.find_element(By.CSS_SELECTOR, Q_TYPE_LABEL).get_attribute(
                    'aria-label') or ''
                m = re.search(r'[\(（]\s*([^,，)）]+?)\s*[,，]', label)
                if m:
                    type_name = m.group(1).strip()
            except Exception:
                pass
        if type_name not in BANK_TYPES:
            type_name = '单选题'

        options = []
        for opt in box.find_elements(By.CSS_SELECTOR, OPT_BOX):
            item = {'real_letter': '', 'display_letter': '', 'text': '',
                     'kind': 'judge' if 'judgeoption' in (
                         opt.get_attribute('class') or '') else 'single'}
            try:
                span = opt.find_element(By.CSS_SELECTOR, OPT_SPAN)
                item['real_letter'] = (span.get_attribute('data') or '').strip().upper()
                item['display_letter'] = (span.text or '').strip().upper()[:2]
            except Exception:
                pass
            try:
                item['text'] = (opt.find_element(By.CSS_SELECTOR, OPT_TEXT).text or '').strip()
            except Exception:
                pass
            options.append(item)
        question = ExamQuestion(qid, stem, type_name, options, hidden)
        # 文本题型：探测页面上可写入的编辑器数量（旧版 UEditor / textarea 兜底）。
        # 新版逐题页若无编辑器则为 0，后面会按「读不到就跳过」处理，绝不瞎写。
        if type_name in TEXT_ANSWER_TYPES:
            question.blank_count = _count_text_inputs(driver, box)
        else:
            question.blank_count = 0
        return question
    except Exception as e:
        print(color.yellow(f'读取当前考试题失败：{e}'), flush=True)
        return None


def _count_text_inputs(driver, box):
    """统计当前题里可填写的文本输入（textarea / contenteditable），失败返回 0。"""
    try:
        return len(box.find_elements(By.CSS_SELECTOR, FALLBACK_TEXT_INPUT))
    except Exception:
        return 0


# ---------------------------------------------------------------- 安全守卫
class ExamGuard:
    """每次操作前检查监控字段、剩余时间与作答次数；任一命中即停止。"""

    def __init__(self, cfg=None):
        cfg = cfg or runtime.state()
        try:
            self.min_remain = int(cfg.get('exam_min_remain_minutes', 10)) * 60
        except (TypeError, ValueError):
            self.min_remain = 600

    def reasons(self, hidden):
        out = []
        h = hidden or {}
        for key in MONITOR_KEYS:
            v = str(h.get(key, '0') or '0').strip().lower()
            if v not in ('', '0', 'false', '-1'):
                out.append('监控字段 %s=%s（人脸/抓拍/切屏，本程序不处理）' % (key, v))
        if str(h.get('timeOver', 'false')).lower() == 'true':
            out.append('timeOver=true，考试已结束')
        if str(h.get('answerTimes', '')).strip() == '0':
            out.append('answerTimes=0，已无可用作答次数')
        try:
            remain = int(float(h.get('remainTime', 0) or 0))
            if 0 < remain < self.min_remain:
                out.append('剩余 %d 秒 < 保护阈值 %d 秒' % (remain, self.min_remain))
        except (TypeError, ValueError):
            pass
        return out

    @property
    def min_remain_seconds(self):
        return self.min_remain


# ---------------------------------------------------------------- 答案映射
class AnswerResolver:
    """把题库/AI 返回的答案映射到 span[data] 的真实字母，绝不按显示位置。"""

    def resolve(self, question, answers):
        """把题库/AI 答案映射到 span[data] 的真实字母，绝不按显示位置点。

        2026-11-10 整合开源实现（见 task/exam_answer_kb.py）：
          * 文本答案改走「答案⊇选项」严格方向匹配，避免 TCP 误选 TCP/IP协议；
          * 相似候选做消歧，多选题不再多个答案串到同一个选项；
          * 判断题词表扩充，语义判定优先于字母硬映射。
        """
        letters = set(question.letters)
        if not letters:
            return []
        out = []
        # 单选题 / 判断题只能有一个答案。题库偶尔返回「A,B」这类脏数据，
        # 若原样全点，单选页后点的字母会覆盖前面的，最终答案变成最后一个（B）。
        qtype = str(getattr(question, 'type_name', '') or '')
        multi = qtype in ('多选题', '多项选择题')
        # 纯桩测试里的简化 Q 可能没有 type_name：此时按选项数保守判断，
        # 避免把「只造了 2 个选项」的多选测试误裁成 1 个。
        if not qtype:
            multi = len(question.letters) > 2
        max_letters = 4 if multi else 1

        for raw in answers or []:
            s = str(raw).strip()
            if not s:
                continue
            # 只有「纯字母 / 逗号分隔字母串」才按字母解释，
            # 否则选项文本里的 "A型血" 会被误判成字母答案。
            if re.fullmatch(r'[A-Da-d](?:\s*[,，、/|]\s*[A-Da-d])*', s):
                found = [x.upper() for x in re.findall(r'[A-Da-d]', s)]
                if not multi and len(found) > 1:
                    print(color.yellow(
                        '本题是%s，但题库返回了多个字母 %r；只取第一个 %s，'
                        '避免后点的字母覆盖成错误答案'
                        % (qtype or '非多选题', s, found[0])), flush=True)
                for letter in found:
                    if letter in letters and letter not in out:
                        if not multi and len(out) >= max_letters:
                            break
                        out.append(letter)
                continue

            # 判断题：按语义词表判定，而不是简单比对字母
            side = kb.judge_side(s)
            if question.is_judge and side:
                ordered = question.letters
                want = ordered[0] if side == 'true' else (
                    ordered[1] if len(ordered) > 1 else None)
                # 选项乱序时优先按选项文本反查真实字母
                for opt in question.options:
                    if opt.get('text') and kb.judge_side(opt['text']) == side:
                        want = opt.get('real_letter') or want
                        break
                if want and want in letters and want not in out:
                    out.append(want)
                    continue

            # 文本答案：严格方向匹配优先，相似度兜底，候选消歧后按真实字母写入
            idxs = kb.resolve_by_text([s], question.option_texts)
            for i in idxs:
                if i < len(question.options):
                    letter = question.options[i].get('real_letter')
                    if letter and letter in letters and letter not in out:
                        if not multi and len(out) >= max_letters:
                            break
                        out.append(letter)

        # 兜底裁剪：无论答案从哪条路径进来，单选/判断最多 1 个字母
        if not multi and len(out) > max_letters:
            print(color.yellow(
                '%s 解析出多个候选 %s，只保留第一个 %s'
                % (qtype or '非多选题', out, out[0])), flush=True)
            out = out[:max_letters]
        return out


# ---------------------------------------------------------------- 答案来源
class AnswerSource:
    def __init__(self, api_key=''):
        self.api = api_key or ''

    def get(self, question):
        mode = str(runtime.get('exam_answer_mode', '题库优先+AI兜底'))
        if mode == '只识别不作答':
            return []
        typ = question.type_name if question.type_name in BANK_TYPES else '单选题'
        blank_count = self._blank_count(question)
        answers = []
        try:
            # 题库多源并发；ai_wen_da.main 是 async，子进程里没有事件循环
            raw = asyncio.run(bank_main(typ, question.stem,
                                        question.option_texts, self.api))
            answers = _as_answer_list(raw)
        except Exception as e:
            print(color.yellow(f'题库查询失败：{e}'), flush=True)
        if answers or mode == '仅题库':
            return self._normalize(answers, typ, blank_count)
        if not ai_config.is_key_valid(self.api):
            return []
        try:
            # 按题型定制输出（借鉴 pang-qin/chaoxing-toolkit）：
            # 多选返回字母串、判断只回正确/错误、填空多空用 | 分隔。
            # 只在考试模块生效，不改 DeepSeekAsk，章节测验行为不变。
            prompt = kb.exam_ai_prompt(typ, question.stem,
                                       question.option_texts, blank_count)
            raw = DeepSeekAsk(self.api, prompt, typ)
            return self._normalize(_as_answer_list(raw), typ, blank_count)
        except Exception as e:
            print(color.yellow(f'AI 兜底失败：{e}'), flush=True)
            return []

    @staticmethod
    def _blank_count(question):
        """文本题型的空位数：优先用页面上探测到的编辑器数量。"""
        return int(getattr(question, 'blank_count', 0) or 0)

    @staticmethod
    def _normalize(answers, typ, blank_count):
        """填空题按空位数校验，避免答案串空（上一版填空题错位的教训）。"""
        if typ in TEXT_ANSWER_TYPES and blank_count > 0:
            return kb.as_text_answers(answers, expected=blank_count)
        return answers


# ---------------------------------------------------------------- 会话
class ExamSession:
    # 复习模式绝不重做考试：会覆盖已有成绩，且倒计时不可逆。
    REVIEWABLE = False

    def __init__(self, driver, course_name, api_key='', stop_check=None):
        self.driver = driver
        self.course_name = course_name
        self.guard = ExamGuard()
        self.resolver = AnswerResolver()
        self.source = AnswerSource(api_key)
        self.cfg = runtime.state()
        self.stop_check = stop_check       # 用户点「结束学习」的回调；命中即取消交卷
        # 题数双计数（2026-11-16 任务01）：
        #   total           = 本轮实际遍历/处理过的题数（旧字段名，保留兼容）
        #   paper_total     = 整卷总题数（答题卡题号最大值 / 配置），0=未知
        #   normal_completion = 是否自然走到卷尾（见 run()），异常/提前break/停止均为 False
        self.paper_total = 0
        self.normal_completion = False
        self.stat = {'total': 0, 'answered': 0, 'skipped': 0,
                     'failed': 0, 'submitted': 0, 'reasons': []}
        self.seen_qids = set()

    def _stopped(self):
        try:
            return bool(self.stop_check and self.stop_check())
        except Exception:
            return False

    # ---- 导航 ----------------------------------------------------
    def _exam_targets(self):
        """用 CDP 只读探测所有标签页，避免每轮 window_handles 抢焦点。"""
        try:
            infos = self.driver.execute_cdp_cmd('Target.getTargets', {}).get(
                'targetInfos', [])
            return [t for t in infos if t.get('type') == 'page'
                    and EXAM_URL_MARK in (t.get('url') or '')]
        except Exception:
            return None   # None = CDP 不可用，降级

    def _find_exam_window(self):
        targets = self._exam_targets()
        if targets is not None and not targets:
            return None
        for handle in self.driver.window_handles:
            try:
                self.driver.switch_to.window(handle)
                if EXAM_URL_MARK in (self.driver.current_url or ''):
                    return handle
            except Exception:
                continue
        return None

    def _click_exam_entry(self):
        """尽力点开课程页的「考试」标签；找不到不报错，交给人工兜底。

        只点「考试」入口，绝不点考试列表里的具体考试条目 —— 选哪一场由用户决定。
        """
        try:
            self.driver.switch_to.default_content()
            frames = self.driver.find_elements(By.TAG_NAME, 'iframe')
        except Exception:
            frames = []
        contexts = [None] + list(range(min(len(frames), 4)))
        for idx in contexts:
            try:
                self.driver.switch_to.default_content()
                if idx is not None:
                    self.driver.switch_to.frame(
                        self.driver.find_elements(By.TAG_NAME, 'iframe')[idx])
                found = False
                for css in ENTRY_CSS:
                    for el in self.driver.find_elements(By.CSS_SELECTOR, css):
                        self.driver.execute_script('arguments[0].click();', el)
                        found = True
                        break
                    if found:
                        break
                if not found:
                    for el in self.driver.find_elements(By.XPATH, ENTRY_XPATH):
                        self.driver.execute_script('arguments[0].click();', el)
                        found = True
                        break
                if found:
                    print(color.green('已点开「考试」入口，等待进入考试列表'), flush=True)
                    time.sleep(1.5)
                    break
            except Exception:
                continue
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

    def ensure_exam_page(self, wait_seconds=180):
        if self._find_exam_window():
            self.driver.switch_to.default_content()
            return True
        self._click_exam_entry()
        print(color.yellow(
            '请手动点进要作答的那场考试；程序检测到答题页后会自动开始答题。\n'
            '注意：考试说明页的「进入考试」按钮请由你自己点（一旦进入倒计时不会停止）。'),
            flush=True)
        deadline = time.time() + wait_seconds
        last_tip = 0
        while time.time() < deadline:
            if self._find_exam_window():
                self.driver.switch_to.default_content()
                print(color.green('已检测到考试答题页，开始接管答题'), flush=True)
                return True
            if time.time() - last_tip > 30:
                print(color.yellow('仍在等待进入考试页…（点「结束学习」可随时中止）'),
                      flush=True)
                last_tip = time.time()
            time.sleep(1.5)
        print(color.red('等待进入考试页超时，本门课跳过考试处理'), flush=True)
        return False

    # ---- 作答 ----------------------------------------------------
    def _click_option(self, letter, qid):
        locator = 'span[data="%s"][qid="%s"]' % (letter, qid)
        try:
            span = self.driver.find_element(By.CSS_SELECTOR, locator)
            box = span.find_element(
                By.XPATH, 'ancestor::div[contains(@class,"answerBg")][1]')
            # onclick="addChoice(this)" 绑在外层 div 上；JS 点击可绕开遮罩
            self.driver.execute_script('arguments[0].click();', box)
            return True
        except Exception as e:
            print(color.yellow(f'点选项 {letter} 失败：{e}'), flush=True)
            return False

    def _answer_written(self, qid):
        try:
            js = ("var e=document.getElementById('answer'+" + json.dumps(qid)
                  + ");return e?e.value:null;")
            value = self.driver.execute_script(js)
            return bool(value)
        except Exception:
            return False

    # ---- 文本题型兜底（2026-11-10 整合开源脚本的 UEditor / textarea 写法） ----
    def _text_inputs(self):
        """返回当前页可填写的文本输入元素；新版逐题页若无编辑器则为空。"""
        try:
            return [e for e in self.driver.find_elements(
                By.CSS_SELECTOR, FALLBACK_TEXT_INPUT) if e.is_displayed()]
        except Exception:
            return []

    def _write_text_answer(self, element, value):
        """优先走 UEditor setContent，失败再退化为原生输入；返回是否成功。"""
        name = ''
        try:
            name = element.get_attribute('name') or element.get_attribute('id') or ''
        except Exception:
            pass
        if name:
            try:
                if self.driver.execute_script(WRITE_UE_JS, name, value) == 'ue':
                    return True
            except Exception:
                pass
        try:
            self.driver.execute_script(
                "arguments[0].value=''; arguments[0].dispatchEvent("
                "new Event('input',{bubbles:true}));", element)
            element.send_keys(value)
            return True
        except Exception as e:
            print(color.yellow(f'写入文本答案失败：{e}'), flush=True)
            return False

    def _handle_text_question(self, question, mode):
        """填空/简答/名词解释/论述：按空位数写入，读不到编辑器就跳过不瞎点。"""
        blanks = self._text_inputs()
        if not blanks:
            self.stat['skipped'] += 1
            print(color.yellow('  文本题未找到可填写的编辑器，跳过'
                               '（新版考试页结构未验证，不猜不瞎写）'), flush=True)
            try:
                save_diagnostic_screenshot(self.driver, '考试文本题未识别')
            except Exception:
                pass
            return
        question.blank_count = len(blanks)
        answers = self.source.get(question)
        parts = kb.as_text_answers(answers, expected=len(blanks))
        if len(parts) != len(blanks):
            self.stat['skipped'] += 1
            print(color.yellow('  文本题答案数(%d)与空位数(%d)不符，跳过'
                               % (len(parts), len(blanks))), flush=True)
            return
        ok = all(self._write_text_answer(el, parts[i])
                 for i, el in enumerate(blanks[:len(parts)]))
        written = self._answer_written(question.qid)
        if ok and written:
            self.stat['answered'] += 1
            print(color.green('  文本题已填写 %d 个空' % len(parts)), flush=True)
        else:
            self.stat['failed'] += 1
            print(color.yellow('  文本题已写入但答案校验未通过，记为失败'), flush=True)

    def _go_next(self, current_qid):
        """点「下一题」并等题号真正变化。

        优先按可见文本定位（真机确认是「下一题」）；只有找不到文本按钮时
        才用 CSS 兜底，且仍要校验元素文本含「下一」，避免误点标记/交卷等按钮。
        """
        clicked = self._go_next_by_text()
        if not clicked:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, FALLBACK_NEXT_CSS):
                    if not el.is_displayed():
                        continue
                    if '下一' not in (el.text or ''):
                        continue
                    self.driver.execute_script('arguments[0].click();', el)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            print(color.yellow('未找到「下一题」按钮，停止翻页（不瞎点）'), flush=True)
            return False

        # 等题号变化：用「精确选择器 + iframe 兜底」，和读题时保持同一套定位，
        # 否则页面改版时这里用旧 Q_BOX 会看不到新题、误判翻页失败。
        for _ in range(14):
            time.sleep(0.5)
            try:
                self.driver.switch_to.default_content()
                _css, box = _locate_question_box(self.driver)
                if box is not None:
                    new_qid = (box.get_attribute('data') or '').strip()
                    if new_qid and new_qid != current_qid:
                        return True
            except Exception:
                pass
        return False

    def _go_next_by_text(self):
        """遍历标签找「下一题 / 下一步」按钮并点击。

        真机采集（exam_probe/snap_0012/top.html）确认按钮是
        `<div class="nextDiv"><a class="jb_btn ...">下一题</a></div>`，
        文本为「下一题」；旧实现只找「下一步」，在真实考试页根本点不到。
        """
        for tag in ('a', 'button', 'span', 'div'):
            try:
                for el in self.driver.find_elements(By.TAG_NAME, tag):
                    if (el.text or '').strip() not in ('下一题', '下一步'):
                        continue
                    if not el.is_displayed():
                        continue
                    self.driver.execute_script('arguments[0].click();', el)
                    return True
            except Exception:
                continue
        return False

    # 交卷前置判据：返回 None 表示通过，否则返回拒绝原因。
    # 抽成独立函数，撤销窗口前后用同一套标准复检（2026-11-16 任务01）。
    def _submit_blockers(self, hidden):
        h = hidden or {}
        blockers = []
        # 判据 4/5/8：整卷总数未知或本轮非正常完成，一律不交卷
        if not self.paper_total or self.paper_total <= 0:
            blockers.append('整卷总题数未知（paper_total=%s）' % self.paper_total)
        if not self.normal_completion:
            blockers.append('本轮非正常完成整卷（提前中断/异常/页面丢失/用户停止）')
        if self.paper_total > 0 and self.stat['total'] < self.paper_total:
            blockers.append('本轮只处理 %d/%d 题，未覆盖整卷' % (
                self.stat['total'], self.paper_total))
        # 判据 7：答题率分母必须是整卷题数
        if self.paper_total > 0:
            ratio = self.stat['answered'] * 100.0 / self.paper_total
            threshold = int(self.cfg.get('exam_submit_ratio', 100) or 100)
            if ratio < threshold:
                blockers.append('整卷答题率 %.1f%% 未达阈值 %d%%' % (ratio, threshold))
        # 判据 9/10：剩余时间必须存在、为数字、且非 0
        raw_remain = h.get('remainTime', None)
        if raw_remain is None or str(raw_remain).strip() == '':
            blockers.append('剩余时间 remainTime 缺失，无法确认')
        else:
            try:
                remain = int(float(raw_remain))
                if remain <= 0:
                    blockers.append('剩余时间 remainTime=0（考试可能已结束）')
            except (TypeError, ValueError):
                blockers.append('剩余时间 remainTime 非数字：%r' % raw_remain)
        # 判据 10：安全监控字段必须全部可读（缺失 = 不可靠 = 不交卷）
        for key in MONITOR_KEYS:
            if key not in h or str(h.get(key)).strip() == '':
                blockers.append('安全字段 %s 无法可靠读取' % key)
        # 判据 6：手动停止立即取消
        if self._stopped():
            blockers.append('用户已停止任务')
        # 既有守卫（监控开启 / timeOver / answerTimes=0 / 时间不足）
        blockers.extend(self.guard.reasons(hidden))
        return blockers

    def _maybe_submit(self, hidden):
        """默认不交卷；只有显式开启 + 全部判据满足才执行。返回 (是否交卷, 说明)。

        2026-11-16 任务01 重写：
        - 分母用整卷题数 paper_total，绝不用本轮遍历数（3/3=100% 的老 bug）；
        - 只有 normal_completion=True（自然到卷尾）才允许；
        - 剩余时间缺失/非数字/0、安全字段缺失、整卷总数未知，全部拒绝；
        - 用户停止 + 撤销窗口期间条件变化都会取消；
        - 仍然绝不自动点确认弹窗以外的按钮，失败不重试、不递归。
        """
        if not int(self.cfg.get('exam_auto_submit', 0) or 0):
            return False, '默认不交卷（答案留在页面上，请自行核对后手动交卷）'
        blockers = self._submit_blockers(hidden)
        if blockers:
            return False, '交卷前置判据未满足：' + '；'.join(blockers)

        revoke = int(self.cfg.get('exam_revoke_seconds', 15) or 0)
        head = '《%s》考试准备交卷 · %d秒内可点「结束学习」取消' % (
            self.course_name, revoke)
        print(color.red(head), flush=True)
        try:
            from core.notifier import Notifier, format_message
            Notifier.instance().notify(
                'course_finish', format_message(head, [
                    '整卷：已答 %d/%d ｜ 阈值：%d%%' % (
                        self.stat['answered'], self.paper_total,
                        int(self.cfg.get('exam_submit_ratio', 100) or 100)),
                    '撤销窗口：%d 秒' % revoke]),
                title='考试交卷预告 · %s' % self.course_name)
        except Exception:
            pass
        # 撤销窗口分段 sleep，期间每秒检查用户停止（判据 11）
        if revoke > 0:
            for _ in range(revoke):
                if self._stopped():
                    return False, '撤销窗口内用户点「结束学习」，已取消交卷'
                time.sleep(1)

        # 判据 12：撤销窗口结束后，重新读取 hidden + 重新核对整卷状态/时间/安全
        fresh = {}
        try:
            fresh = self.driver.execute_script(READ_HIDDEN_JS) or {}
        except Exception:
            pass
        blockers = self._submit_blockers(fresh)
        if blockers:
            return False, '撤销窗口后复检未通过，不交卷：' + '；'.join(blockers)

        clicked = False
        for tag in ('a', 'button', 'span', 'div'):
            try:
                for el in self.driver.find_elements(By.TAG_NAME, tag):
                    if (el.text or '').strip() == '交卷' and el.is_displayed():
                        self.driver.execute_script('arguments[0].click();', el)
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                continue
        if not clicked:
            return False, '页面上没找到「交卷」按钮，未交卷（绝不瞎点其它按钮）'
        time.sleep(1)
        try:
            confirm = self.driver.find_element(By.CSS_SELECTOR, CONFIRM_OK)
        except Exception as e:
            # 已点「交卷」但确认框没出现 = 半提交状态，必须如实报告，
            # 绝不能当成「已交卷」（用户会据此离开考场）。
            return False, ('已点击「交卷」但未出现确认框（%s）：请立即人工核对页面，'
                           '本程序未确认提交结果') % e
        try:
            if not confirm.is_displayed():
                # 旧实现：不可见 → 不点确认 → 直接置 submitted=1，对外宣称已交卷
                # （2026-11-19 审计 P0-10）。
                return False, ('「交卷」已点击，但确认按钮不可见：请立即人工核对页面，'
                               '本程序未确认提交结果')
            self.driver.execute_script('arguments[0].click();', confirm)
        except Exception as e:
            return False, ('确认按钮点击失败（%s）：请立即人工核对页面，'
                           '本程序未确认提交结果') % e
        self.stat['submitted'] = 1
        return True, '已按阈值自动交卷（整卷 %d 题，已答 %d）' % (
            self.paper_total, self.stat['answered'])

    def _log_no_question_diagnostic(self):
        """读不到题目时打印只读诊断，帮助区分「窗口不对 / DOM 未加载 / 选择器改版」。"""
        try:
            url = self.driver.current_url or ''
            title = self.driver.title or ''
        except Exception:
            url, title = '', ''
        print(color.yellow('诊断：未读到题目 | window=%s | title=%s' % (url[:130], title[:60])),
              flush=True)
        for label, css in (('题目容器', Q_BOX), ('兜底容器', FALLBACK_Q_BOX),
                           ('选项容器', OPT_BOX)):
            try:
                n = len(self.driver.find_elements(By.CSS_SELECTOR, css))
            except Exception:
                n = -1
            print(color.yellow('  %s %s -> %d 个' % (label, css[:70], n)), flush=True)

    def _save_record(self, note):
        path = paths.record('《%s》的考试记录.txt' % self.course_name)
        try:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write('课程考试处理完成：%s\n' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                f.write('题数：%d ｜ 已答：%d ｜ 跳过：%d ｜ 失败：%d\n' % (
                    self.stat['total'], self.stat['answered'],
                    self.stat['skipped'], self.stat['failed']))
                f.write('结果：%s\n' % note)
                f.write('-' * 50 + '\n')
        except Exception as e:
            print(color.yellow(f'写考试记录失败：{e}'), flush=True)

    def run(self, wait_seconds=180, already_on_page=False):
        """already_on_page=True 时跳过「等待进入答题页」，供独立考试会话调用。

        独立模式由页面链识别器确认当前就是答题页，不需要再点入口或等 180 秒。
        默认 False，旧调用方行为完全不变。
        """
        if not already_on_page and not self.ensure_exam_page(wait_seconds):
            self._save_record('未进入考试答题页（等待超时）')
            return 'wait_timeout'
        if self._stopped():
            self._save_record('用户已停止任务，未开始答题')
            return 'stopped'

        # 整卷题数：答题卡题号最大值；读不到记为 0（未知）→ 禁止自动交卷
        self.paper_total = read_paper_total(self.driver)
        print(color.green('整卷题数：%s（答题卡探测 / 配置）｜本轮最多处理 %d 题' % (
            self.paper_total or '未知',
            int(self.cfg.get('exam_max_questions', 200) or 200))), flush=True)

        max_questions = int(self.cfg.get('exam_max_questions', 200) or 200)
        mode = str(runtime.get('exam_answer_mode', '题库优先+AI兜底'))
        print(color.green('考试模式：%s ｜ 自动交卷：%s' % (
            mode, '开启' if int(self.cfg.get('exam_auto_submit', 0) or 0) else '关闭')),
            flush=True)

        note = ''
        # 刚进入答题页时 DOM 可能还在渲染；第一题读不到要等，不能立刻判定完成。
        # 仅在「一道题都没读到」时生效，答过若干题后读不到仍按正常收尾处理。
        first_question_deadline = time.time() + (30 if already_on_page else 10)
        first_question_wait_logged = False
        try:
            while self.stat['total'] < max_questions:
                # 判据 6：每题循环开始检查用户停止，命中立即退出且不判正常完成
                if self._stopped():
                    note = '用户已停止任务，停止答题并取消任何交卷动作'
                    self.stat['reasons'] = ['stopped']
                    break
                q = read_current_question(self.driver)
                if q is None and self.stat['total'] == 0 and time.time() < first_question_deadline:
                    if not first_question_wait_logged:
                        print(color.yellow(
                            '答题页已识别，题目容器尚未渲染，等待加载…'
                            '（最多 %d 秒，不刷新、不瞎点）'
                            % (30 if already_on_page else 10)), flush=True)
                        first_question_wait_logged = True
                    time.sleep(1)
                    continue
                if q is None:
                    self._log_no_question_diagnostic()
                    note = '当前页没有可识别的题目，停止（不刷新、不瞎点）'
                    break
                reasons = self.guard.reasons(q.hidden)
                if reasons:
                    note = '安全守卫停止：' + '；'.join(reasons)
                    self.stat['reasons'] = reasons
                    break
                if q.qid in self.seen_qids:
                    # 翻页后 DOM 可能还没渲染完，读到的仍是上一题；
                    # 连续多次重复才判到最后一题，避免第 2 题就提前收尾。
                    self.repeat_qid_count = getattr(self, 'repeat_qid_count', 0) + 1
                    if getattr(self, 'repeat_qid_count', 0) <= 3:
                        print(color.yellow(
                            '题号 %s 重复（第 %d 次），可能是页面还在加载，等待重试…'
                            % (q.qid, self.repeat_qid_count)), flush=True)
                        time.sleep(1.5)
                        continue
                    note = '题号重复，判定已到最后一题，停止翻页'
                    # 自然到卷尾（非异常/非提前 break），才允许进入交卷判定。
                    self.normal_completion = True
                    break
                self.repeat_qid_count = 0
                self.seen_qids.add(q.qid)
                self.stat['total'] += 1

                flag = '乱序' if q.shuffled else '顺序'
                print(color.green('第%d题 [%s/%s] qid=%s 选项=%s' % (
                    self.stat['total'], q.type_name, flag, q.qid, q.letters)),
                    flush=True)

                if mode == '只识别不作答':
                    self.stat['skipped'] += 1
                elif q.is_text_answer:
                    # 文本题型（填空/简答/名词解释/论述）：采集只覆盖单选，
                    # 这里走开源脚本的 UEditor / textarea 兜底，读不到就跳过。
                    self._handle_text_question(q, mode)
                else:
                    answers = self.source.get(q)
                    letters = self.resolver.resolve(q, answers)
                    print(color.yellow('  题库返回=%s → 解析字母=%s（题型=%s）'
                                       % (str(answers)[:160], letters, q.type_name)),
                          flush=True)
                    if not letters:
                        self.stat['skipped'] += 1
                        print(color.yellow('  未取到可用答案，跳过（不猜、不随机）'),
                              flush=True)
                        try:
                            save_diagnostic_screenshot(self.driver, '考试未作答题')
                        except Exception:
                            pass
                    else:
                        ok = all(self._click_option(letter, q.qid)
                                 for letter in letters)
                        written = self._answer_written(q.qid)
                        if ok and written:
                            self.stat['answered'] += 1
                            print(color.green('  已选 %s，答案已写入 #answer%s' % (
                                ','.join(letters), q.qid)), flush=True)
                        else:
                            self.stat['failed'] += 1
                            print(color.yellow('  选项已点但答案写入校验失败，记为本题失败'),
                                  flush=True)

                if not self._go_next(q.qid):
                    note = '点「下一步」后题号未变化，判定已到最后一题或页面改版'
                    # 自然到卷尾信号之一；但交卷仍受 paper_total 覆盖校验约束
                    self.normal_completion = True
                    break
            else:
                # 达到硬上限不是「正常答完整卷」，绝不允许据此交卷
                note = '达到题目数硬上限 %d，停止' % max_questions
                self.normal_completion = False
        except Exception as e:
            note = '考试处理异常，已停止（不重试、不刷新）：%s' % e
            # 判据 6：异常退出绝不是正常完成，撤销任何交卷资格
            self.normal_completion = False
            print(color.red(note), flush=True)
            print(traceback.format_exc(), flush=True)

        hidden = {}
        try:
            hidden = self.driver.execute_script(READ_HIDDEN_JS) or {}
        except Exception:
            pass
        submitted, submit_note = self._maybe_submit(hidden)
        note = (note + '；' if note else '') + submit_note
        print(color.green('考试处理结束：%s' % note), flush=True)
        print(color.green('汇总：%d题 / 已答%d / 跳过%d / 失败%d' % (
            self.stat['total'], self.stat['answered'],
            self.stat['skipped'], self.stat['failed'])), flush=True)

        try:
            runtime.bump('quiz_answered', self.stat['answered'])
            runtime.bump('quiz_skipped', self.stat['skipped'])
        except Exception:
            pass
        self._save_record(note)
        if 'stopped' in (self.stat['reasons'] or []):
            return 'stopped'
        if self.stat['reasons']:
            return 'guarded'
        return 'submitted' if submitted else 'done'


def run_exam(driver, course_name, API='', wait_seconds=180, stop_check=None):
    """main.py 的考试模式入口；返回状态串，绝不抛异常给课程循环。"""
    try:
        return ExamSession(driver, course_name, API, stop_check).run(wait_seconds)
    except Exception as e:
        print(color.red(f'课程考试启动失败：{e}'), flush=True)
        print(traceback.format_exc(), flush=True)
        return 'exam_error'
