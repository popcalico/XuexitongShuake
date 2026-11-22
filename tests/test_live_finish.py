# -*- coding: utf-8 -*-
"""直播讲座修复回归测试（2026-11-12）。

覆盖诊断报告 `E:/xuexi/005/_AI归档/02_诊断与排查/_原件/直播讲座被跳过_诊断_2026-11-12.md` 的 P0/P1：
1. 时长解析公式：三段/两段/一段/空值/负号/非法，锁死旧公式的错算值；
2. 只有平台任务点打勾才算完成，杜绝 `sleep` 一结束就写「已完成」；
3. `Common.verify_finished()` 默认按平台任务点打勾判定（2026-11-19 起不再无条件 True），
   其它模块仍可覆写；
4. 空时长不崩溃、**时长未知仍继续轮询平台打勾**（不再一秒没看就跳过）、
   超时不写完成、失败留诊断截图；
5. 只关确实新开的直播标签，绝不关掉最后一个窗口，并切回章节页；
6. 2026-11-12 17:48 追加：占位 `-0:00` 不得挡住后面有效的剩余时间选择器。

全部纯桩，不联网、不开浏览器、不 sleep 长等待。
用法：C:/Python315/python.exe tests/test_live_finish.py
"""
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task.tool import common
from task import watch_live

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class FakeEl:
    def click(self):
        return None

    def find_element(self, by, value):
        raise RuntimeError('stub: no child')

    def find_elements(self, by, value):
        return []

    def get_attribute(self, name):
        return ''

    def is_displayed(self):
        return False


class FakeSwitch:
    def __init__(self, driver):
        self.driver = driver

    def default_content(self):
        self.driver.actions.append('default_content')

    def frame(self, ref):
        self.driver.actions.append(('frame', ref))

    def window(self, handle):
        self.driver.actions.append(('window', handle))
        self.driver.current_window_handle = handle


class FakeDriver:
    def __init__(self, handles=None, current=None):
        self.actions = []
        self.window_handles = list(handles or ['chapter'])
        self.current_window_handle = current or self.window_handles[0]
        self.switch_to = FakeSwitch(self)
        self.title = '学生学习页面'
        self.closed = []
        self.screenshots = 0
        self.scripts = []

    def find_elements(self, by, value):
        return []

    def find_element(self, by, value):
        raise RuntimeError('stub: no element')

    def execute_script(self, js, *args):
        self.scripts.append(js)
        return None

    def close(self):
        self.closed.append(self.current_window_handle)
        if self.current_window_handle in self.window_handles:
            self.window_handles.remove(self.current_window_handle)

    def save_screenshot(self, path):
        self.screenshots += 1
        return True


def make_live(driver=None, **overrides):
    """绕过 Common.__init__（它会操作真实 frame），手工造一个 Live 桩。"""
    live = watch_live.Live.__new__(watch_live.Live)
    live.driver = driver or FakeDriver()
    live.course_name = '测试课程'
    live.type = '直播'
    live.iframe = FakeEl()
    live.require_mission = False
    live._platform_finished = False
    live._fail_reason = ''
    live._read_duration = overrides.get('duration', lambda: (5230, '1:27:10'))
    live._read_remaining = overrides.get('remaining', lambda: (5000, '1:23:20'))
    live._platform_marked = overrides.get('marked', lambda ch, lh: True)
    live._ensure_playing = lambda: False
    live._enter_player_frame = lambda: True
    live._finish_windows = overrides.get('finish', lambda ch, lh: None)
    return live


def main():
    print('=== 1. 时长解析（旧公式回归） ===')
    p = watch_live.parse_hms
    check('三段 1:27:10 -> 5230 秒', p('1:27:10') == 5230, p('1:27:10'))
    check('两段 58:09 -> 3489 秒', p('58:09') == 3489, p('58:09'))
    check('一段 45 -> 45 秒', p('45') == 45)
    check('剩余时间带负号 -1:23:19', p('-1:23:19') == 4999, p('-1:23:19'))
    check('空字符串 -> 0', p('') == 0)
    check('-0:00 -> 0（不再 int 崩溃）', p('-0:00') == 0)
    check('0:00 -> 0', p('0:00') == 0)
    check('非法文本 -> 0', p('abc') == 0 and p('1:a:3') == 0)
    check('带空格/纯 0 -> 0', p('  ') == 0 and p('0') == 0)
    # 旧公式 SS*60+H 会把 1:27:10 算成 601；明确锁死这个错误值不再是结果
    check('旧公式错算值 601 不再出现', p('1:27:10') != 601)
    check('诊断表 7 条时长解析均正确',
          [p(x) for x in ('1:27:10', '1:23:19', '58:09', '1:34:48',
                          '1:32:39', '1:39:40', '1:42:47')]
          == [5230, 4999, 3489, 5688, 5559, 5980, 6167])

    print('=== 2. Common 完成校验钩子（不影响其它模块） ===')
    check('Common.verify_finished 默认按平台打勾判定（不再无条件 True）',
          common.Common.verify_finished(object()) is False)
    check('Common.main 含有完成校验分支', 'verify_finished()' in
          Path(ROOT, 'task', 'tool', 'common.py').read_text(encoding='utf-8'))

    written = []
    original_save = common.save_record

    class StubCommon(common.Common):
        def __init__(self, verified):
            self.driver = FakeDriver()
            self.type = '测试'
            self.course_name = '测试课程'
            self.require_mission = False
            self.iframes = [FakeEl()]
            self.iframe = self.iframes[0]
            self._verified = verified
            self.started = 0

        def start(self):
            self.started += 1

        def check_audio_finished(self):
            return False

        def verify_finished(self):
            return self._verified

    try:
        common.save_record = lambda *a, **kw: written.append(a)
        a = StubCommon(False)
        a.main()
        check('校验 False 时 start 执行但不写学习记录',
              a.started == 1 and len(written) == 0, (a.started, len(written)))
        b = StubCommon(True)
        b.main()
        check('校验 True 时正常写学习记录',
              b.started == 1 and len(written) == 1, len(written))
    finally:
        common.save_record = original_save

    print('=== 3. Live 完成判定与失败路径 ===')
    live = make_live()
    check('初始未完成', live.verify_finished() is False)
    live._platform_finished = True
    check('平台打勾后 verify_finished 为 True', live.verify_finished() is True)

    shots = []
    orig_shot = watch_live.save_diagnostic_screenshot
    orig_turn = watch_live.turn_page
    orig_poll = watch_live.POLL_INTERVAL
    orig_max = watch_live.MAX_ABSOLUTE
    try:
        watch_live.save_diagnostic_screenshot = lambda d, tag: shots.append(tag) or 'x.png'
        watch_live.turn_page = lambda d, name: True
        watch_live.POLL_INTERVAL = 0

        # 平台打勾 -> 完成
        ok = make_live()
        ok.start()
        check('检测到平台打勾 -> 判定完成',
              ok._platform_finished is True and ok._fail_reason == '')

        # 读不到时长 -> 不再直接放弃：仍轮询，平台打勾才算完成。
        # 旧版在这里直接 return，等于把「崩溃」换成「体面跳过」，用户仍看不到直播。
        empty = make_live(duration=lambda: (0, ''))
        empty.start()
        check('时长未知仍继续等待平台打勾（不再一秒没看就跳过）',
              empty._platform_finished is True and empty._fail_reason == '',
              (empty._platform_finished, empty._fail_reason))
        check('时长未知留诊断截图', any('时长为空' in s for s in shots))

        # 占位 -0:00 不能挡住后面有效的剩余时间选择器
        class TextEl:
            def __init__(self, text):
                self.text = text

        class DurationDriver(FakeDriver):
            def find_elements(self, by, value):
                dom = {
                    '.vjs-duration-display': ['-0:00'],
                    '.vjs-remaining-time-display': ['1:27:10'],
                    '[class="vjs-duration-display"]': ['-0:00'],
                    '[class="vjs-remaining-time-display"]': ['1:27:10'],
                }
                return [TextEl(x) for x in dom.get(value, [])]

        raw_live = watch_live.Live.__new__(watch_live.Live)
        raw_live.driver = DurationDriver()
        raw_live._enter_player_frame = lambda: True
        secs, txt = watch_live.Live._read_duration(raw_live)
        check('占位 -0:00 不挡住有效剩余时间',
              secs == 5230 and txt == '1:27:10', (secs, txt))

        # 超时上限 -> 不写完成、留诊断图
        watch_live.MAX_ABSOLUTE = 0
        never = make_live(marked=lambda ch, lh: False)
        never.start()
        check('超时且平台未打勾 -> 不写完成',
              never._platform_finished is False and '超时上限' in never._fail_reason,
              never._fail_reason)
        check('超时留诊断截图', any('直播未完成' in s for s in shots))
    finally:
        watch_live.save_diagnostic_screenshot = orig_shot
        watch_live.turn_page = orig_turn
        watch_live.POLL_INTERVAL = orig_poll
        watch_live.MAX_ABSOLUTE = orig_max

    print('=== 4. 窗口安全 ===')
    # 直接调真实实现：make_live() 把实例上的 _finish_windows 桩化了，
    # 这里必须用未绑定方法，否则测的是桩。
    d = FakeDriver(['chapter', 'live'], 'live')
    live = make_live(driver=d)
    watch_live.Live._finish_windows(live, 'chapter', 'live')
    check('多窗口时只关新开的直播标签', d.closed == ['live'], d.closed)
    check('关闭后切回章节窗口', d.current_window_handle == 'chapter',
          d.current_window_handle)

    d2 = FakeDriver(['chapter'], 'chapter')
    live2 = make_live(driver=d2)
    watch_live.Live._finish_windows(live2, 'chapter', 'chapter')
    check('只有一个窗口时绝不 close', d2.closed == [], d2.closed)

    d3 = FakeDriver(['chapter', 'live'], 'live')
    live3 = make_live(driver=d3)
    watch_live.Live._finish_windows(live3, 'chapter', 'unknown')
    check('live_handle 不在窗口列表时不误关', d3.closed == [], d3.closed)

    print('=== 5. 源码级红线 ===')
    src = Path(ROOT, 'task', 'watch_live.py').read_text(encoding='utf-8')
    common_src = Path(ROOT, 'task', 'tool', 'common.py').read_text(encoding='utf-8')
    check('旧错公式已删除', "split(':')[-1])*60" not in src)
    check('不再一次性 sleep 整个时长', 'time.sleep(duration_00)' not in src)
    check('使用总时长/剩余时间双选择器', 'vjs-duration-display' in src
          and 'vjs-remaining-time-display' in src)
    check('完成以平台打勾为准', 'ans-job-finished' in common_src
          and '_platform_marked' in src)
    check('失败留诊断截图', 'save_diagnostic_screenshot' in src)
    check('窗口关闭有数量保护', 'len(handles) > 1' in src)
    check('时长未知不再直接 return', 'total = MAX_ABSOLUTE' in src)
    check('占位值会跳过继续找下一个选择器', 'first_nonempty' in src)

    print('---')
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
