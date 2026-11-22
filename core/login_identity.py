# -*- coding: utf-8 -*-
"""登录身份校验与状态机（2026-11-16 登录串号 P0 修复）。

背景：旧逻辑提交密码后无条件尝试旧 Cookie，且只凭 `driver.title != '用户登录'`
就宣布「自动登录成功」——浏览器里残留的**上一个账号**登录态会让错误密码看起来成功，
于是用户看到「自动登录成功」并进入上一个账号的课程（串号）。

本模块提供：
* LoginState：显式登录状态机，禁止 CREDENTIAL_REJECTED → RESTORE_ATTEMPTED；
* extract_identity()：从 Cookie / 页面多来源提取当前身份（UID、手机号脱敏）；
* verify_identity()：把实际身份与目标账号配置比对，取不到可靠身份即判失败；
* clear_site_cookies()：只清理学习通站点的 Cookie，不动其它站点；
* 安全日志：只打印脱敏标识，绝不输出密码 / 完整 Cookie / token。
"""
from __future__ import annotations

import re
from enum import Enum

# 学习通站点域（只清这些域的 Cookie，避免"误删其他站点无关数据"）
SITE_DOMAIN_MARKS = ('chaoxing.com', 'chaoxing.cn')

# 承载身份的 Cookie 名（按可靠性排序）
UID_COOKIE_NAMES = ('uid', '_uid', 'puid', 'userid', 'userId')
PHONE_COOKIE_NAMES = ('phone', 'mobile', 'userPhone', 'loginPhone')


class LoginState(Enum):
    STARTING = 'STARTING'
    CREDENTIAL_SUBMITTED = 'CREDENTIAL_SUBMITTED'
    CREDENTIAL_AUTHENTICATED = 'CREDENTIAL_AUTHENTICATED'
    CREDENTIAL_REJECTED = 'CREDENTIAL_REJECTED'
    RESTORE_ATTEMPTED = 'RESTORE_ATTEMPTED'
    RESTORE_AUTHENTICATED = 'RESTORE_AUTHENTICATED'
    LOGIN_FAILED = 'LOGIN_FAILED'
    IDENTITY_MISMATCH = 'IDENTITY_MISMATCH'


class LoginStateMachine:
    """登录状态机：严禁从「凭据被拒」跳到「恢复旧会话」。"""

    # 允许的迁移（其余一律拒绝）
    _ALLOWED = {
        LoginState.STARTING: {LoginState.CREDENTIAL_SUBMITTED,
                              LoginState.RESTORE_ATTEMPTED},
        LoginState.CREDENTIAL_SUBMITTED: {LoginState.CREDENTIAL_AUTHENTICATED,
                                          LoginState.CREDENTIAL_REJECTED,
                                          LoginState.LOGIN_FAILED,
                                          LoginState.IDENTITY_MISMATCH},
        LoginState.CREDENTIAL_AUTHENTICATED: {LoginState.IDENTITY_MISMATCH,
                                              LoginState.LOGIN_FAILED},
        LoginState.RESTORE_ATTEMPTED: {LoginState.RESTORE_AUTHENTICATED,
                                       LoginState.LOGIN_FAILED,
                                       LoginState.IDENTITY_MISMATCH},
        LoginState.RESTORE_AUTHENTICATED: {LoginState.IDENTITY_MISMATCH,
                                           LoginState.LOGIN_FAILED},
    }

    def __init__(self, state=LoginState.STARTING):
        self.state = state
        self.history = [state]

    def can(self, target):
        if target == self.state:
            return True
        return target in self._ALLOWED.get(self.state, set())

    def transition(self, target):
        if not self.can(target):
            raise ValueError('非法登录状态迁移：%s -> %s' % (
                self.state.value, target.value))
        self.state = target
        self.history.append(target)
        return target


def mask_phone(phone):
    """手机号脱敏：只留后四位。非 11 位数字统一返回 '****'。"""
    s = str(phone or '').strip()
    if re.fullmatch(r'\d{11}', s):
        return '****' + s[-4:]
    return '****'


def short_home(home):
    """HOME 路径短标识（只取末段），避免完整路径泄漏用户名。"""
    s = str(home or '').replace('\\', '/').rstrip('/')
    return s.rsplit('/', 1)[-1] if s else '-'


def _is_site_cookie(cookie):
    domain = str(cookie.get('domain') or '').lower()
    return any(mark in domain for mark in SITE_DOMAIN_MARKS)


def clear_site_storage(driver) -> bool:
    """清掉当前页面的 localStorage / sessionStorage（Cookie 之外的第二套登录态）。

    2026-11-20 真机：清完 Cookie 仍然登录着上一个账号（目标 *****1520，实际 *****4384）。
    Edge 的持久化配置里还会留 token / 登录缓存，只清 Cookie 是不够的。
    **必须在学习通域下调用**，否则清的是别的站点（或清不到）。
    """
    try:
        return bool(driver.execute_script(
            "var n=0;"
            "try{if(window.localStorage){window.localStorage.clear();n+=1;}}catch(e){}"
            "try{if(window.sessionStorage){window.sessionStorage.clear();n+=1;}}catch(e){}"
            "return n>0;"))
    except Exception:
        return False


def clear_site_cookies(driver):
    """清掉当前 WebDriver 里所有学习通站点 Cookie，不动其它站点。

    返回被清理的 Cookie 名列表（仅名字，用于日志）。
    """
    cleared = []
    try:
        cookies = list(driver.get_cookies() or [])
    except Exception:
        return cleared
    for c in cookies:
        if not _is_site_cookie(c):
            continue
        name = c.get('name')
        try:
            driver.delete_cookie(name)
            cleared.append(name)
        except Exception:
            # 部分浏览器要求 path/domain 精确匹配，用原始 cookie 再删一次
            try:
                driver.delete_cookie(name, path=c.get('path') or '/',
                                     domain=c.get('domain'))
                cleared.append(name)
            except Exception:
                pass
    return cleared


def extract_identity(driver, account_config=None):
    """从当前会话提取身份，返回 {'uid':..., 'phone_tail':..., 'sources':[...]}。

    来源（按可靠性）：Cookie 中的 uid/_uid/puid → Cookie 中的手机号 →
    页面隐藏域 / JS 里的 uid → 页面文本中的手机号后四位。
    任一来源可靠即采信；取不到返回空 uid/phone_tail。
    """
    out = {'uid': '', 'phone_tail': '', 'sources': []}
    cookies = []
    try:
        cookies = list(driver.get_cookies() or [])
    except Exception:
        pass
    for name in UID_COOKIE_NAMES:
        for c in cookies:
            if str(c.get('name') or '').lower() == name.lower():
                v = str(c.get('value') or '').strip()
                if v.isdigit() and len(v) >= 4:
                    out['uid'] = v
                    out['sources'].append('cookie:' + name)
                    break
        if out['uid']:
            break
    for name in PHONE_COOKIE_NAMES:
        for c in cookies:
            if str(c.get('name') or '').lower() == name.lower():
                v = str(c.get('value') or '').strip()
                if re.fullmatch(r'1\d{10}', v):
                    out['phone_tail'] = v[-4:]
                    out['sources'].append('cookie:' + name)
                    break
        if out['phone_tail']:
            break

    # 页面来源：只读 DOM，不点击
    page_src = ''
    try:
        page_src = driver.execute_script(
            "return document.documentElement ? document.documentElement.outerHTML.slice(0,200000) : '';"
        ) or ''
    except Exception:
        pass
    if page_src:
        if not out['uid']:
            m = re.search(r'(?:["\']?uid["\']?\s*[:=]\s*["\']?)(\d{5,15})', page_src, re.I)
            if m:
                out['uid'] = m.group(1)
                out['sources'].append('page:uid')
        if not out['phone_tail']:
            m = re.search(r'(1\d{10})', page_src)
            if m:
                out['phone_tail'] = m.group(1)[-4:]
                out['sources'].append('page:phone')
    return out


def expected_identity(account_config):
    """从目标账号配置里取期望身份：保存的 uid + 手机号后四位。"""
    cfg = account_config or {}
    uid = str(cfg.get('uid') or cfg.get('user_id') or '').strip()
    phone_tail = ''
    phone = str(cfg.get('phone_number') or cfg.get('phone') or '').strip()
    if re.fullmatch(r'1\d{10}', phone):
        phone_tail = phone[-4:]
    elif re.fullmatch(r'\d{4}', phone):
        phone_tail = phone
    return {'uid': uid if uid.isdigit() else '', 'phone_tail': phone_tail}


def verify_identity(driver, account_config=None, require_identity=True):
    """校验当前会话身份是否属于目标账号。

    返回 (ok: bool, reason: str, identity: dict)。
    require_identity=True（默认）：取不到任何可靠身份 → 判失败（不把 title 当成功）。
    """
    actual = extract_identity(driver, account_config)
    expect = expected_identity(account_config)
    # 目标账号什么都没配（既无 uid 也无手机号）时，下面两个比对会全部跳过 →
    # 直接 return True，等于「身份校验」空转，浏览器残留的上一账号会话照样通过
    # （2026-11-19 审计 P0-8）。严格模式下必须拒绝。
    if require_identity and not expect['uid'] and not expect['phone_tail']:
        return False, '目标账号未配置 uid/手机号，无法核对身份（拒绝把残留会话当作登录成功）', actual
    if not actual['uid'] and not actual['phone_tail']:
        if require_identity:
            return False, '无法读取可靠身份标识（拒绝把页面标题当作登录成功）', actual
        return True, '未取得身份标识， caller 选择放行（不推荐）', actual

    # UID 比对：两边都有才比对；不相等即串号
    if actual['uid'] and expect['uid']:
        if actual['uid'] != expect['uid']:
            return False, 'UID 不匹配（实际 %s… / 目标 %s…）' % (
                actual['uid'][:4], expect['uid'][:4]), actual
    # 手机号后四位比对
    if actual['phone_tail'] and expect['phone_tail']:
        if actual['phone_tail'] != expect['phone_tail']:
            return False, '手机号后四位不匹配（实际 ****%s / 目标 ****%s）' % (
                actual['phone_tail'], expect['phone_tail']), actual
    return True, '身份校验通过（来源：%s）' % ','.join(actual['sources']) or '无来源', actual
