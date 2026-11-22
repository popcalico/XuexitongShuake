# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.

#打包python -m PyInstaller --onefile --collect-all selenium main.py
import json
import os
import re
import pickle
import time
import traceback
from selenium.common import NoSuchDriverException, NoSuchWindowException, WebDriverException, \
    ElementNotInteractableException, SessionNotCreatedException, NoSuchElementException, \
    ElementClickInterceptedException, UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium import webdriver

from core import paths
from core import browser as browser_ext
from core import browser_options
from core import driver as driver_ext
from core import login_identity as login_id
from core import performance as perf
from core import navigation as nav
from core import task_control as tc
from core import dom_dump
from core import runtime
# digest 必须在模块级导入（2026-11-21）：main() 里曾在分支内部
# `from core import runtime, digest`，Python 因此把这两个名字当**局部变量**，
# 普通学习路径没有走到那个分支 → `runtime.review_enabled()` 抛
# UnboundLocalError，整门课崩在复习判定那一行（真机 01:10:41）。
from core import digest
from task.tool.common import save_process, save_diagnostic_screenshot
from task.tool.face import delete_face_popup
from task.finish_dicussion import finish_discussion
from task.play_audio import play_audio
from task.tool import color
from task.watch_live import watch_live
from task.watch_ppt import __ppt
from task.watch_vido import study_page
from task.quiz_ai import  finish_quiz
from task.quiz_ai import close_image_viewer, image_viewer_is_open
from task.tool.send_wx import send_error
from task.do_work import do_work
from task.reading import reading
from task.finish_vote import finish_vote
from task.tool.page_compat import PageCompat
condition=True#用于判断是否调节倍数
error_num=0#错误次数
# 找不到「下一页」按钮而放弃（区别于「课程真的已完结」），供 run() 决定回课程页重试
_NEXT_GAVE_UP = False
_CURRENT_DRIVER = None   # 当前浏览器实例，供顶层异常处理时截图用
def _input_value(element) -> str:
    """读输入框的真实值。

    必须读 `value` 属性：输入框的 `.text` 永远是空串（Selenium 只返回渲染文本），
    这也是 2026-11-19 视频进度误判的同一个坑。
    """
    try:
        return element.get_attribute('value') or ''
    except Exception:
        return ''


def _clear_input(driver, element) -> None:
    """把输入框彻底清空。

    Edge 会把上次登录的手机号自动填进登录页，直接 `send_keys` 会拼成
    「旧号+新号」，平台按前面的旧号识别成上一个账号（真机 2026-11-20：
    目标 *****1520，实际登录成 *****4384）。

    顺序从「最温和」到「最强硬」：`clear()` → 全选删除 → JS 置空。
    JS 放最后：学习通登录页是受控组件，先 JS 改 value 会让框架状态与 DOM
    不一致，反而导致 send_keys 后回读为空。
    """
    try:
        element.clear()
    except Exception:
        pass
    if _input_value(element):
        try:
            from selenium.webdriver.common.keys import Keys
            element.send_keys(Keys.CONTROL, 'a')
            element.send_keys(Keys.DELETE)
        except Exception:
            pass
    if _input_value(element):
        try:
            driver.execute_script(
                "arguments[0].value='';"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                element)
        except Exception:
            pass


def _process_account_config():
    """当前子进程的账号配置：**只认 HOME 下的 tool/account_info.json**。

    2026-11-19 P0（真机 23:02 串号）：
    旧代码在 `login_study` 里用 `ConfigManager().load_account()` 取目标账号，
    而它读的是「主进程的 current_account」。多账号并发时，`accounts.json` 一旦
    落到某个账号的 HOME 里，子进程就会拿到**别的账号**的手机号——
    日志表现为三个号码互不相同：
        账号 *****1520（本次任务传入）｜目标 *****8673（ConfigManager 读到的）
        实际 *****5267（真正登进去的）
    身份校验因此永远对不上，出现「明明登录成功却报身份不匹配」。
    runner 已经把正确配置扁平化成 `HOME/tool/account_info.json`，这里直接读它就
    不会再串号。
    """
    try:
        with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_single_account(account) -> bool:
    """本次任务是否只跑一个账号。

    优先看 runner 传下来的 `XXT_ACCOUNT_COUNT`（**本次批次**的账号数），
    它比配置里的账号总数更准：配了 3 个但这次只勾选 1 个跑，也算单账号，
    可以放心复用浏览器已有登录态。
    """
    try:
        batch = os.environ.get('XXT_ACCOUNT_COUNT')
        if batch not in (None, ''):
            return int(batch) <= 1
    except Exception:
        pass
    try:
        return int(account.get('account_count') or 1) <= 1
    except Exception:
        return True


def _login_form_ready(driver, timeout=8) -> bool:
    """等登录表单出现（`#phone` 与 `#pwd` 都在）。

    2026-11-20 事故：`login_study` 里调用了这个函数，但**函数根本没定义**，
    直接 `NameError: name '_login_form_ready' is not defined` 把整个登录流程打死，
    浏览器停在被清空会话后的登录页 —— 这正是用户看到的「现在浏览器都不登录了」。
    现在补上实现，并让 `tests/test_login_no_cookie.py` 用 AST 守卫
    「调用了但没定义的模块级函数」这类错误。
    """
    end = time.time() + max(1, timeout)
    while time.time() < end:
        try:
            with nav.probe_mode(driver):
                if (driver.find_elements(By.ID, 'phone')
                        and driver.find_elements(By.ID, 'pwd')):
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _page_looks_logged_in(driver) -> bool:
    """页面看上去是不是已经登录进个人空间了。

    2026-11-20 用户场景：浏览器已经登录好、停在首页（`i.chaoxing.com/base`），
    但 Cookie 里的 uid/手机号字段读出来是历史值，`verify_identity` 于是判「串号」
    把有效会话否掉 → app 停住不执行，浏览器一直挂在首页。
    所以「是否已登录」要看页面事实，而不是只信 cookie 里的某个字段。
    """
    try:
        url = (driver.current_url or '').lower()
    except Exception:
        url = ''
    try:
        title = driver.title or ''
    except Exception:
        title = ''
    if 'chaoxing.com' not in url:
        return False
    if 'passport' in url or '/login' in url or '用户登录' in title:
        return False
    if '/base' in url or '/mycourse' in url or '/visit/' in url:
        return True
    try:
        txt = driver.execute_script(
            "return (document.body && document.body.innerText || '').slice(0,40000);") or ''
    except Exception:
        txt = ''
    return ('个人空间' in txt or '退出' in txt or '我的课程' in txt) and '用户登录' not in txt


def _reuse_existing_session(driver, target):
    """浏览器里已经有登录态就直接复用；返回 (是否可用, 说明)。

    2026-11-20 用户反馈：「浏览器已经正确登录我输入的账号，但 app 提示有问题不继续执行」。
    单账号模式没有串号风险，所以**不再用手机号/uid 做硬校验**——cookie 里的
    uid 可能是历史残留，拿它否掉一个明明有效的会话得不偿失。
    判定顺序：有 uid cookie → 已登录；页面停在个人空间 → 也算已登录。
    """
    if driver is None:
        return False, '没有浏览器实例'
    try:
        ok_nav, status, _detail = nav.navigate(
            driver, 'https://i.chaoxing.com/', name='检查已有登录态')
    except Exception as e:
        return False, '导航异常（%s）' % type(e).__name__
    if not ok_nav:
        return False, '打不开首页（%s）' % status

    ident = {}
    try:
        ident = login_id.extract_identity(driver, target) or {}
    except Exception:
        ident = {}
    uid = str(ident.get('uid') or '')
    if uid:
        return True, '发现登录会话（uid=%s…）' % uid[:4]
    if _page_looks_logged_in(driver):
        return True, '页面已处于个人空间（未取到 uid cookie）'
    return False, '未发现登录会话'


def login_study(driver, phone_number, password):
    """登录学习平台；返回 True/False。

    2026-11-19 重大变更（用户要求）：**Cookie / 保存登录态的登录方式已整体移除**。
    现在只有一条路：清站点 Cookie → 打开登录页 → 填账号密码 → 等服务端明确结果
    → 校验身份。不再保存 cookies.json，也不再尝试用它恢复登录
    （那套逻辑在多账号下反复串号，收益极低）。

    成功判据必须是「服务器确认 + 身份与目标账号一致」，绝不是 `title != '用户登录'`。
    """
    account = _process_account_config()
    target = dict(account)
    if phone_number:
        saved = target.get('phone_number')
        if saved and saved != phone_number:
            print(color.yellow('配置里的手机号（%s）与本次任务（%s）不一致，以本次任务为准'
                               % (login_id.mask_phone(saved),
                                  login_id.mask_phone(phone_number))), flush=True)
        target['phone_number'] = phone_number

    state = login_id.LoginStateMachine()
    phone_mask = login_id.mask_phone(phone_number)
    home_tag = login_id.short_home(getattr(paths, 'HOME', ''))
    aid = str(account.get('account_id') or account.get('id') or '-')[:12]
    print(color.green('登录开始：账号 %s ｜ id=%s ｜ HOME=%s ｜ 方式=账号密码' % (
        phone_mask, aid, home_tag)), flush=True)

    # ---- 单账号：浏览器已经登录就直接用 ----
    # 2026-11-20 用户反馈：「浏览器已经登录了，是 app 自己不执行」。
    # 单账号时根本不存在串号风险，而旧流程会先清掉 Cookie 再重新登录——
    # 既把用户手动登录的会话踢下线，又会用配置里的旧密码登成另一个号。
    if _is_single_account(account):
        reused, why = _reuse_existing_session(driver, target)
        if reused:
            print(color.green('浏览器已处于登录状态，直接复用当前会话（单账号模式）· %s'
                              % why), flush=True)
            record_dom(driver, '登录后首页', {'via': 'existing_session'},
                       key='login:existing')
            return True
        print(color.yellow('浏览器当前没有可复用的登录态（%s），改为账号密码登录' % why),
              flush=True)

    if not password:
        print(color.red('没有可用的密码：保存登录态登录已移除，请到「设置」里填写密码后重试'),
              flush=True)
        return False

    try:
        # V5.1 第23节：导航带阶段计时/超时诊断；导航超时 ≠ 身份认证失败，
        # 这里单独判失败并返回，绝不把页面加载失败当成「登录成功」。
        ok_nav, nav_status, nav_detail = nav.navigate(
            driver, "https://i.chaoxing.com/", name='打开登录入口')
        if not ok_nav:
            state.transition(login_id.LoginState.LOGIN_FAILED)
            print(color.red('登录入口导航失败（%s），已停止 · %s'
                            % (nav_status, nav_detail)), flush=True)
            return False

        # 关键顺序（2026-11-20 真机修复）：**先进入学习通域，再清 Cookie**。
        # 旧代码在导航之前就调 clear_site_cookies()，那时 driver 还停在 about:blank，
        # `get_cookies()` 返回空列表 —— 等于什么都没清，浏览器里上一个账号的会话
        # 原封不动，于是「目标 *****1520 却登录成 *****4384」。
        cleared = login_id.clear_site_cookies(driver)
        if cleared:
            print(color.yellow('已清理站点残留 Cookie %d 项（防止串号）' % len(cleared)),
                  flush=True)
        # 注意：**不清理 localStorage/sessionStorage**（2026-11-20 撤回）。
        # 实测清掉后浏览器会彻底退回未登录状态，收益（防旧 token）远小于代价，
        # 而且单账号已经会优先复用现有会话，根本不需要动它。
        # 函数 login_identity.clear_site_storage() 保留备用，不在这里调用。
        state.transition(login_id.LoginState.STARTING)

        # 会话清干净后重新打开入口：这一次才会真的落到未登录的登录表单
        nav.navigate(driver, "https://i.chaoxing.com/", name='重新打开登录入口')
        turn_page(driver, '用户登录')
        if not _login_form_ready(driver):
            print(color.yellow('登录表单没有出现（可能被旧会话带到了个人空间），直接打开登录页'),
                  flush=True)
            try:
                driver.get('https://passport2.chaoxing.com/login'
                           '?refer=https%3A%2F%2Fi.chaoxing.com')
                time.sleep(2)
            except Exception:
                pass
        if not _login_form_ready(driver):
            state.transition(login_id.LoginState.LOGIN_FAILED)
            print(color.red('打不开登录表单（旧会话未能清除），已停止'), flush=True)
            return False
        element = driver.find_element(By.ID, 'phone')
        element1 = driver.find_element(By.ID, 'pwd')

        # 2026-11-20 真机：目标 *****1520，登录进去却是 *****4384。
        # 原因是 Edge 会把上次登录的手机号**自动填进 #phone**，直接 send_keys 会变成
        # 「旧号 + 新号」，平台按前面的旧号识别成上一个账号。所以必须：
        #   1) 先清空输入框（clear 无效时用 JS 置空 + 全选删除双保险）；
        #   2) 输入后**回读 value**，与目标手机号不一致就立即停止，绝不带着脏输入提交。
        before_phone = _input_value(element)
        _clear_input(driver, element)
        _clear_input(driver, element1)
        element.send_keys(phone_number)
        time.sleep(0.3)
        element1.send_keys(password)
        typed = _input_value(element)
        if str(typed).strip() != str(phone_number).strip():
            # 受控输入框偶尔会吞掉第一次输入，重试一次再判失败（不要一上来就停）
            print(color.yellow('手机号回读不一致（实际 %s / 期望 %s），重新输入一次'
                               % (login_id.mask_phone(typed),
                                  login_id.mask_phone(phone_number))), flush=True)
            _clear_input(driver, element)
            element.send_keys(phone_number)
            time.sleep(0.3)
            typed = _input_value(element)
        if str(typed).strip() != str(phone_number).strip():
            print(color.red('手机号输入框内容异常（期望 %s，实际 %s，清空前 %s），已停止'
                            % (login_id.mask_phone(phone_number),
                               login_id.mask_phone(typed),
                               login_id.mask_phone(before_phone))), flush=True)
            try:
                state.transition(login_id.LoginState.LOGIN_FAILED)
            except Exception:
                pass
            return False
        if not str(_input_value(element1)).strip():
            print(color.red('密码输入框为空（清空后未能写入），已停止'), flush=True)
            return False
        state.transition(login_id.LoginState.CREDENTIAL_SUBMITTED)
        login_button = driver.find_element(By.ID, 'loginBtn')
        try:
            login_button.click()
        except Exception:
            pass

        # 等待服务器给出明确结果（最多 20 秒，逐秒检查，不固定 sleep 后盲判）
        deadline = time.time() + 20
        result = 'unknown'
        while time.time() < deadline:
            title = driver.title or ''
            if '登录' not in title:
                result = 'probably_ok'
                break
            try:
                page = driver.execute_script(
                    "return document.body ? document.body.innerText.slice(0,6000) : '';") or ''
            except Exception:
                page = ''
            if any(kw in page for kw in ('密码错误', '账号或密码错误', '用户名或密码错误',
                                         '验证码错误', '账号不存在', '登录失败')):
                result = 'rejected'
                break
            if any(kw in page for kw in ('需要验证码', '请输入验证码', '滑块验证')):
                result = 'captcha'
                break
            time.sleep(1)

        if result in ('rejected', 'captcha'):
            # P0 红线：凭据被拒/需验证码 → 停止，绝不尝试任何复用会话的手段
            state.transition(login_id.LoginState.CREDENTIAL_REJECTED)
            print(color.red('本次密码登录失败（%s），已停止；不会回退旧登录态，'
                            '也不会进入课程' % result), flush=True)
            login_id.clear_site_cookies(driver)
            return False
        if result == 'unknown':
            state.transition(login_id.LoginState.LOGIN_FAILED)
            print(color.red('登录超时：20 秒内未获得服务器明确结果，已停止（不猜测成功）'),
                  flush=True)
            login_id.clear_site_cookies(driver)
            return False

        # 服务器看起来通过 → 做身份校验，防止残留会话串号。
        # 但**单账号时只警告不阻断**：cookie 里的 uid/手机号可能是历史残留，
        # 拿它否掉一个明明有效的登录会让「浏览器已经登录好、app 却不执行」
        # （2026-11-20 用户现场）。多账号必须严格，因为那才有真串号风险。
        ok, reason, identity = login_id.verify_identity(driver, target,
                                                        require_identity=True)
        if not ok:
            if _is_single_account(account):
                print(color.yellow('身份标识与配置不一致（%s），但本次只跑一个账号，'
                                   '按已登录继续执行' % reason), flush=True)
            else:
                state.transition(login_id.LoginState.IDENTITY_MISMATCH)
                print(color.red('登录身份不匹配（%s），已清理站点 Cookie 并停止' % reason),
                      flush=True)
                login_id.clear_site_cookies(driver)
                return False
        state.transition(login_id.LoginState.CREDENTIAL_AUTHENTICATED)
        # 不再保存 cookies.json：保存登录态那套已整体移除（用户要求）
        print(color.green('登录成功（%s）' % reason), flush=True)
        record_dom(driver, '登录后首页', {'via': 'password'}, key='login:password')
        return True
    except Exception as e:
        print(color.red('登录异常已停止：%s' % _error_brief(str(e))), flush=True)
        try:
            login_id.clear_site_cookies(driver)
        except Exception:
            pass
        return False



def save_course_lst(driver,class_name,course_elements,phone_number):
    try:
        have_task_course_element=driver.find_element(By.ID,'stuNormalCourseListDiv')
    except:
        have_task_course_element=driver
    try:
        new_course_elements=have_task_course_element.find_elements(By.CSS_SELECTOR, f'[class*="{class_name}"]')
        if len(new_course_elements)!=0:
            course_elements=new_course_elements
        course_list = [course_element.get_attribute('title') for course_element in course_elements if
                       course_element.get_attribute('title')!= '']
        if len(course_list)==0:
            course_list=[course_element.text for course_element in new_course_elements if
                       course_element.text!= '']
        if len(course_list) == 0:
            print(color.red(f'获取课程列表失败'), flush=True)
        else:
            try:
                with open(paths.data('tool', 'course_name.json'), 'r', encoding='utf-8') as f:
                    dit = json.load(f)
                with open(paths.data('tool', 'course_name.json'), 'w', encoding='utf-8') as f:
                    new_list = dit.get(phone_number,[]) + course_list
                    # 去重
                    new_list = list(set(new_list))
                    dit[phone_number]=new_list
                    json.dump(dit, f, ensure_ascii=False, indent=2)
                print(color.green(f'保存课程列表成功,共有{len(new_list)}个课程'), flush=True)
            except:
                print(color.red('保存课程列表失败'), flush=True)
    except:
        print(color.red('保存课程列表失败'), flush=True)

def experience(driver):
    # 体验最新版本
    try:
        # 多数页面没有这个按钮；隐式等待 2 秒时每门课都白等 2 秒
        with nav.probe_mode(driver):
            element = driver.find_element(By.CLASS_NAME, 'experience')
        time.sleep(0.3)
        element.click()
        print(color.green('正在体验最新版本'), flush=True)
    except NoSuchElementException:
        pass

# 课程列表顶部标签的关键字。不同版本文字不一样（我学的课 / 我的课程 / 我学的课(12)…），
# 旧代码写死 class='course-tab' 且要求文字完全相等，换个版本就点不到，后面就找不到课程。
COURSE_TAB_KEYWORDS = ('我学的课', '我的课程', '学习的课')

# 课程卡片可能出现的结构：老版 #stuNormalCourseListDiv/.course-name，新版是 card 或课程链接。
COURSE_CARD_SELECTORS = (
    '#stuNormalCourseListDiv [class*="course-name"]',
    '[class*="course-name"]',
    '[class*="courseName"]',
    '[class*="w_cour_txtH"]',
    '[class*="course-card"]',
    '[class*="courseCard"]',
    '[class*="course_item"]',
    '[class*="courseItem"]',
    'a[href*="courseId"]',
    'a[href*="courseid"]',
)

# 「我学的课」标签和课程列表可能在这些帧里（不同版本不一样）
COURSE_FRAME_NAMES = ('frame_content', 'iframe')

# 菜单点不动时的兜底：直接打开课程列表页（登录态靠 Cookie 复用）。
# 每个地址都会验证「是否真的出现了课程卡片」，不出现就换下一个。
COURSE_LIST_URLS = (
    'https://mooc1.chaoxing.com/visit/courses',
    'https://mooc1-1.chaoxing.com/visit/courses',
    'https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courses/list',
)

# 按文字点击的 JS 兜底：Selenium 的 XPath 匹配不到时，在页面里把空白去掉再比一次。
# 2026-10-31 修正：优先点真正的 <a> / <li>（点 div/span 不会触发菜单跳转），并返回点了什么，
# 便于日志确认到底点到了哪个节点。
# 2026-11-19 修正（真机日志：`li=我学的课|cls=|onclick=` 点完页面毫无反应）：
# 原来只按「文字最短」挑节点，会挑中一个既无 href、也无 onclick、也无 class 的
# 空 <li>（纯布局节点，不可能有跳转），于是点多少次都白费。现在改为**两遍扫描**：
# 先只考虑「看起来能点」的节点（<a href> / onclick / data-href / data-url / 有 class），
# 找不到才退回原来的宽松策略。
_CLICK_BY_TEXT_JS = """
const target = arguments[0].replace(/\\s+/g, '');
const maxLen = arguments[1];
const textOf = (node) => (node.textContent || '').replace(/\\s+/g, '');
const attr = (node, name) => { try { return (node.getAttribute(name) || '').trim(); } catch (e) { return ''; } };
const clickable = (node) => {
  try {
    if (node.tagName === 'A' && attr(node, 'href')) { return true; }
    if (attr(node, 'onclick') || attr(node, 'data-href') || attr(node, 'data-url')) { return true; }
    if (String(node.className || '').trim()) { return true; }
  } catch (e) {}
  return false;
};
const pick = (onlyClickable) => {
  let best = null, bestLen = 1e9;
  for (const node of document.querySelectorAll('a,li,button,span,div,p')) {
    if (node.offsetParent === null) { continue; }
    const t = textOf(node);
    if (!t || t.length > maxLen || !t.includes(target)) { continue; }
    if (onlyClickable && !clickable(node)) { continue; }
    if (t.length < bestLen) { best = node; bestLen = t.length; }
  }
  return best;
};
const best = pick(true) || pick(false);
if (!best) { return ''; }
const abs = (el) => {
  try { return el.href || new URL(el.getAttribute('href'), location.href).href; }
  catch (e) { return el.getAttribute('href') || ''; }
};
// 1) 自己或祖先是链接
const link = best.closest ? best.closest('a') : null;
if (link) { link.click(); return 'a=' + abs(link); }
// 2) 后代里有链接（<li><a>我学的课</a></li> 这种最常见）
const inner = best.querySelector ? best.querySelector('a[href]') : null;
if (inner) { inner.click(); return 'inner=' + abs(inner); }
// 3) 退而点最近的 li，并把「所有可能承载跳转的属性」带回去，便于判断为什么没反应
const item = best.closest ? best.closest('li') : null;
if (item) {
  item.click();
  return 'li=' + textOf(item).slice(0, 20)
       + '|cls=' + (item.className || '').slice(0, 30)
       + '|onclick=' + (attr(item, 'onclick') || '').slice(0, 40)
       + '|dh=' + (attr(item, 'data-href') || attr(item, 'data-url')).slice(0, 60);
}
best.click();
return 'node=' + best.tagName;
"""

# 直接按链接文字跳转（点不动时的最后一招）：只对真实 http(s) 链接生效
_GOTO_BY_TEXT_JS = """
const target = arguments[0].replace(/\\s+/g, '');
for (const link of document.querySelectorAll('a')) {
  const t = (link.textContent || '').replace(/\\s+/g, '');
  if (!t.includes(target)) { continue; }
  const href = link.getAttribute('href') || '';
  if (/^https?:/i.test(href)) { window.location.href = href; return href; }
  if (href && !/^javascript:/i.test(href)) { window.location.href = href; return href; }
}
return '';
"""


def _switch_to_frame(driver, name):
    """切到指定帧（None = 顶层）；切不到返回 False，不影响调用方继续尝试别的帧。"""
    try:
        driver.switch_to.default_content()
        if name:
            driver.switch_to.frame(name)
        return True
    except Exception:
        return False


# 最近一次 JS 点击点到了什么（`a=<href>` / `inner=<href>` / `li=<文本>|...`），
# 用来在"点了没跳"时按链接地址直接跳转。
_last_click_detail = ''


def _click_text_keyword(driver, keywords, timeout=8, max_text=30):
    """按文字关键字点元素，返回是否点到。

    2026-11-19 性能修复：
    · **JS 优先**：先用一次 `execute_script` 扫描并点击（一个来回），
      不再先跑 `//*[contains(normalize-space(.), 'x')]` 全量 XPath 扫描；
    · Selenium 兜底放在 `probe_mode`（隐式等待 0）里，
      避免「找不到」时每个节点白等 2 秒；
    · 轮询间隔 0.5 → 0.3 秒。
    """
    global _last_click_detail
    end = time.time() + max(1, timeout)
    first_round = True
    while True:
        # 1) JS 快路径：扫描 + 点击一次完成
        for keyword in keywords:
            try:
                hit = driver.execute_script(_CLICK_BY_TEXT_JS, keyword, max_text)
            except Exception:
                hit = ''
            if hit:
                _last_click_detail = str(hit)
                print(color.green(f'已点击「{keyword}」（JS：{_last_click_detail[:80]}）'),
                      flush=True)
                return True
        # 2) Selenium 兜底：只在第一轮和超时前尝试，且关掉隐式等待
        if first_round:
            first_round = False
            with nav.probe_mode(driver):
                for keyword in keywords:
                    try:
                        candidates = driver.find_elements(
                            By.XPATH, "//*[contains(normalize-space(.), '%s')]" % keyword)
                    except Exception:
                        candidates = []
                    for node in candidates[:8]:
                        try:
                            text = (node.text or '').strip()
                            if not text or len(text) > max_text:
                                continue
                            if not node.is_displayed():
                                continue
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});", node)
                            try:
                                node.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", node)
                            print(color.green(f'已点击「{keyword}」'), flush=True)
                            return True
                        except Exception:
                            continue
        if time.time() >= end:
            return False
        time.sleep(0.3)


def _wait_course_list(driver, timeout=8, accept_url=True):
    """等课程列表渲染出来：任一个可搜索的帧里出现课程卡片就算成功。

    `accept_url=True`（默认）时，地址已经是课程列表页也算成功：真机上会出现
    「菜单点了没反应但地址已经跳过去」的情况，此时再死等卡片纯属浪费，后面的
    `_locate_course` 自己会跨帧扫描（它有 15 秒预算和诊断）。
    验证「某个兜底 URL 上到底有没有课程卡片」时必须传 `accept_url=False`，
    否则三个兜底地址会因为地址本身匹配而全部「成功」，等于没验证。
    """
    end = time.time() + max(0.5, timeout)
    while True:
        if accept_url and _looks_like_course_list_url(_safe_url(driver)):
            return True
        # 探测阶段关掉隐式等待：找不到课程卡片的帧不再每个白等 2 秒
        with nav.probe_mode(driver):
            for frame in (None,) + COURSE_FRAME_NAMES:
                if not _switch_to_frame(driver, frame):
                    continue
                if _collect_course_cards(driver):
                    return True
        if time.time() >= end:
            return False
        time.sleep(0.3)


def _goto_course_tab(driver, keywords):
    """按链接文字直接跳转（点不动时的兜底），返回跳转到的地址。"""
    for frame in (None,) + COURSE_FRAME_NAMES:
        if not _switch_to_frame(driver, frame):
            continue
        for keyword in keywords:
            try:
                href = driver.execute_script(_GOTO_BY_TEXT_JS, keyword)
            except Exception:
                href = ''
            if href:
                return str(href)
    return ''


def _open_course_list_via_url(driver, timeout=6):
    """直接打开课程列表页（菜单点不动时的兜底），返回 (是否成功, 最后试的地址)。

    这里必须用 `accept_url=False`：本函数的意义就是「验证这个地址上真的有课程
    卡片」，如果只看地址匹配，三个兜底地址会全部「成功」，等于没验证。
    """
    last = ''
    for url in COURSE_LIST_URLS:
        try:
            driver.get(url)
        except Exception as e:
            print(color.yellow(f'打开 {url} 失败：{e}'), flush=True)
            continue
        last = url
        # driver.get 已经等过 page_load，这里不再固定睡 2 秒，交给列表探测轮询
        time.sleep(0.4)
        if _wait_course_list(driver, timeout=timeout, accept_url=False):
            print(color.green(f'已通过地址直接打开课程列表：{url}'), flush=True)
            return True, url
        print(color.yellow(f'{url} 上没有看到课程卡片'), flush=True)
    return False, last


def _click_course_tab(driver, timeout=15):
    """点「我学的课」并**确认真的进去了**。

    2026-10-31：上一版点完不看结果，日志里写着「已进入列表」实际还停在「我的课表」。

    2026-11-19 重构（真机日志：从点菜单到进入列表花了 26 秒）：
      1. 旧逻辑每个帧点一次、每次点完等 4 秒；而真机上点到的是一个
         「无 href、无 onclick、无 class」的空 `<li>`（`li=我学的课|cls=|onclick=`），
         这种节点上根本没有绑定跳转，等多久都不会有反应，白烧 12 秒以上。
      2. 现在最多点 2 次，每次最多等 2 秒；「地址已跳到课程列表」或「卡片出现」
         任一成立即算成功（卡片是异步渲染的，地址对了就不必死等）。
      3. 一旦识别出点的是死节点，立刻放弃，交给 `_open_course_list_via_url`
         直接开列表页（真机实测这条路 2 秒就能进去）。
    """
    global _last_click_detail
    # 已经在课程列表页就不用点菜单了（页面可能刷新后直接落在列表）
    if _looks_like_course_list_url(_safe_url(driver)):
        return True
    deadline = time.time() + max(1, timeout)
    attempts = 0
    dead_hits = 0
    for frame in (None,) + COURSE_FRAME_NAMES:
        if attempts >= 3 or dead_hits >= 2 or time.time() >= deadline:
            break
        if not _switch_to_frame(driver, frame):
            continue
        _last_click_detail = ''
        if not _click_text_keyword(driver, COURSE_TAB_KEYWORDS, timeout=1.0):
            continue
        attempts += 1
        # 点到「死节点」就没必要再等：这种节点上不可能绑定跳转，等多久都不会有反应。
        # 但不要直接放弃整个函数——真正的菜单可能在下一个帧里，跳过本帧继续试。
        if _click_detail_is_dead(_last_click_detail):
            dead_hits += 1
            if dead_hits == 1:
                print(color.yellow('该菜单项既没有链接也没有点击事件，点了不会跳转，'
                                   '换帧重试；都不行就直接打开课程列表'), flush=True)
            continue
        wait = max(0.8, min(2.0, deadline - time.time()))
        if _wait_course_list(driver, timeout=wait, accept_url=True):
            return True
        print(color.yellow('点了「我学的课」但列表没出来，换一种方式重试'), flush=True)

    # 点到的是链接但页面没跳：直接按那个地址走
    href = ''
    if _last_click_detail.startswith(('a=', 'inner=')):
        href = _last_click_detail.split('=', 1)[1].strip()
    if href:
        print(color.yellow(f'按菜单链接直接跳转：{href[:90]}'), flush=True)
        try:
            driver.get(href)
            time.sleep(0.3)
            if _wait_course_list(driver, timeout=max(2.0, min(5.0, deadline - time.time())),
                                 accept_url=True):
                return True
        except Exception:
            pass

    href = _goto_course_tab(driver, COURSE_TAB_KEYWORDS)
    if href:
        print(color.yellow(f'改为直接跳转：{href[:90]}'), flush=True)
        time.sleep(0.3)          # _GOTO_BY_TEXT_JS 内部已改 location.href，不再 get 一次
        if _wait_course_list(driver, timeout=max(2.0, min(5.0, deadline - time.time())),
                             accept_url=True):
            return True
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return False


def _collect_course_cards(driver):
    """收集当前帧里的「课程名 -> 元素」，兼容新旧两套卡片结构。

    10 个选择器在缺少卡片的结构上都会「找不到」；全局隐式等待 2 秒时，
    单帧最坏 20 秒、5 个帧就是 100 秒。这里统一关掉隐式等待（2026-11-19 性能修复）。
    """
    cards = {}
    with nav.probe_mode(driver):
        for selector in COURSE_CARD_SELECTORS:
            try:
                nodes = driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue
            for node in nodes:
                try:
                    name = (node.get_attribute('title') or '').strip()
                    if not name:
                        name = (node.text or '').strip().split('\n')[0].strip()
                except Exception:
                    continue
                if not name or len(name) > 60:
                    continue
                cards.setdefault(name, node)
    return cards


def _locate_course(driver, course_name, timeout=15):
    """跨帧找课程卡片并点击，返回 (是否点到, 看到的课程名列表)。"""
    # 空课程名会让 `'' in name` 恒真，点中列表第一门课 → 刷错课
    # （2026-11-19 审计 P0-7）。这里直接拒绝，调用方按「未找到课程」处理。
    course_name = str(course_name or '').strip()
    if not course_name:
        print(color.red('课程名为空，已跳过选课（请在设置里填写课程名）'), flush=True)
        return False, []
    end = time.time() + max(1, timeout)
    seen = []
    while True:
        with nav.probe_mode(driver):
            for frame in (None,) + COURSE_FRAME_NAMES:
                if not _switch_to_frame(driver, frame):
                    continue
                cards = _collect_course_cards(driver)
                if cards:
                    seen = list(cards)[:12]
                    for name, node in cards.items():
                        if course_name in name or name in course_name:
                            try:
                                driver.execute_script("arguments[0].scrollIntoView();", node)
                                driver.execute_script("arguments[0].click();", node)
                            except Exception:
                                continue
                            try:
                                driver.switch_to.default_content()
                            except Exception:
                                pass
                            return True, seen
        if time.time() >= end:
            break
        time.sleep(0.3)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return False, seen


def _save_course_names(names, phone_number):
    """把看到的课程名写进 tool/course_name.json（界面里的课程下拉框用它）。"""
    if not names:
        print(color.red('获取课程列表失败'), flush=True)
        return
    try:
        path = paths.data('tool', 'course_name.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        merged = list(dict.fromkeys(list(data.get(phone_number, [])) + list(names)))
        data[phone_number] = merged
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(color.green(f'保存课程列表成功,共有{len(merged)}个课程'), flush=True)
    except Exception:
        print(color.red('保存课程列表失败'), flush=True)


def describe_course_page(driver):
    """课程列表找不到时，把页面结构打进日志，便于定位是哪个版本的版式。"""
    parts = []
    try:
        parts.append('url=%s' % (driver.current_url or '')[:120])
    except Exception:
        parts.append('url=读取失败')
    try:
        parts.append('title=%s' % (driver.title or '')[:40])
    except Exception:
        parts.append('title=读取失败')
    with nav.probe_mode(driver):
        for frame in (None,) + COURSE_FRAME_NAMES:
            if not _switch_to_frame(driver, frame):
                continue
            label = frame or 'top'
            try:
                srcs = [(element.get_attribute('src') or '')[:60]
                        for element in driver.find_elements(By.TAG_NAME, 'iframe')]
                parts.append('%s frames=[%s]' % (label, ' | '.join(srcs[:3]) or '无'))
            except Exception:
                parts.append('%s frames=读取失败' % label)
            try:
                samples = []
                for node in driver.find_elements(
                        By.CSS_SELECTOR, '[class*="cour"], [class*="Cour"]')[:8]:
                    text = (node.text or '').strip().replace('\n', ' ')
                    if text:
                        cls = (node.get_attribute('class') or '')[:24]
                        samples.append('%s=%s' % (cls, text[:24]))
                parts.append('%s 课程元素=[%s]' % (label, ' / '.join(samples) or '无'))
            except Exception:
                pass
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return '; '.join(parts)


def choice_course(driver, course_name,speed,task_type,phone_number,_retry=True):
    # time.sleep(200)
    """
    选择指定名称的课程

    参数:
    driver: WebDriver 对象，用于控制浏览器
    course_name: 字符串，要选择的课程名称

    返回:
    无
    """
    # 1) 进入课程中心：新泛雅 → 课程 → 首页（保持原有优先级）
    # 2026-11-19 性能修复：三次「预期可能找不到」的查找都放进 probe_mode，
    # 找不到时不再各白等 2 秒；固定 sleep 由 1+0.5+1+2=4.5 秒压到 ≤0.6 秒，
    # 真正的页面就绪判断交给下面的课程列表探测（它自己有轮询）。
    _t0 = time.perf_counter()
    with nav.probe_mode(driver):
        for selector, pause in (('[title="新泛雅"]', 0.2),
                                ('[title*="课程"]', 0.4),
                                ('[title*="首页"]', 0.2)):
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
            except Exception:
                continue
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                element.click()
            except Exception:
                continue
            time.sleep(pause)
            break
    print(color.green('[耗时] 进入课程中心 %.2fs' % (time.perf_counter() - _t0)), flush=True)

    # 2) 点「我学的课」标签：顶层和各内容帧都试一遍（不同版本位置不一样）
    _t1 = time.perf_counter()
    if _click_course_tab(driver):
        print(color.green('已进入「我学的课」列表'), flush=True)
    else:
        # 菜单点不进去（常见于纯前端路由的 li）：直接打开课程列表页
        print(color.yellow('菜单没点进去，改为直接打开课程列表页'), flush=True)
        ok, last_url = _open_course_list_via_url(driver)
        if ok:
            print(color.green('已进入「我学的课」列表'), flush=True)
        else:
            print(color.yellow(f'直接打开课程列表也没成功（最后试的是 {last_url}）'), flush=True)
            print(color.yellow('课程列表诊断：' + describe_course_page(driver)), flush=True)
    print(color.green('[耗时] 打开课程列表 %.2fs' % (time.perf_counter() - _t1)), flush=True)
    # 课程列表页也留档：平台改版时「卡片选择器失效」是第一类故障
    record_dom(driver, '课程列表', {'task_type': task_type, 'target': course_name},
               key='course_list:%s' % _safe_url(driver), min_interval=120)

    # 3) 找课程卡片（跨帧 + 轮询等待异步渲染，最多 15 秒）
    print(color.green(f'正在定位《{course_name}》...'), flush=True)
    _t2 = time.perf_counter()
    found, names = _locate_course(driver, course_name)
    print(color.green('[耗时] 定位课程 %.2fs（总 %.2fs）'
                      % (time.perf_counter() - _t2, time.perf_counter() - _t0)), flush=True)
    if found:
        _save_course_names(names, phone_number)
        if task_type != '作业':
            set_speed(speed, driver)
        print(color.green(f'您已选择《{course_name}》'), flush=True)
        return

    # 4) 没找到：先把页面结构写进日志，再试一次「体验最新版本」
    print(color.yellow(f'没看到《{course_name}》，当前看到的课程：{names or "（空）"}'), flush=True)
    print(color.yellow('课程列表诊断：' + describe_course_page(driver)), flush=True)
    if _retry:
        try:
            driver.execute_script(
                "arguments[0].click();", driver.find_element(By.CSS_SELECTOR, ".experience"))
            print(color.green('正在体验最新版本'), flush=True)
            if not turn_page(driver, '新泛雅'):
                turn_page(driver, '课程')
            element = driver.find_elements(By.XPATH, '//*[@id="stukc"]/div[1]/div[1]/div/a')
            time.sleep(2)
            if len(element) != 0:
                element[0].click()
                driver.find_element(
                    By.XPATH, '//*[@id="stukc"]/div[1]/div[1]/div/div/ul/li[1]').click()
            choice_course(driver, course_name, speed, task_type, phone_number, _retry=False)
            return
        except Exception:
            pass

    # 5) 仍然没有：等人手动打开课程（最多 60 秒），期间定时提示
    #    2026-11-22：原来等 180 秒 × 3 轮 = 9 分钟纯等待，而且判据只有
    #    「窗口句柄数量变了」—— 用户在同一个标签页里点进课程是**不会**开新窗口的，
    #    所以它注定等满、注定失败。现在同时认「地址变成了学习页」。
    print(color.red(f"未找到《{course_name}》这门课程，请检查名称是否正确，"
                    f"或在这 60 秒内手动点进这门课"), flush=True)
    try:
        driver.switch_to.default_content()
        base_handles = len(driver.window_handles)
        base_url = _safe_url(driver)
        waited = 0
        while waited < 60:
            time.sleep(2)
            waited += 2
            opened = False
            try:
                opened = len(driver.window_handles) != base_handles
            except Exception:
                pass
            if not opened:
                try:
                    now_url = _safe_url(driver)
                    opened = bool(now_url and now_url != base_url
                                  and 'studentstudy' in now_url.lower())
                except Exception:
                    pass
            if opened:
                print(color.green('检测到课程已经打开，继续学习'), flush=True)
                return
            if waited % 20 == 0:
                print(color.yellow(f'等待手动打开课程…已等 {waited} 秒'), flush=True)
        print(color.yellow('60 秒内没有检测到课程被打开'), flush=True)
    except Exception:
        pass
    return

def new_find_mission(driver,speed):

    course_list=driver.find_element(By.CLASS_NAME,'list-four')
    #寻找正在学习中的课程
    all_course=course_list.find_elements(By.TAG_NAME,'li')
    for course in all_course:
        try:
            course.find_element(By.CSS_SELECTOR,"[class='list-status studying']").click()
            set_speed(speed, driver)
            return
        except NoSuchElementException:
            pass
    else:
        print(color.red('该页未找到正在学习中的课程，点击下一页'),flush=True)
        driver.find_element(By.CLASS_NAME,'arrow').click()
        time.sleep(2)
        new_find_mission(driver,speed)

def _open_first_chapter(driver, speed):
    """没有「未完成任务点」时，从章节列表的最前面打开一个节点。

    用户要求：考试/测验也要做。考试在很多课程里不算任务点，旧逻辑遇到
    「所有任务点均已完成」就整门跳过，考试永远轮不到。这里改为按顺序尝试
    几种章节/知识点节点，能进到学生学习页就返回 True，由 run() 逐页往后扫。
    """
    candidates = (
        '.catalog_tishi120',                  # 未完成任务点（正常情况）
        '[class*="catalog_tishi"]',           # 其它状态的任务点
        '.catalog_jiedian',                   # 知识点节点
        'a[onclick*="getTeacherAjax"]',       # 章节节点
        'li[onclick*="getTeacherAjax"]',
        '.catalog_name',
    )
    for selector in candidates:
        try:
            nodes = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        # 每个选择器最多试 3 个节点：点第一个没进学习页，多半这一批都不对，避免一页刷几十秒
        for node in nodes[:3]:
            try:
                if not node.is_displayed():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", node)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", node)
            except Exception:
                continue
            time.sleep(2)
            if _landed_on_study_page(driver):
                print(color.magenta(f'已从章节列表进入：{selector}'), flush=True)
                try:
                    set_speed(speed, driver)
                except Exception:
                    pass
                return True
    return False


def _landed_on_study_page(driver):
    """判断是否已经离开目录页、进入了学生学习页。"""
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
        driver.find_element(By.CLASS_NAME, 'prev_title')
        return True
    except Exception:
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def find_mission(driver,task_type,speed,course_name):
    _t0 = time.perf_counter()
    # 点击开始学习（多数页面没有这个按钮，关掉隐式等待，避免每门课白等 2 秒）
    try:
        with nav.probe_mode(driver):
            element = driver.find_element(By.CSS_SELECTOR,'[class="start-study readclosecoursepop"]')
        element.click()
    except Exception:
        pass
    experience(driver)
    # 点击章节/作业标签（页面刚打开时元素可能还没出来，等一会儿）
    elements = _wait_elements(driver, By.CLASS_NAME, 'nav_content', timeout=15)
    print(color.green('[耗时] 定位「%s」标签 %.2fs'
                      % (task_type, time.perf_counter() - _t0)), flush=True)
    # 课程详情页（章节列表外壳）留档：任务点选择器失效时靠它离线定位
    record_dom(driver, '课程详情_%s' % task_type,
               {'course': course_name}, key='course_detail:%s|%s' % (course_name, _safe_url(driver)),
               min_interval=120)
    if not elements:
        # 有些课程页没有 nav_content，直接找「章节 / 作业」标签
        tab = _wait_element(driver, By.CSS_SELECTOR,
                            "li[data-nav-name='%s']" % task_type, timeout=10)
        if tab is None:
            tab = _wait_element(driver, By.CSS_SELECTOR,
                                "li[data-nav-name*='%s']" % task_type, timeout=3)
        if tab is None:
            print(color.red(f'课程页面没有出现「{task_type}」标签，跳过本课程'), flush=True)
            print(color.yellow(f'当前页面标题：{_safe_title(driver)}'), flush=True)
            return False
        try:
            tab.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", tab)
            except Exception:
                print(color.red(f'点击「{task_type}」标签失败，跳过本课程'), flush=True)
                return False
        elements = []
    else:
        for element in elements:
            if element.text== task_type:
                element.click()
                break
    try:
        driver.switch_to.frame(driver.find_element(By.TAG_NAME, 'iframe'))
    except NoSuchElementException:
        table = driver.find_element(By.CSS_SELECTOR, "[class='tabs font15 color333 fl']")
        # 点击全部知识点（旧实现硬取第 3 个 <a>，DOM 少一个就直接 IndexError 崩进程）
        links = table.find_elements(By.TAG_NAME, 'a')
        if len(links) >= 3:
            links[2].click()
        elif links:
            links[-1].click()
        else:
            print(color.red('没有找到「全部知识点」入口，跳过本课程'), flush=True)
            return False
        time.sleep(0.5)
        new_find_mission(driver,speed)
        return True
    # 切换到名为 frame_content-zj 的 iframe
    if task_type == '作业':
        return True
    try:
        if runtime.review_enabled():
            # 复习模式：找「已完成」任务点作为起点，从头重刷（刷学习时长/次数）。
            # catalog_tishi56 是上游 26.11.18 实测可用的已完成标记。
            element = driver.find_element(By.CSS_SELECTOR, '.catalog_tishi56')
            print(color.yellow('复习模式：从第一个已完成任务点开始重刷'), flush=True)
        else:
            # 查找待完成任务点的元素
            element = driver.find_element(By.CSS_SELECTOR, '.catalog_tishi120')
    except:
        driver.switch_to.default_content()
        iframes=driver.find_elements(By.TAG_NAME, 'iframe')
        print(len(iframes))
        _review = runtime.review_enabled()
        _selector = ('.catalog_tishi56' if _review else '.catalog_tishi120')
        for iframe in iframes:
            driver.switch_to.frame(iframe)
            try:
                element= driver.find_element(By.CSS_SELECTOR, _selector)
                break
            except:
                driver.switch_to.default_content()
                continue
        else:
            # 没有未完成任务点：可能只剩考试/测验（不算任务点）。
            # 按用户要求改为「从章节列表最前面进去往后扫」，让 run() 发现考试。
            print(color.yellow('未找到未完成任务点，改为从章节最前面开始扫描（用于发现考试/测验）'),
                  flush=True)
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame('iframe')
            except Exception:
                pass
            if _open_first_chapter(driver, speed):
                return True
            print(color.red('章节列表也点不进去，跳过本课程'), flush=True)
            return False
    refresh_process(driver,course_name)
    # 打印提示信息，表示已检测到未完成点
    print(color.magenta('已检测到未完成点'),flush=True)
    time.sleep(0.5)
    #滚动到未完成的任务点的位置
    driver.execute_script("arguments[0].scrollIntoView();", element)
    set_speed(speed, driver)
    # 点击待完成任务点的元素
    driver.execute_script("arguments[0].click();", element)
    return True

def refresh_process(driver,course_name):
    try:
        process_text=driver.find_element(By.CLASS_NAME, 'xs_head_name').text.strip()
        # 分别提取两个数字
        match = re.search(r'已完成任务点:\s*(\d+)/(\d+)', process_text)
        if match:
            done = int(match.group(1))  # 7
            total = int(match.group(2))  # 17
            save_process(course_name,done,total)
    except Exception as r:
        print(color.red(f'刷新进度失败'), flush=True)

def _safe_title(driver) -> str:
    try:
        return (driver.title or '')[:60]
    except Exception:
        return '（读取失败）'


def _safe_url(driver) -> str:
    """取当前 URL（诊断/DOM 快照用）；异常返回空串，绝不影响主流程。"""
    try:
        return (driver.current_url or '')[:300]
    except Exception:
        return ''


def record_dom(driver, tag, extra=None, key=None, min_interval=30.0, quiet=False):
    """统一的页面 DOM 落盘入口（2026-11-19）。

    用户要求「记住所有页面的 DOM」，所以登录、选课、课程列表、课程详情、
    课程循环里的每一页、考试、签到、问卷、作业、讨论都要走这里。

    `key` 用于去重：同一个键在 `min_interval` 秒内只抓一次。视频页这种
    「一页 10+ 个 frame」的页面不参与去重的话，每轮循环都写一遍纯属浪费。
    调用后 driver 会回到 default_content —— 调用点必须在帧上下文之外。
    """
    try:
        data = {'url': _safe_url(driver)}
    except Exception:
        data = {'url': ''}
    if extra:
        try:
            data.update(dict(extra))
        except Exception:
            pass
    try:
        path = dom_dump.record(driver, tag, data, key=key, min_interval=min_interval)
    except Exception as e:
        if not quiet:
            print(color.yellow('页面 DOM 保存失败（不影响学习）：%s' % e), flush=True)
        return ''
    if path and not quiet:
        print(color.blue('页面 DOM 已保存：%s（%s）'
                         % (path, dom_dump.summary_line())), flush=True)
    return path


# 已经处在「课程列表页」的地址特征（2026-11-19）
_COURSE_LIST_URL_MARKS = ('/visit/courses', '/mycourse', '/course/list')


def _looks_like_course_list_url(url) -> bool:
    """地址是否已经是课程列表页。

    真机日志：点「我学的课」有时地址确实跳到了 `/visit/courses`，但课程卡片
    是异步渲染的，程序还在按「卡片出现」判定，于是白等好几秒。

    2026-11-22 真机（P0）：**学习页地址 `/mycourse/studentstudy?...` 也含
    `/mycourse`**，于是这里把「正在学的那一章」当成课程列表：
      · `_click_course_tab` 直接返回 True，菜单没点、页面没导航（0.01 秒）；
      · 接着在学习页上找课程卡片，只找到「返回课程」；
      · 三轮重试各干等 180 秒，最后「回不到课程页，结束本课程」。
    表现就是用户看到的那样：明明还能学，程序却再也不敢进课程了。
    """
    u = str(url or '').lower()
    if not u:
        return False
    if 'studentstudy' in u or 'studentstudycourse' in u:
        return False        # 学习页/选课页，不是课程列表
    return any(mark in u for mark in _COURSE_LIST_URL_MARKS)


def _click_detail_is_dead(detail) -> bool:
    """判断刚才那次点击的目标是否「根本没有跳转能力」。

    真机日志里出现过 `li=我学的课|cls=|onclick=|dh=`：一个既无 href、也无
    onclick、也无 data-href、连 class 都没有的空 <li>。这种节点上不可能绑定
    跳转逻辑，点多少次都不会有反应——继续等只是浪费时间，应立即走 URL 兜底。
    """
    text = str(detail or '')
    if not text.startswith('li='):
        return False
    fields = {}
    for part in text.split('|')[1:]:
        if '=' in part:
            key, _, value = part.partition('=')
            fields[key.strip()] = value.strip()
    return not (fields.get('cls') or fields.get('onclick') or fields.get('dh'))


def _wait_element(driver, by, value, timeout=10, interval=0.5):
    """等元素出现；超时返回 None。

    页面刚打开/刚切窗口时直接 find_element 会立刻抛 NoSuchElementException，
    以前就是这么把整门课带崩的（报错还显示成「浏览器启动失败」）。
    """
    end = time.time() + max(0.5, timeout)
    # 轮询本身有超时，隐式等待只会让每次「还没出现」都白等 2 秒
    with nav.probe_mode(driver):
        while True:
            try:
                return driver.find_element(by, value)
            except Exception:
                if time.time() >= end:
                    return None
                time.sleep(interval)


def _wait_elements(driver, by, value, timeout=10, interval=0.5):
    end = time.time() + max(0.5, timeout)
    with nav.probe_mode(driver):
        while True:
            try:
                found = driver.find_elements(by, value)
            except Exception:
                found = []
            if found or time.time() >= end:
                return found
            time.sleep(interval)


def _window_titles(driver, limit=6):
    """列出所有窗口标题（当前窗口前面加 *），排查「切错窗口」用。"""
    try:
        current = driver.current_window_handle
    except Exception:
        current = None
    try:
        handles = driver.window_handles or []
    except Exception:
        handles = []
    titles = []
    for handle in handles[:limit]:
        try:
            driver.switch_to.window(handle)
            titles.append('%s%s' % ('*' if handle == current else '',
                                    (driver.title or '')[:28]))
        except Exception:
            continue
    try:
        if current:
            driver.switch_to.window(current)
    except Exception:
        pass
    return ' | '.join(titles) or '（没有窗口）'


def _switch_to_newest_window(driver, before_handles, timeout=10):
    """切到点击之后**新开**的窗口（不依赖标题），返回是否切到。

    有的任务点会在新标签页里打开学习页，标题也不叫「学生学习页面」；
    只按标题切窗口会失败，然后继续在目录页上找元素 → 页面无法识别。
    """
    before = set(before_handles or [])
    end = time.time() + max(1, timeout)
    while time.time() < end:
        try:
            handles = driver.window_handles or []
        except Exception:
            handles = []
        new = [handle for handle in handles if handle not in before]
        if new:
            try:
                driver.switch_to.window(new[-1])
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _close_course_tab(driver):
    """关掉当前课程窗口；只剩一个窗口时不要关，避免把会话关没。"""
    try:
        handles = driver.window_handles or []
    except Exception:
        handles = []
    if len(handles) > 1:
        try:
            driver.close()
            time.sleep(1)
        except Exception:
            pass
    else:
        print(color.yellow('当前只剩一个窗口，跳过关闭课程页'), flush=True)


def turn_page(driver,page_name,timeout=20):
    """切换到标题包含 page_name 的窗口。

    新开标签页时标题要等页面加载后才出现，旧实现只 sleep(1) 就判断，
    拿不到就返回 False，调用方却继续在旧页面上找元素 → 连锁报错。
    这里改成轮询等待，并且对 window_handles 为空/会话失效做防护。
    """
    end = time.time() + max(1, timeout)
    last_error = ''
    while True:
        try:
            handles = driver.window_handles or []
        except Exception as e:
            last_error = str(e)
            handles = []
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if page_name in (driver.title or ''):
                    return True
            except Exception as e:
                last_error = str(e)
                continue
        if time.time() >= end:
            break
        time.sleep(0.5)
    if last_error:
        print(color.yellow(f'切换窗口失败：{last_error}'), flush=True)
    return False

def fold(driver):
    """折叠侧边目录"""
    try:
        with nav.probe_mode(driver):
            element = driver.find_element(By.XPATH, '//*[@id="selector"]/div[2]')
        element.click()
        time.sleep(0.3)
    except Exception:
        pass

def set_speed(speed,driver):
    global condition
    if not condition:
        return
    print(color.blue(f'调节倍数为：{speed}X'), flush=True)
    try:
        speed=int(speed)-1
        # 走 CDP 发给本标签页，多账号并发时不会打到别人的窗口上
        sent = browser_ext.press_key(driver, 'd', times=int(speed) * 10, interval=0.1)
        if sent == 0:
            print(color.yellow('按键未送达，倍速可能未生效'), flush=True)
        print(color.green('调节成功'), flush=True)
        condition=False
    except Exception as e:
        print(color.yellow(f'调节失败{e}'), flush=True)
        condition=True

def _scan_content_types(driver, content_types, found):
    """在当前帧里扫一遍内容类型，把命中的塞进 found。"""
    for name, by, selector in content_types:
        if name in found:
            continue
        try:
            driver.find_element(by, selector)
            found[name] = selector
        except NoSuchElementException:
            pass
    return found


# 一次 JS 判断「这页是不是单卡片的纯文本章节」。
#
# 真机 DOM 事实（2026-11-22 从用户真实快照
# `record/DOM/20261122/001812_未识别页面_1` 解出来的）：
#   · 章节卡片列表只有一个条目：`<ul class="prev_ul"><li class="active" id="dct1">`；
#   · 正文由 `uParse(".ans-cc")` 渲染，**页面上没有任何 `ans-attach-online`**；
#   · `.prev_title`（章节标题）与 `#prevNextFocusNext`（下一节）都在**顶层文档**。
#
# 旧实现要扫 1 个顶层 + 4 个 iframe × 12 种选择器（约 1~2 秒），再被
# `wait_page_message` 连试 3 轮 → 每个纯文本页白花 5~6 秒，然后被算成
# 「无法识别」累加，凑满 10 页就把整门课判死。日志里 00:47:50→00:49:45
# 连翻 11 页（chapterId 773→886→889→892→893 一直在变）全被记成"无法识别"。
#
# 判据刻意保守：必须同时满足「有章节标题」+「没有真任务点元素」+
# 「卡片只有 1 个」。只要章节里有多个卡片标签（截图里那种「1 正文 / 2 视频 /
# 3 ppt」），一律不认成文本页，继续走完整识别 —— 绝不静默吞掉任务点。
_QUICK_TEXT_JS = r"""
return (function(){
  function probe(doc){
    var out = {title: 0, job: 0, cards: 0, ready: 1, text: 0, media: 0};
    try {
      out.title = doc.querySelectorAll('.prev_title').length;
      out.job = doc.querySelectorAll('[class*="ans-attach-online"]').length;
      var li = doc.querySelectorAll('.prev_ul > li').length;
      var dct = doc.querySelectorAll('[id^="dct"]').length;
      out.cards = li > dct ? li : dct;
    } catch(e) {}
    try {
      // 正文实际字数：纯文本章节的卡片里一定有正文；
      // 而视频/ppt 卡片的播放器是 uParse 之后才插进 DOM 的，
      // 在它插进来之前这一帧「没正文也没媒体」——那种状态必须判为「未就绪」，
      // 否则会把整页任务点当纯文本跳过去（绝对不能犯的错）。
      var body = doc.body;
      out.text = body ? String(body.innerText || '').replace(/\s+/g, '').length : 0;
      // 注意：**不能把 iframe 算进来** —— 学习页顶层天生就有一个 #iframe，
      // 算进去的话每页都会被判成「有媒体」，纯文本章节永远认不出来。
      out.media = doc.querySelectorAll(
        'video, audio, embed, object, .ans-insertvideo, .ans-insertaudio,' +
        ' [class*="ans-attach"], [class*="insertdoc"]').length;
    } catch(e) {}
    try {
      var rs = doc.readyState || '';
      if (rs && rs !== 'complete' && rs !== 'interactive') out.ready = 0;
    } catch(e) {}
    return out;
  }
  var agg = probe(document);
  var frames = [];
  try { frames = document.querySelectorAll('iframe'); } catch(e) {}
  for (var i = 0; i < frames.length && i < 6; i++) {
    var cd = null;
    try { cd = frames[i].contentDocument; } catch(e) { cd = null; }
    if (!cd) continue;                       // 跨域帧不计，也无从判断
    var r = probe(cd);
    agg.title += r.title;
    agg.job += r.job;
    agg.text += r.text;
    agg.media += r.media;
    if (r.cards > agg.cards) agg.cards = r.cards;
    if (!r.ready) agg.ready = 0;
  }
  return agg;
})();
"""


def _is_text_chapter_fast(driver) -> bool:
    """单卡片纯文本章节：一次 JS 判定（约 50ms），不扫选择器。

    **只有在页面完全加载后才认**：视频/ppt 卡片的播放器是 uParse 之后才插入
    DOM 的，iframe 还在 loading 时页面「看起来什么都没有」，此时判成纯文本
    会把整页任务点跳过去。加载判断失败就一律走完整识别（宁可慢，不许漏）。
    """
    try:
        info = driver.execute_script(_QUICK_TEXT_JS)
    except Exception:
        return False
    if not isinstance(info, dict):
        return False
    if not int(info.get('ready') if info.get('ready') is not None else 1):
        return False          # 还没加载完 → 走完整识别，绝不当纯文本
    if int(info.get('title') or 0) <= 0:
        return False          # 没有章节标题栏 → 不像章节页，走完整识别
    if int(info.get('job') or 0) > 0:
        return False          # 页面上确实有任务点 → 必须走完整识别，绝不吞
    if int(info.get('cards') or 0) > 1:
        return False          # 章节内有多个卡片标签（可能有视频/ppt）→ 走完整识别
    if int(info.get('media') or 0) > 0:
        return False          # 页面上有播放器/文档/附件元素 → 不是纯文本
    if int(info.get('text') or 0) < 30:
        return False          # 卡片里既没正文也没媒体 → 还没渲染出来，走完整识别等待
    return True


def _page_rendered(driver) -> bool:
    """卡片帧里是否已经渲染出内容（正文 / 播放器 / 任务点元素）。

    只做一次 JS（约 50ms）。用于「页面还在切卡片、播放器还没插进来」时
    快速返回、交给上层重试，而不是立刻判「无法识别」后点下一节
    （那样会把刚切出来的视频/ppt 整页跳过去）。
    """
    try:
        info = driver.execute_script(_QUICK_TEXT_JS)
    except Exception:
        return True          # 读不到就当已渲染，保持原有行为，不引入新风险
    if not isinstance(info, dict):
        return True
    if int(info.get('job') or 0) > 0:
        return True
    if int(info.get('media') or 0) > 0:
        return True
    return int(info.get('text') or 0) >= 30


def _detect_text_chapter(driver, max_frames=4) -> bool:
    """页面没有任何已知模块，但确实是「章节页」（纯文本 / 新版章节详情）时返回 True。

    2026-11-22 真机（用户截图确认）：这个课的章节是**纯文字**，页面只有
    「上一节 / 下一节」，没有任何视频/ppt/题目。旧逻辑把它算「无法识别」，
    连续 11 页后直接结束整门课 —— 用户看到的就是「学着学着不学了」。
    同类事故 2026-11-14 也发生过一次（附件下载页）。

    判据（任一成立即可，都是「学习页」独有的）：
      1. 章节标题栏 `.prev_title` / 章节导航 `#prevNextFocusNext`；
      2. 地址仍是 `studentstudy?...chapterId=...`（新版「章节详情」页
         不一定有上面两个元素，但它毫无疑问是章节页）。
    反例保护：只要页面上还有 `ans-attach-online` 这类附件元素，就说明
    「有任务点但类型没认出来」——那种情况必须继续算未识别（要留 DOM、要计数），
    绝不能悄悄当成纯文本跳过。
    """
    def _has(driver_, selector):
        try:
            return bool(driver_.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            return False

    def _frame_has(driver_, selector):
        if _has(driver_, selector):
            return True
        for frame in frames[:max_frames]:
            try:
                driver_.switch_to.default_content()
                driver_.switch_to.frame(frame)
            except Exception:
                continue
            if _has(driver_, selector):
                return True
        return False

    try:
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, 'iframe')
        # 有附件元素却没认出来 → 不是纯文本页，别吞掉
        if _frame_has(driver, '[class*="ans-attach-online"]'):
            return False
        if _frame_has(driver, '.prev_title') or _frame_has(driver, '#prevNextFocusNext'):
            return True
        url = (_safe_url(driver) or '').lower()
        return 'studentstudy' in url and 'chapterid=' in url
    except Exception:
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def page_message(driver, max_frames=4):
    """检测页面内容类型，返回包含视频、PPT、测验、直播、讨论、音频、阅读的字典。

    2026-10-31 修正：新版学习页是 `mooc1-2.../visit/courses/study?s=...`，
    **外层只套了一个没有 id 的 iframe**，内容全在里边；旧代码只认 `#iframe`，
    所以整页"无法识别"，刷几次就被当成异常页跳过。
    现在按顺序扫：顶层文档 → `#iframe` → 其它任何 iframe（最多 4 个）。
    """
    # 内容类型配置：(名称, 查找方式, 选择器)
    CONTENT_TYPES = [
        ('视频', By.CSS_SELECTOR, '[class="ans-attach-online ans-insertvideo-online"]'),
        ('ppt', By.CSS_SELECTOR, '[class="ans-attach-online insertdoc-online-ppt"]'),
        ('ppt', By.CSS_SELECTOR, '[class="ans-attach-online insertdoc-online-pdf"]'),
        # 测验 iframe 用 src 包含匹配：平台前端发版会改 ?v= 后面的构建号，
        # 有的部署还会把 src 写成绝对 URL；前缀匹配也会漏（整章测验被静默跳过）。
        ('测验', By.CSS_SELECTOR, 'iframe[src*="/ananas/modules/work/index.html"]'),
        ('直播', By.CSS_SELECTOR, '[src^="/ananas/modules/live/index.html"]'),
        ('讨论', By.CSS_SELECTOR, '[src^="/ananas/modules/insertbbs/index.html"]'),
        ('音频', By.CSS_SELECTOR, '[class="ans-attach-online ans-insertaudio"]'),
        ('阅读', By.CSS_SELECTOR, '[class="ans-attach-online ans-book"]'),
        ('阅读', By.CSS_SELECTOR, '[src^="/ananas/modules/read/indexV2.html"]'),
        # 问卷（2026-11-19 从上游移植）。用包含匹配：平台会加 ?v=构建号，
        # 也可能把 src 写成绝对 URL，前缀匹配会漏掉整个问卷。
        ('问卷', By.CSS_SELECTOR, '[src*="/ananas/modules/zt/vote/index"]'),
        # 附件下载页（1.9 章节这类）本身没有任务点，但必须被识别成「已知页面」，
        # 否则会累计「无法识别」10 次后结束整门课（2026-11-14 真机日志）。
        # dispatch_page 只处理已知 key，'附件' 会被安全忽略，直接翻页。
        ('附件', By.CSS_SELECTOR, '[src*="/ananas/modules/downloadfile/index"]'),
    ]

    page_message_dict = {}
    # 0) 快速路径①：单卡片纯文本章节，一次 JS 就认出来（真机 2026-11-22 采到的结构）。
    #    这类页面没有任务点可做，**必须算「已知页面」**，否则会累加成
    #    「连续 N 页无法识别」把整门课判死（用户现场就是这么被掐断的）。
    try:
        if _is_text_chapter_fast(driver):
            return {'文本': [True]}
    except Exception:
        pass
    # 0b) 快速路径②：卡片帧还没渲染出任何正文/媒体 —— 现在扫选择器也是白扫，
    #     直接返回空让 `wait_page_message` 重试（每次只要 50ms，不再一页 2 秒）。
    #     真机关键事实：章节内「1 正文 / 2 视频 / 3 ppt」是卡片标签，
    #     切换到视频卡片后播放器要过一会儿才插进 DOM；这段时间里页面
    #     「看起来什么都没有」。旧代码这时立刻判「无法识别」并点下一节，
    #     等于把刚切出来的视频/ppt 整页跳过去（用户这门课 51 个任务点只完成 11 个）。
    try:
        if not _page_rendered(driver):
            return {}
    except Exception:
        pass
    original_wait = driver.timeouts.implicit_wait  # 保存原始等待时间
    driver.implicitly_wait(0)

    try:
        # 1) 顶层文档
        try:
            driver.switch_to.default_content()
            _scan_content_types(driver, CONTENT_TYPES, page_message_dict)
        except Exception:
            pass
        # 2) 逐帧下钻（#iframe 只是其中一个，新版可能用无 id 的 iframe）
        if not page_message_dict:
            for index in range(max_frames):
                try:
                    driver.switch_to.default_content()
                    frames = driver.find_elements(By.TAG_NAME, 'iframe')
                    if index >= len(frames):
                        break
                    driver.switch_to.frame(frames[index])
                except Exception:
                    continue
                _scan_content_types(driver, CONTENT_TYPES, page_message_dict)
                if page_message_dict:
                    break
        # 3) 兜底：一个已知模块都没匹配到，但页面确实是标准章节页
        #    （有章节标题栏 / 章节导航）——那就是「纯文本 / 无任务点」章节。
        #    2026-11-22 真机：连续 11 个文本章节被判「无法识别」，
        #    累计超过阈值后整门课被结束，后面章节一个都没学到。
        if not page_message_dict:
            try:
                if _detect_text_chapter(driver, max_frames):
                    page_message_dict = {'文本': [True]}
            except Exception:
                pass
    finally:
        driver.implicitly_wait(original_wait)  # 恢复原始等待时间
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

    return page_message_dict


def describe_page(driver, limit=4):
    """诊断用：打印当前页面标题 + 顶层/学习页里所有 iframe 的 src。

    识别不到页面时调用，日志里能直接看到这一页到底是什么模块，
    不用再靠人猜或用截图。
    """
    parts = []
    try:
        parts.append('title=%s' % _short(driver.title or '', 40))
    except Exception:
        parts.append('title=读取失败')
    for scope in ('top', 'iframe'):
        try:
            driver.switch_to.default_content()
            if scope == 'iframe':
                driver.switch_to.frame('iframe')
            srcs = []
            for element in driver.find_elements(By.TAG_NAME, 'iframe'):
                src = (element.get_attribute('src') or '').strip()
                if src:
                    srcs.append(src[:80])
            parts.append('%s=[%s]' % (scope, ' | '.join(srcs[:limit]) if srcs else '无 iframe'))
        except Exception as e:
            parts.append('%s=读取失败(%s)' % (scope, e))
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return '; '.join(parts)

NEXT_PAGE_XPATH = '//*[@id="prevNextFocusNext"]'

# 「下一页/下一节」按钮在不同版式里的备选找法（新版学习页没有 prevNextFocusNext）
NEXT_PAGE_XPATHS = (
    NEXT_PAGE_XPATH,
    '//*[self::a or self::button][normalize-space(.)="下一页"]',
    '//*[self::a or self::button][normalize-space(.)="下一节"]',
    '//*[self::a or self::button][contains(normalize-space(.), "下一页")]',
    '//*[self::a or self::button][contains(@class, "next")]',
    '//*[contains(@class, "nextBtn") or contains(@class, "next-btn")]',
)


# 一次 JS 扫完整棵帧树找「下一页」并按需点击（2026-11-21）。
#
# 为什么不用逐帧 XPath：
#   ① 长文本页上 `//*[contains(normalize-space(.), "下一页")]` 会先命中一串
#      祖先 div（整块正文里都含「下一页」三个字），真正的 <a> 排在后面；
#      旧代码只看前 4 个节点 → 永远找不到。
#   ② 逐帧 XPath 的每条都付一次 implicit_wait(2)：6 条 × 5 个上下文 = 60 秒。
#      真机日志每次「跳转下一页」卡 55~56 秒就是它。
#   ③ 只下钻一层 iframe，学习通深层模块（测验/作业在 #iframe 里面再套一层）找不到。
# 这里改为：同一份 JS 里递归 contentDocument（同源帧），只扫交互元素，
# 按「精确文本 > prevNextFocusNext > class 含 next」打分，同分取页面最下方那个
# （用户反馈的正是「按钮在最下方」），然后 scrollIntoView + click。全程不切 frame。
_NEXT_SEARCH_JS = r"""
return (function(){
  var EXACT = {'下一页':1,'下一节':1,'下一章':1,'下一页>':1,'下一节>':1};
  var MAXD = 4, CAP = 3000;
  var stats = {docs:0, frames:0, blocked:0, cands:0};

  function norm(s){ return (s == null ? '' : String(s)).replace(/[\s\u00a0]+/g, ''); }
  function clsOf(el){
    var c = el.className;
    if (c && typeof c === 'object' && 'baseVal' in c) c = c.baseVal;
    return norm(c);
  }
  function textOf(el){
    // 只认「本身几乎就是按钮文字」的元素：子节点多于 3 个的一律跳过，
    // 避免对祖先大 div 求整棵子树文本（那才是长文本页变慢的主因）。
    if (el.children && el.children.length > 3) return '';
    var t = norm(el.innerText || el.textContent || '');
    return t.length <= 8 ? t : '';
  }
  function rectOf(el, doc){
    var r = null;
    try { r = el.getBoundingClientRect(); } catch(e) { return null; }
    if (!r || r.width <= 0 || r.height <= 0) return null;
    try {
      var st = doc.defaultView.getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden') return null;
      if (parseFloat(st.opacity || '1') === 0) return null;
    } catch(e) {}
    return r;
  }
  function look(doc, depth, out){
    stats.docs++;
    var nodes = null;
    try {
      nodes = doc.querySelectorAll(
        '#prevNextFocusNext, a, button, input[type=button], input[type=submit],' +
        ' [onclick], [class*=next], [class*=Next], [id*=next], [id*=Next]');
    } catch(e) { nodes = null; }
    if (nodes) {
      var cap = nodes.length > CAP ? CAP : nodes.length;
      for (var i = 0; i < cap; i++) {
        var el = nodes[i];
        var t = textOf(el);
        var isId = (el.id === 'prevNextFocusNext');
        var isCls = /next/i.test(clsOf(el));
        var score = 0;
        // prevNextFocusNext 是学习通正版章节导航，优先级必须最高：
        // 否则文档阅读器里的「下一页」（更深、更靠下）会被误当成章节翻页，
        // 点了只翻文档不换章节 → 内容指纹不变 → 被判卡死。
        if (isId) score = 5;
        else if (EXACT[t]) score = 3;
        else if (isCls) score = 1;
        if (!score) continue;
        var r = rectOf(el, doc);
        if (!r) continue;
        out.push({el: el, score: score, top: r.top, depth: depth,
                  text: t, tag: (el.tagName || '').toUpperCase()});
      }
    }
    try {
      var frames = doc.querySelectorAll('iframe, frame');
      for (var j = 0; j < frames.length && depth < MAXD; j++) {
        stats.frames++;
        var cd = null;
        try { cd = frames[j].contentDocument; } catch(e) { cd = null; }
        if (!cd) { stats.blocked++; continue; }
        look(cd, depth + 1, out);
      }
    } catch(e) {}
  }

  var list = [];
  try { look(document, 0, list); } catch(e) {}
  stats.cands = list.length;
  // 失败时也要把扫描面平铺返回：日志里靠它判断是「没渲染」还是「选择器不对」。
  if (!list.length) {
    return {ok: 0, cands: stats.cands, docs: stats.docs,
            frames: stats.frames, blocked: stats.blocked};
  }
  list.sort(function(a, b){
    if (a.score !== b.score) return b.score - a.score;
    if (a.depth !== b.depth) return a.depth - b.depth;   // 优先章节导航所在的外层帧
    return b.top - a.top;                                // 同一帧里取页面最下方的（用户反馈：按钮在最底部）
  });
  var best = list[0], el = best.el;
  try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch(e) {}
  var clicked = false, err = '';
  try { el.click(); clicked = true; } catch(e) { err = String((e && e.message) || e); }
  if (!clicked) {
    try {
      var d = el.ownerDocument || document;
      var ev = d.createEvent('MouseEvents');
      ev.initMouseEvent('click', true, true, d.defaultView, 0, 0, 0, 0, 0,
                        false, false, false, false, 0, null);
      el.dispatchEvent(ev);
      clicked = true;
    } catch(e2) { err = err || String((e2 && e2.message) || e2); }
  }
  return {ok: clicked ? 1 : 0, text: best.text, tag: best.tag, depth: best.depth,
          score: best.score, top: Math.round(best.top), cands: stats.cands,
          docs: stats.docs, frames: stats.frames, blocked: stats.blocked,
          err: err};
})();
"""


def _click_next_by_js(driver):
    """一次 JS 遍历整棵帧树找「下一页」并点击；返回 (是否点到, 说明)。

    不切 frame，所以不会把播放器/控制条弄失焦，也不付 implicit_wait。
    跨域 iframe 读不到 contentDocument 时会跳过并计数（真正的跨域模块本身
    也没有章节导航，不影响）。
    """
    try:
        info = driver.execute_script(_NEXT_SEARCH_JS)
    except Exception as e:
        return False, 'JS 执行失败（%s）' % type(e).__name__
    if not isinstance(info, dict):
        return False, 'JS 无返回'
    cands = int(info.get('cands') or 0)
    docs = int(info.get('docs') or 0)
    frames = int(info.get('frames') or 0)
    if info.get('ok'):
        return True, '第%d层 %s<%s>（候选 %d，扫描 %d 文档/%d 帧）' % (
            int(info.get('depth') or 0) + 1, info.get('text') or 'next',
            info.get('tag') or '?', cands, docs, frames)
    return False, '未找到候选（扫描 %d 文档/%d 帧，跨域跳过 %d）' % (
        docs, frames, int(info.get('blocked') or 0))


def _is_study_page(driver) -> bool:
    """当前是否**就在学习页**（`/mycourse/studentstudy?chapterId=...`）。

    用户 2026-11-22 明确要求：既然人在学习页、下一节就在眼前，
    「认不出来」就只能继续点下一节，**绝不许回课程页重新定位**。
    真机后果：旧逻辑认不出 10 页就 return 'lost' → 回课程列表 → 列表判据又把
    学习页误当列表 → 找不到课程 → 等 180 秒 × 3 轮 → 整门课放弃（日志 00:49:45~00:57）。
    """
    try:
        url = (driver.current_url or '').lower()
    except Exception:
        return False
    return 'studentstudy' in url or '/visit/courses/study' in url


# 直接执行学习通自己的翻页函数（`#prevNextFocusNext` 的 onclick）。
#
# 真机 DOM（2026-11-22 快照）：
#   <div id="prevNextFocusNext" class="jb_btn ... prev_next next fr btnWidAuto"
#        onclick="PCount.next('1','1211178756','265852000','155480978','',true)">下一节</div>
#
# 它是 **div + onclick**，不是 <a href>，所以没有链接可以直连；
# 一旦点击被水印/遮罩/焦点/`display` 挡住，就完全推进不了。
# 直接执行 onclick 里的代码是最可靠的推进方式：不需要可见、不需要焦点、
# 不经过命中测试，也绕开所有 toggle 类陷阱。
_FORCE_NEXT_JS = r"""
return (function(){
  var el = document.querySelector('#prevNextFocusNext');
  if (!el) {
    var cands = document.querySelectorAll(
      '[id*="prevNextFocusNext"], .prev_next.next, .prevNextFocusNext, a.nextChapter');
    for (var i = 0; i < cands.length; i++) { el = cands[i]; break; }
  }
  if (!el) return {ok: 0, why: '页面里没有「下一节」元素'};
  var code = el.getAttribute('onclick') || '';
  try {
    if (code) {
      (new Function(code))();
      return {ok: 1, how: 'onclick', code: code.slice(0, 70)};
    }
    el.click();
    return {ok: 1, how: 'click'};
  } catch(e) {
    return {ok: 0, why: String((e && e.message) || e)};
  }
})();
"""


def _force_next_chapter(driver):
    """不靠点击，直接调用学习通自己的翻页函数；返回 (是否成功, 说明)。"""
    try:
        info = driver.execute_script(_FORCE_NEXT_JS)
    except Exception as e:
        # 执行翻页函数后页面立即卸载时，WebDriver 可能报脚本中断，这属于正常现象
        name = type(e).__name__
        if 'script' in name.lower() or 'timeout' in name.lower():
            return True, '已触发翻页（页面正在跳转）'
        return False, '执行失败（%s）' % name
    if not isinstance(info, dict):
        return False, '无返回'
    if info.get('ok'):
        return True, '已调用学习通翻页函数（%s）' % (info.get('how') or '')
    return False, str(info.get('why') or '未成功')


# 章节内的卡片标签（「1 正文 / 2 视频 / 3 ppt」）。
#
# 真机 DOM（2026-11-22 用户快照 `002436_未识别页面_1` / `002446_视频`）：
#   <ul class="prev_ul">
#     <li id="dct1" class="active" title="第二节 华夏共同体的发展"
#         onclick="changeDisplayContent(1,3,'1211178694','265852000','155480978','')">
#     <li id="dct2" title="视频"
#         onclick="changeDisplayContent(2,3,'1211178694','265852000','155480978','')">
#     <li id="dct3" title="ppt"
#         onclick="changeDisplayContent(3,3,'1211178694','265852000','155480978','')">
#   </ul>
#
# 点哪个 li，就把 `#iframe` 的 src 换成对应的 `num`（dct1→num=0、dct2→num=1）。
# **进入章节时永远默认激活 dct1（正文）**，视频/ppt 在别的卡片里 ——
# 只处理当前激活的卡片，就会把整章的视频和 ppt 全部跳过。
_CARDS_JS = r"""
return (function(){
  var out = {chapter: '', total: 0, items: []};
  try {
    var m = String(location.href || '').match(/chapterId=(\d+)/i);
    out.chapter = m ? m[1] : '';
  } catch(e) {}
  var lis = [];
  try { lis = document.querySelectorAll('.prev_ul > li[id^="dct"]'); } catch(e) {}
  for (var i = 0; i < lis.length; i++) {
    var li = lis[i];
    var cls = li.className || '';
    if (cls && typeof cls === 'object' && 'baseVal' in cls) cls = cls.baseVal;
    out.items.push({
      idx: parseInt(String(li.id || '').replace('dct', ''), 10) || 0,
      title: String(li.getAttribute('title') || '').slice(0, 24),
      active: /(^|\s)active(\s|$)/.test(String(cls))
    });
  }
  out.total = lis.length;
  return out;
})();
"""

_SWITCH_CARD_JS = r"""
return (function(idx){
  var li = document.querySelector('#dct' + idx);
  if (!li) {
    var all = document.querySelectorAll('.prev_ul > li[id^="dct"]');
    for (var i = 0; i < all.length; i++) {
      if (String((all[i].id || '')).replace('dct', '') === String(idx)) { li = all[i]; break; }
    }
  }
  if (!li) return {ok: 0, why: '没有第 ' + idx + ' 个卡片'};
  var code = li.getAttribute('onclick') || '';
  try {
    // 直接执行平台自己的 changeDisplayContent(...)：换 src 是同步的，
    // 点击反而可能被标签栏的遮罩/圆角命中层挡住。
    if (code) { (new Function(code))(); return {ok: 1, how: 'onclick'}; }
    li.click();
    return {ok: 1, how: 'click'};
  } catch(e) {
    return {ok: 0, why: String((e && e.message) || e)};
  }
})(arguments[0]);
"""

# chapterId -> 已处理过的卡片序号；以及 (chapterId, 序号) -> 尝试次数
_CARD_SEEN = {}
_CARD_TRIES = {}


def _cards_info(driver):
    """读章节内的卡片标签；单卡片或读不到时返回 None。"""
    try:
        info = driver.execute_script(_CARDS_JS)
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    items = info.get('items') or []
    if len(items) <= 1:
        return None
    return info


def _switch_card(driver, index) -> bool:
    """切到章节内第 index 个卡片；返回是否切换成功。"""
    try:
        info = driver.execute_script(_SWITCH_CARD_JS, int(index))
    except Exception:
        return False
    return isinstance(info, dict) and bool(info.get('ok'))


def _advance_card(driver, chapter_key='') -> bool:
    """把当前卡片记为已处理，并切到下一个未处理的卡片。

    返回 True 表示**已经切换**，调用方应 `continue` 重新识别这个卡片；
    返回 False 表示本章所有卡片都处理完了（或者只有一张卡片），可以正常翻页。

    安全阀：
      · 每张卡片最多尝试 2 次，切不过去就标记已处理，绝不死循环；
      · 只保留最近 3 个章节的状态，避免长跑时字典无限增长。
    """
    info = _cards_info(driver)
    if not info:
        return False
    items = info.get('items') or []
    key = str(info.get('chapter') or chapter_key or '-')
    if key not in _CARD_SEEN and len(_CARD_SEEN) >= 3:
        _CARD_SEEN.clear()
        _CARD_TRIES.clear()
    seen = _CARD_SEEN.setdefault(key, set())

    # 当前正在显示的那张，说明刚被 dispatch 处理过（处理器是阻塞的）
    for item in items:
        if item.get('active'):
            seen.add(int(item.get('idx') or 0))
            break

    for item in items:
        idx = int(item.get('idx') or 0)
        if idx <= 0 or idx in seen:
            continue
        tried = _CARD_TRIES.get((key, idx), 0)
        if tried >= 2:
            seen.add(idx)          # 切了两次都不过去，放弃这张，避免死循环
            continue
        _CARD_TRIES[(key, idx)] = tried + 1
        if _switch_card(driver, idx):
            titles = ' / '.join(str(i.get('title') or '?') for i in items)
            print(color.green('本章共 %d 个卡片（%s），切到第 %d 个：%s'
                              % (len(items), titles, idx, item.get('title') or '')), flush=True)
            time.sleep(1.5)        # 等 #iframe 换 src 并渲染出任务点
            return True
    return False


def _find_next_button(driver):
    """找「下一页/下一节」按钮（Selenium 兜底路径，JS 不可用时才走）；找不到返回 None。

    2026-11-21 两个必改点：
      ① 必须包在 probe_mode 里。全局 implicitly_wait(2)，6 条 XPath × 5 个上下文
         一次找不到就是 60 秒 —— 真机日志里每次「跳转下一页」卡 55~56 秒就是这么来的。
      ② 不再只看前 4 个节点。长文本页里 `contains(normalize-space(.), "下一页")`
         会先命中一串祖先容器，真正的按钮排在第 5 个以后，只看前 4 个永远找不到。
    """
    with nav.probe_mode(driver):
        for xpath in NEXT_PAGE_XPATHS:
            try:
                nodes = driver.find_elements(By.XPATH, xpath)
            except Exception:
                continue
            for node in nodes[:40]:
                try:
                    if not node.is_displayed():
                        continue
                    size = node.size or {}
                    if size.get('width', 0) <= 0 or size.get('height', 0) <= 0:
                        continue
                    return node
                except Exception:
                    continue
    return None


def _looks_like_home_page(driver) -> bool:
    """是不是被踢回登录页/个人空间首页了（会话失效、别处登录都会这样）。"""
    try:
        url = (driver.current_url or '').lower()
    except Exception:
        url = ''
    try:
        title = (driver.title or '')
    except Exception:
        title = ''
    if 'passport' in url or 'login' in url:
        return True
    if 'i.chaoxing.com/base' in url or url.rstrip('/').endswith('i.chaoxing.com'):
        return True
    return '用户登录' in title


def _looks_like_course_home(driver) -> bool:
    """是不是"被打回的课程空壳页"（/visit/courses/study 但没有学习内容）。

    用户实测：这个 URL 是平台把人打回来时落的页面，里面没有 `#iframe`、没有任务点，
    也没有「下一页」，在这一页上翻页只会空转，正确做法是回课程页重新定位任务点。
    """
    try:
        url = (driver.current_url or '').lower()
    except Exception:
        return False
    if '/visit/courses/study' not in url:
        return False
    # 有内容就算正常学习页（page_message 已经先判过一次，这里只是兜底）
    try:
        return not bool(driver.find_elements(By.ID, 'iframe'))
    except Exception:
        return True

# 识别不到页面时先刷新几次（平台偶尔要刷新才把内容渲染出来），仍不行才跳过
UNKNOWN_REFRESH_LIMIT = 3

# 连续几次「页面内容完全没变化」就结束本课程，避免死循环
STUCK_LIMIT = 3

# 连续多少个页面认不出来就结束本课程。认不出的页面现在只截图 + 直接下一节（很便宜），
# 所以给得宽一些，避免把一门课里几页纯文本知识点误判成"整门课没救了"。
UNKNOWN_SKIP_LIMIT = 10


# 能从 URL / iframe src 里稳定区分「第几章第几个任务点」的参数。
# 不收 s= / ktoken= 这类每次请求都可能变的随机 token，否则刷新也会被误判成「换页了」。
_PAGE_IDENTITY_KEYS = ('chapterid', 'courseid', 'clazzid', 'knowledgeid',
                       'objectid', 'jobid', 'cardid')


def _page_identity(driver):
    """当前学习页的「身份」：URL 与各 iframe src 里能区分章节的稳定参数。

    2026-11-05 修复：新版学习页切到下一节时，模块 iframe 的 class 和 src 前 80 字符
    完全不变（都是 .../ananas/modules/video/index.html?v=2026-1030-1154），
    `.prev_title` 也取不到，导致 `_content_signature()` 对所有视频页返回同一个值；
    `run()` 连续 3 次比对相同就误报卡住、提前结束整门课。
    诊断日志里 `studentstudy?chapterId=xxx` 每次都在变，那才是换页的硬凭据。
    取不到任何稳定参数时返回空串，由调用方退回到原来的内容指纹。
    """
    urls = []
    try:
        urls.append(driver.current_url or '')
    except Exception:
        pass
    try:
        elements = driver.find_elements(By.TAG_NAME, 'iframe')[:8]
    except Exception:
        elements = []
    for element in elements:
        try:
            src = element.get_attribute('src') or ''
        except Exception:
            continue
        if src:
            urls.append(src)
    keys = []
    for url in urls:
        for key in _PAGE_IDENTITY_KEYS:
            for value in re.findall(r'[?&]' + key + r'=([^&#\s]+)', url or '', flags=re.I):
                item = '%s=%s' % (key, value)
                if item not in keys:
                    keys.append(item)
    # 排序后再拼：iframe 异步加载/顺序抖动不该让同一个页面产生不同指纹
    return ','.join(sorted(keys))


def _content_signature(driver):
    """当前学习页的内容指纹：章节标题 + 已完成任务点数 + 任务点总数 + 模块 iframe 的 src。

    平台切到下一个任务点时**不一定重建 #iframe**（常见做法是改 iframe 的 src 或直接换内容），
    所以「元素句柄是否失效」不能当作翻页成功的判据——按句柄判断会误报「没前进」，
    进而把整门课提前结束，看起来就是「后面章节/测验被跳过」。
    这里改用内容特征：完成数、任务点数、模块 src 任何一项变化都算换页。
    """
    title = ''
    finished = -1
    icons = -1
    srcs = ''
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
        try:
            title = driver.find_element(By.CLASS_NAME, 'prev_title').get_attribute('title') or ''
        except Exception:
            title = ''
        try:
            finished = len(driver.find_elements(By.CSS_SELECTOR, '[class*="ans-job-finished"]'))
            icons = len(driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear'))
        except Exception:
            pass
        try:
            srcs = ';'.join((element.get_attribute('src') or '')[:80]
                            for element in driver.find_elements(By.TAG_NAME, 'iframe'))
        except Exception:
            srcs = ''
    except Exception:
        pass
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return '%s|%s|%s|%s' % (title, finished, icons, srcs)


def _short(text, limit=70):
    return (text or '').replace('\n', ' ')[:limit]


def _page_all_tasks_done(driver) -> bool:
    """当前学习页里的任务点是不是**全部**已完成。

    用途（2026-11-21）：区分「课程真的到底了」和「页面卡住了」。
    真机那门课只有一个视频，平台已经打勾，`下一页` 点了没反应（本来就没有下一页），
    旧逻辑一律当成「卡住」→ return 'lost' → 回课程页 → 在学习页上找「章节」标签
    → 白等 15 秒 → 三次之后整门课放弃，最后还抛 UnboundLocalError。
    只要本页所有任务点都打勾，就应该判「本课程已学完」，而不是「丢了」。
    """
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
        icons = driver.find_elements(By.CLASS_NAME, 'ans-job-icon-clear ')
        if not icons:
            return False
        for el in icons:
            try:
                txt = el.get_attribute('aria-label') or ''
                parent_cls = el.find_element(By.XPATH, '..').get_attribute('class') or ''
            except Exception:
                return False
            if txt != '任务点已完成' and 'ans-job-finished' not in parent_cls:
                return False
        return True
    except Exception:
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def click_next_page(driver, pass_face):
    """点「下一页」，返回 False 表示课程已到底；True 表示点过了。

    是否真的换页**不在这里判断**（平台换内容时 #iframe 不失效，按句柄判断会误报「没前进」），
    交给 run() 用 `_content_signature()` 比对：连续几次内容都没变化才算卡死。
    """
    global error_num, _NEXT_GAVE_UP

    def _try_click():
        """在「当前上下文」里找并点下一页；返回 'clicked' / 'none'。"""
        button = _find_next_button(driver)
        if button is None:
            return 'none'
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        except Exception:
            pass
        try:
            button.click()
        except Exception:
            # 「不可交互」≠「课程已完结」：按钮被遮罩/禁用/懒加载都会抛
            # ElementNotInteractable。旧逻辑直接返回 'finished'，会打印
            # 「该课程全部已完结」并把整门课判完成（2026-11-19 审计 P0-5）。
            # 这里先退化为 JS 点击，仍失败就按「没找到按钮」处理，交给
            # error_num / 回课程页重定的逻辑，而不是误判学完。
            try:
                driver.execute_script("arguments[0].click();", button)
            except Exception:
                return 'none'
        return 'clicked'

    started = time.perf_counter()

    # 1) 首选：一次 JS 扫完整棵帧树并点击。
    #    不切 frame（不会把播放器弄失焦），不付 implicit_wait，能下钻多层。
    ok_js, detail = _click_next_by_js(driver)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    result = 'clicked' if ok_js else 'none'

    # 2) JS 没点到（跨域帧 / 页内脚本被拦 / 老版式）：Selenium 兜底
    if result == 'none':
        print(color.yellow('JS 没点到「下一页」（%s），改用逐帧查找' % detail), flush=True)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if _try_click() == 'clicked':
            result, detail = 'clicked', 'Selenium 顶层'
        else:
            for index in range(4):
                try:
                    driver.switch_to.default_content()
                    frames = driver.find_elements(By.TAG_NAME, 'iframe')
                    if index >= len(frames):
                        break
                    driver.switch_to.frame(frames[index])
                except Exception:
                    continue
                if _try_click() == 'clicked':
                    result, detail = 'clicked', 'Selenium 第%d层' % (index + 1)
                    break
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

    cost = time.perf_counter() - started

    if result == 'none':
        # 点击路径全部失败时，先别急着把「找不到按钮」当卡死 ——
        # 真机（2026-11-22）学习通把「下一节」做成 `div + onclick="PCount.next(...)"`，
        # 一旦被水印/遮罩挡住，点多少次都不会生效，但直接调用那个函数一定能翻页。
        ok_force, force_detail = _force_next_chapter(driver)
        if ok_force:
            print(color.green('点击没生效，改为直接调用学习通翻页函数：%s' % force_detail),
                  flush=True)
            error_num = 0          # 这是有效推进动作，不能算「找不到按钮」
            _NEXT_GAVE_UP = False
            time.sleep(0.8)
            return True
        # 把耗时和扫描面一起打出来：下次再「找不到」能一眼看出是没渲染、
        # 还是选择器不对、还是又退回慢路径空等了。
        print(color.yellow('暂时没找到「下一页」按钮（耗时 %.1fs，%s），等一会儿再点'
                           % (cost, detail)), flush=True)
        print(color.yellow('直接调用翻页函数也失败（%s）' % force_detail), flush=True)
        error_num += 1
        if error_num >= 8:
            # 找不到按钮 ≠ 课程完结。旧逻辑返回 False 会被 run() 当成 'done'
            # 直接结束整门课（2026-11-14：用户手动跳过后程序自动退出）。
            print(color.red('❌ 连续多次找不到「下一页」，回课程页重新定位'), flush=True)
            _NEXT_GAVE_UP = True
            return False
        time.sleep(2)
        return True
    error_num = 0          # 成功点击后清零，避免历史错误把后面的课拖停
    _NEXT_GAVE_UP = False
    if cost > 2:
        # 正常应该是 0.2~0.5 秒（一次 JS）。超过 2 秒说明又走了兜底慢路径。
        print(color.yellow('[耗时] 找「下一页」%.1fs（%s）' % (cost, detail)), flush=True)

    # 有的页面翻页会弹确认框，先点掉再等页面渲染
    time.sleep(0.8)
    try:
        driver.find_element(By.XPATH, '//*[@id="mainid"]/div[1]/div/div[3]/a[2]').click()
    except Exception:
        pass
    time.sleep(1.5)
    return True

chapter_name=''
def check_chapter_active(driver,multi_device):
    """判断该章节是否正在被学习"""
    global chapter_name
    if not multi_device:
        return True
    # prev_title 在学习页 #iframe 里。旧实现不切帧就在顶层找，多端模式下第一轮
    # 必抛 NoSuchElementException，而 run()/main() 都没有 try → 整个账号进程退出，
    # 后面的课全部不处理（2026-11-19 审计 P0-6）。
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame('iframe')
        element = driver.find_element(By.CLASS_NAME, 'prev_title')
        chapter_name = element.get_attribute('title')
    except Exception:
        # 拿不到章节名就放行：真正的多端互斥还有 studying_record.txt 兜底，
        # 不能因为读不到标题就把整门课丢掉。
        return True
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    record_path = paths.record('studying_record.txt')
    marker = f'正在学习{chapter_name}...\n'
    try:
        with open(record_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if marker in content:
                print(color.yellow(f'检测到{chapter_name}章节正在学习中，跳过'), flush=True)
                return False
    except FileNotFoundError:
        pass
    with open(record_path, 'a', encoding='utf-8') as f:
        f.write(marker)
    return True

def delete_record():
    """删除该章节的正在学习记录"""
    record_path = paths.record('studying_record.txt')
    try:
        with open(record_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        return
    # 兼容旧版本写下的标记（转义写法）
    legacy_prefix = '\u6b63\u5728\u5237'
    if chapter_name=='':
        content = re.sub(r'正在学习.*?\.\.\.\n', '', content)
        content = re.sub(legacy_prefix + r'.*?\.\.\.\n', '', content)
    else:
        content = content.replace(f'正在学习{chapter_name}...\n', '')
        content = content.replace(f'{legacy_prefix}{chapter_name}...\n', '')
    with open(record_path, 'w', encoding='utf-8') as f:
        f.write(content)

def wait_page_message(driver, tries=3, interval=1.5):
    """页面可能还在加载，多试几次再判定为「无法识别」。

    旧逻辑一次识别不到就直接跳过，遇到加载慢的页面会白白漏掉任务点。
    """
    for i in range(max(1, tries)):
        try:
            message = page_message(driver)
        except Exception:
            message = {}
        if message:
            return message
        if i < tries - 1:
            time.sleep(interval)
    # 等满 3 次仍识别不到：区分「页面还在加载」和「这节本来就没有任务点」。
    # 真机日志 2026-11-14：部分章节是空白节，一个 iframe 都没有，旧逻辑
    # 会把它们当异常页累计 10 次后结束整门课。
    # 判据刻意收紧：必须是学习页 + readyState complete + 完全没有 iframe。
    # 只要还有 iframe，就说明可能是选择器失效导致漏识别，保留诊断能力。
    try:
        url = driver.current_url or ''
        ready = driver.execute_script('return document.readyState') or ''
        frames = driver.find_elements(By.TAG_NAME, 'iframe')
        if (ready == 'complete' and not frames
                and ('studentstudy' in url or '/visit/courses/study' in url)):
            return {'无任务点': 'empty-section'}
    except Exception:
        pass
    return {}


def dispatch_page(driver, message, course_name, choice, API, lock_screen, pass_face,
                  video_title_choice, discussion_choice, after_finish_question,
                  pass_vido, speed):
    """按页面内容类型分发处理；每类单独兜底，单点异常不中断整页。"""
    def safe(name, func):
        try:
            func()
        except UnexpectedAlertPresentException:
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass
        except Exception as e:
            print(color.red(f'{name}处理出错: {e}'), flush=True)
            save_diagnostic_screenshot(driver, f'{name}出错')
            try:
                from core.notifier import Notifier, format_message
                n = Notifier.instance()
                tag = '%s:%s' % (name, type(e).__name__)
                count = n.error_count(tag)
                head = '%s · 今日第%d次 · %s处理出错' % (type(e).__name__, count, name)
                lines = [str(e)[:40], '最近操作：%s' % name,
                         '完整堆栈已写入 error.log']
                n.notify('error', format_message(head, lines),
                         title='异常告警 · %s' % name)
            except Exception:
                pass

    if 'ppt' in message:
        safe('PPT', lambda: __ppt(driver, course_name))
    if '直播' in message:
        safe('直播', lambda: watch_live(driver, message['直播'], course_name))
    if '音频' in message:
        safe('音频', lambda: play_audio(driver, message['音频'], course_name))
    if '讨论' in message:
        if discussion_choice != '跳过讨论':
            safe('讨论', lambda: finish_discussion(
                driver, API, message['讨论'], discussion_choice, course_name))
        else:
            print(color.yellow(f'您已选择{discussion_choice}'), flush=True)
    if '阅读' in message:
        safe('阅读', lambda: reading(driver, message['阅读'], course_name))
    if '问卷' in message:
        # 关闭时也要「识别但不处理」：否则问卷页会被当成未识别页累计后结束整门课
        if runtime.vote_enabled():
            safe('问卷', lambda: finish_vote(driver, message['问卷'], course_name))
        else:
            print(color.yellow('已关闭「自动问卷」，跳过该问卷'), flush=True)
    if '视频' in message:
        # 视频内弹题固定随机作答（见 task/watch_vido.py check_video_question），
        # 不再需要 API 与 video_title_choice
        safe('视频', lambda: study_page(
            driver, course_name, lock_screen, pass_vido, speed, pass_face))
    if '测验' in message:
        # 兼容旧配置里保存的值（旧版本用过一个现在不再使用的叫法）
        if choice not in ('不作答', '\u4e0d\u5237\u9898'):
            safe('测验', lambda: finish_quiz(
                driver, course_name, API, choice, after_finish_question))
        else:
            print(color.yellow('您已选择不作答，即将跳过测试题'), flush=True)


def run(driver,choice,course_name,API,lock_screen,pass_face,video_title_choice,
        discussion_choice,after_finish_question,pass_vido,speed,multi_device):
    global error_num, _NEXT_GAVE_UP
    # error_num 是全局的，以前跨课程/跨重进累积，8 次找不到「下一页」就把
    # 后面一门好课判成「已完结」。每轮循环开始清零（2026-11-14 真机日志）。
    error_num = 0
    _NEXT_GAVE_UP = False
    unknown_count = 0
    # 「无法识别」只有在**同一页反复发生**时才算卡住。换了页还认不出，
    # 说明只是这一章没有已知模块（文本/公告类），继续翻页即可。
    # 2026-11-22 真机：11 个不同 chapterId 的文本页被累加成「连续无法识别」，
    # 整门课被结束 —— 用户看到的就是「学着学着不学了」。
    unknown_same = 0
    last_unknown_id = ''
    no_progress = 0
    last_sig = ''
    last_dump_sig = ''      # 已落盘 DOM 的页面指纹，避免同一页反复写盘
    while True:
        cond = True
        delete_face_popup(driver, pass_face)
        # 图片题被点开后可能留下一层全屏遮罩，先收掉，否则整页点击都会失效
        try:
            if image_viewer_is_open(driver):
                close_image_viewer(driver)
        except Exception:
            pass
        if check_chapter_active(driver, multi_device):
            print(color.green('正在检测页面内容'), flush=True)
            page_message_dict = wait_page_message(driver)

            if not page_message_dict:
                # 认不出来的页面本身就说明页面结构/内容变了，不能让「卡住」计数跨页累积
                # （2026-11-05：计数曾跨 38 分钟、跨多个章节挂到第 3 次，误结束整门课）。
                no_progress = 0
                last_sig = ''
                # 用户要求：认不出来的页面不要反复刷新耗时间，先截一张诊断图，然后直接点下一节。
                unknown_count += 1
                # 用页面身份判断「是不是同一页」：课程里连着好几个文本章节时，
                # chapterId 会变，那属于正常推进，绝不能累计成卡死。
                unknown_id = _page_identity(driver) or _safe_url(driver)
                if unknown_id and unknown_id == last_unknown_id:
                    unknown_same += 1
                else:
                    unknown_same = 0
                    last_unknown_id = unknown_id
                print(color.red(f'该页面无法识别（第 {unknown_count} 次，同页 {unknown_same} 次）'),
                      flush=True)
                # 连续无法识别时不必每页都截一张大图 + 打一长串诊断（写盘很慢）
                if unknown_count == 1 or unknown_count % 3 == 0:
                    print(color.yellow('页面诊断：' + describe_page(driver)), flush=True)
                    save_diagnostic_screenshot(driver, '未识别页面')
                    # 未识别页是最需要留档的：完整 DOM 存下来，事后离线分析平台改版
                    record_dom(driver, '未识别页面_%d' % unknown_count,
                               {'course': course_name},
                               key='unknown:%s|%s' % (_safe_url(driver), _safe_title(driver)),
                               min_interval=60)
                if _looks_like_home_page(driver):
                    print(color.red('页面已经回到首页/登录页（会话失效或被别处登录踢下线），'
                                    '结束本课程'), flush=True)
                    return 'home'
                if _looks_like_course_home(driver):
                    # 被平台打回课程空壳页：这页没有任务点也没有「下一页」，死磕只会空转
                    print(color.yellow('当前是"被打回的课程空壳页"，回去重新定位未完成任务点'),
                          flush=True)
                    return 'lost'
                if unknown_same >= UNKNOWN_SKIP_LIMIT:
                    # 同一页（chapterId 没变）连续认不出：先直接调学习通翻页函数推进一次。
                    # 用户 2026-11-22 明确要求：人在学习页、下一节就在眼前，
                    # **不许回课程页**，能往下翻就往下翻。
                    ok_force, force_detail = _force_next_chapter(driver)
                    if ok_force:
                        print(color.yellow(
                            '同一页连续认不出，已直接调用翻页函数推进：%s' % force_detail),
                            flush=True)
                        unknown_same = 0
                        unknown_count = 0
                        time.sleep(1)
                        continue
                    print(color.red(f'同一页连续 {UNKNOWN_SKIP_LIMIT} 次都无法识别、页面没变，'
                                    f'翻页函数也调不动（{force_detail}），才判定卡住'), flush=True)
                    return 'lost'
                print(color.yellow('跳过该页，直接点下一节'), flush=True)
            else:
                unknown_count = 0
                unknown_same = 0
                last_unknown_id = ''
                # 「页面身份」优先：chapterId / objectId 变了就说明确实换了页，
                # 哪怕模块 iframe 的 src、完成点数看起来一模一样。
                signature = _content_signature(driver)
                identity = _page_identity(driver)
                fingerprint = '%s||%s' % (identity, signature)
                repeat_page = False
                if signature and fingerprint == last_sig:
                    repeat_page = True
                    no_progress += 1
                    print(color.yellow(f'第 {no_progress} 次仍停留在同一页：{_short(fingerprint)}'),
                          flush=True)
                    if no_progress >= STUCK_LIMIT:
                        print(color.yellow(
                            f'连续 {STUCK_LIMIT} 次页面身份和内容都没变化，先刷新重试一次'), flush=True)
                        print(color.yellow('页面诊断：' + describe_page(driver)), flush=True)
                        save_diagnostic_screenshot(driver, '页面卡住')
                        try:
                            driver.refresh()
                            time.sleep(2)
                        except Exception:
                            pass
                        new_identity = _page_identity(driver)
                        new_signature = _content_signature(driver)
                        refreshed = '%s||%s' % (new_identity, new_signature)
                        # 刷新后 iframe 还没加载完时 identity 会是空串，不能把「参数还没出来」
                        # 当成「换页了」，否则真卡住时会一直「刷新→误判有进展」无限循环。
                        # 只认「页面身份」（chapterId 等稳定参数）真的变了。
                        # 刷新瞬间 iframe 尚未加载完，signature 里的 src / 任务点数会抖动，
                        # 拿它当换页凭据就会「刷新→误判有进展→无限循环」
                        # （2026-11-14 真机日志：同一 chapterId 空转 40 分钟）。
                        changed = bool(new_identity and new_identity != identity)
                        if changed:
                            print(color.green('刷新后页面已变化，继续学习'), flush=True)
                            no_progress = 0
                            last_sig = refreshed
                            delete_record()
                            continue
                        # 真的卡住了：先看是不是「课程已经到底」。
                        # 本页任务点全部打勾 + 下一页点不动 = 这门课学完了，
                        # 绝不能当成「页面丢了」回去重找（真机 2026-11-21 就是这么丢掉整门课的）。
                        if _page_all_tasks_done(driver):
                            print(color.green(
                                '本页任务点已全部完成，且没有下一页 —— 判定本课程已学完'),
                                flush=True)
                            return 'done'
                        # 'lost' 会让 main() 回课程页重新定位任务点（最多 3 次），
                        # 不再像以前那样 return 'done' 直接把整门课判成「学完」。
                        print(color.red('刷新后仍停在同一页，回课程页重新定位任务点'), flush=True)
                        return 'lost'
                elif signature:
                    no_progress = 0
                    last_sig = fingerprint
                if list(page_message_dict.keys()) == ['文本']:
                    # 纯文本章节：没有任务点可做，也不能算「无法识别」
                    print(color.yellow('该页面是纯文本章节（无任务点）'), flush=True)
                else:
                    print(color.green(f'该页面含有{list(page_message_dict.keys())}'), flush=True)
                # 每个新页面（按身份指纹判定）落一份完整 DOM，供事后离线分析改版/结构
                if fingerprint != last_dump_sig:
                    last_dump_sig = fingerprint
                    record_dom(driver, '+'.join(page_message_dict.keys()),
                               {'course': course_name, 'identity': identity},
                               key='page:%s' % fingerprint, min_interval=0)
                if repeat_page:
                    # 同一页处理两遍会让测验/讨论**重复提交**（2026-11-19 审计 P0-4）。
                    # 指纹没变说明上一轮已经处理过，这里只推进翻页，不再 dispatch。
                    print(color.yellow('该页与上次处理时完全一致，跳过重复处理，直接尝试翻页'),
                          flush=True)
                else:
                    try:
                        dispatch_page(driver, page_message_dict, course_name, choice, API,
                                      lock_screen, pass_face, video_title_choice,
                                      discussion_choice, after_finish_question, pass_vido, speed)
                    except Exception as e:
                        # 刷新一次就够了：刷新后不再原地重跑同一页，交给下面的翻页逻辑推进，
                        # 否则处理器每次都抛同一个异常时就会永久循环。
                        print(color.red(f'处理页面出错: {e}'), flush=True)
                        unknown_count += 1
                        record_dom(driver, '处理出错',
                                   {'course': course_name, 'error': str(e)[:300]},
                                   key='err:%s|%s' % (_safe_url(driver), type(e).__name__),
                                   min_interval=60)
                        # 刚刷新过，页面内容必然重新加载，不能让「卡住」计数挂着跨到下一轮
                        no_progress = 0
                        last_sig = ''
                        try:
                            driver.refresh()
                            time.sleep(2)
                        except Exception:
                            pass
                        if unknown_count <= UNKNOWN_REFRESH_LIMIT:
                            delete_record()
                            continue
                        print(color.yellow('该页处理连续失败，跳过该页'), flush=True)
                        unknown_count = 0
                        cond = True      # 允许推进，避免在同一页反复重试
                # 章节内多卡片（正文 / 视频 / ppt 标签）：当前这张处理完了，
                # 先切到下一个没处理过的卡片，而不是直接翻到下一节。
                # 真机 2026-11-22：一门课 51 个任务点只完成 11 个，就是因为
                # 进入章节时默认激活的永远是 dct1（正文），视频/ppt 在 dct2/dct3 里。
                if not repeat_page and _advance_card(driver, identity):
                    continue
            delete_record()

        driver.switch_to.default_content()
        if cond:
            print(color.green('跳转下一页'), flush=True)
            if not click_next_page(driver, pass_face):
                gave_up = _NEXT_GAVE_UP
                _NEXT_GAVE_UP = False
                return 'lost' if gave_up else 'done'
            # 翻页后不再固定等 1 秒：下一轮会按页面身份/指纹判断是否真的换了页
            time.sleep(0.3)
        else:
            delete_face_popup(driver, pass_face)
            fold(driver)
        time.sleep(1)


def start_browser(browser,driver_path,speed):
    print(color.green('启动浏览器中...'), flush=True)
    browser_key = 'chrome' if browser == 'chrome' else 'edge'
    if browser_key == 'chrome':
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        driver_class = webdriver.Chrome
    else:
        from selenium.webdriver.edge.service import Service
        from selenium.webdriver.edge.options import Options
        driver_class = webdriver.Edge
    options = Options()
    # 任务04：安全参数集中生成，默认不再关闭同源策略（--disable-web-security 已移除）。
    # 旧参数经 main.py 这里的显式开关才启用，默认 0。
    for arg in browser_options.build_argument_list(speed=speed):
        options.add_argument(arg)
    if speed != '1':
        options.add_extension(paths.asset('tool', 'speed.crx'))
    options.add_extension(paths.asset('tool', 'chrome-extension.crx'))

    # 新电脑上没有驱动缓存时，Selenium Manager 需要联网下载；
    # 国内网络/代理缺失/Edge 刚更新时会报 NoSuchDriverException。
    # 先用本机已有的驱动（配置路径、drivers/ 目录、Selenium 缓存），没有再联网。
    resolved = None
    try:
        resolved = driver_ext.resolve(browser_key, driver_path)
    except Exception as e:
        print(color.yellow(f'解析本机驱动失败: {e}'), flush=True)
    if resolved:
        print(color.green(f'使用本机驱动: {resolved}'), flush=True)
        service = Service(resolved)
    else:
        print(color.yellow('本机没有可用驱动，交给 Selenium 自动获取'), flush=True)
        service = Service()

    try:
        driver = driver_class(service=service, options=options)
    except NoSuchDriverException:
        print(color.yellow('自动获取驱动失败，尝试按本机浏览器版本直接下载...'), flush=True)
        try:
            downloaded = driver_ext.download(browser_key)
        except Exception as e:
            message = driver_ext.human_error(browser_key, e)
            print(color.red(message), flush=True)
            raise WebDriverException(message)
        driver = driver_class(service=Service(downloaded), options=options)

    # 隐式等待只保留 2 秒：避免与显式等待叠乘（V5.1 第23节）
    driver.implicitly_wait(2)
    # 首次导航前明确设置页面加载 / 脚本执行超时，避免 driver.get 无限阻塞
    try:
        applied = nav.configure_timeouts(driver)
        print(color.green('[导航] 超时策略 page_load=%s 秒 / script=%s 秒'
                          % (applied.get('page_load_timeout'),
                             applied.get('script_timeout'))), flush=True)
    except Exception as e:
        print(color.yellow('[导航] 超时策略设置失败（%s），由显式等待兜底'
                           % type(e).__name__), flush=True)
    return driver

def main(browser, driver_path, phone_number, password, choice, course_lst,API,after_finish_question,
         lock_screen,speed, task_type,homework,pass_face,video_title_choice,discussion_choice,pass_vido,multi_device):
    global _CURRENT_DRIVER
    # 空名称会匹配任意课程；过滤掉再进循环（2026-11-19 审计 P0-7）
    course_lst = [str(c).strip() for c in (course_lst or []) if str(c).strip()]
    if not course_lst:
        print(color.red('没有配置有效的课程名，本次不执行任何课程'), flush=True)
        return
    _t_browser = time.perf_counter()
    driver = start_browser(browser, driver_path,speed)
    perf._record('启动浏览器', time.perf_counter() - _t_browser)
    perf.mark_progress('main', '启动浏览器', 'WebDriver 已就绪')
    _CURRENT_DRIVER = driver
    # P0：登录失败必须终止本账号，绝不进入课程列表/任务循环
    _t_login = time.perf_counter()
    _logged_in = login_study(driver, phone_number, password)
    perf._record('账号登录', time.perf_counter() - _t_login)
    perf.mark_progress('main', '账号登录', '结果=%s' % _logged_in)
    if not _logged_in:
        print(color.red('本次登录失败，已停止本课程的所有后续操作'
                        '（未读取课程列表、未执行任何任务）'), flush=True)
        return
    # 让用户一眼看到「DOM 到底记在哪、今天记了多少」，避免再出现
    # 「以为在记其实配额被打满、一个都没写」的情况（2026-11-19）
    try:
        print(color.blue('页面 DOM 记录目录：%s（%s）'
                         % (dom_dump.root(), dom_dump.summary_line())), flush=True)
    except Exception:
        pass
    for course_name in course_lst:
        status = '未进入学习'      # 供课程结束通知兜底，避免未进学习分支时变量未定义
        delete_record()
        choice_course(driver, course_name, speed,  task_type,phone_number)
        if not turn_page(driver, course_name):
            # 课程页可能还在加载；只提示，不在这里放弃（find_mission 里会再等导航标签）
            print(color.yellow(f'暂未切换到《{course_name}》窗口，继续等页面加载'), flush=True)
            print(color.yellow(f'当前页面标题：{_safe_title(driver)}'), flush=True)
        experience(driver)
        delete_face_popup(driver,pass_face,course_name)

        # 课程考试是独立页面（顶层文档、逐题显示），不走章节任务点循环。
        # 不要照抄作业模式的 return：那会终止整个课程循环，后面的课永远处理不到。
        if task_type == '考试':
            from task.do_exam import run_exam
            print(color.green(f'《{course_name}》进入课程考试模式'), flush=True)
            exam_status = run_exam(driver, course_name, API)
            print(color.green(f'《{course_name}》考试结束：{exam_status}'), flush=True)
            try:
                from core.notifier import Notifier, format_message
                runtime.bump('courses_done')
                d = digest.build('course_finish', course_name=course_name)
                Notifier.instance().notify(
                    'course_finish',
                    format_message('《%s》考试已结束 · %s' % (course_name, exam_status),
                                   ['模式：课程考试',
                                    '错误：%d ｜ 用时：%s' % (d['errors'], d['elapsed_text'])]),
                    title='考试结束 · %s' % course_name)
            except Exception:
                pass
            _close_course_tab(driver)
            turn_page(driver, '个人空间')
            continue

        # 点任务点有可能开新标签页，先记下当前窗口，后面用它判断"是不是开了新窗口"
        try:
            before_handles = list(driver.window_handles or [])
        except Exception:
            before_handles = []

        if find_mission(driver,task_type,speed,course_name):
            if task_type == '作业':
                # 任务05：作业是「单门课处理」，绝不能 return 终止整个课程循环，
                # 否则第一门课做完作业后其余课程永远处理不到。与考试分支一样走
                # 完整收尾后 continue 到下一门课；异常只影响本门课。
                _work_ok = False
                try:
                    # V5.1 第22节：失败/超时/未识别必须记录，绝不计为完成课程
                    _work = do_work(driver, course_name, homework, API)
                    _work_status = getattr(_work, 'result_status', None)
                    _work_ok = (_work_status == tc.TaskStatus.SUCCESS)
                    if not _work_ok:
                        print(color.red(f'《{course_name}》作业未确认完成（{_work_status}），'
                                        f'不计入完成课程，继续下一门'), flush=True)
                except Exception as e:
                    print(color.red(f'《{course_name}》作业处理异常，'
                                    f'跳过本课程继续下一门：{e}'), flush=True)
                    print(traceback.format_exc(), flush=True)
                if _work_ok:
                    try:
                        runtime.bump('courses_done')
                        d = digest.build('course_finish', course_name=course_name)
                        from core.notifier import Notifier, format_message
                        Notifier.instance().notify(
                            'course_finish',
                            format_message('《%s》作业处理已结束' % course_name,
                                           ['模式：作业', '用时：%s' % d['elapsed_text']]),
                            title='作业结束 · %s' % course_name)
                    except Exception:
                        pass
                _close_course_tab(driver)
                turn_page(driver, '个人空间')
                continue
            if not turn_page(driver, '学生学习页面', timeout=8):
                # 学习页可能开在新标签页里、标题也不叫「学生学习页面」：
                # 切到新开的窗口再说，否则会在目录页上找元素 → 整页无法识别。
                if _switch_to_newest_window(driver, before_handles, timeout=8):
                    print(color.yellow(f'已切换到新开的页面：{_safe_title(driver)}'), flush=True)
                elif _landed_on_study_page(driver):
                    print(color.yellow(f'当前窗口就是学习页（标题：{_safe_title(driver)}），继续'),
                          flush=True)
                else:
                    print(color.yellow(f'没能切换到学习页，当前窗口：{_window_titles(driver)}'),
                          flush=True)

            if multi_device:
                monitor = PageCompat(driver)
                monitor.enable()

            delete_face_popup(driver,pass_face)
            fold(driver)
            browser_ext.press_combo(driver, 'm', browser_ext.CTRL)
            status = run(driver, choice, course_name, API, lock_screen, pass_face,
                         video_title_choice, discussion_choice, after_finish_question,
                         pass_vido, speed, multi_device)

            # 「页面丢了」不再一律回课程页。
            # 用户 2026-11-22 明确要求：如果人**就在学习页**（下一节就在眼前），
            # 回课程页纯属多余，而且真机里经常回不去（列表判据误判、课程卡片异步渲染），
            # 一等就是 180 秒 × 3 轮，整门课直接报废。
            # 所以：还在学习页 → 直接在当前页继续学（最多连续 3 次），绝不回列表。
            retry = 0
            stay_retry = 0
            while status == 'lost' and retry < 3:
                if _is_study_page(driver):
                    stay_retry += 1
                    if stay_retry <= 3:
                        print(color.yellow(
                            '仍停在学习页（下一节就在这页上），不回课程页，直接继续学'
                            '（第 %d/3 次）' % stay_retry), flush=True)
                        ok_force, force_detail = _force_next_chapter(driver)
                        if ok_force:
                            print(color.green('已直接调用翻页函数推进：%s' % force_detail),
                                  flush=True)
                        time.sleep(1)
                        delete_face_popup(driver, pass_face)
                        fold(driver)
                        status = run(driver, choice, course_name, API, lock_screen, pass_face,
                                     video_title_choice, discussion_choice,
                                     after_finish_question, pass_vido, speed, multi_device)
                        continue
                    print(color.red('学习页上连续 3 次都推进不了，才回课程页重新定位'),
                          flush=True)
                retry += 1
                print(color.yellow(f'页面丢了，回课程页重新定位未完成任务点（第 {retry} 次）'),
                      flush=True)
                if not _reenter_course(driver, course_name, speed, task_type, phone_number):
                    if retry >= 3:
                        print(color.red('回不到课程页，结束本课程'), flush=True)
                        break
                    print(color.yellow(f'第 {retry} 次没能回到课程页，刷新后重试'), flush=True)
                    try:
                        driver.refresh()
                        time.sleep(2)
                    except Exception:
                        pass
                    continue
                delete_face_popup(driver, pass_face)
                fold(driver)
                status = run(driver, choice, course_name, API, lock_screen, pass_face,
                             video_title_choice, discussion_choice, after_finish_question,
                             pass_vido, speed, multi_device)

            # 复习模式：一遍走完（status == 'done'）后回到第一章重刷，直到刷够遍数。
            # review_times <= 0 表示不限制，但仍用「单遍 < 30 秒 = 没有可刷内容」兜底，
            # 避免页面异常时秒速空转把日志刷爆。
            if runtime.review_enabled() and status == 'done':
                limit = runtime.review_times()
                rounds = 1
                max_rounds = limit if limit > 0 else 20
                print(color.yellow('复习模式：本遍已结束' +
                                   ('，计划共刷 %d 遍' % limit if limit > 0 else '，不限制遍数')),
                      flush=True)
                while rounds < max_rounds:
                    rounds += 1
                    print(color.yellow(f'复习模式：开始第 {rounds} 遍'), flush=True)
                    if not _reenter_course(driver, course_name, speed, task_type, phone_number):
                        print(color.red('复习模式：回不到课程页，结束复习'), flush=True)
                        break
                    delete_face_popup(driver, pass_face)
                    fold(driver)
                    # 必须回到第一章，否则第二遍会从上次停的位置往后翻，等于没复习
                    try:
                        driver.switch_to.default_content()
                        driver.switch_to.frame('iframe')
                    except Exception:
                        pass
                    if not _open_first_chapter(driver, speed):
                        print(color.red('复习模式：打不开第一章，结束复习'), flush=True)
                        break
                    t_round = time.time()
                    status = run(driver, choice, course_name, API, lock_screen, pass_face,
                                 video_title_choice, discussion_choice, after_finish_question,
                                 pass_vido, speed, multi_device)
                    cost = time.time() - t_round
                    print(color.green('复习模式：第 %d 遍结束，用时 %.0f 秒' % (rounds, cost)),
                          flush=True)
                    if status != 'done':
                        break
                    if cost < 30:
                        print(color.yellow('复习模式：本遍不足 30 秒就结束，判定没有可刷内容，自动停止'),
                              flush=True)
                        break
                print(color.green(f'复习模式：完成，共刷 {rounds} 遍'), flush=True)

            if status == 'home':
                print(color.red(f'《{course_name}》已被踢回首页/登录页，跳过剩余内容'), flush=True)
                try:
                    from core.notifier import Notifier, format_message
                    d = digest.build('course_abnormal', course_name=course_name)
                    head = '《%s》被踢回首页 · 本门课提前结束' % course_name
                    lines = ['状态：home（会话可能失效或被别处登录）',
                             '未识别页：%d ｜ 错误：%d' % (d['unknown_pages'], d['errors'])]
                    Notifier.instance().notify('course_abnormal',
                                               format_message(head, lines),
                                               title='课程异常结束 · %s' % course_name)
                except Exception:
                    pass
        try:
            from core.notifier import Notifier, format_message
            d = digest.build('course_finish', course_name=course_name)
            head = '《%s》已结束 · %s · %s' % (course_name, status, d['elapsed_text'])
            lines = ['视频记录：%d ｜ 答题：%d成功 %d跳过' % (
                        d['videos'], d['quiz_answered'], d['quiz_skipped']),
                     '讨论：%d成功 %d失败 ｜ 错误：%d' % (
                        d['discussion_ok'], d['discussion_failed'], d['errors']),
                     '未识别页：%d' % d['unknown_pages']]
            Notifier.instance().notify('course_finish', format_message(head, lines),
                                       title='课程结束 · %s' % course_name)
            runtime.bump('courses_done')
        except Exception:
            pass
        _close_course_tab(driver)
        turn_page(driver, '个人空间')


def _reenter_course(driver, course_name, speed, task_type, phone_number):
    """从空壳页/首页/学习页回到课程页并重新定位未完成任务点。

    2026-11-21 真机：连续 3 次「回不到课程页」→ 整门课被放弃。
    原因是一条死路：
      · 页面停在**学习页**（`studentstudy?chapterId=...`，标题「学生学习页面」）；
      · 旧实现在这里只会 `driver.refresh()`，刷新后还是同一个 chapterId；
      · 接着 `find_mission` 在学习页上找 `nav_content` / 「章节」标签，
        白等 15 秒后判「课程页面没有出现章节标签」→ 返回 False；
      · 三次全这么走，课程结束。
    现在：只要不是课程详情页，就**回「我学的课」重新进入课程**（和首次进入完全同一条路），
    这才是真正能回到课程页的办法。
    """
    if _looks_like_home_page(driver):
        # 被踢回个人空间：重新选课进去
        choice_course(driver, course_name, speed, task_type, phone_number)
        if not turn_page(driver, course_name, timeout=10):
            print(color.yellow(f'没能切回《{course_name}》页面'), flush=True)
            return False
    else:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if not turn_page(driver, course_name, timeout=5):
            # 刷新救不了学习页：必须回课程列表重新进入
            print(color.yellow('当前不在课程详情页（多半停在学习页），'
                               '刷新无效，改为回课程列表重新进入课程'), flush=True)
            choice_course(driver, course_name, speed, task_type, phone_number)
            if not turn_page(driver, course_name, timeout=10):
                print(color.yellow(f'没能切回《{course_name}》页面'), flush=True)
                return False
    try:
        before = list(driver.window_handles or [])
    except Exception:
        before = []
    if not find_mission(driver, task_type, speed, course_name):
        return False
    if not turn_page(driver, '学生学习页面', timeout=8):
        _switch_to_newest_window(driver, before, timeout=5)
    return True


def extract_browser_versions(error_text):
    """从错误信息中提取浏览器和驱动版本"""

    versions = {
        'driver_supported_version': None,  # 驱动支持的版本
        'browser_version': None,  # 浏览器当前版本
        'browser_type': None  # 浏览器类型
    }

    # 模式1: 匹配ChromeDriver/EdgeDriver支持的版本
    # 兼容两种格式:
    # 1. This version of ChromeDriver only supports Chrome version 140
    # 2. This version of EdgeDriver only supports Microsoft Edge version 120
    driver_pattern = r'This version of (ChromeDriver|Microsoft Edge WebDriver) only supports (?:Chrome|Microsoft Edge) version (\d+)'
    driver_match = re.search(driver_pattern, error_text)

    if driver_match:
        versions['browser_type'] = driver_match.group(1).replace('Driver', '')
        versions['driver_supported_version'] = driver_match.group(2)

    # 模式2: 匹配当前浏览器版本
    # 兼容多种格式:
    # 1. Current browser version is 143.0.7499.193
    # 2. Current Microsoft Edge version is 120.0.2210.91
    browser_pattern = r'Current (?:browser|Microsoft Edge) version is ([\d.]+)'
    browser_match = re.search(browser_pattern, error_text)

    if browser_match:
        versions['browser_version'] = browser_match.group(1)

    return versions

def parse_versions_from_text(error_text):
    """从文本中解析版本信息的完整函数"""

    # 提取版本信息
    versions = extract_browser_versions(error_text)

    # 打印结果
    print(color.red("=" * 50))
    print("版本信息分析结果:")
    print("=" * 50)

    if versions['browser_type']:
        print(f"浏览器类型: {versions['browser_type']}")

    if versions['driver_supported_version']:
        print(f"驱动支持版本: {versions['driver_supported_version']}")

    if versions['browser_version']:
        print(f"浏览器当前版本: {versions['browser_version']}")

    # 给出建议
    print("\n" + "=" * 50)
    print("问题诊断和建议:")
    print("=" * 50)

    if versions['browser_type'] and versions['driver_supported_version'] and versions['browser_version']:
        driver_ver = int(versions['driver_supported_version'])
        browser_main_ver = int(versions['browser_version'].split('.')[0])

        if browser_main_ver > driver_ver:
            print(f"版本不兼容: {versions['browser_type']}浏览器版本(v{browser_main_ver})过高，"
                  f"但驱动仅支持到v{driver_ver}")
            print(f"📋 解决方案:")
            print(f"  1. 下载{versions['browser_type']}Driver {browser_main_ver}的版本")
            print(f"  2. 或降级{versions['browser_type']}浏览器到{driver_ver}版本")
        elif browser_main_ver < driver_ver:
            print(f"⚠️  浏览器版本(v{browser_main_ver})可能过旧")
            print(f"📋 建议: 更新{versions['browser_type']}浏览器到最新版本")
        else:
            print(f"✅ 版本匹配: {versions['browser_type']}浏览器和驱动版本一致")

    print("\n相关下载链接:")
    if versions['browser_type'] == 'Chrome':
        print("  • 谷歌驱动: https://chromedriver.chromium.org/")
        print("  • Chrome浏览器: https://www.google.com/chrome/")
    elif versions['browser_type'] == 'Microsoft Edge Web':
        print("  • Edge驱动: https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/")
        print("  • Edge浏览器: https://www.microsoft.com/edge")
    print('\n具体操作步骤见用户须知')

    return versions

def _error_brief(error_msg: str) -> str:
    """取异常摘要：优先取「异常类型: 消息」那一行。

    旧实现取最后一行，遇到 WebDriver 附带的原生堆栈就只剩一句 `(No symbol) [0x...]`，
    日志面板看起来像「浏览器启动失败」，完全误导排查。
    """
    lines = [l.strip() for l in (error_msg or '').strip().splitlines() if l.strip()]
    for line in reversed(lines):
        if ('selenium.common.exceptions.' in line
                or 'Error:' in line or 'Exception:' in line):
            return line[:200]
    for line in reversed(lines):
        if (line.startswith('(No symbol)') or line.startswith('File "')
                or line.startswith('^')):
            continue
        return line[:200]
    return (lines[-1] if lines else '未知错误')[:200]


def run_exam_only():
    """独立「开始考试」子进程入口（2026-11-11）。

    与 run_main() 的区别：**不进课程循环、不点任何考试入口**。
    只做「起浏览器 → 自动登录 → 交给 task.exam_standalone 只读识别页面链」，
    用户自己选考试；答完停在页面由用户手动提交，程序绝不交卷。
    """
    global _CURRENT_DRIVER
    try:
        with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as fil:
            account_info = json.load(fil)
        try:
            from core import runtime
            runtime.configure(account_info)
            from core.notifier import Notifier
            Notifier.instance().configure(account_info)
        except Exception:
            pass

        driver = start_browser(account_info['browser'], account_info['driver_path'],
                               account_info.get('speed') or '1')
        _CURRENT_DRIVER = driver
        # P0：独立考试入口同样必须在登录失败时立即停止
        if not login_study(driver, account_info['phone_number'],
                           account_info['password']):
            print(color.red('本次登录失败，独立考试会话未启动'), flush=True)
            return
        print(color.green('登录完成，考试会话开始监听页面…'), flush=True)
        record_dom(driver, '考试会话开始', key='exam_start:%s' % _safe_url(driver))

        from task.exam_standalone import run_exam_standalone
        status = run_exam_standalone(driver, account_info.get('API', ''))
        print(color.green('独立考试会话结束：%s（交卷始终由用户手动完成）' % status),
              flush=True)
    except NoSuchWindowException:
        print(color.red('窗口意外关闭，考试会话结束（未交卷）'), flush=True)
    except SessionNotCreatedException as e:
        print(color.red('浏览器驱动版本不匹配：%s' % _error_brief(str(e))), flush=True)
    except Exception as e:
        import traceback
        print(color.red('独立考试启动失败：%s' % e), flush=True)
        print(traceback.format_exc(), flush=True)


def run_main():
    try:
        with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as fil:
            account_info = json.load(fil)
        # 装载运行时策略与通知配置（任何失败都不能影响学习主流程）
        try:
            from core import runtime
            runtime.configure(account_info)
            from core.notifier import Notifier
            Notifier.instance().configure(account_info)
        except Exception:
            pass
        main(account_info['browser'], account_info['driver_path'], account_info['phone_number'], account_info['password'],
             account_info['choice'], account_info['cour'],account_info['API'],account_info['after_finish_question'],
             account_info['lock_screen'],account_info['speed'],account_info['task_type'],account_info['homework'],
             account_info['pass_face'],account_info['video_title_choice'],account_info['discussion_choice'],
             account_info['pass_vido'],account_info['multi_device'])
    except NoSuchWindowException as e:
        print(color.red('❌ 窗口意外关闭'),flush=True)
    except SessionNotCreatedException as e:
        error_msg = traceback.format_exc()
        send_error(
            "\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n" + error_msg)
        print(color.red(f'❌ 浏览器驱动版本不匹配: {_error_brief(error_msg)}'), flush=True)
        # 执行分析
        result = parse_versions_from_text(error_msg)

    except NoSuchElementException as e:
        # 页面元素没找到 ≠ 浏览器启动失败：多半是页面没加载完、没切到课程页，或平台改版
        error_msg = traceback.format_exc()
        send_error(
            "\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n" + error_msg)
        save_diagnostic_screenshot(_CURRENT_DRIVER, '找不到元素')
        print(color.red(f'❌ 页面上找不到预期元素（不是浏览器问题）: {_error_brief(error_msg)}'), flush=True)
        print(color.yellow('常见原因：页面没加载完、课程页没切过去、平台改版；完整堆栈见 error.log'), flush=True)
    except WebDriverException as e:
        if 'ERR_INTERNET_DISCONNECTED' in str(e) or 'ERR_NAME_NOT_RESOLVED' in str(e):
            print(color.red('❌ 网络未连接，请检查网络后重试'),flush=True)
        else:
            error_msg = traceback.format_exc()
            send_error("\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n"+error_msg)
            save_diagnostic_screenshot(_CURRENT_DRIVER, '浏览器会话异常')
            print(color.red(f'❌ 浏览器会话异常: {_error_brief(error_msg)}'), flush=True)
            print(color.yellow('完整堆栈已写入程序目录下的 error.log，可在「报错日志」页查看'), flush=True)
    except PermissionError:
        print(color.red('请关闭该窗口后，再右键点击本程序用管理员权限打开'))
    except Exception as e:
        error_msg = traceback.format_exc()
        send_error("\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n"+error_msg)
        save_diagnostic_screenshot(_CURRENT_DRIVER, '程序异常')
        print(color.red(f'❌ 出错了: {_error_brief(error_msg)}'), flush=True)
        print(color.yellow('完整堆栈已写入程序目录下的 error.log，可在「报错日志」页查看'), flush=True)

if __name__ == '__main__':
    run_main()
