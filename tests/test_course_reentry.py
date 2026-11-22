# -*- coding: utf-8 -*-
"""回归：整门课被提前结束 + 之后再也进不去课程（2026-11-22 真机）。

现场（用户导出日志 00:49~01:00）：

    00:49:35 该页面无法识别（第 10 次）
    00:49:45 连续 10 页都无法识别，结束本课程（避免空转）
    00:49:51 已进入「我学的课」列表          ← 其实还在学习页，0.01 秒
    00:50:07 没看到《中华民族共同体概论》，当前看到的课程：['返回课程']
    00:53 ~ 01:00 三轮重试，每轮干等 180 秒
    01:00:39 回不到课程页，结束本课程

两个独立缺陷：

① `_COURSE_LIST_URL_MARKS` 含 `'/mycourse'`，而学习页地址是
   `/mycourse/studentstudy?...` → 恢复流程误判「已经在课程列表」，
   于是既不点菜单也不导航，直接在学习页上找课程卡片 → 必然失败。
② 这门课的章节是**纯文字**（截图确认：只有「上一节/下一节」），
   11 个不同 chapterId 的文本页被累加成「连续无法识别」→ 整门课被终止。

同类事故 2026-11-14 也发生过（附件下载页）。这里把两条判据都钉死。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
# 与其它会 import main 的测试一样，必须先隔离数据目录，
# 否则测试会往真实的 record/DOM 里写垃圾快照（2026-11-19 事故）。
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_reentry_')

from selenium.webdriver.common.by import By

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Switch:
    def __init__(self, frames):
        self._frames = frames

    def default_content(self):
        pass

    def frame(self, arg):
        pass


class PageDriver:
    """可配置「有哪些 iframe / 页面上有哪些选择器」的桩。"""

    def __init__(self, url, mats=None, frame_hits=None):
        self.current_url = url
        self.mats = set(mats or [])
        self.frame_hits = frame_hits or {}
        self.switch_to = Switch(list(self.frame_hits))

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == 'iframe':
            return list(range(len(self.frame_hits)))
        if by == By.CSS_SELECTOR:
            pool = self.frame_hits.get('_top', set()) if True else set()
            return [1] if value in self.mats else []
        return []

    def find_element(self, *a):
        raise core.NoSuchElementException('none')


class StudyDriver(PageDriver):
    """顶层没有任何标记，所有标记都在第 0 个 iframe 里（真机就是这样）。"""

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == 'iframe':
            return [0]
        if by == By.CSS_SELECTOR:
            return [1] if value in self.frame_hits.get(0, set()) else []
        return []


def main():
    src = open('main.py', encoding='utf-8').read()

    print('=== 1. 学习页地址绝不能被当成「课程列表」 ===')
    study = ('https://mooc1.chaoxing.com/mycourse/studentstudy'
             '?chapterId=1211178893&courseId=265852000&clazzid=155480978')
    check('studentstudy 不算课程列表',
          core._looks_like_course_list_url(study) is False, study[:60])
    check('/visit/courses 算课程列表',
          core._looks_like_course_list_url('https://mooc1.chaoxing.com/visit/courses') is True)
    check('/mooc2-ans/visit/courses/list 算课程列表',
          core._looks_like_course_list_url(
              'https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courses/list') is True)
    check('空地址不算课程列表', core._looks_like_course_list_url('') is False)
    check('源码里显式排除 studentstudy',
          "if 'studentstudy' in u" in src)

    print()
    print('=== 2. 纯文本章节要识别成「已知页」，不能再累计成卡死 ===')
    d = StudyDriver(study, frame_hits={0: {'#prevNextFocusNext'}})
    check('有章节导航 → 判为章节页', core._detect_text_chapter(d) is True)

    d2 = StudyDriver(study, frame_hits={0: set()})
    check('没有标记但地址是 studentstudy + chapterId → 也判为章节页（新版章节详情）',
          core._detect_text_chapter(d2) is True)

    d3 = StudyDriver(study, frame_hits={0: {'#prevNextFocusNext',
                                            '[class*="ans-attach-online"]'}})
    check('页面上还有附件却没认出来 → 不算纯文本（留着当未识别，别静默跳过）',
          core._detect_text_chapter(d3) is False)

    d4 = PageDriver('https://i.chaoxing.com/base')
    check('个人空间首页不算章节页', core._detect_text_chapter(d4) is False)

    d5 = PageDriver('https://mooc1.chaoxing.com/visit/courses/study?s=abc')
    check('课程空壳页（无 chapterId）不算章节页',
          core._detect_text_chapter(d5) is False)

    print()
    print('=== 3. 「无法识别」只按同一页累计，换页就清零 ===')
    check('有 unknown_same 计数', 'unknown_same' in src)
    check('用页面身份判断是否同一页',
          'unknown_id = _page_identity(driver) or _safe_url(driver)' in src)
    check('上限只作用于同一页',
          'if unknown_same >= UNKNOWN_SKIP_LIMIT' in src
          and 'if unknown_count > UNKNOWN_SKIP_LIMIT' not in src)
    check('识别成功的分支会清零同页计数',
          'unknown_count = 0\n                unknown_same = 0' in src)
    check('纯文本页走正常流程（page_message 兜底）',
          '_detect_text_chapter(driver, max_frames)' in src
          and "{'文本': [True]}" in src)

    print()
    print('=== 4. 手动等待课程：60 秒、且认「同一标签页进了课程」 ===')
    check('等待上限降到 60 秒', 'while waited < 60:' in src and 'waited < 180' not in src)
    check('认新窗口', 'len(driver.window_handles) != base_handles' in src)
    check('认同一标签页跳到学习页',
          "'studentstudy' in now_url.lower()" in src)

    print()
    if FAILS:
        print('FAILED=%d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
