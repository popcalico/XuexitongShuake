# -*- coding: utf-8 -*-
"""问卷处理（2026-11-19 从上游 26.11.18 移植，按本工程标准重写）。

上游 `finish_vote.py` 有三个**必然崩**的问题，这里全部修掉：

1. `confirm = find_elements(...)` 之后直接 `confirm.click()` —— list 没有 click，
   只要出现确认弹窗就抛 AttributeError，整页被记成「处理出错」；
2. 点完提交就当完成，**从不校验平台是否真的记了完成**（假完成）；
3. `find_element(By.CLASS_NAME,'breadcrumb').click()` 无保护，取不到就崩，
   而且异常时把 driver 留在 iframe 里，后续任务全部找不到元素。

现在的做法：
  * 点开问卷后**有界等待**题目渲染，兼容两种部署（顶层 `#voteIframe` / `#iframe` 内直接展开）；
  * 每题按题型随机选 radio / checkbox，没有选项就 textarea 填「无」；
  * 提交按钮与确认框都做「取不到就不点、点了就报告」，绝不对 list 调 click；
  * `finally` 一定切回 `default_content` 并尝试返回章节页；
  * 完成判据沿用全工程标准：**平台任务点打勾才算完成**，否则不写学习记录。
"""
import random
import time

from selenium.webdriver.common.by import By

from task.tool import color
from task.tool.common import Common, turn_page

VOTE_QUESTION_TIMEOUT = 8     # 在当前文档里等题目渲染
VOTE_IFRAME_TIMEOUT = 12      # 等顶层 #voteIframe 出现
VOTE_AFTER_SUBMIT = 3         # 提交后给平台更新时间


class Vote(Common):
    def __init__(self, driver, iframe_element, course_name):
        # 问卷不一定带任务点图标，所以不强制要求；完成与否交给 verify_finished 判断
        super().__init__(driver, iframe_element, '问卷', course_name,
                         require_mission=False)
        self.iframe_element = iframe_element

    # ---------------------------------------------------------------- 主流程
    def start(self):
        try:
            self._open_and_answer()
        except Exception as e:
            print(color.red(f'问卷处理异常（{type(e).__name__}）：{e}'), flush=True)
        finally:
            # 任何路径都必须把上下文切回学习页，否则后续任务全部找不到元素
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            try:
                turn_page(self.driver, '学生学习页面', timeout=8)
            except Exception:
                pass

    def _open_and_answer(self):
        # 1) 点开问卷（先滚到可见，普通点击失败再退 JS 点击）
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", self.iframe)
        except Exception:
            pass
        if not self._click(self.iframe):
            print(color.red('问卷打不开（点击失败），本次跳过'), flush=True)
            return

        # 2) 找题目
        questions = self._locate_questions()
        if not questions:
            print(color.red('未找到问卷题目，本次跳过（不写学习记录）'), flush=True)
            return
        print(color.green(f'问卷题目总数：{len(questions)}'), flush=True)

        # 3) 逐题作答
        answered = 0
        for num, question in enumerate(questions, 1):
            try:
                self._answer_one(question)
                answered += 1
                print(color.green(f'问卷第{num}题已作答'), flush=True)
            except Exception as e:
                print(color.yellow(f'问卷第{num}题作答失败（{type(e).__name__}），跳过'), flush=True)
            time.sleep(0.3)

        if answered == 0:
            print(color.red('一题都没答上，放弃提交（避免交空问卷）'), flush=True)
            return

        # 4) 提交
        if self._submit():
            time.sleep(VOTE_AFTER_SUBMIT)

    # ---------------------------------------------------------------- 定位
    def _locate_questions(self):
        """返回题目元素列表；找不到返回 None。

        两种部署形态都要兼容：
          a) 问卷直接在 `#iframe` 内展开（点开后当前文档就有 .question-item）
          b) 点开后弹顶层 `#voteIframe`（上游代码走的就是这条）
        """
        deadline = time.time() + VOTE_QUESTION_TIMEOUT
        while time.time() < deadline:
            try:
                found = self.driver.find_elements(By.CLASS_NAME, 'question-item')
                if found:
                    return found
            except Exception:
                pass
            time.sleep(0.4)

        deadline = time.time() + VOTE_IFRAME_TIMEOUT
        while time.time() < deadline:
            try:
                self.driver.switch_to.default_content()
                frames = self.driver.find_elements(By.ID, 'voteIframe')
                if frames:
                    self.driver.switch_to.frame(frames[0])
                    found = self.driver.find_elements(By.CLASS_NAME, 'question-item')
                    if found:
                        return found
            except Exception:
                pass
            time.sleep(0.5)
        return None

    # ---------------------------------------------------------------- 作答
    def _answer_one(self, question):
        """单题作答：优先选项，其次文本框。"""
        options = question.find_elements(By.CSS_SELECTOR, 'input[type="radio"]')
        if not options:
            options = question.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
        if options:
            self._click(random.choice(options))
            return

        boxes = question.find_elements(By.TAG_NAME, 'textarea')
        if not boxes:
            boxes = question.find_elements(By.CSS_SELECTOR, '[contenteditable="true"]')
        if boxes:
            box = boxes[0]
            try:
                box.click()
            except Exception:
                pass
            box.send_keys('无')
            return
        raise RuntimeError('既没有选项也没有输入框')

    def _click(self, element):
        """普通点击被遮罩挡住时退回 JS 点击；两者都失败返回 False。"""
        if element is None:
            return False
        try:
            element.click()
            return True
        except Exception:
            pass
        try:
            self.driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- 提交
    def _submit(self):
        button = None
        for by, selector in ((By.ID, 'submitSuccess'),
                             (By.CSS_SELECTOR, '[class*="submit"][class*="btn"]')):
            try:
                nodes = self.driver.find_elements(by, selector)
            except Exception:
                nodes = []
            if nodes:
                button = nodes[0]
                break
        if button is None:
            print(color.red('未找到问卷提交按钮，本次不提交（避免半提交）'), flush=True)
            return False
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", button)
        except Exception:
            pass
        if not self._click(button):
            print(color.red('问卷提交按钮点击失败，本次不提交'), flush=True)
            return False
        time.sleep(1)
        self._confirm_submit()
        return True

    def _confirm_submit(self):
        """处理可能出现的确认弹窗。

        上游在这里写的是 `confirm.click()` —— 对 find_elements 返回的 list 调用，
        只要弹窗出现就抛 AttributeError。这里改为取第一个元素，并做存在性判断。
        """
        try:
            confirms = self.driver.find_elements(
                By.CSS_SELECTOR, '[class="jb_btn jb_btn_92 fr fs14 dialog_affrim"]')
        except Exception:
            return False
        if not confirms:
            # 有的部署不弹确认框，点「提交」即生效
            return False
        if self._click(confirms[0]):
            print(color.green('已确认提交问卷'), flush=True)
            return True
        print(color.yellow('问卷确认框点击失败：请手动检查是否已提交'), flush=True)
        return False

    # ---------------------------------------------------------------- 完成判据
    def verify_finished(self):
        """平台任务点打勾才算完成。

        问卷不一定计入任务点，取不到打勾状态时**不写学习记录**（fail-closed），
        与视频/PPT/阅读/音频的判据保持一致。真机确认问卷的打勾方式后再放宽。
        """
        try:
            return bool(self.platform_finished())
        except Exception:
            return False


def finish_vote(driver, iframe_element, course_name):
    vote = Vote(driver, iframe_element, course_name)
    vote.main()
