# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.
"""音频任务（V5.1 第20节）：等待有上限、可停止、可诊断，绝不误报完成。

保留真实音频播放时长与既有快进逻辑；只把「结束检测」从
`while True + sleep(1)` 换成有超时预算/停止事件/心跳日志的条件等待。
只有平台任务点打勾（`check_audio_finished()`）才允许写学习记录。
"""
import time
from selenium.webdriver.common.by import By

from core import task_control as tc
from task.tool import color
from task.tool.common import Common


class Audio(Common):
    REVIEWABLE = True   # 复习模式下已完成也重刷（刷学习时长）

    def __init__(self, driver, iframe_element, course_name):
        super().__init__(driver, iframe_element, '音频', course_name)
        self._platform_finished = False
        self.last_status = tc.TaskStatus.FAILED
        self.last_detail = ''
        self.last_duration = ''

    def verify_finished(self):
        """Common.main() 的完成钩子：超时/停止/异常一律不写学习记录。"""
        return bool(self._platform_finished)

    def start(self):
        self.driver.switch_to.frame(self.iframe)
        play_button = self.driver.find_element(
            By.CSS_SELECTOR, '[class="vjs-play-control vjs-control vjs-button"]')
        play_button.click()
        vjs_duration = self.driver.find_element(By.CLASS_NAME, "vjs-duration-display")
        duration_text = vjs_duration.text or ''
        self.last_duration = duration_text
        print('当前音频时长为{}'.format(duration_text), flush=True)

        # 既有「快进十秒」逻辑保留：这是平台真实的播放推进方式，不得为了加速跳过。
        # 格式异常时跳过快进而不是让整个任务崩溃，后续仍按平台状态等待。
        try:
            fast_forward_num = int(
                duration_text.split(':')[0] + duration_text.split(':')[1]) // 10
        except (ValueError, IndexError):
            fast_forward_num = 0
            print(color.yellow('音频时长格式异常（%r），跳过快进，仍按平台状态等待'
                               % duration_text), flush=True)
        try:
            vjs_fast_forward_button = self.driver.find_element(
                By.ID, "vjs-fast-forward-button")
            for _ in range(fast_forward_num):
                vjs_fast_forward_button.click()
                time.sleep(0.1)
        except Exception as e:
            print(color.yellow('快进按钮不可用（%s），继续按平台状态等待'
                               % type(e).__name__), flush=True)

        seconds = tc.parse_duration(duration_text)
        budget = tc.time_budget(seconds)
        print(color.blue('[音频] 时长 %s（%.0f 秒）· 等待上限 %.0f 秒 · '
                         '每 30 秒输出一次心跳 · 支持用户停止'
                         % (duration_text or '未知', seconds, budget)), flush=True)

        state = {'last': '播放中'}

        def _check():
            """每轮重新定位 iframe，绝不复用跨页面缓存的 WebElement。"""
            try:
                self.driver.switch_to.default_content()
                frame = self.driver.find_element(By.CSS_SELECTOR, '[id="iframe"]')
                self.driver.switch_to.frame(frame)
                self.iframe = frame          # 同步给 check_audio_finished，避免 stale
            except Exception as e:
                if tc.looks_like_driver_error(e):
                    raise            # 交给 bounded_wait 直接判 DRIVER_ERROR
                state['last'] = '音频元素不存在/页面异常（%s）' % type(e).__name__
                return (False, state['last'])
            try:
                finished = bool(self.check_audio_finished())
            except Exception as e:
                if tc.looks_like_driver_error(e):
                    raise
                state['last'] = '完成标志读取异常（%s）' % type(e).__name__
                return (False, state['last'])
            state['last'] = '平台已打勾' if finished else '平台未更新完成标志'
            return (finished, state['last'])

        status, detail, elapsed = tc.bounded_wait(
            '音频播放完成', _check, timeout=budget, poll_interval=1.0,
            heartbeat=30.0,
            diagnose=lambda: '%s · 最后状态=%s · 时长=%s' % (
                tc.page_snapshot(self.driver), state['last'], duration_text or '未知'))

        self.last_status = status
        self.last_detail = detail
        self._platform_finished = (status == tc.TaskStatus.SUCCESS)
        if self._platform_finished:
            print(color.green('音频已确认完成（平台任务点已打勾），耗时 %.1f 秒'
                              % elapsed), flush=True)
            return
        # 明确区分：平台未更新完成标志 / 音频元素不存在 / 页面异常 / 用户停止 / driver 失效
        print(color.red(
            '[音频] 未确认完成（%s）· 已等待 %.1f 秒 / 上限 %.0f 秒 · 时长=%s · '
            '最后状态=%s · %s · 不写学习记录'
            % (status, elapsed, budget, duration_text or '未知', state['last'],
               tc.page_snapshot(self.driver))), flush=True)


def play_audio(driver, iframe_element, course_name):
    audio = Audio(driver, iframe_element, course_name)
    audio.main()
