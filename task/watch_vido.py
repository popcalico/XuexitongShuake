# AI生成
# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.
import json
from selenium.common import  NoAlertPresentException
import time

from selenium.webdriver.common.by import By
import itertools

from core import paths
from core import browser
from core import task_control as tc
from core import navigation as nav
from core import runtime
from task.tool import color
from task.tool.common import save_record
from task.tool.face import delete_face_popup

drag_progress_condition = True


# 真实鼠标点击播放按钮：Selenium 的 click() 会被水印/加载层拦截，
# 这里用 ActionChains 在元素中心做一次真实指针事件（浏览器认它是用户手势，
# 之后 <video>.play() 才不会被自动播放策略拒绝）。
_PLAY_BTN_SELECTORS = (
    'button.vjs-big-play-button',
    '[class*="vjs-big-play-button"]',
    '[class*="vjs-play-control"]',
)


def _read_player_state(driver):
    """读播放器的真实状态，用于诊断「为什么没播」。"""
    try:
        return driver.execute_script(
            "var v=document.querySelector('video');"
            "if(!v){return {video:false};}"
            "return {video:true, paused:!!v.paused, muted:!!v.muted,"
            " ready:v.readyState, net:v.networkState, dur:v.duration||0,"
            " cur:v.currentTime||0, srcLen:(v.currentSrc||v.src||'').length,"
            " preload:v.getAttribute('preload')||'',"
            " err:(v.error? v.error.code : 0)};") or {'video': False}
    except Exception as e:
        return {'video': False, 'error': type(e).__name__}


def _force_play(driver, allow_mute=True):
    """把视频真正播起来；返回 (是否在播, 诊断串)。

    2026-11-19 真机根因（录下来的播放器 DOM 是铁证）：
      `<video preload="none">` + `vjs-duration-display` 显示 0:00 —— 视频
      **从未开始加载**；控制条 `class="vjs-play-control vjs-control vjs-button"`
      **没有表示"暂停态"的 class**（旧代码正是按它找播放按钮的，永远点不到）；
      点 `vjs-big-play-button` 又被水印/加载层拦截（异常被 except 吞掉）。
      结果整页没有一次成功手势 → `play()` 被自动播放策略拒绝 → 永远 0:00 →
      8 次判定后抛「视频播放异常」。
    现在的策略（从"最像用户操作"到"最强硬"逐级降级）：
      1. 真实鼠标点击大播放按钮（ActionChains，产生用户手势）；
      2. 读状态，仍在暂停 → JS `play()`（不改静音）；
      3. 仍暂停 → 静音 `play()`（静音自动播放一定被允许），并顺手把
         `preload=none` 改成 `auto` 触发真正的元数据加载；
      4. 仍暂停 → 用 JS 直接点按钮（绕过覆盖层拦截）。
    """
    reasons = []

    # 0) 已经在播就绝对不要碰它。
    #    vjs 的播放按钮是 toggle：对正在播放的视频点一下等于**暂停**。
    #    真机 21:40 就是这样——视频本来播得好好的，程序每 3 秒"恢复"一次，
    #    把正在播的视频反复点停，看起来就是"明明一切正常却一直报暂停"。
    state = _read_player_state(driver)
    if state.get('video') and state.get('paused') is False:
        return True, '已在播放（cur=%.1fs, ready=%s）' % (
            float(state.get('cur') or 0), state.get('ready'))

    # 1) 真实指针点击：最接近用户手点，成功后续 play() 就不会被策略拦
    clicked = False
    for sel in _PLAY_BTN_SELECTORS:
        try:
            with nav.probe_mode(driver):
                node = driver.find_element(By.CSS_SELECTOR, sel)
            try:
                node.click()
            except Exception:
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(driver).move_to_element(node).click().perform()
                except Exception:
                    driver.execute_script('arguments[0].click();', node)
            clicked = True
            time.sleep(0.6)
            break
        except Exception:
            continue
    if not clicked:
        reasons.append('没找到播放按钮')

    state = _read_player_state(driver)
    if state.get('video') and not state.get('paused'):
        return True, '点击播放按钮后开始播放（preload=%s）' % state.get('preload')

    # 2) JS 直接 play()，保留声音
    try:
        driver.execute_script(
            "window.__xxt_play_err='' ;"
            "var v=document.querySelector('video');"
            "if(v){try{var p=v.play(); if(p&&p.catch){p.catch(function(e){"
            "window.__xxt_play_err=(e&&e.name)||String(e);});}}catch(e){"
            "window.__xxt_play_err=String(e);}}")
    except Exception as e:
        reasons.append('play()执行失败(%s)' % type(e).__name__)
    time.sleep(0.8)
    state = _read_player_state(driver)
    if state.get('video') and not state.get('paused'):
        return True, 'JS play() 后开始播放'

    # 3) 静音 + 触发加载：静音自动播放是唯一不会被策略拒绝的路
    if allow_mute:
        try:
            driver.execute_script(
                "window.__xxt_play_err='';"
                "var v=document.querySelector('video');"
                "if(v){try{if(v.getAttribute('preload')==='none'){v.preload='auto';}"
                "v.muted=true;"
                "try{v.load();}catch(e){}"
                "var p=v.play(); if(p&&p.catch){p.catch(function(e){"
                "window.__xxt_play_err=(e&&e.name)||String(e);});}}"
                "catch(e){window.__xxt_play_err=String(e);}}")
        except Exception as e:
            reasons.append('静音play失败(%s)' % type(e).__name__)
        time.sleep(1.2)
        state = _read_player_state(driver)
        if state.get('video') and not state.get('paused'):
            return True, '静音后开始播放（preload=%s, ready=%s）' % (
                state.get('preload'), state.get('ready'))

    # 4) JS 点按钮兜底
    for sel in _PLAY_BTN_SELECTORS:
        try:
            hit = driver.execute_script(
                "var n=document.querySelector(arguments[0]);"
                "if(!n){return false;} n.click(); return true;", sel)
            if hit:
                time.sleep(0.8)
                state = _read_player_state(driver)
                if state.get('video') and not state.get('paused'):
                    return True, 'JS 点击 %s 后开始播放' % sel
        except Exception:
            continue

    try:
        err = driver.execute_script("return window.__xxt_play_err||'';") or ''
    except Exception:
        err = ''
    state = _read_player_state(driver)
    diag = 'paused=%s ready=%s net=%s dur=%s preload=%s err=%s' % (
        state.get('paused'), state.get('ready'), state.get('net'),
        state.get('dur'), state.get('preload'), state.get('err'))
    if err:
        diag += ' playErr=%s' % err
    if reasons:
        diag = '；'.join(reasons) + ' · ' + diag
    return False, diag


def click_video_progress_end(driver,pass_vido):
    global drag_progress_condition
    if pass_vido and drag_progress_condition:
        print(color.green('尝试拖动视频进度条'), flush=True)
        driver.execute_script("""
                    var video = document.querySelector('video');
                    if (video) {
                        video.currentTime = video.duration;
                    }
                """)
        drag_progress_condition=False
def generate_combinations_list(input_list):
    """
    返回列表形式的组合，完整组合放在第一位
    """
    n = len(input_list)

    if n == 3:
        # 三者都出现的情况（放在第一位）
        triple = [input_list.copy()]
        # 两两组合的所有可能
        pairs = [list(combo) for combo in itertools.combinations(input_list, 2)]
        return triple + pairs

    elif n == 4:
        # 取四个元素的情况（放在第一位）
        quadruple = [input_list.copy()]
        # 取三个元素的所有可能
        pairs = [list(combo) for combo in itertools.combinations(input_list, 2)]
        triples = [list(combo) for combo in itertools.combinations(input_list, 3)]
        return quadruple + triples + pairs
    else:
        return [input_list]

def check_internet(driver):
    try:
        internet_choice=driver.find_element(By.CSS_SELECTOR,"[class='ans-vjserrdisplay-opts']")
        # print(color.red('网络异常'), flush=True)
        choice_list=internet_choice.find_elements(By.CSS_SELECTOR,"[name='ans-vjserrdisplay-opt']")
        for choice in choice_list:
            choice.click()
    except:
        pass

def check_video_question(driver):
    """视频播放中弹出的题目：一律随机作答，不调用 AI。

    平台对视频题没有错题校验——选错了只会给出提示，接着选下一个就能一路试到正确，
    所以这里用「逐个选项提交」的方式穷举。早期版本会先让 AI 答一次，但提交之后
    随机分支仍会继续跑，反而把 AI 的答案覆盖掉（且视频题答案不可靠），因此 AI 分支已删除。
    """
    try:
        element=driver.find_element(By.CLASS_NAME,'tkTopic')
        print(color.yellow('已检测到视频中有题目'), flush=True)
        try:
            question_type=element.find_element(By.CLASS_NAME,'tkTopic_type').text
        except:
            question_type=element.find_element(By.CLASS_NAME,'tkTopic_title').text
        options = element.find_element(By.CLASS_NAME, 'tkItem_ul')
        options = options.find_elements(By.TAG_NAME, 'li')
        submit = element.find_element(By.ID, 'videoquiz-submit')
        try:
            if question_type=='单选题' or question_type=='判断题':
                for option in options:
                    option.click()
                    #提交
                    submit.click()
                    time.sleep(0.5)
            elif question_type=='多选题':
                answer_lost=generate_combinations_list(options)
                last_ans_set=set()
                for answer in answer_lost:
                    now_ans_set=set(answer)
                    # 使用 ^ 运算符计算对称差集
                    different_elements = now_ans_set ^ last_ans_set
                    for option in different_elements:
                        option.click()
                    last_ans_set=now_ans_set
                    #提交
                    submit.click()
                    time.sleep(0.5)
        except:
            pass
        #继续学习
        try:
            continue_learn=element.find_element(By.ID,'videoquiz-continue')
            continue_learn.click()
        except :
            pass
        try:
            return_video=driver.find_element(By.CSS_SELECTOR, '[class="bntWhiteBorder ans-videoquiz-back fr"]')
            return_video.click()
        except:
            pass
    except:
        return

def _fmt_seconds(sec) -> str:
    """秒数 → 'M:SS' / 'H:MM:SS'，用于日志和与平台文本对照。"""
    try:
        total = int(max(0, float(sec)))
    except Exception:
        return ''
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


def check_vido_play(driver, last_time, current_time,speed,pass_face,vido_iframe,paused=None):
    """判断视频是否卡住并恢复播放。

    判据只有一个：`<video>.paused` 是不是 `True`（2026-11-19 真机修正）。

    旧实现比较「控制条上的当前时间文本有没有变」，而 vjs 会在鼠标静止时把控制条
    隐藏，Selenium 对隐藏元素取 `.text` 得到空串 → 每轮都判「卡住」→ 去点播放
    按钮（toggle）→ 把正在播的视频点停 → 下一轮再点回来。
    日志表现就是「明明一切正常却一直报暂停」，视频实际进度几乎不前进。

    现在：`paused=False` 一律视为正常（缓冲/换码时进度也会短暂不动，属正常现象），
    只有 `paused=True` 才尝试恢复；真卡死由 `bounded_wait` 的总预算兜底。
    """
    global b, pause_start_time
    if paused is None:
        paused = _read_player_state(driver).get('paused')
    if paused is not True:
        # 在播（或读不到播放器），绝不点按钮：vjs 的播放键是 toggle，点了就是暂停
        pause_start_time = 0
        b = 0
        return
    with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as f:
        account_info = json.load(f)
    if pause_start_time == 0:
        pause_start_time = time.time()
    b += 1
    pause_duration = time.time() - pause_start_time
    if b == 6 and int(speed) >= 2:
        # 尝试多次仍未恢复且倍速>=2：降到 2 倍/1 倍，规避平台倍速检测
        try:
            if int(speed)>2:
                print(color.red(f'当前视频已被设置不能调节高倍数，现在将倍数调至2倍'), flush=True)
                new_speed=2
                account_info['speed'] = '2'
            else:
                new_speed=1
                print(color.red(f'当前视频已被设置不能调节高倍数，现在将倍数调至1倍'), flush=True)
                account_info['speed'] = '1'
            browser.press_key(driver, 'a', times=int(int(speed) - new_speed) * 10, interval=0.1)
            print(color.green('调节成功'), flush=True)
        except:
            pass
        try:
            with open(paths.data('tool', 'account_info.json'), 'w', encoding='utf-8') as fil:
                json.dump(account_info, fil)
        except Exception:
            pass
        pause_start_time = time.time()
    elif b <= 8 and pause_duration >= 2:
        # 用 _force_play 逐级降级（真实点击 → play() → 静音 play() → JS 点按钮）。
        # 旧实现只会按「暂停态」的 class 找播放按钮，而真机播放器根本没有这个
        # class（录下来的 DOM 是 `vjs-play-control vjs-control vjs-button`），
        # 等于每次「恢复」都是空操作。
        print(color.red('视频确实处于暂停（进度 %s），尝试恢复播放'
                        % (_fmt_seconds(current_time) or '0:00')), flush=True)
        try:
            ok, detail = _force_play(driver, allow_mute=True)
        except Exception as e:
            ok, detail = False, '恢复播放异常（%s）' % type(e).__name__
        if ok:
            print(color.green('已恢复播放：%s' % detail), flush=True)
            b = 0
        else:
            print(color.red('恢复播放失败：%s' % detail), flush=True)
        pause_start_time = time.time()
    elif b > 8:
        # 持续暂停且多次恢复失败：抛出让上层按失败处理（绝不假完成）
        raise Exception('视频播放异常')

def handle_video_error_alert(driver):
    try:
        alert = driver.switch_to.alert
        alert_text = alert.text
        if "视频播放异常" in alert_text:
            print(color.red("检测到视频播放异常弹窗，尝试刷新页面..."))
            alert.accept()
            time.sleep(2)
            driver.refresh()
            # 刷新后需要重新进入 iframe，具体逻辑根据实际情况补充
            raise Exception('视频播放异常')
        else:
            alert.accept()
            raise Exception('视频播放异常')
    except NoAlertPresentException:
        return

def check_vido_finish(driver,i,time_start,total_time,vido_iframe,lock_screen,speed,pass_face):
    """等待单个视频完成（V5.1 第20/21/27节）。

    * 不再是 `while True + sleep(1)`：有基于视频时长的等待上限、停止检查、
      30 秒心跳日志与超时诊断；
    * 每轮**重新定位**任务点与视频 iframe，绝不复用跨页面/跨帧的旧 WebElement
      （`vido_iframe` 参数保留仅为兼容既有签名，不再当跨帧缓存对象使用）；
    * 只有平台任务点打勾才返回 True；超时/停止/driver 异常一律返回 False，
      调用方因此不会写学习记录，也不会打印「所有视频均已完成」。
    """
    state = {'last_time': 0, 'h': 0, 'last': '播放中', 'rewound': False,
             'err': 0, 'refresh': 0}
    budget = tc.time_budget(tc.parse_duration(total_time))
    if tc.parse_duration(total_time) <= 1:
        # 时长没读到（播放器还没拿到元数据）时不能只给 600 秒：
        # 一门 13 分钟的视频会被直接判失败（真机 16:34 就是这样 TIMEOUT 的）。
        budget = max(budget, 1800)
    if runtime.review_enabled() and tc.parse_duration(total_time) <= 1:
        # 复习模式且总时长没读到：给足最大上限，否则长视频会被 10 分钟的 floor 掐断
        budget = 3 * 3600

    def _relocate():
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
        fresh = driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')
        if len(fresh) <= i:
            return None, None, '任务点列表变短/页面已切换（%d 项，索引 %d）' % (len(fresh), i)
        el = fresh[i]
        try:
            frame = el.find_element(By.XPATH, "following-sibling::iframe[1]")
        except Exception as e:
            return el, None, '视频 iframe 定位失败（%s）' % type(e).__name__
        return el, frame, ''

    def _check():
        try:
            element2, vframe, err = _relocate()
        except Exception as e:
            state['last'] = '页面/driver 异常（%s）' % type(e).__name__
            return (False, state['last'])
        if err:
            state['last'] = err
            return (False, err)
        try:
            parent_class = (element2.find_element(By.XPATH, "..")
                            .get_attribute("class") or '')
            txt = element2.get_attribute('aria-label') or ''
        except Exception as e:
            state['last'] = '任务点属性读取失败（%s）' % type(e).__name__
            return (False, state['last'])
        if txt == '任务点已完成' or 'ans-job-finished' in parent_class:
            if not runtime.review_enabled():
                return (True, '平台任务点已打勾')
            # 复习模式：已打勾也要把这一遍播完，否则等于没刷
            state['last'] = '复习模式：任务点已打勾，继续播放到本遍结束'
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame('iframe')
            driver.switch_to.frame(vframe)
            check_internet(driver)
            check_video_question(driver)
            # 复习模式：第一次进来把进度拨回开头再播。已完成的视频点开往往停在
            # 末尾，不拨回去就是「一秒播完」，等于没刷。
            if runtime.review_enabled() and not state['rewound']:
                state['rewound'] = True
                try:
                    driver.execute_script(
                        "var v=document.querySelector('video');"
                        "if(v){v.currentTime=0; if(v.paused){v.play();}}")
                    print(color.yellow('复习模式：已把播放进度拨回开头'), flush=True)
                except Exception:
                    pass
            # 进度必须从 <video> 的 currentTime 读，不能读控制条上的文本：
            # vjs 在鼠标不动时会自动隐藏控制条，而 Selenium 对**隐藏元素**的
            # `.text` 一律返回空串 → 旧代码把「读不到文本」误判成「视频卡住」，
            # 于是每 3 秒去点一次播放按钮（toggle），把正在播的视频反复点停。
            # 真机 21:40 的「明明一切正常却一直报暂停」就是这个原因。
            ps = _read_player_state(driver)
            if not ps.get('video'):
                raise RuntimeError('播放器 video 元素消失')
            cur_sec = float(ps.get('cur') or 0)
            dur_sec = float(ps.get('dur') or 0)
            if dur_sec <= 0:
                dur_sec = float(tc.parse_duration(total_time))
            current_time = _fmt_seconds(cur_sec)
            cur_total = _fmt_seconds(dur_sec) if dur_sec > 0 else total_time
            check_vido_play(driver, state['last_time'], cur_sec,
                            speed, pass_face, vframe, paused=ps.get('paused'))
            state['last_time'] = cur_sec
            if dur_sec > 0 and cur_sec >= dur_sec - 0.5:
                if runtime.review_enabled():
                    return (True, '复习模式：本遍已播放完毕')
                if state['h'] == 0:
                    state['h'] += 1
                    print(color.yellow('视频已播放完毕，但任务点仍未完成，开始重播'), flush=True)
                    # 旧实现写死了
                    # `[class="vjs-play-control vjs-control vjs-button vjs-paused vjs-ended"]`，
                    # 真机控制条的 class 拼接根本不含 `vjs-paused`，等于永远点不到。
                    # 直接用 _force_play：ended 状态下 play() 会从头重播。
                    ok, detail = _force_play(driver, allow_mute=True)
                    if ok:
                        print(color.green('已从头重播（%s）' % detail), flush=True)
                    else:
                        print(color.red('重播未能启动：%s' % detail), flush=True)
                else:
                    # 旧逻辑在这里 `return (True, ...)`：播到底两次就判完成，
                    # 平台没打勾时会把没学完的视频写成已完成（审计 P0-2）。
                    # 现在只更新状态，继续由 bounded_wait 轮询，直到平台打勾或预算耗尽。
                    state['last'] = ('播放到底但任务点未打勾（已重播 %d 次），继续等待'
                                     % state['h'])
            if lock_screen:
                # 用系统 API 阻止休眠，不再抖鼠标（多账号并发时抖鼠标会干扰用户）
                browser.keep_awake()
        except Exception as e:
            state['last'] = '视频帧异常（%s）' % type(e).__name__
            state['err'] += 1
            # 不做 driver.refresh()：刷新后视频任务点必须重新点开才会加载播放器，
            # 只会把「能救的」变成「彻底没救」（真机 17:06 刷新后一路 /1）。
            # 重定位回播放器帧再强制播一次，比刷新安全得多。
            if state['err'] in (3, 8):
                try:
                    el2, frame2, err2 = _relocate()
                    if frame2 is not None:
                        driver.switch_to.frame(frame2)
                        _force_play(driver, allow_mute=True)
                except Exception:
                    pass
            return (False, state['last'])
        state['err'] = 0          # 正常读到进度，异常计数清零
        state['last'] = '播放进度 %s/%s' % (current_time, cur_total)
        return (False, state['last'])

    status, detail, elapsed = tc.bounded_wait(
        '视频完成确认#%d' % (i + 1), _check, timeout=budget,
        poll_interval=1.0, heartbeat=30.0,
        diagnose=lambda: '%s · 最后状态=%s' % (
            tc.page_snapshot(driver), state['last']))
    if status != tc.TaskStatus.SUCCESS:
        print(color.red(
            '[视频] 第%d个视频未确认完成（%s）· 已等待 %.1f 秒 / 上限 %.0f 秒 · '
            '%s · 不写学习记录' % (i + 1, status, elapsed, budget, state['last'])),
            flush=True)
        return False
    time_end = time.time()
    print(color.green('已完成第%d个视频（%s），总共耗费了%.2f秒.'
                      % (i + 1, detail, time_end - time_start)), flush=True)
    return True

def _confirm_video_task_area(driver):
    """V5.1 第21节：`find_elements` 返回空列表时，先确认任务区域是否真的加载。

    绝不把空列表直接推断为「全部完成」。返回 (TaskStatus, reason)：
      NO_TASK      —— 页面确认已加载，本节确实没有视频任务点；
      UNKNOWN      —— 页面未加载 / 已跳转 / 选择器疑似失效，无法判定；
      DRIVER_ERROR —— iframe 都进不去，WebDriver 或页面异常。
    """
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
    except Exception as e:
        return tc.TaskStatus.DRIVER_ERROR, 'iframe 不可用（%s）' % type(e).__name__
    # 1) 广义任务点图标存在但原选择器没命中 → 选择器失效，绝不当作「没有视频」
    for sel in ('.ans-job-icon', '.ans-job-icon-clear', '[class*=ans-job-icon]'):
        try:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                return (tc.TaskStatus.UNKNOWN,
                        '检测到任务点图标（%s）但原选择器未命中，疑似选择器失效' % sel)
        except Exception:
            continue
    # 2) 确认当前确实是课程/章节学习页
    landed = False
    for sel in ('.prev_title', '.ans-attach-ct', '.catalog_points'):
        try:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                landed = True
                break
        except Exception:
            continue
    if not landed:
        try:
            body = driver.execute_script(
                "return document.body ? document.body.innerText.slice(0,3000) : '';") or ''
        except Exception:
            body = ''
        if any(k in body for k in ('任务点', '章节', '视频')):
            landed = True
    if not landed:
        return (tc.TaskStatus.UNKNOWN,
                '当前页面未确认是课程/章节页（可能未加载或已跳转）')
    return tc.TaskStatus.NO_TASK, '任务区域已加载且确实没有视频任务点'


def _video_diagnostic(driver, reason):
    """未识别时输出诊断并留截图，绝不把它当作完成。"""
    print(color.yellow('[视频诊断] %s · %s'
                       % (reason, tc.page_snapshot(driver))), flush=True)
    try:
        from task.tool.common import save_diagnostic_screenshot
        save_diagnostic_screenshot(driver, 'video_unknown')
    except Exception:
        pass


def study_page(driver,course_name,lock_screen,pass_vido,speed,pass_face):
    """视频任务点主流程（V5.1 第21/27节）。

    状态明确拆分：NO_TASK / ALL_DONE / 有未完成项 / UNKNOWN / DRIVER_ERROR。
    只有「找到任务且每个都得到平台完成确认」才打印「所有视频均已完成」。
    """
    global drag_progress_condition
    driver.switch_to.default_content()
    driver.switch_to.frame('iframe')
    try:
        elements1 = driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')
        print(color.magenta(f'已检测到{len(elements1)}个视频包含有任务点'),flush=True)
    except Exception as e:
        # find_elements 抛异常是页面/driver 问题，不是「视频已完成」
        print(color.red('视频任务点识别异常（%s），不判定完成'
                        % type(e).__name__), flush=True)
        _video_diagnostic(driver, 'find_elements 抛异常')
        return tc.TaskStatus.DRIVER_ERROR

    if not elements1:
        status, reason = _confirm_video_task_area(driver)
        if status == tc.TaskStatus.NO_TASK:
            print(color.green('页面已确认加载，本节确实没有视频任务点（不写完成记录）'),
                  flush=True)
        else:
            print(color.red('未识别到视频任务点（%s）：绝不判定为「所有视频均已完成」'
                            % reason), flush=True)
            _video_diagnostic(driver, reason)
        return status

    total = len(elements1)
    done = 0
    failed = 0
    # 复习模式：已完成的视频也要重刷一遍（刷学习时长/次数）。
    # 用 runtime 读而不是加函数参数 —— 签名被 test_video_question.py 精确断言，
    # 多一个参数就会让那条回归失败。
    review = runtime.review_enabled()
    if review:
        print(color.yellow('复习模式已开启：本节所有视频都会重新播放一遍'), flush=True)
    for i in range(total):
        drag_progress_condition = True
        # 每轮重新定位，绝不复用页面切换前的 WebElement（V5.1 第27节）
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame('iframe')
            fresh = driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')
            if len(fresh) <= i:
                print(color.red('第%d个视频任务点重新定位时消失，停止本轮识别'
                                % (i + 1)), flush=True)
                failed += 1
                break
            element1 = fresh[i]
            parent_element = element1.find_element(By.XPATH, "..")
            try:
                parent_element_class = parent_element.get_attribute("class") or ''
                txt = element1.get_attribute('aria-label') or ''
            except Exception:
                txt, parent_element_class = '', ''
        except Exception as e:
            print(color.red('第%d个视频任务点定位失败（%s），停止本轮识别'
                            % (i + 1, type(e).__name__)), flush=True)
            failed += 1
            break
        if txt == '任务点已完成' or 'ans-job-finished' in parent_element_class:
            if not review:
                done += 1
                continue
            # 复习模式：已完成也要再播一遍，不能直接跳过
            print(color.yellow(f'复习模式：第{i + 1}个视频已完成，仍按要求重刷'), flush=True)
        if txt != '任务点未完成' or 'ans-attach-ct' not in parent_element_class:
            print(color.yellow('第%d个视频任务点状态未识别（aria=%r class=%r），'
                               '不跳过也不报完成'
                               % (i + 1, txt, parent_element_class[:60])), flush=True)
            failed += 1
            continue
        try:
            vido_iframe = element1.find_element(By.XPATH, "following-sibling::iframe[1]")
            driver.execute_script("arguments[0].scrollIntoView();", vido_iframe)
            driver.switch_to.frame(vido_iframe)
            print(color.green(f'开始播放第{i + 1}个视频'),flush=True)
            time_start=time.time()
            # 真正把视频播起来（旧代码只点一次 vjs-big-play-button，被覆盖层
            # 拦截后静默失败，视频 preload=none 一直没加载 → 全程 0:00）
            played, play_detail = _force_play(driver, allow_mute=True)
            if played:
                print(color.green('播放已启动（%s）' % play_detail), flush=True)
            else:
                print(color.red('播放未能启动：%s' % play_detail), flush=True)
            # 点击「我知道了」；弹窗处理会切到顶层，之后必须重新定位 iframe
            driver.switch_to.default_content()
            delete_face_popup(driver,pass_face)
            driver.switch_to.frame('iframe')
            fresh2 = driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')
            if len(fresh2) <= i:
                raise RuntimeError('弹窗处理后任务点列表变短')
            frame = fresh2[i].find_element(By.XPATH, "following-sibling::iframe[1]")
            driver.switch_to.frame(frame)

            try:
                # 可选的「记笔记」按钮：多数视频没有，隐式等待会白等 2 秒
                with nav.probe_mode(driver):
                    element=driver.find_element(By.CLASS_NAME,'writeNote_vid_blue')
                element.click()
            except Exception:
                pass
            try:
                print(color.blue('调节音量'), flush=True)
                with nav.probe_mode(driver):
                    element = driver.find_element(By.XPATH, '//*[@id="video"]/div[6]/div[6]')
                element.click()
                print(color.green('调节成功'), flush=True)
            except Exception:
                print(color.yellow('未找到音量，或已经调节'), flush=True)

            time.sleep(1)
            # 播放刚启动时元数据还没回来，duration 显示 0:00 是正常现象，
            # 轮询等一会儿再判「读不到」，否则会把能播的视频误判成异常
            total_time = ''
            # 优先读 <video>.duration（不受控制条显隐影响），拿不到再看控制条文本
            for _ in range(12):
                ps_dur = _read_player_state(driver)
                dur_sec = float(ps_dur.get('dur') or 0)
                if dur_sec > 0:
                    total_time = _fmt_seconds(dur_sec)
                    break
                try:
                    txt = driver.find_element(By.CLASS_NAME, 'vjs-duration-display').text
                except Exception:
                    txt = ''
                if txt and txt not in ('0:00', '0:00:00'):
                    total_time = txt
                    break
                time.sleep(0.5)
            if total_time:
                print(color.green(f'该视频总时长为：{total_time}'),flush=True)
            else:
                print(color.red('获取视频总时长失败（%s）' % _read_player_state(driver)),
                      flush=True)
                total_time='1'
            # 复习模式要的是「真实观看时长」，绝不能被「秒过」拖到末尾
            click_video_progress_end(driver, pass_vido and not review)
            print(color.yellow('请不要将窗口最小化，这有可能导致程序异常\n视频播放完毕会自动跳转\n正在观看视频中……'),flush=True)
            driver.switch_to.default_content()
            driver.switch_to.frame('iframe')
            if check_vido_finish(driver,i,time_start,total_time,frame,lock_screen,speed,pass_face):
                save_record(driver, course_name, time, frame, '视频')
                done += 1
            else:
                failed += 1
        except Exception as e:
            print(color.red('第%d个视频处理异常（%s），不计为完成'
                            % (i + 1, type(e).__name__)), flush=True)
            failed += 1
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame('iframe')
        except Exception:
            pass

    if failed == 0 and done == total and total > 0:
        if review:
            # 复习模式的「完成」= 本遍播放完毕，不等于平台重新打勾，
            # 措辞必须区分，避免读日志的人以为平台确认了。
            print(color.green('复习模式：本节 %d 个视频已全部重播一遍' % total), flush=True)
        else:
            print(color.green('所有视频均已完成（%d/%d，平台均确认）' % (done, total)),
                  flush=True)
        return tc.TaskStatus.ALL_DONE
    print(color.yellow('视频任务未全部确认完成：完成 %d/%d，失败/未识别 %d，'
                       '不报告「所有视频均已完成」' % (done, total, failed)), flush=True)
    return tc.TaskStatus.FAILED if failed else tc.TaskStatus.UNKNOWN
