# AI生成
# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.
import random
import time
import re
import os
import ast
import asyncio
import traceback

from selenium.common import NoSuchElementException
from selenium.webdriver.common.by import By

from task.tool.common import turn_page, Common, save_diagnostic_screenshot
from task.tool.no_secret import DecodeSecret
from task.tool import color
import sys
import io
from task.tool.ai_wen_da import main
from task.tool.DeepSeekAsk import DeepSeekAsk, DeepSeekAskImage
from core import ai_config
from task.tool.send_wx import send_error
from core import runtime
from task.tool import quota
from core import paths
from core import browser as browser_ext

# 设置默认编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')

# 图片题（题干/选项是图片）交给多模态模型时用的提示词。
# 用户要求：只返回字母；多选返回多个字母；选项多于 4 个就按字母顺序继续。
_IMAGE_QUESTION_PROMPT = (
    '下面是一道题目的截图，题干和选项都在图片里。\n'
    '请只返回正确选项的字母，不要解释、不要标点、不要引号、不要换行。\n'
    '- 单选题、判断题：只返回一个字母，例如 B\n'
    '- 多选题：返回多个字母，例如 ACD\n'
    '- 选项超过 4 个：按字母顺序继续使用 E、F、G…\n'
    '如果图片里完全看不到字母，就按选项从上到下的顺序返回序号，例如 2 或 1,3。'
)

# 图片题截图的最长边：太大会让请求体过大、耗时变长，1600px 足够看清题目和选项
_IMAGE_MAX_SIDE = 1600

# 学习平台题目图片点开后的「全屏看图」遮罩：
# 常见实现是 #outerdiv + #innerdiv，另有一些页面用 fixed 浮层铺满屏幕。
# 这层遮罩会把后续所有点击吃掉，看起来就是「图片全屏了，程序卡住不动」。
_VIEWER_SCAN_JS = r"""
return (function () {
  var hits = [];
  function visible(el) {
    try {
      var s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' &&
             parseFloat(s.opacity || '1') > 0.2;
    } catch (e) { return false; }
  }
  function fullscreen(el) {
    try {
      if (getComputedStyle(el).position !== 'fixed') return false;
      var r = el.getBoundingClientRect();
      return r.width >= innerWidth * 0.6 && r.height >= innerHeight * 0.6;
    } catch (e) { return false; }
  }
  function mark(el) { return ((el.id || '') + ' ' + (el.className || '')).toLowerCase(); }
  function skip(el) { return /face|popdiv|popok|popcontent|popuplogin/.test(mark(el)); }
  function kill(el) {
    if (!el || !el.parentNode || skip(el)) return;
    el.parentNode.removeChild(el);
    hits.push(el.id || el.className || el.tagName);
  }
  var known = ['#outerdiv', '#imgShow', '.img-view', '.image-view',
               '.imgViewer', '.viewer-mask', '.big-img-box', '.imgBox'];
  for (var i = 0; i < known.length; i++) {
    var nodes = document.querySelectorAll(known[i]);
    for (var j = 0; j < nodes.length; j++) {
      var el = nodes[j];
      if (visible(el) && (el.id === 'outerdiv' || fullscreen(el))) kill(el);
    }
  }
  if (!document.body) return hits;
  var kids = Array.prototype.slice.call(document.body.children);
  for (var k = 0; k < kids.length; k++) {
    var c = kids[k];
    if (!c.tagName || /^(SCRIPT|STYLE|LINK|IFRAME|NOSCRIPT)$/.test(c.tagName)) continue;
    if (skip(c)) continue;
    if (visible(c) && fullscreen(c) && c.querySelector('img')) kill(c);
  }
  return hits;
})()
"""

_VIEWER_OPEN_JS = r"""
return (function () {
  var known = ['#outerdiv', '#imgShow', '.img-view', '.image-view',
               '.imgViewer', '.viewer-mask', '.big-img-box', '.imgBox'];
  function shown(el) {
    try {
      var s = getComputedStyle(el), r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' &&
             r.width >= innerWidth * 0.6 && r.height >= innerHeight * 0.6;
    } catch (e) { return false; }
  }
  for (var i = 0; i < known.length; i++) {
    var nodes = document.querySelectorAll(known[i]);
    for (var j = 0; j < nodes.length; j++) { if (shown(nodes[j])) return true; }
  }
  if (!document.body) return false;
  var kids = Array.prototype.slice.call(document.body.children);
  for (var k = 0; k < kids.length; k++) {
    var c = kids[k];
    if (!c.tagName || /^(SCRIPT|STYLE|LINK|IFRAME|NOSCRIPT)$/.test(c.tagName)) continue;
    if (shown(c) && c.querySelector('img')) return true;
  }
  return false;
})()
"""


def image_viewer_is_open(driver) -> bool:
    """当前页面是不是盖着一层全屏看图遮罩（只检查，不修改页面）。"""
    try:
        return bool(driver.execute_script(_VIEWER_OPEN_JS))
    except Exception:
        return False


def close_image_viewer(driver, esc=True) -> int:
    """关掉题目图片的全屏预览层，返回清掉的层数。"""
    if esc:
        try:
            browser_ext.press_escape(driver)
        except Exception:
            pass
    try:
        removed = driver.execute_script(_VIEWER_SCAN_JS) or []
    except Exception:
        return 0
    if removed:
        print(color.yellow('已关闭图片全屏预览层: %s' % '、'.join(map(str, removed))), flush=True)
    return len(removed)


def _is_image_url(url: str) -> bool:
    """浏览器是不是被导航到了图片本身（点到 <a href="xxx.jpg"> 的典型症状）。"""
    path = (url or '').split('?')[0].split('#')[0].lower()
    return path.endswith(IMAGE_SUFFIXES)


def _as_answer_list(raw):
    """把题库 / AI 返回的答案解析成纯文本列表。

    题库是第三方 HTTP 接口，返回内容不可信：这里只接收字面量（ast.literal_eval），
    绝不能用 eval —— 畸形返回会抛异常打断整场测验，恶意返回会在本机执行任意代码。
    不是合法字面量时只按分隔符做纯文本切分（切分不会执行任何东西），仍为空就当「无答案」，
    交给下一级兜底。
    """
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    if not text:
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError):
        value = None
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    text = text.strip('[]').strip()
    cleaned = [part.strip().strip('\'"').strip() for part in re.split(r'[,，]', text)]
    return [part for part in cleaned if part]


def _shrink_image(src, dst, max_side=_IMAGE_MAX_SIDE):
    """把截图等比缩到最长边 max_side 以内，返回最终文件路径。

    题目截图动辄 800x4000，base64 后请求体会非常大、容易超时；
    压缩到 1600px 仍然能看清题干和 ABCD，代价也小。
    """
    try:
        from PIL import Image
        with Image.open(src) as image:
            image = image.convert('RGB')
            width, height = image.size
            scale = max_side / float(max(width, height))
            if scale >= 1:
                return src
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                                 Image.LANCZOS)
            jpg = os.path.splitext(dst)[0] + '.jpg'
            image.save(jpg, 'JPEG', quality=88)
        try:
            os.remove(src)
        except OSError:
            pass
        return jpg
    except Exception:
        return src


def _parse_finish_ratio(value):
    """把设置项「搜到90%的题自动提交」解析成 0.9；已经是数字就直接用。

    重做一场测验时会把上一次解析出的 float 再传进来（Answer 递归调用自己），
    旧代码对它再跑一次 `re.search`，于是抛
    `TypeError: expected string or bytes-like object, got 'float'`，整场测验直接放弃。
    """
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r'(\d+)%', str(value or ''))
    if match:
        return int(match.group(1)) / 100
    return value


# 提交确认弹窗兜底：确认按钮没有稳定 id/class 时按文字点（严格相等，避免误点）
_POPUP_OK_BY_TEXT_JS = r"""
const texts = ['确定', '确认', '提交', '是'];
for (const t of texts) {
  for (const node of document.querySelectorAll('a,button,span,div')) {
    if (node.offsetParent === null) { continue; }
    const s = (node.textContent || '').replace(/\s+/g, '');
    if (s === t) { node.click(); return t; }
  }
}
return '';
"""

# 中文题型 -> 缓存里用的编码（与 ai_wen_da.main 的 typ_dict 保持一致）
QUIZ_TYPE_CODES = {'单选题': '0', '多选题': '1', '填空题': '2', '判断题': '3',
                   '简答题': '9', '论述题': '9', '名词解释': '9'}

# 提交后结果页：按题块收集 class 与文本，用于判断这一题平台判对还是判错。
# 各校/各版本页面结构差异很大，这里只做「宽收集」，判定放在 Python 里，
# 且**数量对不上就整体放弃**——错位的缓存比没有缓存危害大得多。
_HARVEST_RESULT_JS = r"""
const blocks = document.querySelectorAll(
  '.questionLi, .TiMu, [class*="questionLi"], .mark_item, .answerList .item');
const out = [];
for (const b of blocks) {
  let cls = String(b.className || '');
  const nodes = b.querySelectorAll('*');
  for (let i = 0; i < nodes.length && i < 60; i++) {
    cls += ' ' + String(nodes[i].className || '');
  }
  out.push({
    cls: cls.slice(0, 600),
    text: String(b.innerText || '').replace(/\s+/g, '').slice(0, 300)
  });
}
return out;
"""


class Answer:
    # 复习模式绝不作用于测验/作业：重做会覆盖已有成绩。
    # 本类不继承 Common，写在这里是为了让「哪些类型参与复习」有一处可查，
    # 也防止以后被改成继承 Common 时意外被重刷。
    REVIEWABLE = False

    def __init__(self, driver, test_frame, course_name, api,work_choice,after_finish_question,times=0,
                 frame_selector=None):
        self.driver=driver
        self.frame = test_frame
        self._frame_selector = frame_selector
        self.decodeSecret = None
        self.questionList0 = []
        self.no_answer_dit = {}# 存储无答案的题目
        self.answer_list = []
        self.course_name = course_name
        self.driver=driver
        self.API_KEY=api
        self.work_choice=work_choice
        self.turn_page=turn_page
        self.after_finish_question = _parse_finish_ratio(after_finish_question)

        self.ans_rate =None
        self.all_title_dit={}
        self.num_option_dit = {}
        self.questionType_list = []
        self.all_title = ''
        self.num_answer_dit = {}
        self.only_title_text = []
        self.reword_time = 0
        self.all_optionWebElementList = []
        self.image_questions = set()   # 题干或选项是图片的题号，改用多模态识别
        self.image_unanswered = set()  # 多模态也没答出来的图片题，最后统一提示
        self.skipped_questions = set() # 程序答不了的题型（计算/简答/填空…）：不联网、不点选项
        # 主观题本应答上却没答成（无答案 / 写入失败）。提交不可逆，据此拦截自动提交。
        self.unanswered_subjective = set()
        # 反向白名单：只有 _write_answer 回读确认「答案真的在作答区里」才登记。
        # submit() 要求本场每道主观题都在这个集合里，防止「看着 7/7 其实交白卷」。
        self.subjective_written = set()
        # 「AI 尽力完成」模式下被放行、准备走 AI 作答的题号（默认策略）
        self.needs_ai_questions = set()
        # 提交结果：'' 未提交 / 'submitted' 平台明确提示成功 /
        #           'unknown' 点了提交但平台没给任何提示 / 'saved' 只保存了 / 'failed' 被平台拒绝
        # 上层 `Quiz.verify_finished()` 用它区分「真的交上去了」和「只是点了没反应」
        # （2026-11-19：测验常常不打任务点勾，只看 ans-job-finished 会把成功判成失败）。
        self.submit_state = ''
        self.times = times
        if self.work_choice is not None:
            self.main()

    # 这些题型程序既点不了选项、也填不了空：联网搜也是白花额度，直接跳过（用户明确要求）
    UNANSWERABLE_TYPES = ('简答题', '论述题', '填空题', '名词解释',
                          '计算题', '连线题', '其他', '未知')

    # 可点选项的题型 / 主观题型
    CHOICE_TYPES = ('单选题', '多选题', '判断题')
    ESSAY_TYPES = ('简答题', '论述题', '填空题', '名词解释')

    @classmethod
    def question_kind(cls, question_type):
        """把题型分三类：choice=能点选项、essay=主观题、unknown=程序不认识（如计算题）。"""
        if question_type in cls.CHOICE_TYPES:
            return 'choice'
        if question_type in cls.ESSAY_TYPES:
            return 'essay'
        return 'unknown'

    # 一次最多批量问 AI 这么多道题（避免一场大测验把额度刷爆）
    MAX_AI_BATCH = 15

    # 这些状态词出现即认为这场测验已经做完/提交，不要重复submit
    _FINISHED_STATUS_WORDS = ('已完成', '已提交', '已交', '批阅')

    def _quiz_finished_by_status(self):
        """判断这场测验是否已经完成/提交，返回 (是否完成, 依据描述)。

        旧实现读 iframe 前一个兄弟 div 的 aria-label，只要含「已完成」就整场跳过。
        可那个 div 常常是同一章节里**别的任务点**（比如已看完的视频），
        于是没做过的测验也被当成「已完成」——用户看到的就是「遇见章节测验直接跳过」。
        现在只看测验自己的状态元素 `testTit_status`，取不到再退到「本 iframe 的父容器」标记。
        """
        try:
            self._enter_context()
            element = self.driver.find_element(By.CLASS_NAME, 'testTit_status')
            text = (element.text or '').strip()
            if text:
                for word in self._FINISHED_STATUS_WORDS:
                    if word in text:
                        return True, f'测验状态={text}'
                return False, f'测验状态={text}'
        except Exception:
            pass
        if self.frame is not None:
            try:
                parent_class = self.frame.find_element(By.XPATH, '..').get_attribute('class') or ''
                if 'ans-job-finished' in parent_class:
                    return True, '父容器 ans-job-finished'
            except Exception:
                pass
        return False, ''

    def main(self):
        # 滚动到测试
        try:
            self.driver.execute_script("arguments[0].scrollIntoView();", self.frame)
        except Exception:
            pass
        # 只按「测验自己」的状态判断是否已完成（见 _quiz_finished_by_status）
        finished, reason = self._quiz_finished_by_status()
        if image_viewer_is_open(self.driver):
            close_image_viewer(self.driver)
        if finished:
            print(color.green(f'测试已完成（{reason}），跳过'), flush=True)
            return
        else:
            print(color.yellow(f'测验未标记完成（{reason or "没有找到状态标记"}），开始作答'), flush=True)
            self.get_title_option()
            print(color.red('正在搜索中，请耐心等待...'))
            has_ai = ai_config.is_key_valid(self.API_KEY)
            # 题库查询与「有没有配 AI Key」无关：未配 Key 只意味着没有 AI 兜底，
            # 不代表可以跳过题库。旧实现把哨兵 Key 分支直接建成「全部无答案」，
            # 于是没填 Key 的用户整场测验都不会查题库，必然空答（审计 P1）。
            self.use_ai_wen_da()
            if has_ai:
                for i, answer in self.num_answer_dit.items():
                    if (not answer and i not in self.image_questions
                            and i not in self.skipped_questions
                            and self.questionType_list[i] not in self.UNANSWERABLE_TYPES):
                        self.no_answer_dit[i] = self.all_title_dit[i]
                self.use_deepseek()
            else:
                print(color.yellow('未配置有效的 AI Key：本轮只用题库作答，不做 AI 兜底'), flush=True)
            # 查答案期间页面可能重渲染/跳转，答题前统一把上下文切回来
            turn_page(self.driver, '学生学习页面')
            if not self._enter_context():
                print(color.red('答题页已不可用，跳过本次测验'), flush=True)
                return
            if len(self.skipped_questions) >= len(self.questionList0) and not self.image_questions:
                # 整场都是计算/简答这类程序答不了的题：不搜题、不点、不提交，避免空转
                print(color.yellow(f'本场 {len(self.skipped_questions)} 道题都无法自动作答，'
                                   f'整场跳过（未消耗 AI 额度）'), flush=True)
                return
            print(color.green('开始答题'), flush=True)
            for title_num in self.num_answer_dit.keys():
                if self.finish_title(title_num):
                    self.reword_time += 1
                else:
                    # 兜底：任何没答成的主观题都登记，submit() 据此拒绝自动提交。
                    # 覆盖 AI 未答出 / 写入失败 / 定位失败 / 答题异常 全部失败路径。
                    self._mark_subjective_unanswered(title_num)
                time.sleep(1)
            if self.skipped_questions:
                nums = '、'.join(str(i + 1) for i in sorted(self.skipped_questions))
                print(color.yellow(f'跳过 {len(self.skipped_questions)} 道无法自动作答的题'
                                   f'（第 {nums} 题），未消耗 AI 额度'), flush=True)
            if self.image_questions:
                answered = sorted(self.image_questions - self.image_unanswered)
                failed = sorted(self.image_unanswered)
                if answered:
                    nums = '、'.join(str(i + 1) for i in answered)
                    print(color.green(f'图片题：{len(answered)} 道已由多模态模型作答（第 {nums} 题）'),
                          flush=True)
                if failed:
                    nums = '、'.join(str(i + 1) for i in failed)
                    print(color.yellow(f'图片题：{len(failed)} 道未能作答（第 {nums} 题），'
                                       f'截图在 record/图片题/，请自行检查'), flush=True)
            total = len(self.questionList0)
            self.ans_rate = self.reword_time / total if total else 0
            print(self.reword_time, '/', total)
            try:
                info = quota.stats()
                print(color.yellow(
                    f'本轮累计联网 {info["total"]} 次（最近一分钟 {info["per_minute"]}/'
                    f'{info["max_per_minute"]} 次，按频率限流，不设总量上限）'), flush=True)
            except Exception:
                pass
            message = self.submit()
            if not message and self.times<3:
                print(color.green('开始第{}次重新做题'.format(self.times+1)))
                self.driver.switch_to.default_content()
                print(color.green('重新做题'), flush=True)
                Answer(self.driver, self.frame, self.course_name, self.API_KEY, self.work_choice,
                       self.after_finish_question, self.times + 1,
                       frame_selector=self._frame_selector)
            elif self.times>=3:
                print(color.red('测试答题失败，已重试3次'), flush=True)
    def get_title_option(self):
        if not self._enter_context():
            raise RuntimeError('无法进入答题页面（题目列表为空）')
        # 实例化 DecodeSecret 类
        self.decodeSecret = DecodeSecret(1)
        print(color.yellow("启用字体解密"), flush=True)
        self.decodeSecret.getFontFace(self.driver)
        # 获取页面中的所有题目
        self.questionList0 = self.driver.find_elements(By.CSS_SELECTOR, '[class="singleQuesId"]')
        print(color.yellow("当前测试共有{}题".format(len(self.questionList0))), flush=True)

        for i in range(len(self.questionList0)):
            self.title_num = i + 1
            self.option_text_list = []
            self.title_and_option_element = self.questionList0[i]
            self.title_and_option_text = re.sub(r'\s+', '',
                                               self.decodeSecret.decode(self.title_and_option_element.text).strip())
            # 整题是图片时拿不到标题元素，不能让一道题把整场测验打断
            self.title_element = None
            try:
                self.title_element = self.title_and_option_element.find_element(
                    By.CSS_SELECTOR, '[class="clearfix font-cxsecret fontLabel"]')
                self.title = re.sub(r'\s+', '', self.decodeSecret.decode(self.title_element.text).strip())
            except NoSuchElementException:
                self.title = ''
            # 题目类型 / 题目文本（没有【】前缀时整体当作题干）
            if '【' in self.title and '】' in self.title:
                self.questionType = self.title[self.title.find("【") + 1: self.title.find("】")]
                self.title_text = self.title[self.title.find("】") + 1:]
            else:
                self.questionType = ''
                self.title_text = self.title
                # 题干元素里没有【xx题】时，再从整题文本里捞一次（计算题常见）
                leftover = re.search(r'【([^】]{1,8})】', self.title_and_option_text or '')
                if leftover:
                    self.questionType = leftover.group(1)
                    self.title_text = (self.title_text or '').replace(
                        leftover.group(0), '').strip()
                    print(color.yellow(f'第{i+1}题从题目文本补出题型：{self.questionType}'),
                          flush=True)
            self.only_title_text.append(self.title_text)
            self.questionType_list.append(self.questionType or '未知')

            if self.question_kind(self.questionType) == 'choice':
                options = self.title_and_option_element.find_elements(By.TAG_NAME, 'li')
                self.all_optionWebElementList.append(options)
                for option in options:
                    self.option_text_list.append(
                        re.sub(r'\s+', '', self.decodeSecret.decode(option.text).strip()))
                # 图片选项的 .text 可能是空串（也可能只剩 alt/文件名），
                # 只要有一个选项是「图片 + 无文字」，AI 就只能瞎猜，直接跳过。
                image_option = any(
                    not text.strip() and self._element_has_image(option)
                    for option, text in zip(options, self.option_text_list))
                # 题干里有插图/公式图片：文字可能取到一部分（如「函数的间断点为()」），
                # 但关键信息在图片里，纯文字搜题必错，必须走多模态。
                stem_image = self._question_has_stem_image(self.title_and_option_element)
                if len(self.option_text_list) >= 2 and not any(t.strip() for t in self.option_text_list):
                    self._mark_image_question(i, self.title_and_option_element)
                    print(color.yellow(f'第{i+1}题：选项为图片，改由多模态作答'), flush=True)
                elif not self.title_text.strip():
                    self._mark_image_question(i, self.title_and_option_element)
                    print(color.yellow(f'第{i+1}题：题干为图片，改由多模态作答'), flush=True)
                elif stem_image:
                    self._mark_image_question(i, self.title_and_option_element)
                    print(color.yellow(f'第{i+1}题：题干含图片/公式，改由多模态作答'), flush=True)
                elif image_option:
                    self._mark_image_question(i, self.title_and_option_element)
                    print(color.yellow(f'第{i+1}题：选项含无文字图片，改由多模态作答'), flush=True)
            elif self.question_kind(self.questionType) == 'essay':
                self.all_optionWebElementList.append(None)
                self.option_text_list = ['']
                if runtime.ai_effort_enabled():
                    # 默认放行：题干已由 decodeSecret 解好，交给 AI 生成答案后走填写分支
                    self._mark_needs_ai(i)
                    print(color.yellow(f'第{i+1}题：{self.questionType}交给 AI 尽力完成'),
                          flush=True)
                else:
                    self.skipped_questions.add(i)
                    print(color.yellow(f'第{i+1}题：{self.questionType}无法自动作答，跳过（不联网）'),
                          flush=True)
            else:
                # 题型没解析出来（题干/整题是图片，或计算题这类非常规题）：
                # 用「真实的选项数量」+「有没有作答编辑器」来判断——
                #   有作答编辑器 / 真实选项不足 2 个：计算/填空/简答，只截图、绝不调 AI；
                #   有 ≥2 个真实选项：多半是整题图片的选择题，交给多模态。
                if not self._can_answer_by_options(self.title_and_option_element):
                    self.all_optionWebElementList.append(None)
                    self.option_text_list = ['']
                    # 无论哪种策略都留一张截图，方便真机核对
                    self._save_image_question(i, self.title_and_option_element)
                    if runtime.ai_effort_enabled() and self._has_rich_editor(
                            self.title_and_option_element):
                        # 有作答区（填空/简答富文本）才放行，计算题/连线题仍跳过
                        self._mark_needs_ai(i)
                        print(color.yellow(f'第{i+1}题无选项但有作答区，交给 AI 尽力完成'),
                              flush=True)
                    else:
                        self.skipped_questions.add(i)
                        print(color.yellow(f'第{i+1}题无作答选项（{self.questionType or "题型未知"}），'
                                           f'已跳过（截图留档，不调 AI）'), flush=True)
                else:
                    options = self.title_and_option_element.find_elements(By.TAG_NAME, 'li')
                    self.all_optionWebElementList.append(options)
                    for option in options:
                        self.option_text_list.append(
                            re.sub(r'\s+', '', self.decodeSecret.decode(option.text).strip()))
                    self._mark_image_question(i, self.title_and_option_element)
                    print(color.yellow(f'第{i+1}题题型未解析但有选项，按图片题交给多模态'),
                          flush=True)

            # 无论是否可作答都要登记，否则 only_title_text/questionType_list 与题号错位
            self.all_title_dit[i] = self.title_and_option_text
            self.num_option_dit[i] = self.option_text_list

    @staticmethod
    def _has_rich_editor(question_element):
        """题目里有没有"填写答案"的富文本/文本框（填空、计算、简答都有）。

        这是最硬的判据：只要出现作答编辑器，就说明这题是主观题，程序点不了选项。
        """
        if question_element is None:
            return False
        try:
            driver = getattr(question_element, 'parent', None)
            if driver is None:
                return False
            return bool(driver.execute_script(
                "const r = arguments[0];"
                "return !!(r.querySelector('[contenteditable=\"true\"], .w-e-text-container,"
                " textarea, .edui-body-container, iframe.w-e-iframe]'));",
                question_element))
        except Exception:
            return False

    @staticmethod
    def _count_real_options(question_element):
        """数这道题有几个**真正的选项**。

        计算题/填空题的题目块里同样会出现 `<li>`（富文本编辑器的工具栏就是 li 组成的），
        所以不能只看"有没有 li"。真正的选项满足其一：
        - 带 radio/checkbox 或 `num_option` 标号；
        - 文本以 A./B./C. 这类标号开头；
        - 里面是图片（图片选项）。
        编辑器区域里的 li 一律不算。
        """
        if question_element is None:
            return 0
        try:
            driver = getattr(question_element, 'parent', None)
            if driver is None:
                return 0
            count = driver.execute_script("""
                const root = arguments[0];
                const editor = '.w-e-toolbar, .w-e-text-container, [class*="toolbar"],'
                             + ' [class*="editor"], .edui-toolbar, .edui-body-container';
                let count = 0;
                for (const li of root.querySelectorAll('li')) {
                  if (li.closest(editor)) { continue; }
                  if (li.querySelector('input[type=radio], input[type=checkbox],'
                        + ' [class*="num_option"], [class*="option"]')) { count++; continue; }
                  const text = (li.innerText || '').replace(/\\s+/g, '');
                  if (/^[A-Ha-h][.、．)）]/.test(text)) { count++; continue; }
                  if (li.querySelector('img')) { count++; continue; }
                }
                return count;
            """, question_element)
            return int(count or 0)
        except Exception:
            return 0

    def _can_answer_by_options(self, question_element):
        """这道题能不能靠点选项作答。"""
        if self._has_rich_editor(question_element):
            return False
        return self._count_real_options(question_element) >= 2

    def _mark_image_question(self, index, element):
        """登记一道图片题：先记为「未作答」，答出来以后再从 image_unanswered 里移除。"""
        self.image_questions.add(index)
        self.image_unanswered.add(index)
        self._save_image_question(index, element)

    def _save_image_question(self, title_num, element):
        """整题（题干+选项）截图，返回文件路径；失败返回空串。

        截图有两个用途：① 交给多模态模型作答；② 万一答不出来，留档给用户手动补。
        """
        try:
            folder = paths.record('图片题')
            os.makedirs(folder, exist_ok=True)
            name = f'第{title_num + 1}题_{time.strftime("%H%M%S")}.png'
            raw_path = os.path.join(folder, name)
            element.screenshot(raw_path)
            path = _shrink_image(raw_path, raw_path)
            print(color.yellow(f'  图片题截图已保存: record/图片题/{os.path.basename(path)}'), flush=True)
            return path
        except Exception as e:
            print(color.red(f'  图片题截图保存失败: {e}'), flush=True)
            return ''

    @staticmethod
    def _parse_choice_letters(text, option_count):
        """把模型回答解析成选项下标列表（从 0 开始）。

        约定回答只有字母（多选返回多个字母）。兼容 "B."、"ACD"、"A,C"、"答案：B" 这类写法，
        也兼容模型按序号回答（1/2/3…）；越界和重复一律丢弃。
        """
        if not isinstance(text, str) or not text.strip() or option_count <= 0:
            return []
        cleaned = re.sub(r'[\s，,、。．.：:；;、"\'“”()（）]', '', text)
        if re.fullmatch(r'[A-Za-z]{1,8}', cleaned):
            letters = list(cleaned)
        else:
            # 只认大写字母，避免把 "answer" 里的 e 误当成选项 E
            letters = re.findall(r'[A-Z]', text)
        indices = []
        for letter in letters:
            index = ord(letter.upper()) - ord('A')
            if 0 <= index < option_count and index not in indices:
                indices.append(index)
        if indices:
            return indices
        for number in re.findall(r'\d+', text):
            index = int(number) - 1
            if 0 <= index < option_count and index not in indices:
                indices.append(index)
        return indices

    def _ask_image_question(self, title_num, question):
        """把整题截图交给多模态模型，返回选项下标列表；失败返回 []。"""
        if not ai_config.is_key_valid(self.API_KEY):
            print(color.yellow(f'第{title_num + 1}题为图片题，未配置有效的 API Key，无法识图'), flush=True)
            return []
        path = self._save_image_question(title_num, question)
        if not path:
            return []
        # 选项数量以页面上的 <li> 为准：整题是图片时 num_option_dit 里只有 [''] 占位，
        # 用它算数量会把 B/C/D 全判成越界，等于白问。
        option_count = len(self.all_optionWebElementList[title_num] or [])
        if option_count <= 0:
            option_count = len(self.num_option_dit.get(title_num) or [])
        text = DeepSeekAskImage(self.API_KEY, _IMAGE_QUESTION_PROMPT, [path])
        indices = self._parse_choice_letters(text, option_count)
        if indices:
            picked = ''.join(chr(ord('A') + index) for index in indices)
            print(color.green(f'第{title_num + 1}题（图片题）AI 答案: {picked}'), flush=True)
        else:
            print(color.red(f'第{title_num + 1}题（图片题）AI 未返回可用答案'), flush=True)
        return indices

    def _finish_image_title(self, title_num):
        """图片题：截图问多模态 → 按返回的字母点击选项。

        答不出来时：随机答题模式下随机点一个，其余情况跳过并记录，最后统一提示。
        """
        question = self._relocate(title_num)
        if question is None:
            print(color.red(f'第{title_num + 1}题（图片题）定位失败，跳过'), flush=True)
            self.image_unanswered.add(title_num)
            return False
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", question)
        except Exception:
            pass
        indices = self._ask_image_question(title_num, question)
        if not indices:
            options = self.all_optionWebElementList[title_num] or []
            if self.work_choice == '随机答题' and options:
                index = random.randrange(len(options))
                print(color.red(f'第{title_num + 1}题改用随机作答: {chr(ord("A") + index)}'), flush=True)
                indices = [index]
            else:
                self.image_unanswered.add(title_num)
                return False
        clicked = False
        for index in indices:
            if self._click_option(title_num, index):
                clicked = True
            time.sleep(0.5)
        if clicked:
            self.image_unanswered.discard(title_num)
        else:
            self.image_unanswered.add(title_num)
        return clicked

    def _search_bank(self, index):
        """查题库（多源并发），返回纯文本答案列表；失败返回 []。"""
        try:
            raw = asyncio.run(main(self.questionType_list[index],
                                   self.only_title_text[index],
                                   self.num_option_dit[index], self.API_KEY))
        except Exception as e:
            print(color.red(f'第{index + 1}题搜索失败：{e}'), flush=True)
            return []
        return _as_answer_list(raw)

    def _option_already_checked(self, index) -> bool:
        """这道选择题是否已经有选项被选中。

        2026-11-22 用户反馈：「原本已经作答的题目，还调用 AI 再搜一遍，浪费额度」。
        根因：`use_ai_wen_da()` 一上来就遍历所有题去查题库/问 AI，
        「是否已回答」的判断却放在后面 `_click_option()` 里 —— 那时答案已经花钱买到了。

        这里只处理**选择题**（单选/多选/判断）：`aria-checked="true"` 是强信号，
        不会误判。填空题/简答题的空位里常有占位文字，贸然当成「已作答」会漏答，
        所以主观题交给 `_fill_blank()` 在写入前判断。
        """
        try:
            options = self.all_optionWebElementList[index] or []
        except Exception:
            return False
        for option in options:
            try:
                if option.get_attribute('aria-checked') == 'true':
                    return True
            except Exception:
                continue
        return False

    def use_ai_wen_da(self):
        needs_ai = getattr(self, 'needs_ai_questions', set())
        for i in self.all_title_dit.keys():
            if self._option_already_checked(i):
                # 已经选过答案了：不查题库、不问 AI，也不再去点（省额度、不覆盖人工答案）
                print(color.yellow(f'第{i+1}题已有选中答案，跳过搜索（省额度）'), flush=True)
                self.num_answer_dit[i] = []
                self.skipped_questions.add(i)
                continue
            if i in self.image_questions:
                # 图片题没有可检索的文本，查题库纯属浪费请求
                self.num_answer_dit[i] = []
                continue
            if (i in needs_ai and runtime.ai_effort_enabled()
                    and ai_config.is_key_valid(self.API_KEY)):
                # 放行模式：题库对填空/简答会直接跳过，这里逐题问 AI 更可靠。
                # AI 没答出来就自动降级成「截图跳过」，不卡住整场测验。
                # 未配有效 Key 时这个分支不会进（否则每题都白跑一次失败请求），
                # 直接落到下面的题库搜索路径。
                try:
                    answers = self._ask_one(i)
                except Exception as e:
                    answers = []
                    print(color.red(f'第{i+1}题 AI 作答异常：{e}'), flush=True)
                if answers:
                    self.num_answer_dit[i] = answers
                    print(color.green(f'第{i+1}题 AI 已给出答案：{answers}'), flush=True)
                else:
                    self.num_answer_dit[i] = []
                    needs_ai.discard(i)
                    self.skipped_questions.add(i)
                    print(color.yellow(f'第{i+1}题 AI 未答出，已降级为截图跳过'), flush=True)
                continue
            if i in self.skipped_questions or self.questionType_list[i] in self.UNANSWERABLE_TYPES:
                # 简答/论述/填空/名词解释/计算等程序答不了的题：直接跳过，不去联网（省额度）
                self.num_answer_dit[i] = []
                print(color.yellow(f'第{i+1}题：{self.questionType_list[i]}无法自动作答，跳过'),
                      flush=True)
                continue
            if self.times==0 and self.work_choice!='随机答题':
                print(color.green('\n<===================  分隔线  ===================>\n'), flush=True)
                self.answer_list = self._search_bank(i)
            elif self.work_choice!='随机答题':
                self.answer_list=[]
            elif self.work_choice=='随机答题':
                self.answer_list=[random.choice(self.num_option_dit[i])]
                print(color.red(f'本次采用随机答题,随机答案为：{self.answer_list}'),flush=True)
            if not self.answer_list:
                self.num_answer_dit[i] = []
                # print(color.red('无答案，跳过'), flush=True)
                continue
            else:
                self.num_answer_dit[i] = self.answer_list

    def use_deepseek(self):
        """批量问一次 AI；答案段数与题目数对不上时，退化为逐题提问。

        旧实现按 '/' 的位置把答案回填到题目：少一段就 parts[num] 越界（后面的题全不答），
        多一段就静默错位（第 N 题的答案填给第 N+1 题，日志却完全正常）。
        现在先校验段数，对不上就不猜位置，改成逐题重新问。
        （AI 答案不再写缓存：写错一次会被永久固化，见 ai_wen_da 的 cacheable 标记。）
        """
        if not self.no_answer_dit:
            return

        # 额度保护：一次最多批量问 15 道题；超出的先跳过，避免一场大测验把额度刷爆
        keys = list(self.no_answer_dit.keys())
        if len(keys) > self.MAX_AI_BATCH:
            print(color.yellow(f'待 AI 作答的题有 {len(keys)} 道，超过单次上限 '
                               f'{self.MAX_AI_BATCH} 道，其余先跳过'), flush=True)
            keys = keys[:self.MAX_AI_BATCH]

        # 打印清楚「是为哪几道题问的」：否则日志紧跟在某道**缓存命中**的题后面，
        # 看起来像是「明明有缓存还去问 AI」（2026-11-19 用户反馈的误解点）。
        title_map = getattr(self, 'all_title_dit', None) or {}
        briefs = []
        for key in keys[:5]:
            try:
                briefs.append('第%s题「%s」' % (key + 1, str(title_map.get(key, ''))[:16]))
            except Exception:
                briefs.append(str(key))
        brief = '、'.join(briefs)
        if len(keys) > 5:
            brief += ' 等 %d 道' % len(keys)
        print(color.red(f'正在使用deepseek搜题（题库未命中的 {len(keys)} 道：{brief}），'
                        f'请耐心等待...'), flush=True)
        title = ''.join(self.no_answer_dit[key] for key in keys)
        answers = DeepSeekAsk(self.API_KEY, title, 'all')
        parts = self._split_batch_answer(answers, len(keys))
        if parts is None:
            print(color.yellow(f'AI 返回的答案段数与待答题数({len(keys)})对不上，'
                               f'改为逐题提问，避免答案错位'), flush=True)
            for key in keys:
                self.num_answer_dit[key] = self._ask_one(key)
            return
        for key, part in zip(keys, parts):
            self.num_answer_dit[key] = [p.strip() for p in re.split(r'[,，]', part) if p.strip()]

    @staticmethod
    def _split_batch_answer(raw, expected):
        """把 'C/B,ABCD' 拆成 expected 段；数量不符返回 None（绝不按位置硬回填）。"""
        text = raw.strip() if isinstance(raw, str) else ''
        if not text:
            return None
        if text.startswith('[') and text.endswith(']'):
            text = text[1:-1].strip()
        if not text:
            return None
        if expected == 1:
            # 只有一道题时不存在错位风险，整段就是答案（省掉一次逐题提问）
            return [text]
        if '/' not in text:
            return None
        parts = re.split(r'/', text)
        # 模型常多给一个结尾 '/', 只容忍这一种「末尾空段」，其余数量不符一律降级
        while len(parts) > expected and not parts[-1].strip():
            parts.pop()
        if len(parts) != expected:
            return None
        return parts

    @staticmethod
    def _answer_to_text(answer):
        """AI / 题库返回的答案可能是列表或字符串，统一成可填入的文本。"""
        if isinstance(answer, (list, tuple)):
            return '\n'.join(str(x) for x in answer if x is not None)
        return '' if answer is None else str(answer)

    def _sync_editor(self, el):
        """写入后触发同步：UEditor 一类富文本编辑器把内容放在 iframe 里，
        必须触发 input/change/blur（或 UE.instants[*].sync()）才会写回表单隐藏字段，
        否则页面看着有字、平台提交时读到的仍是空值 —— 这正是「交白卷」的成因之一。
        """
        try:
            self.driver.execute_script(
                "var el=arguments[0];"
                "try{el.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}"
                "try{el.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}"
                "try{if(el.blur)el.blur();}catch(e){}"
                "try{if(window.UE&&UE.instants){for(var k in UE.instants)"
                "{try{UE.instants[k].sync();}catch(e){}}}}catch(e){}", el)
        except Exception:
            pass

    def _write_answer(self, question_element, blank_index, text):
        """往第 blank_index 个作答区写文本；**回读确认真的写进去了**才返回 True。

        学习通不同版式用 textarea / contenteditable / ueditor iframe / wangEditor。
        旧实现 `send_keys` 之后无条件 return True，且 iframe 分支只要节点里有
        任意文本（可能是题干/占位符）就当成「已有答案」直接返回 —— 结果简答题
        页面上其实空着，程序却计入答题率，达到阈值后自动提交，空答案不可逆。
        这里改成写入后回读比对，任何一种写进去且内容匹配才算成功。
        """
        value = str(text or '').strip()
        if not value:
            return False

        def looks_written(current):
            a = re.sub(r'\s+', '', str(current or ''))
            b = re.sub(r'\s+', '', value)
            return bool(b) and b in a

        def read_back(el):
            try:
                current = el.get_attribute('value')
            except Exception:
                current = ''
            if not current:
                try:
                    current = self.driver.execute_script(
                        'return arguments[0].innerText || arguments[0].textContent || "";', el)
                except Exception:
                    current = ''
            return current

        for by, selector in ((By.CSS_SELECTOR, 'textarea'),
                             (By.CSS_SELECTOR, '[contenteditable="true"]')):
            try:
                elements = question_element.find_elements(by, selector)
                if blank_index >= len(elements):
                    continue
                el = elements[blank_index]
                try:
                    el.click()
                except Exception:
                    self.driver.execute_script('arguments[0].click();', el)
                try:
                    self.driver.execute_script("arguments[0].value = '';", el)
                except Exception:
                    pass
                el.send_keys(value)
                self._sync_editor(el)
                if looks_written(read_back(el)):
                    return True
            except Exception:
                continue

        entered = False
        try:
            frames = question_element.find_elements(By.TAG_NAME, 'iframe')
            if blank_index < len(frames):
                self.driver.switch_to.frame(frames[blank_index])
                entered = True
                nodes = self.driver.find_elements(By.TAG_NAME, 'p')
                if not nodes:
                    nodes = self.driver.find_elements(
                        By.CSS_SELECTOR, '[contenteditable="true"], body')
                if nodes:
                    node = nodes[0]
                    # 无论节点里原来有什么（题干 / 占位符 / 上一题残留），一律清空重写；
                    # 旧实现「节点里有任意文本就 return True」是白卷的根因之一。
                    try:
                        self.driver.execute_script("arguments[0].innerHTML = '';", node)
                    except Exception:
                        pass
                    try:
                        node.click()
                    except Exception:
                        self.driver.execute_script('arguments[0].click();', node)
                    node.send_keys(value)
                    self._sync_editor(node)
                    if looks_written(read_back(node)):
                        return True
        except Exception:
            pass
        finally:
            if entered:
                try:
                    self.driver.switch_to.parent_frame()
                except Exception:
                    pass
        return False

    def _mark_needs_ai(self, i):
        """登记一道放行题；兼容纯桩测试用 object.__new__ 造的实例。"""
        if not hasattr(self, 'needs_ai_questions'):
            self.needs_ai_questions = set()
        self.needs_ai_questions.add(i)

    def _blank_elements(self, key):
        """返回该题的空位元素列表（按 DOM 顺序）。

        学习通填空题的空位是 InpDIV（作业页是 edui iframeholder），
        必须**先点击空位**才会展开/激活对应的编辑器；不能只在题目里按索引找 iframe，
        否则多个答案会追加进同一个框（2026-11-06 真机截图确认）。
        """
        try:
            q = self.questionList0[key]
        except Exception:
            return []
        # 合并所有类型的空位并去重。
        # 旧实现「第一个有结果的 selector 就返回」，所以同一题里混用 InpDIV 与 textarea 时
        # 会少算空位（真机日志「第17题填写 1/1 个空」，但页面上明明有两个空）。
        seen, merged = set(), []
        for by, selector in (
                (By.CLASS_NAME, 'InpDIV'),
                (By.CSS_SELECTOR, '[class="edui-editor-iframeholder edui-default"]'),
                (By.CSS_SELECTOR, '[contenteditable="true"]'),
                (By.CSS_SELECTOR, 'textarea')):
            try:
                for element in q.find_elements(by, selector):
                    marker = getattr(element, 'id', None) or id(element)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    merged.append(element)
            except Exception:
                continue
        return merged

    def _blank_frames(self, key):
        """题目里的 iframe 列表，作为「空位内找不到编辑器」时的回退。"""
        try:
            return self.questionList0[key].find_elements(By.TAG_NAME, 'iframe')
        except Exception:
            return []

    def _editor_has_text(self, node, value):
        """回读校验：写入的内容真的出现在节点里才算成功（2026-11-19 审计 P0-9）。

        旧 `_fill_blank` 发完 send_keys 就无条件 return True：填空编辑器
        readonly / 富文本未激活 / 节点不可编辑时，send_keys 不抛异常但内容
        根本没进去，于是「已写入」被登记，白卷闸门直接放行。
        """
        try:
            current = node.get_attribute('value')
        except Exception:
            current = ''
        if not current:
            try:
                current = self.driver.execute_script(
                    'return arguments[0].innerText || arguments[0].textContent || "";', node)
            except Exception:
                current = ''
        # 必须**严格相等**：旧实现用「包含」，所以往 `2` 后面再追加一个 `2` 也能判成功，
        # 于是同一个空被写了三遍变成 `222`（2026-11-22 真机截图）。
        a = re.sub(r'[\s\u200b]+', '', str(current or ''))
        b = re.sub(r'[\s\u200b]+', '', str(value or ''))
        return bool(b) and a == b

    def _node_any_text(self, node) -> str:
        """读节点里已有的内容（value 或 innerText），用于「已有内容就不再写」。

        2026-11-22 用户反馈：填空题「有答案、不知道，搜到了又写了一遍」，
        「有的空被重复写了好几遍」。根因之一是 `_fill_blank` 只看写入是否成功，
        从不看这个空**是不是本来就有内容**；之二是候选 iframe 用了 `frames[index:]`
        （本题后面的所有 iframe），回读失败就会把同一个答案往后面几个空里写。
        """
        try:
            current = node.get_attribute('value') or ''
        except Exception:
            current = ''
        if not current:
            try:
                current = self.driver.execute_script(
                    'return (arguments[0].innerText || arguments[0].textContent || "");', node)
            except Exception:
                current = ''
        text = str(current or '').strip()
        if not text:
            return ''
        # 富文本空位常带"请输入答案"之类的占位提示，绝不能被当成"用户已填"，
        # 否则会一个空都不写、还把这题登记成已完成（等于放行白卷）。
        compact = re.sub(r'\s+', '', text)
        hints = ('请输入', '请填写', '输入答案', '点击此处', '在此输入', '填写答案')
        if len(compact) <= 8 and any(h in compact for h in hints):
            return ''
        return text

    @staticmethod
    def _same_answer(current, target) -> bool:
        """已有内容与目标答案是否等价（去空格/标点后比较）。"""
        def norm(x):
            return re.sub(r'[\s,，、;；。.．"\'`]+', '', str(x or '')).lower()
        a, b = norm(current), norm(target)
        if not a or not b:
            return False
        if a == b:
            return True
        # 目标较长时才允许「包含」判定，避免 "1" 命中 "11"
        return len(b) >= 2 and b in a

    def _clear_editor(self, node) -> bool:
        """清空编辑器并**确认真的空了**；清不干净返回 False（调用方就不写）。

        真机 2026-11-22 截图铁证：第 17 题第 1 空最后显示成 `222` —— 同一个空被写了 3 次。
        两个原因叠加：
          · `innerHTML=''` / `value=''` 对富文本无效（UE 的内容在 `<p>` 里）；
          · 回读判定用的是「包含」，`2` 在 `22` 里就算成功，于是越写越长。
        所以这里必须：全选删除 → JS 清空 → 回读确认，确认不了就不写。
        """
        try:
            node.click()
        except Exception:
            pass
        try:
            from selenium.webdriver.common.keys import Keys
            node.send_keys(Keys.CONTROL, 'a')
            node.send_keys(Keys.DELETE)
        except Exception:
            pass
        try:
            self.driver.execute_script(
                "arguments[0].innerHTML = '';"
                "try { arguments[0].value = ''; } catch(e) {}"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                node)
        except Exception:
            pass
        return not self._node_any_text(node)

    def _fill_blank(self, blank_el, text, frames, index):
        """激活第 index 个空位并写入答案。**一个空只写一次**，回读严格相等才算成功。

        2026-11-22 真机：一个空被写 3 次（显示 `222`）。根因是候选编辑器列表里
        同一个 iframe 出现了多次（`blank_el` 内的 iframe 与 `frames[index]` 是同一个），
        每写一次「回读」都因「包含」判定通过不了就继续换下一个候选再写。
        现在：候选去重；写完无论成败都 `return`，绝不对同一个空写第二遍。
        """
        # 优先**真实点击**：学习通的填空空位（InpDIV / ueditor iframeholder）要靠
        # focus 事件才展开编辑器；纯 JS `el.click()` 只派发事件、常常不触发 focus。
        activated = False
        try:
            blank_el.click()
            activated = True
        except Exception:
            pass
        if not activated:
            try:
                self.driver.execute_script("arguments[0].click();", blank_el)
            except Exception:
                return False
        # ueditor 是点击之后才往 DOM 里插 iframe 的，给它一点时间挂载
        time.sleep(0.25)
        # 点击可能把空位元素本身替换掉（StaleElement）：失效就交回上层重新取
        try:
            blank_el.tag_name
        except Exception:
            return False
        value = str(text)

        # 候选：空位内可编辑节点 → 空位内 iframe → 题目里对应的那一个 iframe。
        # **空位内已经有 iframe 时就不再追加 frames[index]**，避免同一个编辑器进两次。
        node_targets = []
        for by, selector in ((By.CSS_SELECTOR, 'textarea'),
                             (By.CSS_SELECTOR, '[contenteditable="true"]')):
            try:
                node_targets.extend(blank_el.find_elements(by, selector))
            except Exception:
                continue
        inner_frames = []
        try:
            inner_frames = blank_el.find_elements(By.TAG_NAME, 'iframe')
        except Exception:
            pass

        for target in node_targets:
            existing = self._node_any_text(target)
            if existing:
                if self._same_answer(existing, value):
                    print(color.yellow('  该空已有相同答案，跳过不重写'), flush=True)
                    return True
                print(color.yellow('  该空已有其它内容（%s），保留不覆盖' % existing[:12]),
                      flush=True)
                return False
            if not self._clear_editor(target):
                continue
            try:
                target.send_keys(value)
            except Exception:
                continue
            # 写完就结束：绝不换下一个候选再写一遍
            if self._editor_has_text(target, value):
                return True
            return False

        frames_to_try = list(inner_frames)
        if not frames_to_try and index < len(frames):
            frames_to_try = [frames[index]]

        for frame in frames_to_try:
            entered = False
            try:
                self.driver.switch_to.frame(frame)
                entered = True
                nodes = self.driver.find_elements(By.TAG_NAME, 'p')
                if not nodes:
                    nodes = self.driver.find_elements(
                        By.CSS_SELECTOR, '[contenteditable="true"], body')
                if not nodes:
                    continue
                node = nodes[0]
                existing = self._node_any_text(node)
                if existing:
                    if self._same_answer(existing, value):
                        print(color.yellow('  该空已有相同答案，跳过不重写'), flush=True)
                        return True
                    print(color.yellow('  该空已有其它内容（%s），保留不覆盖'
                                       % existing[:12]), flush=True)
                    return False
                if not self._clear_editor(node):
                    continue
                try:
                    node.click()
                except Exception:
                    pass
                node.send_keys(value)
                if self._editor_has_text(node, value):
                    return True
                return False
            except Exception:
                continue
            finally:
                if entered:
                    try:
                        self.driver.switch_to.parent_frame()
                    except Exception:
                        pass
        return False

    def _count_blanks(self, key):
        """数一道填空题有几个空位；数不出返回 0，调用方会降级为截图跳过。

        学习通不同版式用 InpDIV / ueditor iframeholder / contenteditable / textarea，
        这里按优先级逐个探测，不写死一种选择器。
        """
        # 与 _blank_elements 同一口径，避免「数出 2 个空、只填 1 个」这类错位
        return len(self._blank_elements(key))

    def _ask_one(self, key):
        """逐题问 AI，返回答案列表；失败返回 []。"""
        typ = self.questionType_list[key]
        options = self.num_option_dit[key] or []
        prompt = f'【{typ}】{self.only_title_text[key]}{options}'
        if typ == '填空题':
            blanks = self._count_blanks(key)
            if blanks:
                prompt += (f'\n本题共有 {blanks} 个空，请给出恰好 {blanks} 个答案，'
                           f'按空位顺序放在列表里，不要合并成一个字符串。')
        try:
            raw = DeepSeekAsk(self.API_KEY, prompt, typ)
        except Exception as e:
            print(color.red(f'第{key + 1}题 AI 兜底失败：{e}'), flush=True)
            return []
        return _as_answer_list(raw)


    # 题目/选项定位器：作业页与测验页不同，收集时记录，重定位时复用
    _q_selector = '[class="singleQuesId"]'
    _opt_by = By.TAG_NAME
    _opt_value = 'li'
    _context_mode = 'quiz'      # quiz: 需要走 iframe 链；window: 作业页在自己的窗口里
    _frame_selector = None      # 测验模块 iframe 的选择器，由 Quiz 注入

    # ---------------- 文档上下文 ----------------
    def _has_questions(self):
        try:
            return bool(self.driver.find_elements(By.CSS_SELECTOR, self._q_selector))
        except Exception:
            return False

    def _switch_work_frame(self):
        """切进「作业/测验模块」那个 iframe；元素失效时按选择器重找。"""
        # 注意：Selenium 里 switch_to.frame(None) 不会报错，而是原地不动，
        # 所以要显式跳过 None，否则会误判成「已经切进去了」。
        if self.frame is not None:
            try:
                self.driver.switch_to.frame(self.frame)
                return True
            except Exception:
                pass
        if not self._frame_selector:
            return False
        try:
            frames = self.driver.find_elements(By.CSS_SELECTOR, self._frame_selector)
        except Exception:
            return False
        for frame in frames:
            try:
                self.driver.switch_to.frame(frame)
                self.frame = frame
                return True
            except Exception:
                continue
        return False

    def _enter_context(self):
        """把浏览器切回「题面所在的文档」，并确认能读到题目。

        以前是在 main() 里写死 switch_to.frame(self.frame) + frame_content：
        页面一旦重渲染，self.frame 就是失效元素，switch 直接抛异常，整场测验被跳过。
        这里每次都从 default_content 重新走一遍，并按 id/选择器兜底。
        """
        if self._context_mode != 'quiz':
            return self._has_questions()
        driver = self.driver
        for outer in ('iframe', None):
            try:
                driver.switch_to.default_content()
            except Exception:
                return False
            if outer:
                try:
                    driver.switch_to.frame(outer)
                except Exception:
                    continue
            if not self._switch_work_frame():
                # 有的页面题目直接铺在模块页上，没有单独的模块 iframe
                if self._has_questions():
                    return True
                continue
            # 模块内部的 frame_content（部分页面没有这一层）
            entered_inner = False
            try:
                driver.switch_to.frame('frame_content')
                entered_inner = True
            except Exception:
                pass
            if self._has_questions():
                return True
            if not entered_inner:
                try:
                    frames = driver.find_elements(By.TAG_NAME, 'iframe')
                except Exception:
                    frames = []
                for frame in frames:
                    try:
                        driver.switch_to.frame(frame)
                        if self._has_questions():
                            return True
                    except Exception:
                        pass
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        break
        return False

    def _recover_image_navigation(self):
        """误点到图片链接时浏览器会停在图片本身，退回去继续答题。

        既可能是顶层窗口跳到图片（新标签/整页），也可能只是题目所在的 iframe
        被导航成图片，所以两种 URL 都要看。
        """
        urls = []
        try:
            urls.append(self.driver.current_url or '')
        except Exception:
            pass
        try:
            urls.append(self.driver.execute_script('return location.href') or '')
        except Exception:
            pass
        if not any(_is_image_url(u) for u in urls):
            return
        print(color.yellow('页面被导航到了图片地址，正在返回上一页'), flush=True)
        try:
            self.driver.execute_script('history.back();')
        except Exception:
            pass
        time.sleep(2)
        try:
            if _is_image_url(self.driver.current_url or ''):
                self.driver.back()
                time.sleep(2)
        except Exception as e:
            print(color.red(f'返回失败: {e}'), flush=True)

    @staticmethod
    def _element_has_image(element):
        try:
            return bool(element.find_elements(By.TAG_NAME, 'img'))
        except Exception:
            return False

    @staticmethod
    def _element_has_meaningful_image(element, min_width=40, min_height=16):
        """元素里是否有「像题干插图/公式」的图片（过滤掉 16×16 的小图标）。

        题干常常是「文字 + 公式图片」：文字能取到（例如「函数的间断点为()」），
        但关键的公式在 <img> 里，纯文字搜题只会答错。所以只要题干里有像样的图片，
        就当成图片题交给多模态。
        """
        if element is None:
            return False
        try:
            for image in element.find_elements(By.TAG_NAME, 'img'):
                try:
                    size = image.size or {}
                    if (size.get('width', 0) >= min_width
                            and size.get('height', 0) >= min_height):
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    @staticmethod
    def _question_has_stem_image(question_element, min_width=40, min_height=16):
        """题干区域是否有图片：排除选项 <li> 里的图，只算选项之外的部分。"""
        if question_element is None:
            return False
        try:
            driver = getattr(question_element, 'parent', None)
            if driver is None:
                return False
            return bool(driver.execute_script("""
                const root = arguments[0];
                const minW = arguments[1], minH = arguments[2];
                for (const img of root.querySelectorAll('img')) {
                  if (img.closest('li')) { continue; }      // 选项里的图不算题干
                  const w = img.naturalWidth || img.width || 0;
                  const h = img.naturalHeight || img.height || 0;
                  if (w >= minW && h >= minH) { return true; }
                }
                return false;
            """, question_element, min_width, min_height))
        except Exception:
            return False

    def _relocate(self, title_num):
        """重新定位题目与其选项元素。

        AI 查答案往往要几十秒，期间页面会重渲染，之前抓到的 WebElement 会失效
        （Edge 报 no such element: element not found），所以每次作答前重新抓一遍。
        """
        self._recover_image_navigation()
        if not self._enter_context():
            return None
        try:
            questions = self.driver.find_elements(By.CSS_SELECTOR, self._q_selector)
            if not questions or title_num >= len(questions):
                return None
            self.questionList0 = questions
            question = questions[title_num]
            options = question.find_elements(self._opt_by, self._opt_value)
            if options:
                while len(self.all_optionWebElementList) <= title_num:
                    self.all_optionWebElementList.append(None)
                self.all_optionWebElementList[title_num] = options
            return question
        except Exception:
            return None

    # 点击选项时优先命中的「字母/文字」节点（A/B/C/D 通常在这些节点里）
    _LABEL_JS = r"""
    return (function (li) {
      var sels = ['i', 'em', 'b', 'span', '.num_option', '.option-label', '.answer_p', 'p'];
      for (var i = 0; i < sels.length; i++) {
        var nodes = li.querySelectorAll(sels[i]);
        for (var j = 0; j < nodes.length; j++) {
          var node = nodes[j];
          if (node.querySelector && node.querySelector('img')) continue;
          var txt = (node.textContent || '').trim();
          if (!txt) continue;
          node.click();
          return node.tagName + ':' + txt.substring(0, 8);
        }
      }
      li.click();
      return 'LI';
    })(arguments[0])
    """

    def _js_click_option(self, option):
        """JS 点选项：优先点 A/B/C/D 所在的文字节点，退而点 <li> 本身。

        返回点中了什么（日志/测试用），失败返回空串。
        含图片的选项必须走这里：原生点击会落在 <img> 上并弹全屏遮罩。
        """
        try:
            return self.driver.execute_script(self._LABEL_JS, option) or ''
        except Exception:
            return ''

    @staticmethod
    def _option_selected(option):
        try:
            return option.get_attribute('aria-checked') == 'true'
        except Exception:
            return False

    def _click_option(self, title_num, idx):
        """点击某题第 idx 个选项；元素失效时重定位后重试一次。

        选项里带图片时不能原生点击：鼠标落在 <img> 上会触发学习平台的全屏看图，
        遮罩盖住整页后所有点击都点不动，看起来就是程序卡死。
        含图片的选项改走 JS：先点 A/B/C/D 所在的文字节点，没选中再点 <li> 本身。
        """
        for attempt in (0, 1):
            options = self.all_optionWebElementList[title_num] or []
            if idx is None or idx < 0 or idx >= len(options):
                return False
            option = options[idx]
            try:
                if option.get_attribute('aria-checked') == 'true':
                    print(color.red('已回答，无需重复回答'), flush=True)
                    return True
                has_image = self._element_has_image(option)
                if has_image:
                    clicked = self._js_click_option(option)
                    if clicked:
                        print(color.green(
                            f'  已点击选项 {chr(ord("A") + idx)} 的文字节点({clicked})'), flush=True)
                    if not self._option_selected(option):
                        # 文字节点没触发选中（事件可能绑在 li 上），再点一次容器
                        self.driver.execute_script("arguments[0].click();", option)
                        if not self._option_selected(option):
                            print(color.yellow(
                                f'  选项 {chr(ord("A") + idx)} 点击后未检测到选中状态'), flush=True)
                else:
                    option.click()
                # 万一页面还是弹出遮罩，立刻收掉，别让它把后面的题全挡住
                if has_image or image_viewer_is_open(self.driver):
                    close_image_viewer(self.driver)
                return True
            except Exception:
                if attempt == 0:
                    close_image_viewer(self.driver)
                    self._relocate(title_num)
                else:
                    return False
        return False

    def finish_title(self, title_num):
        if title_num in self.skipped_questions:
            # 特殊题型（计算/简答/填空/题型未知）：直接跳过，不搜题、不点选项、不调 AI
            print(color.yellow(f'第{title_num + 1}题已跳过（{self.questionType_list[title_num]}）'),
                  flush=True)
            return False
        if title_num in self.image_questions:
            # 图片题（题干/选项是图片）：截图交给多模态模型，按返回的字母点选项
            ok = self._finish_image_title(title_num)
            if not ok:
                self._mark_subjective_unanswered(title_num)
            return ok
        question = self._relocate(title_num)
        if question is None:
            print(color.red(f'第{title_num + 1}题定位失败，跳过'), flush=True)
            self._mark_subjective_unanswered(title_num)
            return False
        try:
            # 滚动到题目
            self.driver.execute_script("arguments[0].scrollIntoView();", question)
        except:
            pass
        print(color.green(f'正在回答第{title_num + 1}题...'), flush=True)
        answer=self.num_answer_dit[title_num]
        #去重
        print(color.green(f'该题为{self.questionType_list[title_num]}，答案：{answer}'), flush=True)
        option_num=0
        try:
            if self.questionType_list[title_num]=='单选题' or self.questionType_list[title_num]=='判断题':
                matched = None
                for idx, option_text in enumerate(self.num_option_dit[title_num]):
                    if answer and answer[0] and str(answer[0]) in option_text:
                        matched = idx
                        break
                if matched is None and answer:
                    # 答案只给了字母、选项文本又不含该字母时，按字母序号兜底
                    letter = str(answer[0]).strip()[:1].upper()
                    if letter in 'ABCDEFGH':
                        matched = ord(letter) - 65
                if matched is None:
                    print(color.red('未匹配到选项，跳过该题'), flush=True)
                    return False
                if not self._click_option(title_num, matched):
                    print(color.red('选项点击失败（元素可能已失效），跳过该题'), flush=True)
                    return False
                return True
            elif self.questionType_list[title_num] == '多选题':
                self.answer_num=[]
                if len(answer)==1:
                    lst = answer[0]
                else:
                    lst = answer
                for option in self.num_option_dit[title_num]:
                    for ans in lst:
                        if ans in option:
                           self.answer_num .append(option_num)
                    option_num+=1
                self.answer_num=list(set(self.answer_num))
                if not self.answer_num:
                    print(color.red('多选题未匹配到选项，跳过该题'), flush=True)
                    return False
                # 点击正确答案
                for ans in self.answer_num:
                    time.sleep(1)
                    self._click_option(title_num, ans)
                return True

            elif self.questionType_list[title_num] in ('简答题', '论述题', '名词解释'):
                text = self._answer_to_text(answer)
                if not text.strip():
                    print(color.red(f'第{title_num + 1}题没有可用答案，降级为截图跳过'), flush=True)
                    self._mark_subjective_unanswered(title_num)
                    self._save_image_question(title_num, self.questionList0[title_num])
                    return False
                if self._write_answer(self.questionList0[title_num], 0, text):
                    self._mark_subjective_written(title_num)
                    return True
                print(color.red(f'第{title_num + 1}题作答区写入失败，降级为截图跳过'), flush=True)
                self._mark_subjective_unanswered(title_num)
                self._save_image_question(title_num, self.questionList0[title_num])
                return False
            elif self.questionType_list[title_num]=='填空题':
                parts = ([str(x) for x in answer]
                         if isinstance(answer, (list, tuple)) else [str(answer)])
                blank_els = self._blank_elements(title_num)
                blanks = len(blank_els)
                frames = self._blank_frames(title_num)
                # 只有「单个字符串 + 空位数 > 1 + 拆分后数量正好等于空位数」才拆。
                # 否则「生我者父母，知我者鲍子也。」这类含中文逗号的正常答案
                # 会被误拆，只填前半段（2026-11-06 真机截图确认的 bug）。
                if (len(parts) == 1 and blanks > 1
                        and (',' in parts[0] or '，' in parts[0])):
                    split = [p.strip() for p in re.split(r'[,，]', parts[0]) if p.strip()]
                    if len(split) == blanks:
                        parts = split
                if not blanks or len(parts) < blanks:
                    print(color.red(f'第{title_num + 1}题数不出空位或答案不足'
                                    f'（空位 {blanks} / 答案 {len(parts)}），降级为截图跳过'),
                          flush=True)
                    self._mark_subjective_unanswered(title_num)
                    self._save_image_question(title_num, self.questionList0[title_num])
                    return False
                filled = 0
                for number in range(blanks):
                    # 点击空位可能让 DOM 变化，每轮重新取一次元素，避免 StaleElement
                    fresh = self._blank_elements(title_num)
                    if number >= len(fresh):
                        break
                    if self._fill_blank(fresh[number], parts[number], frames, number):
                        filled += 1
                    time.sleep(0.3)
                if filled < blanks:
                    # 旧实现只要 filled > 0 就登记「已写入」，3 个空只写进 1 个
                    # 也算主观题已确认 → 提交闸门放行（审计 P0-9）。
                    # 现在必须每个空都回读通过，否则按未完成处理（只保存不提交）。
                    print(color.red(
                        f'第{title_num + 1}题只写进 {filled}/{blanks} 个空，按未完成处理'),
                        flush=True)
                    self._mark_subjective_unanswered(title_num)
                    if filled == 0:
                        self._save_image_question(title_num, self.questionList0[title_num])
                    return False
                print(color.green(f'第{title_num + 1}题已填写 {filled}/{blanks} 个空'), flush=True)
                self._mark_subjective_written(title_num)
                return True
            else:
                kind = ''
                try:
                    kind = self.questionType_list[title_num]
                except Exception:
                    kind = self.questionType or ''
                print(color.red(f'第{title_num + 1}题（{kind}）暂时无法作答'), flush=True)
                # 题型不在 ESSAY_TYPES（计算题 / "填空题（每空1分）" 等变体）但
                # 题目里确实有作答编辑器：必须登记，否则闸门看不见它，
                # 一个字没写也会被提交（2026-11-19 审计 P0-9）。
                has_editor = False
                try:
                    has_editor = self._has_rich_editor(self.questionList0[title_num])
                except Exception:
                    has_editor = False
                if has_editor:
                    self._mark_subjective_unanswered(title_num, force=True)
                return False
        except Exception as e:
            print(color.red(f'答题时出错了{e}'), flush=True)
            # 异常同样会留下空答案：登记后 submit() 才不会把它交上去
            self._mark_subjective_unanswered(title_num)
            return False

    def _work_button(self, selector):
        """找保存/提交按钮并点掉。

        按钮在「模块页」而不是 frame_content 里，答题结束时 driver 往往还待在
        frame_content，直接 find_element 会报 no such element（以前就是这么崩的）。
        这里按 当前帧 → 父帧 → 更外层 逐级找，并且原生点击失败时退化成 JS 点击。
        """
        for _ in range(3):
            button = None
            try:
                button = self.driver.find_element(By.CSS_SELECTOR, selector)
            except NoSuchElementException:
                button = None
            except Exception:
                button = None
            if button is not None:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", button)
                    time.sleep(0.3)
                except Exception:
                    pass
                try:
                    button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", button)
                return True
            try:
                self.driver.switch_to.parent_frame()
            except Exception:
                break
        return False

    def _mark_subjective_unanswered(self, title_num, force=False):
        """登记一道没答成的主观题；客观题 / 未知题型默认不登记（避免误拦自动提交）。

        force=True：题型不在 ESSAY_TYPES 但**题目里确实有作答编辑器**时强制登记
        （计算题、"填空题（每空1分）"这类变体标签）。旧实现只看题型白名单，
        这类题一个字没写也绕过了提交闸门（2026-11-19 审计 P0-9）。

        兼容用 object.__new__ 造出来的旧测试桩：属性缺失时就地创建，绝不抛
        AttributeError —— 那会让 finish_title 掉进 except，连降级截图都留不下。
        """
        if not force:
            try:
                kinds = self.questionType_list
                if not (0 <= title_num < len(kinds)) or kinds[title_num] not in self.ESSAY_TYPES:
                    return
            except Exception:
                return
        bag = getattr(self, 'unanswered_subjective', None)
        if bag is None:
            bag = set()
            self.unanswered_subjective = bag
        bag.add(title_num)

    def _mark_subjective_written(self, title_num):
        """只有 _write_answer 回读确认「答案真的在作答区里」才登记；兼容 __new__ 旧桩。"""
        bag = getattr(self, 'subjective_written', None)
        if bag is None:
            bag = set()
            self.subjective_written = bag
        bag.add(title_num)

    def _unanswered_subjective_numbers(self):
        """仍未作答的主观题题号（0-based）。

        两个来源：① 主观题本应答上却没答成（无 AI 答案 / 作答区写入失败 /
        定位失败 / 答题过程异常）；② 被整体跳过的主观题。计算题/连线题这类
        程序本来就答不了的题不计入，否则会让自动提交几乎永远不触发。
        """
        blocked = set(getattr(self, 'unanswered_subjective', ()) or ())
        kinds = getattr(self, 'questionType_list', ()) or ()
        for idx in (getattr(self, 'skipped_questions', ()) or ()):
            if 0 <= idx < len(kinds) and kinds[idx] in self.ESSAY_TYPES:
                blocked.add(idx)
        return sorted(blocked)

    def _save_only(self):
        """只保存不提交；保存按钮找不到也不重答整场测验。"""
        self.submit_state = 'saved'
        print(color.yellow('3秒后保存'), flush=True)
        time.sleep(3)
        if not self._work_button('[class="btnSave workBtnIndex"]'):
            print(color.red('未找到保存按钮，请手动保存'), flush=True)
        return True

    def submit(self):
        formatted_result = "{:.2%}".format(self.ans_rate or 0)
        print(color.red(f'本次答题率为{formatted_result}'), flush=True)
        if image_viewer_is_open(self.driver):
            close_image_viewer(self.driver)

        # 提交不可逆，主观题两道硬闸（缺一不可）：
        # ① 反向白名单：本场每道主观题都必须由 _write_answer 回读确认「答案真的
        #    写进了作答区」。旧实现只统计「搜到/点到的题」，7 道全简答题即使一个字
        #    都没写进去也会算出 7/7=100% 并自动交白卷
        #    （2026-11-12 20:49 真机日志，另一台电脑的 26.11.12.1322）。
        # ② 失败登记：无答案 / 写入失败 / 整题跳过 / 答题异常 / 定位失败。
        # 「随机答题」与「强制自动提交」也必须过这两道闸。
        kinds = getattr(self, 'questionType_list', ()) or ()
        essay = {i for i, k in enumerate(kinds) if k in self.ESSAY_TYPES}
        written = set(getattr(self, 'subjective_written', ()) or ())
        unverified = essay - written
        blocked = sorted(set(self._unanswered_subjective_numbers()) | unverified)
        if blocked:
            nums = '、'.join(str(i + 1) for i in blocked)
            print(color.red(
                f'还有 {len(blocked)} 道主观题未作答或未能确认答案已写入（第 {nums} 题）；'
                f'提交后无法修改，本次改为「仅自动保存」，请你手动补答后再提交。'),
                flush=True)
            return self._save_only()

        if self.after_finish_question == '仅自动保存':
            return self._save_only()

        try:
            threshold_ok = float(self.ans_rate or 0) >= float(self.after_finish_question)
        except (TypeError, ValueError):
            # 阈值是「强制自动提交」这类字符串时不做数值比较
            threshold_ok = False

        if (self.after_finish_question == '强制自动提交'
                or self.work_choice == '随机答题'
                or threshold_ok):
            print(color.yellow('3秒后提交'), flush=True)
            time.sleep(3)
            if not self._work_button('[class="btnSubmit workBtnIndex"]'):
                # 旧实现直接 return True：既没提交也没保存，作答随页面一起丢掉，
                # 上层还以为这场测验成功了（审计 D2）。改为落一次「保存」。
                print(color.red('当前测验找不到提交按钮，改为仅保存'), flush=True)
                return self._save_only()
            time.sleep(1)
            # 点击确认（弹窗在顶层文档里）
            self.driver.switch_to.default_content()
            state = self._click_popup_ok()
            if state == 'failed':
                # 有确认弹窗却点不动：此时提交并未发生，继续往下走只会得到
                # 「查不到成绩 + 平台未确认完成」这种无法定性的结果。
                print(color.red('提交确认按钮点不到（选择器失效或被遮挡），'
                                '改为仅保存，请手动点提交'), flush=True)
                self._dump_page('测验提交确认失败')
                return self._save_only()
            time.sleep(2)
            try:
                message = self.driver.find_element(By.ID, 'popcontent').text
            except Exception:
                message = ''
            if message:
                # 旧实现把 #popcontent 的**任何**文本都当失败：平台提示「提交成功」
                # 也会被判失败 → 整场重答并再次提交，最多 4 次（审计 D1）。
                if any(k in message for k in ('成功', '已提交', '已完成', '提交完成')):
                    self.submit_state = 'submitted'
                    print(color.green(f'平台提示：{message}'), flush=True)
                else:
                    self.submit_state = 'failed'
                    print(color.red(f'提交失败，原因：{message}'), flush=True)
                    self._click_popup_ok()
                    self._dump_page('测验提交被平台拒绝')
                    return False
            else:
                self.submit_state = 'unknown'
                print(color.yellow(f'平台未返回提交提示（确认按钮={state}），'
                                   f'按已提交继续，若成绩异常请查看日志'), flush=True)
            self.save_score()
            # 提交后如果结果页标了对错，把「答对」的答案固化进缓存（答错的不写）
            self._harvest_correct_answers()
        else:
            print(color.yellow(f'答题率未达到提交要求{self.after_finish_question}'), flush=True)
            return self._save_only()

        return True

    # 提交确认按钮的候选选择器（平台改版会换 id/class，多试几个）
    POPUP_OK_SELECTORS = ('#popok', '.popok', 'a.pop_ok', '#popokbtn')

    def _click_popup_ok(self):
        """点「提交」确认弹窗里的确定按钮。

        返回值：
          * `'clicked'` —— 点到了；
          * `'none'`    —— 页面上没有确认弹窗（一键提交的平台），无需确认；
          * `'failed'`  —— 有确认弹窗但点不动（选择器失效 / 被遮挡）。

        2026-11-19：旧实现只试 `#popok` 且**不看结果**。点不到时静默继续，
        后面既读不到成功提示也读不到成绩，最后由上层判「平台未确认完成」——
        根本分不清到底是「平台没弹窗」还是「我们没点到」，这也是本次
        「小测提交失败」无法定性的原因之一。
        """
        visible = False
        for selector in self.POPUP_OK_SELECTORS:
            try:
                nodes = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    if not node.is_displayed():
                        continue
                except Exception:
                    pass
                visible = True
                try:
                    node.click()
                    return 'clicked'
                except Exception:
                    try:
                        self.driver.execute_script("arguments[0].click();", node)
                        return 'clicked'
                    except Exception:
                        continue
        if visible:
            return 'failed'
        # 没有候选按钮时，先确认「确实存在确认弹窗容器」，再按文字点，
        # 避免在没弹窗的页面上误点页面里的「提交」按钮（会导致重复提交）。
        try:
            has_container = bool(self.driver.find_elements(By.ID, 'popcontent'))
        except Exception:
            has_container = False
        if not has_container:
            return 'none'
        try:
            hit = self.driver.execute_script(_POPUP_OK_BY_TEXT_JS)
        except Exception:
            hit = ''
        return 'clicked' if hit else 'failed'

    def _dump_page(self, tag):
        """把当前页面结构落盘（顶层 + iframe），用于排查提交类问题；失败静默。"""
        try:
            from core import dom_dump
            path = dom_dump.capture(self.driver, tag)
            if path:
                print(color.yellow(f'已保存页面结构：{path}'), flush=True)
        except Exception:
            pass
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

    def _collect_result_blocks(self):
        """在提交结果页采集题块；当前帧取不到时再进 `#iframe` 试一次。"""
        for frame in (None, 'iframe'):
            try:
                if frame:
                    self.driver.switch_to.default_content()
                    self.driver.switch_to.frame(frame)
                blocks = self.driver.execute_script(_HARVEST_RESULT_JS) or []
            except Exception:
                blocks = []
            if blocks:
                return blocks
        print(color.yellow('结果页没有识别到题块（可能是没有对错标记的页面）'), flush=True)
        return []

    def _harvest_correct_answers(self):
        """提交后把「平台判定答对」的题写进缓存，答错的绝不写。

        需求来源（2026-11-19）：部分测验的结果页会在每题后标出对错。答对说明我们
        提交的答案与平台一致，可以放心固化，下次同题直接命中；答错若也写缓存，
        以后同题会一直错下去，比没有缓存更糟。

        三条安全约束（宁可少缓存，不可缓存错）：
        1. **题块数量必须等于本次题目数**，否则整体放弃 —— 数量错位会把 A 题的答案
           记到 B 题头上，而缓存一旦写错就是长期的；
        2. 只认明确的「答对」标记（class 含 `dui` / 文本含「回答正确」），
           认不出就当没有，不猜；
        3. 只用本题**实际提交过**的答案回填（`num_answer_dit`），不从页面反推。
        """
        total = len(getattr(self, 'questionType_list', ()) or ())
        if total == 0:
            return
        blocks = self._collect_result_blocks()
        if not blocks:
            return
        if len(blocks) != total:
            print(color.yellow(f'提交结果页识别到 {len(blocks)} 个题块、本次共 {total} 道，'
                               f'数量对不上；为避免答案错位，本次不写缓存'), flush=True)
            self._dump_page('测验结果块数量不符')
            return
        api = None
        right = wrong = 0
        for index, block in enumerate(blocks):
            classes = str(block.get('cls', ''))
            text = str(block.get('text', ''))
            if ('cuo' in classes) or ('回答错误' in text) or ('答案错误' in text):
                wrong += 1
                continue
            if not (('dui' in classes) or ('回答正确' in text) or ('答案正确' in text)):
                wrong += 1
                continue
            answer = (getattr(self, 'num_answer_dit', {}) or {}).get(index) or []
            if not answer:
                continue
            try:
                if api is None:
                    from task.tool.ai_wen_da import AnswerAPI, Question
                    api = AnswerAPI()
                question = Question(
                    type=QUIZ_TYPE_CODES.get(self.questionType_list[index], '9'),
                    question=self.only_title_text[index],
                    options=(self.num_option_dit or {}).get(index) or [],
                    API=self.API_KEY)
                api.cache_answer(question, list(answer))
                right += 1
            except Exception:
                continue
        if right or wrong:
            print(color.green(f'提交结果回填：答对 {right} 道已写入缓存，'
                              f'答错/未判定 {wrong} 道不写缓存'), flush=True)

    def save_score(self):
        # save_process(self.course_name)
        title = ''
        try:
            element = self.driver.find_element(By.CLASS_NAME, 'prev_title')
            title = element.get_attribute('title') or ''
        except Exception:
            pass
        if not title:
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame('iframe')
                title = self.driver.find_element(By.CLASS_NAME, 'prev_title').get_attribute('title') or ''
            except Exception:
                pass
        score = '未查询到'
        try:
            self._enter_context()
            score = self.driver.find_element(By.CSS_SELECTOR, '.achievement i').text
        except Exception:
            print(color.yellow('未查询到本次测试成绩'), flush=True)
        try:
            with open(paths.record(f'《{self.course_name}》的成绩记录.txt'), 'a', encoding='utf-8') as f:
                f.write(
                    f'已完成:《{title}》章节中的测试题，完成时间：{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))}\n测试得分：{score}分(本次使用{self.work_choice})\n\n')
            print(color.green(f'已保存进度'), flush=True)
        except Exception:
            pass
class Quiz(Common):
    # 用「src 包含」匹配：平台前端发版会把 ?v=2025-1028-1629 换成新日期，
    # 有的部署还会把 src 写成绝对 URL（https://xxx/ananas/...），
    # 用 `^=` 只要前缀一变就认不出测验页、整章测验被静默跳过；`*=` 两者都能命中。
    IFRAME_SELECTOR = 'iframe[src*="/ananas/modules/work/index.html"]'

    def __init__(self, driver, course_name,API, choice,after_finish_question):
        super().__init__(
            driver,
            self.IFRAME_SELECTOR,
            "测验",
            course_name,
            # 考试/测验经常不算任务点：只要页面上有测验 iframe 就答，不再要求任务点图标
            require_mission=False
        )
        self.API = API
        self.choice = choice
        self.after_finish_question = after_finish_question
        self.driver = driver
        self.course_name = course_name
        self.session = None          # 保存 Answer 实例，供 verify_finished 读提交结果

    def verify_finished(self):
        """测验的完成判据：**平台确认提交**优先，其次才看任务点打勾。

        2026-11-19（用户 15:39 那次小测）：本类传 `require_mission=False`，因为
        平台上的测验/考试经常不算任务点；但父类 `verify_finished()` 只认
        `ans-job-finished`。两者矛盾——即使真的提交成功，只要平台不给测验打勾，
        就会打印「平台未确认完成，不写学习记录」，成绩记录永远不落地。

        现在的判定顺序：
          1. 平台明确提示「成功/已提交」→ 直接认定完成；
          2. 只做了保存（没提交）→ 明确不算完成；
          3. 点了提交但平台没给提示 → 回落到父类的任务点判定（保守，不假完成）。
        """
        state = getattr(getattr(self, 'session', None), 'submit_state', '')
        if state == 'submitted':
            print(color.green('平台已确认提交成功（不再要求任务点打勾）'), flush=True)
            return True
        if state == 'saved':
            print(color.yellow('本次只保存未提交，不记为完成'), flush=True)
            return False
        try:
            return bool(super().verify_finished())
        except Exception:
            return False

    def start(self):
        try:
            self.session = Answer(self.driver, self.iframe, self.course_name, self.API, self.choice,
                                  self.after_finish_question, frame_selector=self.IFRAME_SELECTOR)
        except Exception as e:
            error_msg = traceback.format_exc()
            send_error(
                "\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n" + error_msg)
            save_diagnostic_screenshot(self.driver, '测验出错')
            print(color.red(f'测验处理出错，跳过本场：{e}'), flush=True)
            print(color.yellow('具体原因请前往错误日志查看，请自行保存或提交，15秒后继续'), flush=True)
            # 出错时页面可能还盖着图片遮罩，先收掉再回学习页，否则后面整页都点不动
            try:
                close_image_viewer(self.driver)
            except Exception:
                pass
            turn_page(self.driver,'学生学习页面')
            time.sleep(15)
def finish_quiz(driver, course_name, API, choice,after_finish_question):
    # driver.switch_to.default_content()
    # driver.switch_to.frame('iframe')
    # test_frames = driver.find_elements(By.XPATH,
    #                                    '//iframe[@src="/ananas/modules/work/index.html?v=2025-1028-1629&castscreen=0"]')
    # print(color.magenta(f'已检测到{len(test_frames)}个测试'), flush=True)
    # i=0
    # for test_frame in test_frames:
    #     if i!=0:
    #         driver.switch_to.default_content()
    #         driver.switch_to.frame('iframe')
    #     print(f'当前处理第{i+1}个测试', flush=True)
    #     i+=1
    #     try:
    #         Answer(driver, test_frame, course_name, API, choice,after_finish_question)
    #     except:
    #         error_msg = traceback.format_exc()
    #         send_error(
    #             "\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n" + error_msg)
    #         print(color.yellow('出错了，具体原因请前往错误日志查看，请自行保存或提交,15秒后继续'), flush=True)
    #         turn_page(driver,'学生学习页面')
    #         time.sleep(15)
    quiz=Quiz(driver, course_name, API, choice,after_finish_question)
    quiz.main()
