# -*- coding: utf-8 -*-
"""pickle 反序列化安全兜底 + JSON 格式迁移（2026-11-16 任务06）。

工程历史上有 3 类 pickle：题库缓存、浏览器 Cookie、题库查询 GUI 读取。
旧 pkl 不能直接删除（用户缓存有保留价值），但来源可能不可信，
因此统一走 RestrictedUnpickler：只许内置数据结构，拒绝任意全局对象。

新数据一律写 JSON（带 `_fmt` 版本标记）；读时优先 JSON，没有再受限读旧 pkl
并迁移。迁移前备份旧文件，成功后才切换读取路径。

绝不：
* 对来源不明的 pickle 调用普通 pickle.load；
* 允许 __reduce__ / 任意类实例被构造。
"""
from __future__ import annotations

import io
import json
import os
import pickle

FORMAT_VERSION = 1


class UnsafePickleError(ValueError):
    """pickle 含不允许的全局对象 / 结构非法。"""


_ALLOWED_GLOBALS = {
    ('builtins', 'set'), ('builtins', 'frozenset'),
    ('builtins', 'complex'),
    ('datetime', 'datetime'), ('datetime', 'date'), ('datetime', 'timedelta'),
}


class RestrictedUnpickler(pickle.Unpickler):
    """只允许白名单全局 + 基础数据类型的 Unpickler。"""

    def find_class(self, module, name):
        if (module, name) in _ALLOWED_GLOBALS:
            return super().find_class(module, name)
        # collections.OrderedDict 等容器也常见，按具体需要放行纯数据结构
        if module == 'collections' and name in ('OrderedDict',):
            return super().find_class(module, name)
        raise UnsafePickleError('禁止加载全局对象：%s.%s' % (module, name))


def safe_load(fp):
    """受限读取一个 pickle（fp 可为文件对象或 bytes）。非法内容抛 UnsafePickleError。"""
    if isinstance(fp, (bytes, bytearray)):
        fp = io.BytesIO(fp)
    return RestrictedUnpickler(fp).load()


def safe_load_path(path, default=None):
    try:
        with open(path, 'rb') as f:
            return safe_load(f)
    except FileNotFoundError:
        return default


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json_or_pickle(json_path, pickle_path, *, validator=None, backup=True):
    """读数据：优先新 JSON；否则受限读旧 pickle -> 校验 -> 备份 -> 迁移 JSON。

    validator(value) 返回 True 才认为数据合法；不合法抛 UnsafePickleError，
    旧文件保留不动。迁移成功后返回 (value, 'json'|'migrated'|'pickle')。
    """
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                obj = json.load(f)
            value = obj.get('data') if isinstance(obj, dict) and '_fmt' in obj else obj
            if validator is None or validator(value):
                return value, 'json'
        except (OSError, json.JSONDecodeError, ValueError):
            pass   # JSON 损坏：尝试回退旧 pickle

    if not os.path.exists(pickle_path):
        return None, 'missing'

    # 受限读取旧 pickle（绝不普通 load）
    value = safe_load_path(pickle_path, None)
    if value is None or (validator and not validator(value)):
        raise UnsafePickleError('旧 pickle 结构非法或校验失败：%s' % pickle_path)

    # 备份旧文件（不删除）
    if backup:
        try:
            import shutil
            bak = pickle_path + '.prejson'
            if not os.path.exists(bak):
                shutil.copy2(pickle_path, bak)
        except OSError:
            pass
    # 迁移写 JSON（先写成功再认为切换完成）
    try:
        _atomic_write_json(json_path, {'_fmt': FORMAT_VERSION, 'data': value})
    except OSError as e:
        raise UnsafePickleError('迁移写 JSON 失败：%s' % e)
    return value, 'migrated'
