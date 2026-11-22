# -*- coding: utf-8 -*-
"""课程讨论自动完成（2026-11-06 重写）。

用户确认的行为：
- 默认**直接用 DeepSeek 作答**，回复尽可能简短（30~50 字）；
  只有用户显式打开「抄已有回复」时才随机抄一条现成回复。
- 默认处理**任务点内遇到的全部讨论**：Common.main() 会遍历当前页所有
  讨论 iframe，这里放宽 require_mission，避免整批被「没有任务点」跳过。
- 「全部讨论（需真机校准）」是 UI 上保留的开关：目前仍按任务点内执行，
  并打印提示，不假装已经能遍历整门课程讨论区（缺讨论区 DOM 依据）。
- 处理不了的话题记失败并跳过，不静默吞掉；窗口只在确认新标签时关闭。
"""
from __future__ import annotations

import ast
import random
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core import runtime, ai_config
from task.tool.DeepSeekAsk import DeepSeekAsk
from task.tool.common import Common, turn_page

# 短回复：30~50 字，最多保留 80 字，避免长篇大论刷屏
SHORT_REPLY_PROMPT = (
    '【讨论话题】{topic}\n'
    '请写一段 30~50 字的简短回复，紧扣话题、语气自然，像学生正常发言。'
    '不要客套话、不要列表、不要分点、不要出现「作为AI」「以下是」「总之」等字样，'
    '直接输出回复正文。'
)
MAX_REPLY = 80


def _as_text(raw):
    """把 DeepSeek / 已有回复统一成一段可直接输入的短文本。"""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw if x is not None]
    else:
        text = str(raw or '').strip()
        try:
            parsed = ast.literal_eval(text)
            parts = ([str(x) for x in parsed if x is not None]
                     if isinstance(parsed, (list, tuple)) else [str(parsed)])
        except Exception:
            parts = [text]
    joined = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
    joined = joined.strip('"\'"“”[]')
    return joined[:MAX_REPLY]


class Discussion(Common):
    def __init__(self, driver, API, iframe_element, discussion_choice, course_name):
        try:
            # 讨论经常不挂任务点图标：放宽 require_mission，否则整批讨论会被跳过
            super().__init__(driver, iframe_element, '讨论', course_name,
                             require_mission=False)
        except Exception as e:
            # 新版学习页可能没有 id="iframe" 的外层帧，退回顶层直接找讨论元素
            print('[讨论] 外层 iframe 切换失败，改用当前帧：%s' % e, flush=True)
            self.course_name = course_name
            self.type = '讨论'
            self.driver = driver
            self.require_mission = False
            self.iframes = driver.find_elements(By.CSS_SELECTOR, iframe_element)
        self.API = API
        self.discussion_choice = discussion_choice
        self.question = ''
        self.answer = ''
        self.copy_reply = runtime.copy_existing_reply()
        self.scope = runtime.get('discussion_scope')
        self.stats = {'total': 0, 'ok': 0, 'skipped': 0, 'failed': 0}
        if self.scope == '全部讨论（需真机校准）':
            print('[讨论] 已选择「全部讨论」：当前先按章节任务点内遇到的全部讨论执行；'
                  '整门课程讨论区遍历需真机确认页面结构后再启用。', flush=True)

    # ------------------------------------------------------------ 取答案
    def _existing_replies(self):
        out = []
        try:
            for item in self.driver.find_elements(By.CLASS_NAME, 'topicDetail_replyItem'):
                try:
                    text = item.find_element(By.CLASS_NAME, 'replyContent').text.strip()
                    if text:
                        out.append(text)
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _ai_answer(self, topic):
        if not ai_config.is_key_valid(self.API):
            print('[讨论] 未配置有效的 API Key，无法 AI 作答', flush=True)
            return ''
        try:
            raw = DeepSeekAsk(self.API, SHORT_REPLY_PROMPT.format(topic=(topic or '')[:300]),
                              '简答题')
        except Exception as e:
            print('[讨论] AI 作答失败：%s' % e, flush=True)
            return ''
        text = _as_text(raw)
        if len(text) < 8:
            print('[讨论] AI 回复过短或为空，视为未答出', flush=True)
            return ''
        return text

    def get_answer(self):
        replies = self._existing_replies()
        if self.copy_reply and replies:
            self.answer = _as_text(random.choice(replies))
            print('[讨论] 按设置抄用一条已有回复', flush=True)
            return
        # 默认路径：直接用 DeepSeek 生成简短回复
        self.answer = self._ai_answer(self.question)
        if not self.answer and replies:
            # AI 未配置/失败时才兜底，避免一个话题白白跳过
            self.answer = _as_text(random.choice(replies))
            print('[讨论] AI 不可用，兜底抄用已有回复', flush=True)

    # ------------------------------------------------------------ 单个话题
    def _already_replied(self):
        """弱判定：找得到本人回复标记就跳过；找不到不阻断作答。"""
        try:
            marks = self.driver.find_elements(
                By.CSS_SELECTOR, '[class*="myReply"], [class*="selfReply"]')
            return bool(marks)
        except Exception:
            return False

    def _submitted(self, textarea):
        try:
            if not (textarea.get_attribute('value') or '').strip():
                return True          # 输入框已清空，通常表示提交成功
        except Exception:
            return True              # 元素失效多半是页面刷新了
        return bool(self._existing_replies())

    def _close_topic(self, before_handles):
        """只在新标签确实打开时关闭它，绝不盲关主窗口。"""
        try:
            handles = list(self.driver.window_handles or [])
            if len(handles) > len(before_handles):
                self.driver.close()
                self.driver.switch_to.window(before_handles[0] if before_handles else handles[0])
            else:
                self.driver.switch_to.default_content()
            try:
                turn_page(self.driver, '学生学习页面')
            except Exception:
                pass
        except Exception as e:
            print('[讨论] 返回学习页失败：%s' % e, flush=True)

    def start(self):
        self.stats['total'] += 1
        before = list(self.driver.window_handles or [])
        try:
            iframe = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(self.iframe))
            iframe.click()
            turn_page(self.driver, '话题详情')
            self.question = self.driver.find_element(
                By.CLASS_NAME, 'topicDetail_title').text.strip()
            print('[讨论] 话题：%s' % self.question[:60], flush=True)

            if self._already_replied():
                self.stats['skipped'] += 1
                print('[讨论] 已回复，跳过', flush=True)
                return

            textarea = self.driver.find_element(
                By.CLASS_NAME, 'textareawrap').find_element(By.TAG_NAME, 'textarea')
            button = self.driver.find_element(
                By.CSS_SELECTOR, '[class="jb_btn jb_btn_92 fr fs14 addReply"]')
            self.get_answer()
            if not self.answer:
                self.stats['failed'] += 1
                print('[讨论] 没有可用回复，跳过该话题', flush=True)
                return
            textarea.click()
            textarea.send_keys(self.answer)
            time.sleep(1)
            button.click()
            time.sleep(1)
            ok = self._submitted(textarea)
            self.stats['ok' if ok else 'failed'] += 1
            print('[讨论] 提交%s：%s' % ('成功' if ok else '未确认', self.answer[:40]),
                  flush=True)
        except Exception as e:
            # 不静默吞掉：记失败并让 Common.main 继续下一个话题
            self.stats['failed'] += 1
            print('[讨论] 话题处理失败，已跳过：%s' % e, flush=True)
        finally:
            self._close_topic(before)


def finish_discussion(driver, API, iframe_element, discussion_choice, course_name):
    """处理当前页所有讨论模块，返回汇总统计。"""
    discussion = Discussion(driver, API, iframe_element, discussion_choice, course_name)
    discussion.main()
    s = discussion.stats
    try:
        from core import runtime
        runtime.bump('discussion_ok', s.get('ok', 0))
        runtime.bump('discussion_failed', s.get('failed', 0))
    except Exception:
        pass
    print('[讨论] 汇总：共 %d 个话题，成功 %d / 跳过 %d / 失败 %d'
          % (s['total'], s['ok'], s['skipped'], s['failed']), flush=True)
    return s
