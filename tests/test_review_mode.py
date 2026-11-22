# -*- coding: utf-8 -*-
"""复习模式回归（2026-11-19 从上游 26.11.18 移植）。

关键约束：**绝不改函数签名**。
`test_video_question.py` 用的是精确列表相等断言
（`page_params == ['driver', 'course_name', ...]`），
所以复习开关一律通过 `core.runtime.review_enabled()` 读配置，
不做参数透传。本测试把这条钉死，避免以后有人「顺手」加参数。

覆盖：
  1. 配置三层（DEFAULTS / ACCOUNT_KEYS / DEFAULT_ACCOUNT）三个键齐全；
  2. `review_enabled()` / `review_times()` / `vote_enabled()` 的默认值与非法值兜底；
  3. `study_page` / `check_vido_finish` 签名与改造前完全一致；
  4. REVIEWABLE 白名单：视频/PPT/阅读/音频重刷，测验/作业/考试不重刷；
  5. 行为：复习开启时，已完成的任务点不再被 `Common.main()` 跳过。

纯桩，不联网、不开浏览器。

用法：C:/Python315/python.exe tests/test_review_mode.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_review_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import inspect

from core import runtime
from core.config_manager import ACCOUNT_KEYS, DEFAULT_ACCOUNT
from task.tool import common as common_mod
from task.tool.common import Common
from task import watch_vido, watch_ppt, reading, play_audio

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------------ 假对象
class FakeSwitchTo:
    def __init__(self):
        self.calls = []

    def default_content(self):
        self.calls.append('default')

    def frame(self, target):
        self.calls.append('frame')


class FakeDriver:
    def __init__(self):
        self.switch_to = FakeSwitchTo()

    def find_elements(self, by, selector):
        return []

    def execute_script(self, script, *args):
        return None


class FakeIframe:
    def find_element(self, by, selector):
        raise RuntimeError('桩：不提供真实元素')


class FakeCommon(Common):
    """最小可跑 Common.main() 的子类；只记录 start / verify 是否被调用。"""
    REVIEWABLE = True

    def __init__(self, already_finished=True):
        self.driver = FakeDriver()
        self.type = 'PPT'
        self.course_name = '测试课'
        self.require_mission = False
        self.iframe = FakeIframe()
        self.iframes = [FakeIframe()]
        self._finished = already_finished
        self.started = 0

    def check_audio_finished(self):
        return self._finished

    def check_exist_mission(self):
        return True

    def start(self):
        self.started += 1

    def verify_finished(self):
        return True


# ------------------------------------------------------------------ 用例
def main():
    print('=== 复习模式回归 ===')

    print('--- 配置三层 ---')
    for key in ('review', 'review_times', 'vote_enabled'):
        check('runtime.DEFAULTS 有 %s' % key, key in runtime.DEFAULTS)
        check('ACCOUNT_KEYS 有 %s' % key, key in ACCOUNT_KEYS)
        check('DEFAULT_ACCOUNT 有 %s' % key, key in DEFAULT_ACCOUNT)

    runtime.reset()
    check('复习默认关闭', runtime.review_enabled() is False)
    check('遍数默认 1', runtime.review_times() == 1)
    check('问卷默认开启', runtime.vote_enabled() is True)

    runtime.configure({'review': 1, 'review_times': 3, 'vote_enabled': 0})
    check("review='1' 解析为 True", runtime.review_enabled() is True)
    check('遍数 3 生效', runtime.review_times() == 3)
    check("vote_enabled='0' 解析为 False", runtime.vote_enabled() is False)

    runtime.configure({'review': 'abc', 'review_times': ''})
    check('非法 review 值兜底为关闭', runtime.review_enabled() is False)
    check('空遍数兜底为 1', runtime.review_times() == 1)

    runtime.configure({'review_times': '0'})
    check('遍数 0 = 不限制（原样返回）', runtime.review_times() == 0)
    runtime.reset()

    print('--- 签名未变（禁止参数透传） ---')
    page_params = list(inspect.signature(watch_vido.study_page).parameters)
    check('study_page 签名未变',
          page_params == ['driver', 'course_name', 'lock_screen',
                          'pass_vido', 'speed', 'pass_face'], page_params)
    finish_params = list(inspect.signature(watch_vido.check_vido_finish).parameters)
    check('check_vido_finish 签名未变',
          finish_params == ['driver', 'i', 'time_start', 'total_time',
                            'vido_iframe', 'lock_screen', 'speed', 'pass_face'],
          finish_params)
    check('study_page 不接收 review 参数', 'review' not in page_params)
    check('check_vido_finish 不接收 review 参数', 'review' not in finish_params)

    print('--- REVIEWABLE 白名单 ---')
    check('Common 默认不参与复习', Common.REVIEWABLE is False)
    check('视频所属的功能走 runtime 判断（无类属性）',
          'review_enabled()' in inspect.getsource(watch_vido.study_page))
    check('PPT 参与复习', watch_ppt.PPT.REVIEWABLE is True)
    check('阅读参与复习', reading.Reading.REVIEWABLE is True)
    check('音频参与复习', play_audio.Audio.REVIEWABLE is True)

    from task.quiz_ai import Answer
    check('测验不参与复习（绝不重做）', Answer.REVIEWABLE is False)
    from task.do_work import do_work
    check('作业不参与复习', do_work.REVIEWABLE is False)
    from task.do_exam import ExamSession
    check('考试不参与复习', ExamSession.REVIEWABLE is False)
    from task.finish_vote import Vote
    check('问卷不参与复习', Vote.REVIEWABLE is False)

    print('--- 行为：已完成的任务点 ---')
    # 用一个可控的 save_record，避免写运行记录
    orig_save = common_mod.save_record
    common_mod.save_record = lambda *a, **k: None
    try:
        runtime.configure({'review': 0})
        c1 = FakeCommon(already_finished=True)
        with redirect_stdout(io.StringIO()):
            c1.main()
        check('未开复习：已完成直接跳过（不 start）', c1.started == 0, c1.started)

        runtime.configure({'review': 1})
        c2 = FakeCommon(already_finished=True)
        with redirect_stdout(io.StringIO()):
            c2.main()
        check('开复习：已完成也执行 start（重刷）', c2.started == 1, c2.started)

        # 非 REVIEWABLE 的类型即使在复习模式下也不重做
        class NotReviewable(FakeCommon):
            REVIEWABLE = False

        c3 = NotReviewable(already_finished=True)
        with redirect_stdout(io.StringIO()):
            c3.main()
        check('非 REVIEWABLE 类型复习时仍跳过（防重做测验/考试）', c3.started == 0, c3.started)
    finally:
        common_mod.save_record = orig_save
        runtime.reset()

    print('--- 源码契约 ---')
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('find_mission 用 catalog_tishi56 作为复习入口',
          '.catalog_tishi56' in main_src)
    check('复习遍数循环存在', 'review_times()' in main_src)
    check('无进展自动停止（防秒速空转）', '不足 30 秒就结束' in main_src)

    vido_src = open(os.path.join(ROOT, 'task/watch_vido.py'), encoding='utf-8').read()
    check('视频复习时把进度拨回开头', 'v.currentTime=0' in vido_src)
    check('复习时「秒过」被禁用', 'pass_vido and not review' in vido_src)
    check('复习完成以「本遍播完」为准', '本遍已播放完毕' in vido_src)

    settings_src = open(os.path.join(ROOT, 'gui/setting_panel.py'), encoding='utf-8').read()
    check('GUI 有复习开关', 'review_check' in settings_src)
    check('GUI 有遍数输入', 'review_times_entry' in settings_src)
    check('GUI 有自动问卷开关', 'vote_enabled_check' in settings_src)

    print()
    if FAILS:
        print('FAILED %d: %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
