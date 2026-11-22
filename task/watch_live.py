# -*- coding: utf-8 -*-
"""直播讲座播放器（2026-11-12 修复）。

旧实现有两个确定性的问题，见 `E:/xuexi/005/_AI归档/02_诊断与排查/_原件/直播讲座被跳过_诊断_2026-11-12.md`：

1. `duration.split(':')[-1] * 60 + duration.split(':')[0]` 把「秒」当「分钟」、
   把「小时」当「秒」，中间那段分钟被丢掉。`1:27:10` 只睡 601 秒（10 分钟），
   实际需要 87 分钟 —— 用户看到的就是「20~30 分钟就被跳过」。
2. `time.sleep()` 一结束就无条件打印「已完成」并写学习记录，**从不校验平台
   是否真的把任务点打勾**，造成「假完成」。另外空时长 `int('')` 会崩溃，
   连带留下直播页、触发「连续多次找不到下一页，结束本课程」。

现在的做法：
  * 正确解析 `H:MM:SS` / `MM:SS` / `SS`，优先用总时长 `vjs-duration-display`，
    时长只用来算**超时上限**，不再当作「睡多久就完成」；
  * 分片轮询（默认 30 秒一轮），每轮检查平台任务点 `ans-job-finished`、
    播放器是否暂停、剩余时间是否递减；
  * **只有平台打勾才算完成**，否则到上限记失败、留诊断截图、不写「已完成」，
    由 `Common.verify_finished()` 拦住 `save_record()`；
  * 安全关闭窗口：只关确实新开的直播标签，绝不关掉最后一个窗口；结束时切回章节页。

安全边界：不改其它内容类型的行为。注意 `Common.verify_finished()` 自 2026-11-19 起
默认就是「按平台打勾判定」（fail-closed），PPT / 阅读 / 讨论同样受益；本模块的
`verify_finished()` 只是在其之上做了直播特有的兜底，不改变判定方向。
"""
import time

from selenium.webdriver.common.by import By

from task.tool.common import Common, turn_page, save_diagnostic_screenshot
from task.tool import color

POLL_INTERVAL = 30          # 每轮轮询间隔（秒）
DURATION_RETRY = 30         # 时长还没渲染出来时最多等多久
GRACE_AFTER_END = 300       # 播放结束后再给平台多久打勾
MAX_ABSOLUTE = 3 * 3600     # 绝对上限，防止无限等待
PLAYER_FRAME_ID = 'middleFrame'


def parse_hms(text):
    """把播放器时长文本解析成秒；空 / 非法 / `-0:00` 都返回 0。

    `1:27:10` -> 5230、`58:09` -> 3489、`45` -> 45。
    旧公式 `SS*60+H` 会分别算成 601 / 598 / 0，正是本次修复的根因。
    """
    s = str(text or '').strip().lstrip('-').strip()
    if not s or s == '0':
        return 0
    parts = s.split(':')
    try:
        nums = [int(p.strip()) for p in parts]
    except (TypeError, ValueError):
        return 0
    if any(n < 0 for n in nums):
        return 0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return 0


class Live(Common):
    def __init__(self, driver, live_url, course_name):
        super().__init__(driver, live_url, "直播", course_name)
        self._platform_finished = False
        self._fail_reason = ''

    def verify_finished(self):
        """只有平台任务点打勾才让 Common.main() 写「已完成」记录。"""
        return self._platform_finished

    # ------------------------------------------------------------ 播放器读取
    def _enter_player_frame(self):
        try:
            self.driver.switch_to.default_content()
            frames = self.driver.find_elements(By.ID, PLAYER_FRAME_ID)
            if frames:
                self.driver.switch_to.frame(frames[0])
            return True
        except Exception:
            return False

    @staticmethod
    def _first_text(driver, selectors):
        """取第一个**能解析成有效时长**的文本，占位值不能挡住后面的选择器。

        播放器未就绪时 `.vjs-duration-display` 常渲染成 `-0:00` 或空串。
        旧实现只要第一个选择器命中元素就立刻 return，导致后面有效的
        `.vjs-remaining-time-display` 永远没机会被读到，整场直播被判
        「读不到时长」而跳过 —— 这正是 2026-11-12 诊断里两次「直播时长: 」为空的场景。
        """
        first_nonempty = ''
        for css in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
            except Exception:
                continue
            for el in els:
                text = (el.text or '').strip()
                if not text:
                    continue
                if not first_nonempty:
                    first_nonempty = text
                if parse_hms(text) > 0:
                    return text
        return first_nonempty

    def _read_duration(self):
        """优先总时长，退化到剩余时间；空值重试，绝不 int('') 崩溃。"""
        deadline = time.time() + DURATION_RETRY
        while time.time() < deadline:
            self._enter_player_frame()
            for css in (('.vjs-duration-display', '.vjs-remaining-time-display'),
                        ('[class="vjs-duration-display"]',
                         '[class="vjs-remaining-time-display"]')):
                text = self._first_text(self.driver, css)
                seconds = parse_hms(text)
                if seconds > 0:
                    return seconds, text
            time.sleep(1)
        return 0, ''

    def _read_remaining(self):
        self._enter_player_frame()
        text = self._first_text(
            self.driver,
            ('.vjs-remaining-time-display', '[class="vjs-remaining-time-display"]'))
        return parse_hms(text), text

    def _ensure_playing(self):
        """播放器暂停/卡住时尝试恢复，不抛异常给主流程。"""
        self._enter_player_frame()
        try:
            controls = self.driver.find_elements(
                By.CSS_SELECTOR, 'button.vjs-play-control, .vjs-play-control')
            for ctrl in controls:
                cls = ctrl.get_attribute('class') or ''
                if 'vjs-paused' in cls or 'vjs-ended' in cls:
                    self.driver.execute_script('arguments[0].click();', ctrl)
                    print(color.yellow('直播播放器已暂停，重新点播放'), flush=True)
                    return True
        except Exception:
            pass
        try:
            big = self.driver.find_elements(By.CSS_SELECTOR, '.vjs-big-play-button')
            if big and big[0].is_displayed():
                self.driver.execute_script('arguments[0].click();', big[0])
                print(color.yellow('直播播放器未开始，点播放按钮'), flush=True)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------ 平台完成标记
    def _platform_marked(self, chapter_handle, live_handle):
        """切回章节页窗口检查任务点是否打勾，再切回直播窗口。"""
        try:
            if chapter_handle:
                self.driver.switch_to.window(chapter_handle)
            self.driver.switch_to.default_content()
            self.driver.switch_to.frame('iframe')
            return bool(self.check_audio_finished())
        except Exception:
            return False
        finally:
            try:
                if live_handle:
                    self.driver.switch_to.window(live_handle)
            except Exception:
                pass

    # ------------------------------------------------------------ 主流程
    def start(self):
        self._platform_finished = False
        self._fail_reason = ''
        chapter_handle = None
        try:
            chapter_handle = self.driver.current_window_handle
        except Exception:
            pass
        before_handles = set()
        try:
            before_handles = set(self.driver.window_handles or [])
        except Exception:
            pass

        # 点开直播（可能开新标签，也可能同窗口导航）
        try:
            self.iframe.click()
        except Exception as e:
            self._fail_reason = '点开直播入口失败：%s' % e
            print(color.red(self._fail_reason), flush=True)
            return
        time.sleep(1)
        turn_page(self.driver, '直播')

        live_handle = None
        try:
            new_handles = [h for h in self.driver.window_handles
                           if h not in before_handles]
            if new_handles:
                live_handle = new_handles[-1]
                self.driver.switch_to.window(live_handle)
            else:
                live_handle = self.driver.current_window_handle
        except Exception:
            live_handle = chapter_handle

        # 点播放 + 静音（找不到控件不致命）
        try:
            self._enter_player_frame()
            play = self.driver.find_elements(By.CLASS_NAME, 'vjs-big-play-button')
            if play:
                play[0].click()
        except Exception:
            pass
        try:
            self._enter_player_frame()
            mute = self.driver.find_elements(By.CLASS_NAME, 'vjs-mute-control')
            if mute:
                mute[0].click()
        except Exception:
            pass

        total, raw = self._read_duration()
        if total <= 0:
            # 时长只是「超时上限」的参考，不是完成条件。播放器没渲染出时长就直接
            # return，等于把旧版「崩溃」换成了「体面跳过」，用户看到的仍是直播没看。
            # 平台 ans-job-finished 打勾才是唯一判据，所以这里改用绝对上限继续轮询。
            print(color.yellow('读不到有效直播时长（空值/占位），'
                               '改用绝对上限继续等待平台打勾'), flush=True)
            save_diagnostic_screenshot(self.driver, '直播时长为空')
            total = MAX_ABSOLUTE
            raw = raw or '未知'

        # 时长只用来算超时上限；平台打勾才是唯一完成条件
        limit = min(max(int(total * 1.2) + GRACE_AFTER_END, GRACE_AFTER_END),
                    MAX_ABSOLUTE)
        deadline = time.time() + limit
        print(color.green('直播时长: %s（%d 秒），超时上限 %d 秒；'
                          '只有平台任务点打勾才算完成' % (raw, total, limit)),
              flush=True)

        last_remaining = None
        stuck = 0
        try:
            while time.time() < deadline:
                # 1) 平台是否已打勾
                if self._platform_marked(chapter_handle, live_handle):
                    self._platform_finished = True
                    print(color.green('直播任务点已打勾，判定完成'), flush=True)
                    break

                # 2) 播放器状态：剩余时间是否递减、是否暂停
                remaining, _ = self._read_remaining()
                if remaining > 0:
                    if last_remaining is not None and remaining >= last_remaining:
                        stuck += 1
                        if stuck >= 3:
                            self._ensure_playing()
                            stuck = 0
                    else:
                        stuck = 0
                    last_remaining = remaining
                else:
                    self._ensure_playing()

                left = max(0, int(deadline - time.time()))
                print(color.yellow('直播播放中：剩余 %s 秒，超时还剩 %d 秒'
                                   % (remaining or '未知', left)), flush=True)
                time.sleep(min(POLL_INTERVAL, max(1, left)))
            else:
                self._fail_reason = ('达到超时上限 %d 秒，平台任务点仍未打勾；'
                                     '不写「已完成」' % limit)
                print(color.red(self._fail_reason), flush=True)
                save_diagnostic_screenshot(self.driver, '直播未完成')
        except Exception as e:
            self._fail_reason = '直播轮询异常，未写「已完成」：%s' % e
            print(color.red(self._fail_reason), flush=True)
            save_diagnostic_screenshot(self.driver, '直播异常')
        finally:
            self._finish_windows(chapter_handle, live_handle)

    def _finish_windows(self, chapter_handle, live_handle):
        """只关确实新开的直播标签；绝不关掉最后一个窗口，并切回章节页。"""
        try:
            handles = list(self.driver.window_handles or [])
            if (live_handle and live_handle != chapter_handle
                    and live_handle in handles and len(handles) > 1):
                self.driver.close()
            if chapter_handle and chapter_handle in (self.driver.window_handles or []):
                self.driver.switch_to.window(chapter_handle)
            else:
                turn_page(self.driver, '学生学习页面')
        except Exception:
            try:
                turn_page(self.driver, '学生学习页面')
            except Exception:
                pass


def watch_live(driver, live_url, course_name):
    live = Live(driver, live_url, course_name)
    live.main()
