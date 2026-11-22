# -*- coding: utf-8 -*-
"""独立「开始考试」会话（2026-11-11 新增）。

与旧的「自动完成考试」(设置页 radio=3) 不同，这个模式**不进课程循环**：

    主页点「开始考试」
      → 起浏览器、自动登录（不点任何课程 / 考试入口）
      → 用户自己在浏览器里选课程、选考试
      → 程序持续只读扫描所有标签页，识别「考试页面链」
      → 识别到确认页 / 列表页 / 预览页：打印提示并继续等（绝不代点）
      → 识别到答题页：自动逐题作答
      → 答完停在页面上，明确提示「请自行核对并手动提交」，绝不交卷

页面链（真机采集 exam_probe/exam_selectors.json，2026-11-07）：
    exam-list            考试列表
    examcode/examnotes   确认开始考试页（有 #startBtn「进入考试」、face_agreement）
    reVersionTestStartNew 答题页
    exam/test/look / mooc2/exam/preview  查看/整卷预览

安全红线（不要改回去）：
  1. 绝不点「进入考试 / 开始考试 / 我已阅读并同意」—— 那会启动倒计时，
     而倒计时一旦开始，离开界面也不暂停、截止自动提交。
  2. 绝不交卷、绝不点 #submitConfirmPop 的确认、绝不刷新答题页。
  3. 只做只读识别（CDP Target.getTargets / current_url / 元素存在性判断），
     不点击、不输入、不改 DOM。
  4. 检测到人脸 / 抓拍 / 切屏监控任一开启立即停止并提示。
"""
from __future__ import annotations

import time
from datetime import datetime

from selenium.webdriver.common.by import By

from core import paths
from task.tool import color
from task import do_exam as exam

# ---------------------------------------------------------------- 页面链常量
PAGE_LIST = '/exam-ans/mooc2/exam/exam-list'
PAGE_NOTES = '/exam-ans/exam/test/examcode/examnotes'
PAGE_ANSWER = '/exam-ans/exam/test/reVersionTestStartNew'
PAGE_LOOK = '/exam-ans/exam/test/look'
PAGE_PREVIEW = '/exam-ans/mooc2/exam/preview'

# 识别优先级：答题页 > 确认页 > 列表 > 预览/查看
PAGE_MARKS = (
    ('answer', PAGE_ANSWER),
    ('notes', PAGE_NOTES),
    ('list', PAGE_LIST),
    ('preview', PAGE_PREVIEW),
    ('look', PAGE_LOOK),
)

NOTES_HINTS = {
    '进入考试', '开始考试', '我已阅读并同意', '考试须知', '考试说明',
    '确认开始考试', '同意并进入', '承诺',
}

PAGE_LABEL = {
    'answer': '考试答题页',
    'notes': '确认开始考试页',
    'list': '考试列表页',
    'preview': '整卷预览页',
    'look': '考试查看页',
}

POLL_INTERVAL = 1.5
IDLE_TIP_SECONDS = 30


def classify_url(url):
    """按 URL 判断属于考试页面链的哪一环；不属于返回 None。"""
    text = str(url or '')
    for kind, mark in PAGE_MARKS:
        if mark in text:
            return kind
    return None


def classify_document(driver):
    """URL 之外的兜底识别：确认页/列表页可能挂在 iframe 里或 URL 变体。

    只读检查页面文本与特征元素，绝不点击。
    """
    try:
        body = driver.find_element(By.TAG_NAME, 'body').text or ''
    except Exception:
        return None
    head = body[:1500]
    if any(h in head for h in NOTES_HINTS):
        # 答题页也有「进入考试」历史按钮文本，用 form#submitTest + #startBtn 区分
        try:
            driver.find_element(By.CSS_SELECTOR, '#startBtn, .face_agreement')
            return 'notes'
        except Exception:
            pass
    return None


def page_targets(driver):
    """用 CDP 只读拿到所有页面/iframe 的 URL；CDP 不可用时返回 None（降级）。

    考试列表页常嵌在 iframe 里，所以 iframe 类型也要收，不能只收 page。
    """
    try:
        infos = driver.execute_cdp_cmd('Target.getTargets', {}).get('targetInfos', [])
        return [(t.get('url') or '') for t in infos
                if t.get('type') in ('page', 'iframe')]
    except Exception:
        return None


def scan_pages(driver):
    """扫描当前所有窗口，返回 (阶段, 窗口句柄, url)。

    只读：优先 CDP，其次遍历 window_handles 读 current_url（不改焦点）。
    """
    targets = page_targets(driver)
    if targets:
        for kind, mark in PAGE_MARKS:
            for url in targets:
                if mark in url:
                    return kind, None, url
        # CDP 可用但没有考试页：也检查当前窗口的 DOM 兜底
        try:
            kind = classify_document(driver)
            if kind:
                return kind, driver.current_window_handle, (driver.current_url or '')
        except Exception:
            pass
        return None, None, ''

    # CDP 不可用：逐个窗口读 URL（switch_to.window 会改焦点，但只读无副作用）
    handles = []
    try:
        handles = list(driver.window_handles or [])
    except Exception:
        pass
    found = (None, None, '')
    priority = [k for k, _ in PAGE_MARKS] + [None]

    def better(new_kind, old_kind):
        """按 PAGE_MARKS 的顺序比较优先级：答题页最高。"""
        if old_kind is None:
            return True
        return priority.index(new_kind) < priority.index(old_kind)

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = driver.current_url or ''
        except Exception:
            continue
        kind = classify_url(url) or classify_document(driver)
        if kind and better(kind, found[0]):
            found = (kind, handle, url)
            if kind == 'answer':
                break
    return found


def _focus(driver, handle):
    if not handle:
        return
    try:
        driver.switch_to.window(handle)
    except Exception:
        pass


def focus_answer_window(driver):
    """把 Selenium 焦点切到「答题页」所在窗口，返回是否成功。

    关键修复（2026-11-12）：CDP `Target.getTargets` 只是**只读**探测到了答题页 URL，
    不会改变 Selenium 的当前窗口。旧版独立会话用 `already_on_page=True` 跳过了
    `ensure_exam_page()`，而后者本来负责遍历 window_handles 切换焦点 —— 结果
    `read_current_question()` 在首页/确认页上找题目，返回 None，2 秒就误判「答题完成」。
    这里显式补上切窗口，且只切窗口、不点任何按钮。
    """
    try:
        handles = list(driver.window_handles or [])
    except Exception:
        return False
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            if PAGE_ANSWER in (driver.current_url or ''):
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def run_exam_standalone(driver, api_key='', wait_seconds=0, stop_check=None):
    """独立考试主循环。wait_seconds=0 表示一直等到用户点「结束学习」。

    返回状态串；绝不抛异常给上层，绝不交卷。
    """
    print(color.green('已登录。请自己在浏览器里选择课程与要作答的考试。'), flush=True)
    print(color.yellow(
        '程序会自动识别「考试列表 / 确认开始考试 / 答题」页面；\n'
        '确认页的「进入考试」请由你自己点，程序绝不代点（一旦进入倒计时不会停止）。\n'
        '答完会停在页面上，请你核对答案后手动提交。'), flush=True)

    seen = set()
    last_kind = None
    last_tip = 0
    started = time.time()
    status = 'idle'

    try:
        while True:
            if stop_check and stop_check():
                status = 'stopped'
                break
            if wait_seconds and time.time() - started > wait_seconds:
                status = 'wait_timeout'
                break

            kind, handle, url = scan_pages(driver)
            if kind != last_kind:
                if kind:
                    print(color.green('识别到%s：%s' % (PAGE_LABEL.get(kind, kind), url[:110])),
                          flush=True)
                    if kind == 'notes':
                        print(color.yellow(
                            '这是确认开始考试页。请自行勾选同意并点「进入考试」；'
                            '进入答题页后程序会自动作答。'), flush=True)
                    elif kind == 'list':
                        print(color.yellow('请自己选择要作答的那场考试。'), flush=True)
                    # 考试列表 / 确认页 / 答题页都要留 DOM：平台改版时这三个页面
                    # 的选择器一变，整个考试功能就静默失效（2026-11-19 用户要求）
                    try:
                        from core import dom_dump
                        dom_dump.record(driver, '考试_%s' % PAGE_LABEL.get(kind, kind),
                                        {'url': url, 'handle': handle},
                                        key='exam:%s' % url, min_interval=300)
                    except Exception:
                        pass
                last_kind = kind
                last_tip = time.time()

            if kind == 'answer':
                # 必须先把 Selenium 焦点切到答题页窗口，否则会在错误窗口读题
                _focus(driver, handle)
                if not focus_answer_window(driver):
                    print(color.yellow(
                        '已识别到答题页，但当前没有可切换的答题页窗口；'
                        '继续等待，绝不把「读不到题」误判为答题完成。'), flush=True)
                    time.sleep(POLL_INTERVAL)
                    continue

                # 独立入口强制不交卷（判据：无论配置/页面如何都不交卷）；
                # stop_check 透传给会话，用户点「结束学习」立即取消任何交卷动作。
                session = exam.ExamSession(driver, '独立考试', api_key,
                                           stop_check=stop_check)
                session.cfg = dict(session.cfg)
                session.cfg['exam_auto_submit'] = 0
                result = session.run(already_on_page=True)
                seen.add(url)
                if session.stat['total'] == 0:
                    print(color.yellow(
                        '本轮未读到任何题目（%s），不判定为完成；'
                        '继续监听，等页面加载或你重新进入答题页。' % result),
                        flush=True)
                    last_kind = None
                    time.sleep(POLL_INTERVAL)
                    continue

                status = 'answered'
                print(color.green(
                    '答题结束（%s，共 %d 题）。答案已留在页面上，请你核对后手动提交。'
                    % (result, session.stat['total'])), flush=True)
                print(color.yellow(
                    '程序不会交卷、不会刷新页面；仍在监听，若你再进入另一场考试会继续作答。'),
                    flush=True)
                # 避免同一答题页反复触发：等用户离开该页
                if _wait_leave_answer(driver, stop_check):
                    last_kind = None
                    continue
                break

            if not kind and time.time() - last_tip > IDLE_TIP_SECONDS:
                print(color.yellow('尚未识别到考试页面，继续等待…'
                                   '（点「结束学习」可随时中止）'), flush=True)
                last_tip = time.time()
            time.sleep(POLL_INTERVAL)
    except Exception as e:
        print(color.red('独立考试会话异常，已停止：%s' % e), flush=True)
        status = 'error'

    _write_record(status)
    return status


def _wait_leave_answer(driver, stop_check, max_seconds=600):
    """答完后等用户离开答题页（提交或切走），避免同一页重复作答。"""
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        if stop_check and stop_check():
            return False
        try:
            kind, _h, _u = scan_pages(driver)
            if kind != 'answer':
                return True
        except Exception:
            return False
        time.sleep(POLL_INTERVAL * 2)
    return False


def _write_record(status):
    path = paths.record('独立考试记录.txt')
    try:
        import os
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write('%s  独立考试会话结束：%s（交卷始终由用户手动完成）\n'
                    % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status))
    except Exception:
        pass
