# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.
import time

from selenium.common import NoSuchElementException
from selenium.webdriver.common.by import By
from task.tool.common import Common,turn_page
from task.tool import color

# 阅读任务最长等待时间（秒）。到点还没打勾就关窗，由 verify_finished 判定失败，
# 宁可不写记录，也不假装读完（2026-11-19 审计 P0-3）。
READ_WAIT_SECONDS = 20 * 60
READ_POLL_SECONDS = 5


class Reading(Common):
    REVIEWABLE = True   # 复习模式下已完成也重刷（刷学习时长/次数）

    def __init__(self,driver,iframe_element,course_name):
        super().__init__(driver,iframe_element,'阅读',course_name)
        self.iframe_element = iframe_element

    def start(self):
        self.driver.switch_to.frame(self.iframe)
        if 'class' in self.iframe_element:
            self.driver.switch_to.frame(self.driver.find_element(By.NAME, "bookifame"))
            try:
                read_web=self.driver.find_element(By.ID, "Readweb")
            except NoSuchElementException:
                print(color.red('未找到阅读内容，本次不写学习记录'),flush=True)
                return
            read_num=read_web.find_elements(By.CLASS_NAME, "duxiuimg")
            print('当前阅读页数有{}'.format(len(read_num)),flush=True)
            for i in range(1, 11):
                self.driver.execute_script(
                    "arguments[0].scrollTo(0, arguments[0].scrollHeight*arguments[1])",
                    read_web, i / 10
                )
                time.sleep(0.8)
            time.sleep(2)
            self._wait_platform_mark()
        elif 'src' in self.iframe_element:
            self._read_in_new_window()

    def _wait_platform_mark(self):
        """在当前窗口里等平台打勾（最多 READ_WAIT_SECONDS）；读不到就静默返回。"""
        deadline = time.time() + READ_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(READ_POLL_SECONDS)
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame("iframe")
            except Exception:
                return
            if self.check_audio_finished():
                return

    def _read_in_new_window(self):
        """点「阅读」进入全屏阅读器，等平台打勾后再关窗。

        旧实现固定 `sleep(10)` 就关窗，几十页的书 10 秒「读完」并写学习记录
        （2026-11-19 审计 P0-3）。现在按平台任务点打勾判定，最多等 READ_WAIT_SECONDS。
        """
        main_handle = None
        try:
            main_handle = self.driver.current_window_handle
        except Exception:
            main_handle = None
        self.driver.switch_to.frame(self.driver.find_element(By.ID, 'frame_content'))
        self.driver.find_element(By.CLASS_NAME, "readInfo").click()
        if not turn_page(self.driver, '全屏显示 专题 章节', timeout=20):
            print(color.yellow('未能打开阅读窗口，本次跳过（不写学习记录）'), flush=True)
            return
        print(color.green('正在阅读中，完成与否以平台任务点打勾为准……'), flush=True)

        reading_handle = None
        try:
            handles = list(self.driver.window_handles)
            reading_handle = self.driver.current_window_handle
            if len(handles) > 1:
                others = [h for h in handles if h != main_handle]
                if others:
                    reading_handle = others[-1]
        except Exception:
            reading_handle = None

        deadline = time.time() + READ_WAIT_SECONDS
        try:
            while time.time() < deadline:
                time.sleep(READ_POLL_SECONDS)
                if not main_handle:
                    break
                try:
                    self.driver.switch_to.window(main_handle)
                    self.driver.switch_to.default_content()
                    self.driver.switch_to.frame("iframe")
                    if self.check_audio_finished():
                        print(color.green('平台已确认阅读完成'), flush=True)
                        break
                except Exception:
                    pass
                try:
                    if reading_handle:
                        self.driver.switch_to.window(reading_handle)
                    else:
                        break
                except Exception:
                    break
        finally:
            # 只关「本次新开的阅读窗口」，绝不关学习页（旧实现关错过窗口）
            try:
                if reading_handle and reading_handle != main_handle:
                    self.driver.switch_to.window(reading_handle)
                    self.driver.close()
            except Exception:
                pass
            try:
                if main_handle:
                    self.driver.switch_to.window(main_handle)
            except Exception:
                pass
            turn_page(self.driver, '学生学习页面', timeout=10)


def reading(driver,iframe_element,course_name):
    read=Reading(driver,iframe_element,course_name)
    read.main()
