# -*- coding: utf-8 -*-
"""本地敏感凭据保护（2026-11-16 任务03）。

把手机号 / 密码 / AI API Key / MeoW 设备 URL / Cookie 等凭据从明文 JSON
挪到独立加密存储：

* Windows 优先用 DPAPI（CryptProtectData / CryptUnprotectData），密钥与当前
  Windows 用户绑定，其他用户/机器无法解密；**失败时抛错，绝不降级明文**。
* 非 Windows（或 DPAPI 不可用）也绝不写明文：抛 CredentialError，由上层
  明确提示用户。本工程目标平台是 Windows。
* 每个凭据一个文件，文件名 = 凭据名做安全散列，天然按 account_id 隔离
  （调用方用 `name='account.<id>.password'` 这类带 id 的命名）。
* 文件带加密版本标记 `ENC1:`，原子写入（tmp + replace），并尽力收紧权限。

对外接口（任务单指定）：
    protect_secret(value) / unprotect_secret(value)
    save_secret(name, value) / load_secret(name) / delete_secret(name)

不要把 task/xxt/constants.py 的平台协议 AES 密钥与本模块的本地凭据加密混淆：
前者是学习通签到接口协议的一部分，后者只保护本机配置文件里的用户秘密。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import tempfile
from ctypes import wintypes

from core import paths
from core import subproc

VERSION_PREFIX = 'ENC1:'
_SENTINEL = ''   # 空值不入库

# 已收紧过权限的文件（进程内缓存，路径 -> True）
# 目的：同一凭据在「启动迁移 -> 保存设置 -> 子进程快照」里会被写多次，
# 没有缓存就会反复起 icacls 进程（既是黑窗来源，也是启动变慢的来源）。
_PERM_DONE = set()


class CredentialError(RuntimeError):
    """凭据加密/解密失败。调用方必须显式处理，绝不静默退回明文。"""


# ---------------------------------------------------------------- Windows DPAPI
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _dpapi():
    if os.name != 'nt':
        raise CredentialError('本地凭据加密仅支持 Windows DPAPI，当前系统不可用')
    try:
        # CryptProtectData/UnprotectData 实际导出在 crypt32.dll（无 A/W 后缀）
        crypt32 = ctypes.WinDLL('crypt32.dll', use_last_error=True)
        crypt = crypt32.CryptProtectData
        crypt.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                          ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(_DATA_BLOB)]
        crypt.restype = wintypes.BOOL
        uncrypt = crypt32.CryptUnprotectData
        uncrypt.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(_DATA_BLOB)]
        uncrypt.restype = wintypes.BOOL
        return crypt, uncrypt
    except Exception as e:
        raise CredentialError('无法加载 Windows DPAPI：%s' % e)


def _to_blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def protect_secret(value) -> str:
    """把明文加密成 `ENC1:<base64>`；失败抛 CredentialError，绝不返回明文。"""
    if value is None or str(value) == '':
        return ''
    import base64
    crypt, _ = _dpapi()
    raw = str(value).encode('utf-8')
    blob, buf = _to_blob(raw)
    out = _DATA_BLOB()
    if not crypt(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        err = ctypes.get_last_error()
        raise CredentialError('DPAPI 加密失败（Windows 错误码 %s），原始凭据未改动' % err)
    try:
        encrypted = ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
    return VERSION_PREFIX + base64.b64encode(encrypted).decode('ascii')


def unprotect_secret(value) -> str:
    """解密 `ENC1:<base64>`；损坏/错误密钥/版本不符抛 CredentialError。"""
    if value is None or str(value) == '':
        return ''
    text = str(value)
    if not text.startswith(VERSION_PREFIX):
        raise CredentialError('凭据格式非法（缺少 %s 版本标记）' % VERSION_PREFIX)
    import base64
    _, uncrypt = _dpapi()
    try:
        encrypted = base64.b64decode(text[len(VERSION_PREFIX):], validate=True)
    except Exception as e:
        raise CredentialError('凭据内容损坏（base64 非法）：%s' % e)
    blob, buf = _to_blob(encrypted)
    out = _DATA_BLOB()
    if not uncrypt(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        err = ctypes.get_last_error()
        raise CredentialError('DPAPI 解密失败（Windows 错误码 %s，密钥/用户不匹配）' % err)
    try:
        return bytes(ctypes.string_at(out.pbData, out.cbData)).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ---------------------------------------------------------------- 文件存储
def _store_dir() -> str:
    return paths.data('tool', 'credentials')


def _secret_path(name) -> str:
    # 凭据名做散列做文件名，天然防止路径穿越，也隐藏账号 id 等语义
    key = hashlib.sha256(str(name).encode('utf-8')).hexdigest()[:32]
    return os.path.join(_store_dir(), key + '.cred')


def save_secret(name, value) -> None:
    """保存单个凭据：加密 -> 原子写入 -> 收紧权限。失败抛 CredentialError。

    2026-11-19 性能修复：**值没有变化时直接返回**，不重新加密、不重写、不重设权限。

    原先每次 `save_accounts()` 都会把所有敏感字段重新 DPAPI 加密 + 写盘 + 调 2 次
    `icacls`；一次「保存设置」要处理 4 个字段，于是弹出 8 个控制台黑窗，启动时的
    凭据迁移校验又同样走一遍。加了这个短路之后，实际只有「真的改过的字段」才落盘。
    """
    if value is None or str(value) == '':
        delete_secret(name)
        return
    text = str(value)
    path = _secret_path(name)
    # 值未变：连加密都省掉（load_secret 是纯文件读 + 解密，比起进程便宜两个数量级）
    try:
        if os.path.exists(path) and load_secret(name, None) == text:
            return
    except Exception:
        # 旧文件损坏就照常重写一遍，让 save 修好它
        pass
    encrypted = protect_secret(text)     # 失败直接抛，绝不写明文
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps({'v': 1, 'name': str(name), 'data': encrypted},
                         ensure_ascii=False)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(payload)
        # tmp 每次都是新文件（ACL 来自目录继承），所以这里不能走缓存
        hardened = _icacls(tmp)
        os.replace(tmp, path)
        # Windows 的 MoveFileEx 保留源文件安全描述符：tmp 收紧成功，目标文件即可继承结论
        if hardened:
            _PERM_DONE.add(_norm(path))
        else:
            _restrict_permissions(path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise CredentialError('凭据写盘失败：%s' % e)


def load_secret(name, default=''):
    """读取单个凭据；不存在返回 default；损坏/解密失败抛 CredentialError。"""
    path = _secret_path(name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise CredentialError('凭据文件损坏：%s' % e)
    if not isinstance(obj, dict) or obj.get('v') != 1 or 'data' not in obj:
        raise CredentialError('凭据文件结构非法')
    return unprotect_secret(obj['data'])


def delete_secret(name) -> None:
    path = _secret_path(name)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        raise CredentialError('删除凭据失败：%s' % e)


def secret_exists(name) -> bool:
    return os.path.exists(_secret_path(name))


def _norm(path) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return str(path)


def _current_user() -> str:
    """当前 Windows 用户名（icacls 的 SID 需要一个真实名字，不能写字面量）。"""
    for key in ('USERNAME', 'USER', 'LOGNAME'):
        value = os.environ.get(key)
        if value:
            return value
    try:
        import getpass
        return getpass.getuser() or ''
    except Exception:
        return ''


def _icacls(path) -> bool:
    """真正执行一次 icacls；成功返回 True。不做缓存（tmp 每次都是新文件）。"""
    if os.name != 'nt':
        try:
            os.chmod(path, 0o600)
            return True
        except Exception:
            return False
    user = _current_user()
    if not user:
        return False
    try:
        # 走 subproc：Windows 上带 CREATE_NO_WINDOW + SW_HIDE，绝不弹控制台黑窗。
        # 修掉旧 bug：以前直接把 %USERNAME% 当用户名传进去，而 subprocess 不经
        # shell 不会展开这种写法，icacls 收到字面量后必然失败，权限其实从未收紧过。
        # 注意不要加 text=True：icacls 输出是系统 ANSI 编码（中文 Windows 为 GBK），
        # 用 text 模式会让读取线程抛 UnicodeDecodeError；我们只需要 returncode。
        proc = subproc.run_hidden(
            ['icacls', path, '/inheritance:r', '/grant:r', '%s:F' % user],
            timeout=10, capture_output=True)
        return getattr(proc, 'returncode', 1) == 0
    except Exception:
        return False


def _restrict_permissions(path) -> bool:
    """尽力收紧文件权限（Windows：仅当前用户可读写）。失败不阻断功能。

    同一路径在一个进程里只处理一次；重复处理既无意义，又会为每次调用
    付一次进程创建开销（旧实现是「每次保存设置闪一堆黑窗」的主因）。
    """
    key = _norm(path)
    if key in _PERM_DONE:
        return True
    ok = _icacls(path)
    if ok:
        _PERM_DONE.add(key)
    return ok


def reset_permission_cache() -> None:
    """清空「已收紧权限」缓存（仅供测试与诊断使用）。"""
    _PERM_DONE.clear()
