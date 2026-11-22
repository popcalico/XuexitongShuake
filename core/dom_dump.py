# -*- coding: utf-8 -*-
"""页面 DOM 全量落盘（2026-11-19 新增，2026-11-19 重做）。

用途：把每次打开的页面**完整 DOM** 存下来，方便事后离线分析平台改版、
未识别页面、答题页结构，而不用再让用户复述或重新跑一遍。

落盘位置（用户数据目录内，不进代码仓库）：
    record/DOM/<YYYYMMDD>/<HHMMSS_mmm>_<标签>/meta.json
    record/DOM/<YYYYMMDD>/<HHMMSS_mmm>_<标签>/frame_000.html   顶层文档
    record/DOM/<YYYYMMDD>/<HHMMSS_mmm>_<标签>/frame_001.html   第 1 个 iframe
    ...

2026-11-19 修正的三个致命问题（旧版等于没记录）：
1. 旧版每天最多 200 个快照目录，而单元测试未隔离 XXT_HOME，一轮回归就写满
   真实 record/DOM，之后**真机所有 capture 全部静默返回空串** → 一个 DOM 都没留下。
   现在：目录上限提到 5000，测试必须隔离（见 tests/test_dom_coverage.py 守卫），
   并且空内容快照不占配额。
2. 旧版只挂在「课程循环里的已识别页 / 未识别页 / 处理出错 / 测验提交」四处，
   登录、选课、课程列表、课程详情、考试、签到、问卷、作业、讨论全都没记录。
   现在由 `record()` 统一入口，各流程点都调它。
3. 旧版抓到 0 字符也照样写 meta，看起来「成功」其实什么都没存。
   现在：所有 frame 都为空 → 删掉目录、返回空串、计入 `empty_skips()`。

约束（防止把磁盘写满 / 把程序拖慢）：
- 每个文档最多存 ``MAX_DOC_CHARS`` 字符，超出截断并在 meta 里记 ``truncated``；
- 单次最多抓 ``MAX_FRAMES`` 个 frame，单文档最多 ``MAX_IFRAME_PER_DOC`` 个子帧，
  最多下钻 ``MAX_DEPTH`` 层；
- 同一天最多 ``DAY_LIMIT`` 个快照目录，超过直接跳过（返回空串）；
- 本进程累计写入超过 ``SESSION_BYTE_LIMIT`` 就停止抓取（防一次跑爆磁盘）；
- 磁盘上 DOM 根目录超过 ``DISK_SOFT_LIMIT`` 时，删掉最老的日期目录（保留最近
  ``KEEP_DAYS`` 天），每次进程只检查一次；
- 任何异常都静默返回空串，**绝不影响学习主流程**；
- 抓完一定回到 default_content，不把 driver 留在 iframe 里。

**调用约定**：`capture()` / `record()` 返回后 driver 停在 default_content。
如果调用点在 iframe 里，必须自己重进 frame。

隐私说明：本项目为纯本地工具，用户已明确不要求脱敏；本模块**不做任何打码**，
只按原样保存 DOM（含页面上的姓名/学号等水印）。导出日志包时由
``core/log_export`` 统一脱敏，二者互不影响。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

from selenium.webdriver.common.by import By

from core import paths

RECORD_SUB = 'DOM'
MAX_DOC_CHARS = 2 * 1024 * 1024
TOTAL_CHARS_LIMIT = 6 * 1024 * 1024           # 单次快照所有 frame 合计上限
MAX_FRAMES = 20
MAX_IFRAME_PER_DOC = 12
MAX_DEPTH = 3
DAY_LIMIT = 5000
SESSION_BYTE_LIMIT = 1536 * 1024 * 1024      # 单进程最多写 1.5GB
DISK_SOFT_LIMIT = 3 * 1024 * 1024 * 1024     # 磁盘上最多留 3GB
KEEP_DAYS = 3                                # 清理时至少保留最近 3 天

_DAY_CACHE = {'day': '', 'count': 0}
_SESSION = {'bytes': 0, 'snapshots': 0, 'empty_skips': 0, 'last_error': ''}
_SEEN = {}                # 去重键 -> 上次抓取时间戳
_PURGED = {'done': False}


def _day_dir(now: float | None = None) -> str:
    return os.path.join(paths.record(RECORD_SUB),
                        time.strftime('%Y%m%d', time.localtime(now)))


def _existing_count(day_dir: str) -> int:
    """当天已有快照数（带进程内缓存，避免每次 listdir）。"""
    day = os.path.basename(day_dir)
    if _DAY_CACHE['day'] != day:
        try:
            names = [n for n in os.listdir(day_dir) if not n.startswith('.')]
            count = len([n for n in names if os.path.isdir(os.path.join(day_dir, n))])
        except OSError:
            count = 0
        _DAY_CACHE['day'] = day
        _DAY_CACHE['count'] = count
    return _DAY_CACHE['count']


def _safe_tag(tag: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', '_', str(tag or '页面')).strip('_')
    return (text or '页面')[:24]


# 一次 JS 遍历整个 frame 树。
#
# 为什么不用 Selenium 的 switch_to.frame 逐帧切换（2026-11-19 真机事故）：
#   视频页的播放器监听 blur/visibilitychange，Selenium 每次切 frame 都会让
#   播放器失焦 → 视频自动暂停 → 之后 `v.play()` 被浏览器策略拒绝
#   （无用户手势 + 页面不可见）→ 视频永远停在 0:00，8 次判定后直接抛
#   「视频播放异常（Exception）」，整页视频报废。
#   真机证据：16:24:21 抓完 DOM，16:24:24 起播放进度 0:00/1、反复「播放被暂停」，
#   同一页不抓 DOM 时（16:28）视频 25 秒正常播完。
#   JS 只读 DOM，不切帧、不触发焦点变化，且一次往返就能拿完整棵树（快 10 倍以上）。
_JS_SNAPSHOT = """
var MAX_FRAME = %(max_frame)d, MAX_TOTAL = %(max_total)d;
var MAX_FRAMES = %(max_frames)d, MAX_DEPTH = %(max_depth)d, MAX_CHILD = %(max_child)d;
var out = [], total = 0, cross = 0;
function walk(doc, path, depth) {
  if (out.length >= MAX_FRAMES) return;
  var html = '';
  try { html = doc.documentElement ? doc.documentElement.outerHTML : ''; } catch (e) { html = ''; }
  var trunc = false;
  if (html.length > MAX_FRAME) { html = html.substring(0, MAX_FRAME); trunc = true; }
  total += html.length;
  var item = {path: path, html: html, chars: html.length, truncated: trunc};
  try { item.url = doc.location ? String(doc.location.href) : ''; } catch (e) { item.url = ''; }
  try { item.title = doc.title || ''; } catch (e) { item.title = ''; }
  out.push(item);
  if (depth >= MAX_DEPTH || total >= MAX_TOTAL) return;
  var frames = [];
  try { frames = doc.querySelectorAll('iframe'); } catch (e) { frames = []; }
  for (var i = 0; i < frames.length && i < MAX_CHILD; i++) {
    if (out.length >= MAX_FRAMES || total >= MAX_TOTAL) break;
    var fd = null;
    try { fd = frames[i].contentDocument; } catch (e) { fd = null; }
    if (!fd) { cross++; continue; }
    walk(fd, path + '/frame_' + i, depth + 1);
  }
}
try { walk(document, 'top', 0); } catch (e) {}
return {frames: out, total: total, cross_origin: cross, capped: (out.length >= MAX_FRAMES || total >= MAX_TOTAL)};
"""


def _js_script() -> str:
    return _JS_SNAPSHOT % {
        'max_frame': MAX_DOC_CHARS,
        'max_total': TOTAL_CHARS_LIMIT,
        'max_frames': MAX_FRAMES,
        'max_depth': MAX_DEPTH,
        'max_child': MAX_IFRAME_PER_DOC,
    }


def _capture_via_js(driver):
    """一次 execute_script 拿完整 frame 树；不支持时返回 None。"""
    try:
        got = driver.execute_script(_js_script())
    except Exception:
        return None
    if not isinstance(got, dict):
        return None
    raw = got.get('frames')
    if not isinstance(raw, list) or not raw:
        return None
    frames = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        html = item.get('html') or ''
        frames.append({
            'path': item.get('path') or 'top',
            'url': item.get('url') or '',
            'title': item.get('title') or '',
            'chars': int(item.get('chars') or len(html)),
            'truncated': bool(item.get('truncated')),
            '_html': html,
        })
    if not frames:
        return None
    return {
        'frames': frames,
        'cross_origin': int(got.get('cross_origin') or 0),
        'capped': bool(got.get('capped')),
        'method': 'js',
    }


def _read_doc(driver):
    """取当前文档的 HTML 与基础信息；失败返回 (空串, {})。"""
    info = {}
    html = ''
    try:
        got = driver.execute_script(
            "return {h: (document.documentElement ? document.documentElement.outerHTML : ''),"
            " u: location.href || '', t: document.title || '',"
            " r: document.readyState || ''};"
        )
        if isinstance(got, dict):
            html = got.get('h') or ''
            info = {'url': got.get('u', ''), 'title': got.get('t', ''),
                    'ready_state': got.get('r', '')}
    except Exception:
        info = {}
    if not html:
        try:
            html = driver.page_source or ''
            info.setdefault('source', 'page_source')
        except Exception:
            html = ''
    return html, info


def _capture_tree(driver, folder: str, meta: dict, depth: int, path: str,
                  budget: list, seq: list):
    """递归抓取当前文档及其子 frame。

    budget 是可变单元素列表，记录剩余可抓 frame 数；seq 记录文件序号。
    """
    html, info = _read_doc(driver)
    truncated = False
    if len(html) > MAX_DOC_CHARS:
        html = html[:MAX_DOC_CHARS]
        truncated = True
    name = 'frame_%03d.html' % seq[0]
    seq[0] += 1
    try:
        with open(os.path.join(folder, name), 'w', encoding='utf-8', errors='replace') as f:
            f.write(html)
    except OSError:
        name = ''
    item = dict(info)
    item.update({'path': path or 'top', 'file': name,
                 'chars': len(html), 'truncated': truncated})
    meta['frames'].append(item)

    if depth >= MAX_DEPTH or budget[0] <= 0:
        return
    try:
        frames = driver.find_elements(By.TAG_NAME, 'iframe')
    except Exception:
        frames = []
    for index, frame in enumerate(frames[:MAX_IFRAME_PER_DOC]):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        child = '%s/frame_%d' % (path or 'top', index)
        try:
            driver.switch_to.frame(frame)
        except Exception:
            continue
        try:
            _capture_tree(driver, folder, meta, depth + 1, child, budget, seq)
        finally:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass


def _folder_bytes(folder: str) -> int:
    total = 0
    try:
        for name in os.listdir(folder):
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _purge_old_once() -> None:
    """磁盘占用超软上限时删最老的日期目录（每进程只做一次）。"""
    if _PURGED['done']:
        return
    _PURGED['done'] = True
    root = paths.record(RECORD_SUB)
    try:
        days = sorted(n for n in os.listdir(root)
                      if len(n) == 8 and n.isdigit())
    except OSError:
        return
    sizes = {}
    total = 0
    for name in days:
        size = _folder_bytes(os.path.join(root, name))
        sizes[name] = size
        total += size
    if total <= DISK_SOFT_LIMIT:
        return
    for name in days[:-KEEP_DAYS] if len(days) > KEEP_DAYS else []:
        if total <= DISK_SOFT_LIMIT:
            break
        try:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            total -= sizes.get(name, 0)
        except Exception:
            continue


def capture(driver, tag: str = '页面', extra: dict | None = None) -> str:
    """抓取当前页面（顶层 + 全部 iframe）并落盘，返回快照目录；失败/超限返回空串。"""
    if driver is None:
        return ''
    if _SESSION['bytes'] >= SESSION_BYTE_LIMIT:
        return ''
    _purge_old_once()

    day_dir = _day_dir()
    if _existing_count(day_dir) >= DAY_LIMIT:
        return ''
    try:
        os.makedirs(day_dir, exist_ok=True)
    except OSError:
        return ''

    stamp = time.strftime('%H%M%S')
    name = '%s_%s' % (stamp, _safe_tag(tag))
    folder = os.path.join(day_dir, name)
    # 同一秒内重复抓同一标签时加后缀，绝不覆盖已有快照
    suffix = 0
    while os.path.exists(folder):
        suffix += 1
        if suffix > 60:
            return ''
        folder = os.path.join(day_dir, '%s_%d' % (name, suffix))
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return ''

    started = time.time()
    meta = {
        'tag': tag,
        'captured_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started)),
        'frames': [],
    }
    if extra:
        try:
            meta['extra'] = {str(k): str(v)[:400] for k, v in dict(extra).items()}
        except Exception:
            pass

    # 主路径：一次 JS 拿完整 frame 树（不切 frame，见 _JS_SNAPSHOT 注释）。
    # 只有在 driver 连最基本的 JS 都跑不了时才回退到逐帧切换的旧方式。
    seq = [0]
    snap = _capture_via_js(driver)
    if snap:
        meta['method'] = 'js'
        if snap.get('cross_origin'):
            meta['cross_origin_frames'] = snap['cross_origin']
        if snap.get('capped'):
            meta['capped'] = True
        for item in snap['frames']:
            html = item.pop('_html', '')
            name = 'frame_%03d.html' % seq[0]
            seq[0] += 1
            try:
                with open(os.path.join(folder, name), 'w', encoding='utf-8',
                          errors='replace') as f:
                    f.write(html)
            except OSError:
                name = ''
            item['file'] = name
            meta['frames'].append(item)
    else:
        meta['method'] = 'frame_switch'
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        budget = [MAX_FRAMES]
        try:
            _capture_tree(driver, folder, meta, 0, 'top', budget, seq)
        except Exception as e:
            meta['error'] = '%s: %s' % (type(e).__name__, e)
            _SESSION['last_error'] = meta['error']
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    frames = meta.get('frames') or []
    meta['total_chars'] = sum(int(f.get('chars') or 0) for f in frames)
    meta['elapsed_ms'] = int((time.time() - started) * 1000)
    meta['truncated_frames'] = len([f for f in frames if f.get('truncated')])

    # 全空 = 什么都没拿到（页面还没加载完 / driver 已失效 / 桩测试）。
    # 这种快照对排查毫无价值，还会白吃配额，直接删掉。
    if meta['total_chars'] <= 0:
        _SESSION['empty_skips'] += 1
        try:
            shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass
        return ''

    try:
        with open(os.path.join(folder, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except OSError:
        return ''
    written = _folder_bytes(folder)
    _SESSION['bytes'] += written
    _SESSION['snapshots'] += 1
    _DAY_CACHE['count'] = _DAY_CACHE.get('count', 0) + 1
    return folder


def record(driver, tag: str = '页面', extra: dict | None = None,
           key: str | None = None, min_interval: float = 20.0) -> str:
    """带去重的抓取入口：同一个 ``key`` 在 ``min_interval`` 秒内只抓一次。

    key 一般传「页面身份 + 内容指纹」，避免同一页反复写盘（视频页尤其明显：
    一个视频页会有 10+ 帧，每轮循环都抓就是纯浪费）。
    """
    now = time.time()
    if key:
        last = _SEEN.get(key)
        if last is not None and now - last < max(0.0, min_interval):
            return ''
        _SEEN[key] = now
        if len(_SEEN) > 4000:          # 长跑进程防止无限增长
            _SEEN.clear()
            _SEEN[key] = now
    return capture(driver, tag, extra)


def stats() -> dict:
    """本次进程内的抓取统计（供日志/自检显示）。"""
    day = _day_dir()
    return {
        'today_dir': day,
        'today_count': _existing_count(day),
        'day_limit': DAY_LIMIT,
        'session_snapshots': _SESSION['snapshots'],
        'session_bytes': _SESSION['bytes'],
        'empty_skips': _SESSION['empty_skips'],
        'last_error': _SESSION['last_error'],
    }


def reset_stats() -> None:
    _SESSION.update({'bytes': 0, 'snapshots': 0, 'empty_skips': 0, 'last_error': ''})
    _SEEN.clear()


def summary_line() -> str:
    """一行可读摘要，接在「页面 DOM 已保存」后面。"""
    s = stats()
    return '今日 %d/%d 个，本次 %d 个（%.1f MB）' % (
        s['today_count'], s['day_limit'], s['session_snapshots'],
        s['session_bytes'] / 1024.0 / 1024.0)


def root() -> str:
    """DOM 快照根目录（供日志/导出提示用）。"""
    return paths.record(RECORD_SUB)
