# -*- coding: utf-8 -*-
"""
签到核心: 只实现 普通签到 与 位置签到

普通签到: 预签到 -> POST 提交 -> (失败) GET 兜底
位置签到: 坐标来自活动 content(见 activity.py), 为空则用地址地理编码;
          服务器返回 "距教师指定签到地点X米" 时执行爬山修正(step≈11米),
          直到进入可签到范围并成功。
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

from . import constants, activity as activity_mod, geocode, storage
from .client import XXTClient
from .courses import Activity
from .crypto import generate_device_code

# 距离修正参数(与 chaoxing-main 一致)
INIT_STEP = 0.001
MAX_ROUNDS = 10
MAX_WALK = 100
MIN_GAIN = 0.1
MIN_STEP = 1e-8

_DISTANCE_RE = re.compile(r"距[^0-9]{0,12}?([\d.]+(?:[eE][+-]?\d+)?)\s*米")


class _RequestBudget:
    """单次位置签到的统一请求预算（2026-11-16 任务09）。

    MAX_ROUNDS / MAX_WALK 只管「轮数 / 步长」，最坏可达 2000+ 次请求。
    这里叠加独立的全局次数预算 + 总时间预算 + stop_event 取消：
    每次发请求前调用 can_spend()，超预算立即停止搜索。
    """

    def __init__(self, stop_event=None,
                 max_requests=None, time_budget=None):
        self.stop_event = stop_event
        self.max_requests = int(max_requests or constants.LOCATION_MAX_TOTAL_REQUESTS)
        self.time_budget = float(time_budget or constants.LOCATION_TIME_BUDGET)
        self.started = time.time()
        self.spent = 0
        self.reason = ''

    def can_spend(self):
        if self.stop_event is not None and getattr(self.stop_event, 'is_set', lambda: False)():
            self.reason = '用户停止'
            return False
        if self.spent >= self.max_requests:
            self.reason = '请求次数达到上限 %d' % self.max_requests
            return False
        if time.time() - self.started >= self.time_budget:
            self.reason = '总时间预算 %.0f 秒耗尽' % self.time_budget
            return False
        return True

    def spend(self):
        self.spent += 1


class SignResult:
    def __init__(self, success: bool, message: str = "", raw: str = "",
                 distance: Optional[float] = None, already: bool = False,
                 need_captcha: bool = False, need_face: bool = False,
                 need_photo: bool = False, auth_error: bool = False,
                 locked: bool = False, ended: bool = False,
                 not_in_class: bool = False):
        self.success = success
        self.message = message
        self.raw = raw
        self.distance = distance
        self.already = already
        self.need_captcha = need_captcha
        self.need_face = need_face
        self.need_photo = need_photo
        self.auth_error = auth_error
        self.locked = locked
        self.ended = ended
        self.not_in_class = not_in_class

    def is_ok(self) -> bool:
        """成功或"无需再管"的结果"""
        return (self.success or self.already or self.ended
                or self.not_in_class)

    @property
    def text(self) -> str:
        if self.success:
            return "签到成功"
        if self.already:
            return "已签到过了"
        return self.message or "签到失败"

    @staticmethod
    def parse(body: str) -> "SignResult":
        b = (body or "").strip()
        # 否定词先行：旧实现 `"成功" in b` 排在所有失败判定之前，
        # 「签到已结束，本次未成功」「操作不成功」都会被判成功并永久 mark_signed，
        # 用户从此不再重试 → 直接错过签到（2026-11-19 审计 P0-10）。
        negative = ("未成功", "不成功", "失败", "未签到", "error", "Error", "ERROR")
        if b == "success" or (("签到成功" in b or "成功" in b)
                              and not any(word in b for word in negative)):
            return SignResult(True, "签到成功", b)
        if "已签到" in b:
            return SignResult(False, "已签到过了", b, already=True)
        m = _DISTANCE_RE.search(b)
        if m:
            try:
                dist = float(m.group(1))
            except Exception:
                dist = None
            return SignResult(False, b, b, distance=dist)
        if "locationAuthError" in b:
            return SignResult(False, "位置鉴权失败(%s)" % b, b, auth_error=True)
        if "锁定" in b or "次数过多" in b:
            return SignResult(False, "服务器已锁定该签到, 请约 1 小时后再试", b, locked=True)
        if "已截止" in b or "下次早点" in b or "结束" in b:
            return SignResult(False, "签到已截止", b, ended=True)
        if "校验失败" in b or "未查询到活动数据" in b:
            return SignResult(False, "不在本班, 跳过", b, not_in_class=True)
        if b.startswith("validate_"):
            token = b[len("validate_"):]
            if token[:1].isdigit():
                return SignResult(False, "需要滑块验证码", b, need_captcha=True)
            return SignResult(False, "需要拍照验证", b, need_photo=True)
        if b.startswith("checkFace_"):
            return SignResult(False, "需要人脸识别", b, need_face=True)
        if "errorLocation" in b:
            return SignResult(False, "位置不在范围", b)
        return SignResult(False, b or "空响应", b)


# ===========================================================================
# 底层请求
# ===========================================================================
def pre_sign(client: XXTClient, act: Activity) -> None:
    """预签到: newsign/preSign + analysis/analysis2 (失败静默)"""
    try:
        url = ("%s?courseId=%s&classId=%s&activePrimaryId=%s&general=1&sys=1&ls=1"
               "&appType=15&uid=%s&ut=s"
               % (constants.PRE_SIGN, act.course.course_id, act.course.class_id,
                  act.aid, client.uid))
        client.get(url, ua="ios")
    except Exception:
        pass
    try:
        body = client.get_text("%s?DB_STRATEGY=RANDOM&aid=%s&vs=1"
                               % (constants.ANALYSIS, act.aid), ua="ios")
        m = re.search(r"code='\+'([^']*)'", body) or re.search(r"code='([^']*)'", body)
        if m and m.group(1):
            client.get("%s?DB_STRATEGY=RANDOM&code=%s"
                       % (constants.ANALYSIS2, m.group(1)), ua="ios")
    except Exception:
        pass


def sign_post(client: XXTClient, act: Activity, latitude="-1", longitude="-1",
              address="") -> str:
    data = {
        "activeId": act.aid, "uid": client.uid, "fid": client.fid,
        "name": client.name, "clientip": "", "appType": "15", "ifTiJiao": "1",
        "vpProbability": "-1", "vpStrategy": "",
        "deviceCode": generate_device_code(),
        "latitude": str(latitude), "longitude": str(longitude),
    }
    if address:
        data["address"] = address
    return client.post_text(constants.SIGN_AJAX, data, ua="ios")


def sign_get(client: XXTClient, act: Activity, latitude="-1", longitude="-1",
             address="") -> str:
    params = {
        "clientip": "", "appType": "15", "ifTiJiao": "1",
        "vpProbability": "-1", "vpStrategy": "",
        "activeId": act.aid, "uid": client.uid,
        "latitude": str(latitude), "longitude": str(longitude),
        "fid": client.fid, "deviceCode": generate_device_code(),
        "name": client.name,
    }
    if address:
        params["address"] = address
    return client.get_text(constants.SIGN_AJAX, ua="ios", params=params)


def submit_once(client: XXTClient, act: Activity, latitude="-1", longitude="-1",
                address="") -> SignResult:
    """POST 优先, GET 兜底, 返回信息量最大的响应"""
    res = SignResult.parse(sign_post(client, act, latitude, longitude, address))
    if res.success or res.already or res.distance is not None or res.need_captcha \
            or res.need_face or res.need_photo or res.auth_error or res.locked \
            or res.ended or res.not_in_class:
        return res
    res2 = SignResult.parse(sign_get(client, act, latitude, longitude, address))
    if res2.success or res2.already or res2.distance is not None or res2.need_captcha \
            or res2.need_face or res2.need_photo or res2.auth_error or res2.locked \
            or res2.ended or res2.not_in_class:
        return res2
    return res if "errorLocation" in res.raw else res2


# ===========================================================================
# 普通签到
# ===========================================================================
def normal_sign(client: XXTClient, act: Activity) -> SignResult:
    res = SignResult.parse(sign_post(client, act))
    if res.is_ok() or res.distance is not None:
        return res
    res2 = SignResult.parse(sign_get(client, act))
    if res2.is_ok() or res2.distance is not None:
        return res2
    return res if res.raw else res2


# ===========================================================================
# 位置签到 (content 坐标优先 + 地址地理编码 + 距离爬山修正)
# ===========================================================================
def _resolve_candidates(client: XXTClient, act: Activity,
                        content: Dict) -> List[Tuple[float, float, str]]:
    cands: List[Tuple[float, float, str]] = []
    address = activity_mod.content_address(content)

    # 0) 上次成功签到用的坐标(同一教师地址)优先, 可省掉坐标爬山
    cached = storage.load_cached_coord(address)
    if cached:
        try:
            cands.append((float(cached["lat"]), float(cached["lon"]),
                          "缓存(上次成功坐标)"))
        except Exception:
            pass

    # 1) 活动 content 里的坐标
    cands.extend(activity_mod.coordinate_candidates(content))

    # 2) content 没有坐标 -> 用地址地理编码
    if not cands and address:
        lat, lon = geocode.geocode(address)
        if lat is not None and lon is not None:
            cands.append((lat, lon, "geocode(地址→高德)"))
    if not cands:
        storage.log("  位置签到: content 无坐标、地址也无法解析, 跳过",sign_text=sign_txt)
    return cands


def location_sign(client: XXTClient, act: Activity,
                  info: Optional[Dict] = None, stop_event=None,
                  max_requests=None, time_budget=None) -> SignResult:
    """位置签到（任务09：统一请求预算 + 用户取消 + 总时间预算）。

    stop_event：threading.Event，set 后立即停止自动定位；
    max_requests/time_budget：覆盖默认预算（便于测试与特殊场景）。
    """
    budget = _RequestBudget(stop_event, max_requests, time_budget)

    def submit_budgeted(la, lo):
        """发请求前统一检查预算；超限返回带 budget 原因的失败结果。"""
        if not budget.can_spend():
            r = SignResult(False, "位置修正停止：%s" % budget.reason)
            r.budget_exhausted = True
            return r
        budget.spend()
        return submit_once(client, act, la, lo, address)

    info = info if info is not None else activity_mod.fetch_activity_info(client, act.aid)
    content = activity_mod.activity_content(client, act.aid, cached=info)
    address = activity_mod.content_address(content)
    candidates = _resolve_candidates(client, act, content)
    if not candidates:
        return SignResult(False, "活动无可用坐标/地址, 无法定位")

    # ---- 阶段1: 每个候选起点试一次, 取距离最小的 ----
    best: Optional[float] = None
    chosen: Optional[Tuple[float, float, str]] = None
    last: Optional[SignResult] = None
    auth_errors = 0
    for lat, lon, label in candidates:
        time.sleep(constants.REQUEST_INTERVAL)
        res = submit_budgeted(lat, lon)
        if getattr(res, 'budget_exhausted', False):
            return res
        last = res
        if res.is_ok():
            if res.success or res.already:     # 已进入有效范围, 记住这个位置
                storage.save_cached_coord(address, lat, lon)
            storage.log("  位置签到命中: %s (起点: %s)" % (res.text, label),sign_text=sign_txt)
            return res
        if res.need_captcha or res.need_face or res.need_photo or res.locked:
            return res
        if res.auth_error:
            auth_errors += 1
            continue
        if res.distance is not None:
            if best is None or res.distance < best:
                best, chosen = res.distance, (lat, lon, label)

    if chosen is None:
        if auth_errors:
            return SignResult(False, "位置鉴权失败(locationAuthError), "
                                     "可能需要在 App 内打开该签到并允许定位")
        return last or SignResult(False, "定位失败")

    lat, lon, label = chosen
    storage.log("  位置签到: 起点 %s, 距教师位置 %s 米, 开始自动修正..." % (label, best),sign_text=sign_txt)

    # ---- 阶段2: 爬山修正 ----
    step = INIT_STEP

    def probe(la, lo):
        if not budget.can_spend():
            # 预算耗尽/用户停止：返回 (None, 失败结果) 让调用点终止循环
            r = SignResult(False, "位置修正停止：%s" % budget.reason)
            r.budget_exhausted = True
            return None, r
        time.sleep(constants.REQUEST_INTERVAL)
        r = submit_budgeted(la, lo)
        if getattr(r, 'budget_exhausted', False):
            return None, r
        if r.is_ok():
            if r.success or r.already:
                storage.save_cached_coord(address, la, lo)
            return r.distance, r
        # 遇到验证码/人脸/拍照/锁定：停止自动操作，交还人工处理
        if r.need_captcha or r.need_face or r.need_photo or r.locked or r.auth_error:
            return r.distance, r
        return r.distance, None

    for _round in range(MAX_ROUNDS):
        # 每轮开始检查总预算（任务09）：确保任何路径耗尽都能透传停止原因
        if not budget.can_spend():
            r = SignResult(False, "位置修正停止：%s" % budget.reason)
            r.budget_exhausted = True
            return r
        improved = False
        for axis in ("lat", "lon"):
            dirs = {}
            for sign in (1, -1):
                la, lo = ((lat + sign * step, lon) if axis == "lat"
                          else (lat, lon + sign * step))
                d, done = probe(la, lo)
                if done is not None:
                    storage.log("  位置签到结果: %s" % done.text,sign_text=sign_txt)
                    return done
                dirs[sign] = d

            direction = 0
            plus, minus = dirs.get(1), dirs.get(-1)
            if plus is not None and minus is not None:
                if best is not None and plus < best and plus < minus:
                    direction = 1
                elif best is not None and minus < best:
                    direction = -1
            elif plus is not None and (best is None or plus < best):
                direction = 1
            elif minus is not None and (best is None or minus < best):
                direction = -1

            if direction != 0:
                for _ in range(MAX_WALK):
                    la, lo = ((lat + direction * step, lon) if axis == "lat"
                              else (lat, lon + direction * step))
                    d, done = probe(la, lo)
                    if done is not None:
                        storage.log("  位置签到结果: %s" % done.text,sign_text=sign_txt)
                        return done
                    if d is None or (best is not None and d >= best - MIN_GAIN):
                        break
                    lat, lon, best = la, lo, d
                    improved = True

        if not improved:
            step *= 0.3
            if step < MIN_STEP:
                break
        else:
            storage.log("  位置签到: 第 %d 轮修正后距离 %s 米" % (_round + 1, best),sign_text=sign_txt)

    return SignResult(False, "位置修正后仍未成功(最佳距离 %s 米)" % best,
                      raw=(last.raw if last else ""))

sign_txt =None
# ===========================================================================
# 分发
# ===========================================================================
def sign_activity(client: XXTClient, act: Activity, sign_text,
                  stop_event=None) -> SignResult:
    global sign_txt
    sign_txt = sign_text
    storage.log("发现签到: [%s] %s (%s) aid=%s"
                % (act.sign_type_text, act.name, act.course.title, act.aid),sign_text=sign_text)
    pre_sign(client, act)
    if act.other_id == constants.SIGN_LOCATION:
        return location_sign(client, act, stop_event=stop_event)
    return normal_sign(client, act)
