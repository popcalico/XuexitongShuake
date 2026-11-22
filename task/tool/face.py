# -*- coding: utf-8 -*-

import hashlib
import os
import random
import time
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote, quote
from selenium.webdriver.support import expected_conditions as EC

import numpy as np
import requests
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait

from task.tool import color
import pickle
import json
import traceback
from task.tool.send_wx import send_error
from core import paths

def check_face(driver,face_url,face_class='popDiv wid640 faceCollectQrPop popClass',course_name='',max_wait=200):
    """等待人脸认证弹窗消失并保存会话；返回是否确实保存成功。

    V5.1：**保存失败不得伪装成成功**（原实现忽略 get_cookie 返回值）。
    max_wait 可配置，循环内检查账号停止事件，不再无条件空等 200 秒。
    """
    from core import task_control as tc
    i = 0
    limit = max(0, int(max_wait or 0))
    while i <= limit:
        if tc.is_stopped():
            print(color.yellow('人脸认证等待已被用户停止'), flush=True)
            return False
        try:
            driver.find_element(By.CSS_SELECTOR, f"[class='{face_class}']")
            if i == 0:
                print(color.red('已检测人脸认证弹窗'),flush=True)
                if auto_login_with_cookies(driver,file_name='face_cookies',url=face_url,face_class=face_class):
                    return True
            if i == 0 :
                print(color.red('请按照要求手机扫码进行人脸认证,如有误判将在%s秒后默认您已经完成认证' % limit), flush=True)
            time.sleep(1)
            i += 1
        except Exception:
            if i != 0:
                print(color.red('人脸认证已完成'), flush=True)
                if not get_cookie(driver,file_name='face_cookies',course_name=course_name):
                    print(color.red('人脸会话保存失败，本次不标记为已完成（需重新认证）'), flush=True)
                    return False
                return True
            break
    print(color.yellow('人脸认证等待超时（%d 秒），未标记完成' % limit), flush=True)
    return False


def _save_face_url(course_name, url):
    """人脸页 URL 映射：原子写 + 回读校验，失败返回 False（不覆盖已有有效文件）。"""
    try:
        try:
            with open(paths.data_or_asset('tool', 'face_url.json'), 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[course_name] = url
        _path = paths.data('tool', 'face_url.json')
        _tmp = _path + '.tmp'
        os.makedirs(os.path.dirname(_path) or '.', exist_ok=True)
        with open(_tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        with open(_tmp, 'r', encoding='utf-8') as f:
            back = json.load(f)
        if back.get(course_name) != url:
            raise ValueError('人脸URL回读校验不通过')
        os.replace(_tmp, _path)
        return True
    except Exception as e:
        print(color.red('[人脸会话] 人脸页 URL 保存失败（%s），已有文件保持不变'
                        % type(e).__name__), flush=True)
        return False


def get_cookie(driver,file_name='cookies',course_name=''):
    """把当前账号的站点 Cookie 写入当前账号数据目录。

    V5.1 P0（原缺陷：通用异常分支末尾仍返回成功值，保存失败伪装成成功）：
      1. `driver.get_cookies()` 失败或返回空 → 返回 False，绝不覆盖已有文件；
      2. 写 .tmp → 回读校验条目数 → `os.replace` 原子替换；任一失败返回 False；
      3. face_cookies 额外写 face_url.json，同样原子写，失败即整体返回 False；
      4. 所有异常分支返回 False；日志只输出项数与名称，绝不输出 Cookie 值。
    调用者必须检查返回值（main.login_study / check_face 已检查）。
    """
    tag = '人脸会话' if file_name == 'face_cookies' else '登录态'
    # 1) 取得 Cookie
    try:
        cookies = driver.get_cookies()
    except Exception as e:
        print(color.red('[%s] 读取浏览器 Cookie 失败（%s），未写入任何文件'
                        % (tag, type(e).__name__)), flush=True)
        return False
    if not isinstance(cookies, list) or not cookies:
        print(color.red('[%s] 浏览器返回 0 项有效 Cookie，拒绝覆盖已有文件' % tag), flush=True)
        return False
    # 2) 原子写入 + 回读校验
    try:
        import json as _json
        _json_path = paths.data('tool', f'{file_name}.json')
        _tmp = _json_path + '.tmp'
        os.makedirs(os.path.dirname(_json_path) or '.', exist_ok=True)
        with open(_tmp, 'w', encoding='utf-8') as _f:
            _json.dump({'value': cookies, 'data': cookies, '_fmt': 1}, _f, ensure_ascii=False)
        with open(_tmp, 'r', encoding='utf-8') as _f:
            _back = _json.load(_f)
        if (not isinstance(_back.get('value'), list)
                or len(_back['value']) != len(cookies)):
            raise ValueError('临时文件回读校验不通过')
        os.replace(_tmp, _json_path)
    except PermissionError:
        print(color.red('请在关闭该窗口后，再右键点击本程序用管理员权限打开'), flush=True)
        return False
    except Exception as e:
        print(color.red('[%s] Cookie 写入/校验失败（%s），已有文件保持不变'
                        % (tag, type(e).__name__)), flush=True)
        return False
    # 3) 附加的人脸页 URL
    if file_name == 'face_cookies':
        try:
            current_url = driver.current_url
        except Exception as e:
            print(color.red('[人脸会话] 读取当前 URL 失败（%s）' % type(e).__name__), flush=True)
            return False
        if not _save_face_url(course_name, current_url):
            return False
    print(color.green('[%s] 已保存 %d 项 Cookie（日志不含 Cookie 值）'
                      % (tag, len(cookies))), flush=True)
    return True


# 关键认证 Cookie（名称小写）：这些注入失败即判定恢复失败，绝不静默继续。
CRITICAL_COOKIE_NAMES = frozenset(
    ('_uid', 'uid', 'uf', 'vc3', '_d', 'lv', 'fid', 'jrose', 'sessionid', 'puid'))


def _classify_cookie_error(exc):
    """Cookie 注入失败分类诊断（只返回类别，不含 Cookie 值）。

    同时看异常类型名与异常文本：不同 Selenium 版本/浏览器驱动抛出的类型名
    不完全一致，单独依赖类型名会把「域名不匹配」误记成泛化错误。
    """
    name = type(exc).__name__
    text = str(exc).lower()
    if 'domain' in name.lower() or 'domain' in text:
        return '域名不匹配'
    if ('invalidargument' in name.lower() or 'unabletosetcookie' in name.lower()
            or 'cookie' in text and 'invalid' in text):
        return '格式不兼容'
    if 'timeout' in name.lower() or 'timeout' in text:
        return '超时'
    if 'session' in name.lower() or 'webdriver' in name.lower():
        return 'WebDriver失效'
    return name[:24]

def auto_login_with_cookies(driver,file_name='cookies', url='https://i.chaoxing.com',face_class='popDiv wid640 faceCollectQrPop popClass', account_config=None):
    """用**当前账号自己的**保存会话恢复登录（2026-11-16 P0 串号修复）。

    关键变化（不要改回去）：
      1. 注入前先清理学习通站点 Cookie —— 旧代码把 `delete_all_cookies()` 注释掉，
         导致上一个账号的认证 Cookie 与新 Cookie 叠加，错误密码也能"看起来登录成功"；
      2. 不再用 `driver.title != '用户登录'` 当成功判据，必须做身份校验；
      3. 身份不匹配或无法验证时清理本次注入的站点 Cookie 并返回 False。
    account_config：目标账号配置（用于 uid / 手机号后四位比对）；不传则从当前 HOME 读取。
    """
    from core import login_identity as li
    state = li.LoginStateMachine()
    if account_config is None:
        # 只读当前进程自己的 account_info.json —— 绝不用 ConfigManager().load_account()，
        # 它读的是主进程的 current_account，多账号并发时会拿到别的账号（2026-11-19 串号）
        try:
            import json as _json
            with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as _f:
                account_config = _json.load(_f) or {}
        except Exception:
            account_config = {}

    # 已在登录态也不能直接相信：先验证身份归属
    if '登录' not in driver.title and file_name == 'cookies':
        ok, _reason, _id = li.verify_identity(driver, account_config)
        if ok:
            return True
        print(color.yellow('当前会话身份与目标账号不符，清理站点 Cookie 后重新恢复'),
              flush=True)
        li.clear_site_cookies(driver)

    state.transition(li.LoginState.RESTORE_ATTEMPTED)
    try:
        # 清除可能存在的旧Cookie（只清学习通站点，不动其它站点）
        cleared = li.clear_site_cookies(driver)
        if cleared:
            print(color.yellow('已清理站点旧 Cookie：%d 项（避免跨账号串号）' % len(cleared)),
                  flush=True)
        try:
            # 任务06：优先读 JSON；旧 pkl 走 RestrictedUnpickler 并迁移
            from core import safe_pickle
            cookies, _ = safe_pickle.load_json_or_pickle(
                paths.data('tool', f'{file_name}.json'),
                paths.data('tool', f'{file_name}.pkl'),
                validator=lambda v: isinstance(v, list))
            if not isinstance(cookies, list):
                return False
        except Exception:
            return False
        print(color.green('正在尝试恢复当前账号保存的登录态。。。'),flush=True)
        # V5.1 第26节：逐个注入并统计成功/失败，失败绝不静默吞掉。
        # 日志只记录 Cookie 名称与失败类别，绝不记录 Cookie 值。
        total = len(cookies)
        injected = 0
        failures = []
        for cookie in cookies:
            name = str(cookie.get('name', '?')) if isinstance(cookie, dict) else '?'
            try:
                cookie_to_add = dict(cookie)
                # Selenium 的 add_cookie 不支持 sameSite 参数
                cookie_to_add.pop('sameSite', None)
                if not str(cookie_to_add.get('domain', '')):
                    failures.append((name, '缺少domain'))
                    continue
                # 分类诊断：过期 Cookie 仍尝试注入（部分平台用会话级 expiry），
                # 但被浏览器拒绝时会计入失败并参与关键 Cookie 判定。
                expiry = cookie_to_add.get('expiry')
                if expiry is not None:
                    try:
                        if float(expiry) <= time.time():
                            failures.append((name, '已过期'))
                    except (ValueError, TypeError):
                        failures.append((name, 'expiry格式不兼容'))
                driver.add_cookie(cookie_to_add)
                injected += 1
            except Exception as e:
                failures.append((name, _classify_cookie_error(e)))
        print(color.green('Cookie 注入统计：共 %d 项，成功 %d 项，失败 %d 项'
                          % (total, injected, len(failures))), flush=True)
        if failures:
            brief = '，'.join('%s(%s)' % (n, r) for n, r in failures[:8])
            print(color.yellow('注入失败明细（仅名称/类别）：%s%s'
                               % (brief, ' …' if len(failures) > 8 else '')), flush=True)
        critical = sorted({n for n, _ in failures
                           if n.lower() in CRITICAL_COOKIE_NAMES})
        if injected == 0:
            print(color.red('Cookie 注入 0 项成功，判定恢复失败（不进入课程）'), flush=True)
            li.clear_site_cookies(driver)
            return False
        if critical:
            print(color.red('关键认证 Cookie 注入失败（%s），判定恢复失败'
                            % '、'.join(critical)), flush=True)
            li.clear_site_cookies(driver)
            return False

        # 刷新页面使 Cookie 生效（导航带阶段计时与超时诊断）
        from core import navigation as nav
        ok_refresh, _status, _detail = nav.refresh(driver, 'Cookie恢复刷新')
        if not ok_refresh:
            print(color.red('Cookie 恢复刷新失败，判定恢复失败'), flush=True)
            li.clear_site_cookies(driver)
            return False

        # 保留 3 秒：平台刷新后的会话初始化/重定向缓冲，属于必要的动画缓冲，
        # 不是任务识别等待；显式打印理由，避免被当作无法解释的静默等待。
        print(color.yellow('[等待] 会话初始化缓冲 3 秒（平台刷新后重定向）'), flush=True)
        time.sleep(3)
        #  访问需要登录的页面测试
        test_url = url  # 个人中心页面
        if test_url:
            ok_nav, nav_status, nav_detail = nav.navigate(
                driver, test_url, name='登录态验证页')
            if not ok_nav:
                print(color.red('登录态验证页导航失败（%s），判定恢复失败 · %s'
                                % (nav_status, nav_detail)), flush=True)
                li.clear_site_cookies(driver)
                return False
        # 验证登录是否成功
        if file_name=='face_cookies':
            try:
                driver.find_element(By.CSS_SELECTOR, f"[class='{face_class}']")
                print(color.red('跳过人脸认证失败'), flush=True)
                return False
            except:
                print(color.green('成功跳过人脸认证'),flush=True)
                return True

        # P0 串号修复：不能再只看 title != '用户登录'（旧账号登录态会伪装成功）
        if '登录' in (driver.title or ''):
            print(color.red('保存的登录态已过期或无效'), flush=True)
            return False
        ok, reason, identity = li.verify_identity(driver, account_config,
                                                  require_identity=True)
        if not ok:
            print(color.red('身份校验失败（%s），停止登录，绝不进入课程' % reason),
                  flush=True)
            li.clear_site_cookies(driver)   # 清理本次注入，避免污染下一次
            state.transition(li.LoginState.IDENTITY_MISMATCH)
            return False
        print(color.green('登录态恢复成功：%s' % reason), flush=True)
        state.transition(li.LoginState.RESTORE_AUTHENTICATED)
        return True

    except Exception as e:
        print(color.red(f"自动登录过程中出错"),flush=True)
        error_msg = traceback.format_exc()
        send_error(
            "\n作者只解决打赏用户提交的问题，请在赞助后将截图与报错信息一同发送至作者邮箱2022865286@qq.com,未赞助的用户请自行查看用户须知文件自行解决\n" + error_msg)
        return False

class ChaoxingFaceHelper:
    """超星人脸识别自动处理"""

    # ============ 常量 ============
    DES_KEY = "Z(AfY@XS"                      # inf_enc 签名盐
    FACE_ENC_SALT = "uWwjeEKsri"              # enc = MD5(puid + salt)
    FACE_API_TOKEN = "4faa8662c59590c6f43ae9fe5b002b42"

    USER_AGENT = (
        "Mozilla/5.0 (Linux; Android 12; SM-N9006 Build/V417IR; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/101.0.4951.61 Safari/537.36 "
        "(schild:a6aba57d51661f412e8b8d2177adf8b4) (device:SM-N9006) "
        "Language/zh_CN com.chaoxing.mobile/ChaoXingStudy_3_6.4.5_android_phone_10831_263 "
        "(@Kalimdor)_6e98c17c08204755905e1537b1933e72"
    )

    MAX_RETRIES = 10      # 最大重试次数
    RETRY_DELAY = 5       # 重试间隔（秒）

    def __init__(self, driver, verbose=True):
        self.driver = driver
        self.verbose = verbose
        self.http = requests.Session()
        self.last_qr_url = None
        self.is_processing = False

    # ---------------------------------------------------------------- 日志
    def log(self, msg, level="INFO"):
        # if self.verbose or level in ("ERROR", "WARN"):
        #     print(f"[超星人脸助手][{level}] {msg}")
        pass
    # ------------------------------------------------------- Cookie 同步
    def _cookie_header(self) -> str:
        """把浏览器 Cookie 拼成 Cookie 请求头"""
        return "; ".join(
            f"{c['name']}={c['value']}" for c in self.driver.get_cookies()
        )

    def _headers(self, extra: dict = None) -> dict:
        h = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": self.driver.current_url,
            "Cookie": self._cookie_header(),
        }
        if extra:
            h.update(extra)
        return h

    def get_uid(self):
        """从浏览器 Cookie 中取出 _uid"""
        for c in self.driver.get_cookies():
            if c["name"] == "_uid":
                return c["value"]
        return None

    # -------------------------------------------------------------- 签名
    @classmethod
    def add_inf_enc_sign(cls, params: dict) -> dict:
        """
        复刻 JS：
            query  = k1=v1&k2=v2...&DESKey=Z(AfY@XS
            inf_enc = MD5(query)
        """
        parts = [
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}"
            for k, v in params.items()
            if v is not None
        ]
        query = "&".join(parts) + f"&DESKey={cls.DES_KEY}"
        signed = dict(params)
        signed["inf_enc"] = hashlib.md5(query.encode("utf-8")).hexdigest()
        return signed

    # ---------------------------------------------------------- 参数提取
    def _extract_qr_url(self):
        """读取 #fcqrimg 的 src"""
        return self.driver.execute_script(
            "var el = document.getElementById('fcqrimg');"
            "return (el && el.src) ? el.src : null;"
        )

    @staticmethod
    def _parse_url_params(url: str) -> dict:
        """解析 URL 参数；若含 content 参数则再解析其嵌套 URL"""
        params = {}
        try:
            parsed = urlparse(url)
            for k, v in parse_qs(parsed.query).items():
                params[k] = v[0]
        except Exception as e:
            print(f"[解析URL失败] {e}",flush=True)
            return params

        content = params.get("content")
        if content:
            try:
                decoded = unquote(content)
                inner = parse_qs(urlparse(decoded).query)
                for k, v in inner.items():
                    params[k] = v[0]
                # 特别处理：content 里的 enc 视作 qrcEnc
                if "enc" in inner:
                    params["qrcEnc"] = inner["enc"][0]
            except Exception as e:
                print(f"[解析content失败] {e}",flush=True)
        return params

    def _extract_hidden_fields(self) -> dict:
        """抓取页面上所有有 id 的 hidden input"""
        return self.driver.execute_script("""
            var fields = {};
            document.querySelectorAll('input[type="hidden"]').forEach(function (el) {
                if (el.id && el.value) { fields[el.id] = el.value; }
            });
            return fields;
        """) or {}

    def get_all_params(self, force=True):
        """
        汇总所有必要参数：
          courseid / clazzid / cpi / knowledgeid / uuid / qrcEnc ...
        优先级：QR 码 URL 参数 > content 内层参数 > 隐藏字段 > 当前页面 URL
        """
        qr_url = self._extract_qr_url()
        if not qr_url:
            self.log("未找到 #fcqrimg 元素，可能人脸弹窗还没出现", "WARN")
            return None

        if not force and qr_url == self.last_qr_url:
            self.log("QR 码 URL 未变化（仍返回参数）")
        self.last_qr_url = qr_url
        self.log(f"QR URL: {qr_url}")

        qr_params = self._parse_url_params(qr_url)
        hidden = self._extract_hidden_fields()

        cur = {}
        try:
            p = urlparse(self.driver.current_url)
            for k, v in parse_qs(p.query).items():
                cur[k] = v[0]
        except Exception:
            pass

        # UUID 来源优先级：QR 参数 > 隐藏字段 > 当前 URL
        uuid = qr_params.get("uuid") or hidden.get("uuid") or cur.get("uuid")
        uuid_src = ("QR" if qr_params.get("uuid")
                    else "hidden" if hidden.get("uuid") else "url")
        self.log(f"UUID 来源: {uuid_src}, 值: {uuid}")

        all_params = {}
        all_params.update(qr_params)
        all_params.update(hidden)
        all_params["courseid"] = (qr_params.get("courseid")
                                  or hidden.get("curCourseId")
                                  or cur.get("courseid"))
        all_params["clazzid"] = (qr_params.get("clazzid")
                                 or hidden.get("curClazzId")
                                 or cur.get("clazzid"))
        all_params["cpi"] = (qr_params.get("cpi")
                             or hidden.get("curCpi")
                             or cur.get("cpi"))
        all_params["knowledgeid"] = (qr_params.get("knowledgeid")
                                     or hidden.get("curChapterId")
                                     or cur.get("knowledgeid"))
        all_params["uuid"] = uuid
        all_params["qrcEnc"] = qr_params.get("qrcEnc") or qr_params.get("enc") or ""

        self.log(f"参数: courseid={all_params.get('courseid')} "
                 f"clazzid={all_params.get('clazzid')} "
                 f"uuid={all_params.get('uuid')} "
                 f"qrcEnc={all_params.get('qrcEnc')}")
        return all_params

    # ---------------------------------------------------------- 图片处理
    def process_image(self, img_bytes: bytes) -> bytes:
        """
        复刻 JS 中的扰动逻辑：
          1. 随机像素 RGB 各通道 ±10 扰动（最多 200 个点）
          2. 随机黑白噪点（最多 50 个）
          3. 整体亮度 ±5 微调
        """
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        arr = np.array(img, dtype=np.int32)
        h, w = arr.shape[:2]

        # 1) 随机像素扰动
        perturbation_count = int(min(200, arr.size / 50))
        for _ in range(perturbation_count):
            y = random.randrange(h)
            x = random.randrange(w)
            for c in range(3):
                delta = random.randint(-10, 10)
                arr[y, x, c] = max(0, min(255, arr[y, x, c] + delta))

        # 2) 黑白噪点
        noise_count = int(min(50, w * h / 1000))
        for _ in range(noise_count):
            y = random.randrange(h)
            x = random.randrange(w)
            b = 255 if random.random() > 0.5 else 0
            arr[y, x, 0] = arr[y, x, 1] = arr[y, x, 2] = b

        # 3) 整体亮度微调
        bright = random.randint(-5, 5)
        arr = np.clip(arr + bright, 0, 255).astype(np.uint8)

        out = Image.fromarray(arr, "RGB")
        buf = BytesIO()
        out.save(buf, format="JPEG", quality=80)
        self.log("图片扰动处理完成")
        return buf.getvalue()

    # ------------------------------------------------------- 请求各接口
    def get_user_token(self) -> str:
        """获取上传用 _token"""
        url = "https://pan-yz.chaoxing.com/api/token/uservalid"
        r = self.http.get(url, headers=self._headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        token = data.get("_token")
        if not token:
            raise RuntimeError(f"获取 token 失败: {data}")
        self.log(f"获取上传 token 成功: {token[:16]}...")
        return token

    def get_user_face_image(self, puid: str) -> bytes:
        """获取本人已注册的人脸图片二进制数据"""
        url = "https://passport2-api.chaoxing.com/api/getUserFaceid"
        enc = hashlib.md5((puid + self.FACE_ENC_SALT).encode("utf-8")).hexdigest()
        params = {
            "enc": enc,
            "token": self.FACE_API_TOKEN,
            "_time": int(time.time() * 1000),
        }
        signed = self.add_inf_enc_sign(params)

        r = self.http.get(url, params=signed, headers=self._headers(), timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("result") != 1:
            raise RuntimeError(f"获取人脸图片 URL 失败: {data}")

        http_url = (data.get("data") or {}).get("http")
        if not http_url:
            raise RuntimeError(f"响应中未包含人脸图片 URL: {data}")

        self.log(f"人脸图片 URL: {http_url}")
        r2 = self.http.get(http_url, headers=self._headers(), timeout=20)
        r2.raise_for_status()
        self.log(f"获取人脸图片成功，{len(r2.content)} 字节")
        return r2.content

    def upload_face_image(self, img_bytes: bytes, puid: str,
                          upload_token: str) -> str:
        """上传处理后的图片，返回 objectId"""
        url = (f"https://pan-yz.chaoxing.com/upload"
               f"?uploadtype=face&_token={upload_token}")
        files = {"file": (f"{puid}.jpg", img_bytes, "image/jpeg")}
        data = {"puid": puid}

        r = self.http.post(url, files=files, data=data,
                           headers=self._headers(), timeout=30)
        r.raise_for_status()
        j = r.json()
        obj_id = j.get("objectId")
        if not obj_id:
            raise RuntimeError(f"上传失败: {j}")
        self.log(f"图片上传成功 objectId={obj_id}")
        return obj_id

    # ------------------------------------------------------- 提交识别
    def _do_submit(self, params: dict, object_id: str) -> dict:
        """单次提交，返回 {'status': success|retry|fatal}"""
        url = ("https://mooc1-api.chaoxing.com/mooc-ans/qr/updateqrstatus"
               f"?uuid2={params.get('uuid', '')}"
               f"&clazzId2={params.get('clazzid', '')}")

        fields = [
            ("clazzId",               params.get("clazzid") or ""),
            ("courseId",              params.get("courseid") or ""),
            ("uuid",                  params.get("uuid") or ""),
            ("qrcEnc",                params.get("qrcEnc") or ""),
            ("cpi",                   params.get("cpi") or ""),
            ("liveDetectionStatus",   "1"),
            ("objectId",              object_id),
            ("knowledgeid",           params.get("knowledgeid") or "0"),
            ("signt",                 params.get("signt") or ""),
            ("signk",                 params.get("signk") or ""),
            ("cxtime",                params.get("cxtime") or ""),
            ("cxcid",                 params.get("cxcid") or ""),
            ("videojobid",            params.get("videojobid") or ""),
            ("videoCollectTime",      params.get("realCollectTime") or "0"),
            ("chaptervideoobjectid",  params.get("chaptervideoobjectid") or ""),
        ]
        # 用 multipart/form-data 提交（等价于 JS 的 FormData）
        files = [(k, (None, v)) for k, v in fields]

        headers = self._headers({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })

        try:
            r = self.http.post(url, files=files, headers=headers, timeout=20)
        except Exception as e:
            self.log(f"提交请求异常: {e}", "ERROR")
            return {"status": "retry"}

        # 解析响应
        try:
            data = r.json()
        except Exception:
            if "网页解析失败" in (r.text or ""):
                self.log("服务器返回『网页解析失败』，URL 或参数可能不对", "ERROR")
                return {"status": "fatal"}
            self.log(f"响应非 JSON: {r.text[:200]}", "ERROR")
            return {"status": "retry"}

        self.log(f"提交响应: {data}")

        code = data.get("code")
        msg = data.get("msg") or data.get("message")
        ok = data.get("status")

        if code == 0 and ok is True and msg == "通过":
            self.log("✅ 人脸验证通过！", "INFO")
            return {"status": "success"}

        if code == -4:
            self.log(f"❌ 权限不足: {msg}", "ERROR")
            return {"status": "fatal"}

        if msg == "二维码已失效，请刷新后重试":
            self.log("❌ 二维码已失效，请刷新页面", "ERROR")
            return {"status": "fatal"}

        self.log(f"⚠️ 提交未通过: {msg}", "WARN")
        return {"status": "retry"}

    def submit_sign(self, params: dict, object_id: str) -> bool:
        """
        提交人脸识别，失败时重新获取 token / 图片 / objectId 再重试
        """
        retry = 0
        while retry <= self.MAX_RETRIES:
            result = self._do_submit(params, object_id)
            if result["status"] == "success":
                return True
            if result["status"] == "fatal":
                return False

            retry += 1
            if retry > self.MAX_RETRIES:
                self.log("已达最大重试次数，放弃", "ERROR")
                return False

            self.log(f"{self.RETRY_DELAY}s 后进行第 {retry} 次重试（重新取图）...")
            time.sleep(self.RETRY_DELAY)

            try:
                puid = self.get_uid()
                if not puid:
                    raise RuntimeError("未获取到 _uid")
                upload_token = self.get_user_token()
                raw = self.get_user_face_image(puid)
                processed = self.process_image(raw)
                object_id = self.upload_face_image(processed, puid, upload_token)
            except Exception as e:
                self.log(f"重试准备阶段失败: {e}", "ERROR")
        return False

    # ------------------------------------------------------- 主流程
    def run(self) -> bool:
        """完整流程：取参数 → 取 token → 取图 → 处理 → 上传 → 提交"""
        if self.is_processing:
            self.log("正在处理中，跳过本次调用")
            return False

        self.is_processing = True
        try:
            puid = self.get_uid()
            if not puid:
                raise RuntimeError("未获取到 _uid，请先登录超星")

            # 1. 提取参数
            params = self.get_all_params(force=True)
            if not params:
                self.log("首次提取失败，2s 后重试...")
                time.sleep(2)
                params = self.get_all_params(force=True)
            if not params:
                raise RuntimeError("未能提取到有效 QR 参数")

            missing = [k for k in ("courseid", "clazzid", "cpi", "uuid")
                       if not params.get(k)]
            if missing:
                raise RuntimeError(f"缺少必要参数: {', '.join(missing)}")

            # 2. 取 token
            self.log("获取上传 token...")
            upload_token = self.get_user_token()

            # 3. 取人脸图片
            self.log("获取人脸图片...")
            img_bytes = self.get_user_face_image(puid)

            # 4. 图片扰动处理
            self.log("处理人脸图片...")
            processed = self.process_image(img_bytes)

            # 5. 上传
            self.log("上传人脸图片...")
            object_id = self.upload_face_image(processed, puid, upload_token)

            # 6. 提交
            self.log("提交人脸识别...")
            return self.submit_sign(params, object_id)

        except Exception as e:
            self.log(f"处理失败: {e}", "ERROR")
            return False
        finally:
            self.is_processing = False

    # ------------------------------------------------------- 弹窗检测
    def wait_for_face_popup(self, condition=False) -> bool:
        """检测人脸识别弹窗是否已出现。

        2026-11-19 性能修复：旧实现逐个选择器 `find_elements` 后还 `sleep(0.5)`，
        三个选择器就是每次 1.5 秒固定开销，而它在**每个页面**都会被调用一次。
        这里改成一次组合选择器查询、查完立即返回，不再睡眠。
        """
        if not condition:
            return False
        print("开始检测人脸弹窗", flush=True)
        try:
            els = self.driver.find_elements(
                By.CSS_SELECTOR,
                '.chapterVideoFaceMaskDiv, .maskDiv, .chapterVideoFaceQrMaskDiv')
        except Exception:
            return False
        for el in els:
            try:
                if el.is_displayed():
                    print("检测到人脸弹窗", flush=True)
                    return True
            except Exception:
                continue
        return False

FACE_POPUP_MAX_ATTEMPTS = 3      # 硬上限：绝不递归自调用（V5.1 第19节）
FACE_POPUP_WAIT_SECONDS = 10     # 每次强制删除的短超时
FACE_AUTH_MAX_WAIT = 20          # 人脸会话恢复等待上限（原注释写 20 秒，代码却写 200）


def delete_face_popup(driver, pass_face,
                      class_name='maskDiv1 chapterVideoFaceQrMaskDiv',
                      stop_event=None):
    """处理人脸弹窗：有最大尝试次数的循环，**绝不递归自调用**。

    返回 core.task_control.TaskStatus 之一（旧调用者忽略返回值，行为不变）。
    状态区分：弹窗不存在（正常，NO_TASK）/ 处理成功 / 处理失败 / 等待超时 /
    用户停止 / WebDriver 失效。每次尝试都检查停止事件并使用明确短超时。
    """
    from core import task_control as tc
    ev = stop_event if stop_event is not None else tc.get_stop_event()
    classes = [class_name,
               'maskDiv1 starttippop faceRecognition_1 chapterVideoFaceMaskDiv']

    for attempt in range(1, FACE_POPUP_MAX_ATTEMPTS + 1):
        cls = classes[min(attempt - 1, len(classes) - 1)]
        if tc.is_stopped(ev):
            print(color.yellow('[人脸弹窗] 用户停止，放弃处理'), flush=True)
            return tc.TaskStatus.CANCELLED
        try:
            helper = ChaoxingFaceHelper(driver)
            present = bool(helper.wait_for_face_popup(pass_face))
        except Exception as e:
            status = (tc.TaskStatus.DRIVER_ERROR
                      if tc.looks_like_driver_error(e) else tc.TaskStatus.FAILED)
            print(color.red('[人脸弹窗] 检测失败（%s），停止处理'
                            % type(e).__name__), flush=True)
            return status
        if not present:
            # 弹窗不存在是完全正常的情况，不当作严重异常
            return tc.TaskStatus.NO_TASK

        print(color.green('正在处理人脸弹窗（第 %d/%d 次），请耐心等待'
                          % (attempt, FACE_POPUP_MAX_ATTEMPTS)), flush=True)
        try:
            ok = bool(helper.run())
        except Exception as e:
            ok = False
            print(color.yellow('[人脸弹窗] 自动处理异常：%s'
                               % type(e).__name__), flush=True)
        print('[结果] 本次处理: %s' % ('成功' if ok else '失败'), flush=True)
        if ok:
            time.sleep(3)
            return tc.TaskStatus.SUCCESS

        # 处理失败：仅首轮（class 不含 maskDiv1）尝试有界的人脸会话恢复
        if 'maskDiv1' not in cls:
            try:
                with open(paths.data_or_asset('tool', 'face_url.json'), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                face_url = data.get(cls, '') or ''
            except Exception:
                face_url = ''   # 表不存在不应让整个弹窗处理崩掉
            check_face(driver, face_url, course_name=cls,
                       max_wait=FACE_AUTH_MAX_WAIT)
            check_face(driver, face_url, face_class='maskDiv', course_name=cls,
                       max_wait=FACE_AUTH_MAX_WAIT)
        if tc.is_stopped(ev):
            print(color.yellow('[人脸弹窗] 用户停止，放弃后续重试'), flush=True)
            return tc.TaskStatus.CANCELLED

        # 强制删除：明确 10 秒短超时，失败不再无休止重试
        print(color.green('开始强制删除人脸弹窗'), flush=True)
        try:
            popup = WebDriverWait(driver, FACE_POPUP_WAIT_SECONDS).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, f"[class='{cls}']")))
            driver.execute_script("arguments[0].remove();", popup)
            print(color.green('成功删除人脸弹窗'), flush=True)
            return tc.TaskStatus.SUCCESS
        except Exception as e:
            if tc.looks_like_driver_error(e):
                print(color.red('[人脸弹窗] WebDriver 失效，停止处理'), flush=True)
                return tc.TaskStatus.DRIVER_ERROR
            print(color.yellow('[人脸弹窗] 第 %d 次强制删除未成功（%s），继续有限重试'
                               % (attempt, type(e).__name__)), flush=True)

    print(color.yellow('[人脸弹窗] 已达最大尝试次数 %d，返回失败（不再递归）'
                       % FACE_POPUP_MAX_ATTEMPTS), flush=True)
    return tc.TaskStatus.FAILED
