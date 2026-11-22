# -*- coding: utf-8 -*-
"""DOM 记录「真的在记」守卫测试（2026-11-19）。

背景（真实事故）：上一版 dom_dump 每天最多 200 个快照，而 `test_page_loop.py`
没有隔离 XXT_HOME，一轮回归就往用户真实 record/DOM 写了 200 个**空**快照
（桩 driver 什么都读不到），配额当场打满；此后真机所有 capture 全部静默返回
空串 —— 功能等于没做，用户打开 record/DOM 只看到一堆垃圾。

本测试锁死四件事：
1. 测试脚本不许往真实数据目录写（凡 import main 的测试必须有 XXT_HOME）；
2. 配额不再按 200 个目录硬截（必须是千级且另有字节上限）；
3. 空快照不落盘、不占配额、不留垃圾目录；
4. 登录/选课/课程列表/课程详情/课程循环/考试/作业 都有记录点，不许退回只记 4 处。

用法：C:/Python315/python.exe tests/test_dom_coverage.py
"""
import glob
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_domcov_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import dom_dump as dd

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Switch:
    def __init__(self, d):
        self.d = d

    def default_content(self):
        self.d.ctx = 'top'

    def frame(self, el):
        self.d.ctx = 'child'

    def parent_frame(self):
        self.d.ctx = 'top'


class RichDriver:
    def __init__(self, html='<html><body><div>REAL</div></body></html>'):
        self.ctx = 'top'
        self.switch_to = Switch(self)
        self.html = html

    def execute_script(self, script, *a):
        return {'h': self.html, 'u': 'https://mooc1.chaoxing.com/x', 't': 'T', 'r': 'complete'}

    def find_elements(self, by, value):
        return []

    @property
    def page_source(self):
        return self.html


class BlindDriver(RichDriver):
    """页面什么都没加载出来：JS 与 page_source 都拿不到 HTML。"""

    def execute_script(self, script, *a):
        raise RuntimeError('not ready')

    @property
    def page_source(self):
        raise RuntimeError('not ready')


class JsDriver:
    """真实浏览器的行为：一次 execute_script 返回整棵 frame 树。

    关键断言点：这条路径**一次 switch_to 都不能调用**。真机事故就是
    Selenium 切 frame 让视频播放器失焦 → 自动暂停 → play() 被拒 → 视频报废。
    """

    def __init__(self):
        self.switch_calls = []
        self.switch_to = self

    def default_content(self):
        self.switch_calls.append('default_content')

    def frame(self, el):
        self.switch_calls.append('frame')

    def parent_frame(self):
        self.switch_calls.append('parent')

    def execute_script(self, script, *a):
        if 'MAX_FRAME' not in script:
            raise AssertionError('JS 快照脚本没有下发')
        return {
            'frames': [
                {'path': 'top', 'html': '<html><body>TOP</body></html>',
                 'chars': 30, 'url': 'https://mooc1.chaoxing.com/ananas/modules/video/index.html',
                 'title': '视频', 'truncated': False},
                {'path': 'top/frame_0', 'html': '<html><body>PLAYER</body></html>',
                 'chars': 33, 'url': 'https://mooc1.chaoxing.com/player',
                 'title': '播放器', 'truncated': False},
            ],
            'total': 63, 'cross_origin': 1, 'capped': False,
        }


def main():
    print('=== DOM 记录守卫 ===')

    # 1) 测试隔离：凡 import main 的测试必须设 XXT_HOME
    dirty = []
    for path in sorted(glob.glob(os.path.join('tests', 'test_*.py'))):
        src = open(path, encoding='utf-8').read()
        if not re.search(r'^\s*(import main|from main import|import main as)', src, re.M):
            continue
        if 'XXT_HOME' not in src:
            dirty.append(os.path.basename(path))
    check('import main 的测试全部隔离了 XXT_HOME', not dirty, dirty)

    # 2) 配额：千级目录上限 + 进程字节上限（旧版 200 是个坑）
    check('每日快照上限不再是 200', dd.DAY_LIMIT >= 2000, dd.DAY_LIMIT)
    check('有单进程写入字节上限', getattr(dd, 'SESSION_BYTE_LIMIT', 0) >= 256 * 1024 * 1024,
          getattr(dd, 'SESSION_BYTE_LIMIT', None))

    # 3) 正常页面：落盘 + meta 可用
    dd.reset_stats()
    folder = dd.capture(RichDriver(), '正常页', {'course': '真实课'})
    check('正常页面落盘', bool(folder) and os.path.isdir(folder), folder)
    meta = json.load(open(os.path.join(folder, 'meta.json'), encoding='utf-8'))
    check('meta 记录总字符数与耗时',
          meta.get('total_chars', 0) > 0 and 'elapsed_ms' in meta, meta.get('total_chars'))
    check('统计里算了一个快照', dd.stats()['session_snapshots'] == 1, dd.stats())

    # 4) 空页面：不留目录、不占配额、只计 empty_skips
    before = dd.stats()
    empty = dd.capture(BlindDriver(), '空页')
    after = dd.stats()
    check('读不到内容时返回空串', empty == '', empty)
    check('空快照不占配额', after['session_snapshots'] == before['session_snapshots'],
          after['session_snapshots'])
    check('空快照计入 empty_skips', after['empty_skips'] == before['empty_skips'] + 1,
          after['empty_skips'])
    day_dir = dd._day_dir()
    leftovers = [n for n in os.listdir(day_dir) if '空页' in n] if os.path.isdir(day_dir) else []
    check('空快照目录被删掉，不留垃圾', not leftovers, leftovers)

    # 5) record() 去重：同 key 立即重复调用只抓一次
    dd.reset_stats()
    d = RichDriver()
    first = dd.record(d, '视频', key='k1', min_interval=60)
    second = dd.record(d, '视频', key='k1', min_interval=60)
    check('同 key 短时间内只抓一次', bool(first) and second == '', (first, second))
    third = dd.record(d, '视频', key='k2', min_interval=0)
    check('不同 key 正常抓取', bool(third), third)

    # 5b) 视频页真机事故回归：抓 DOM 必须走 JS 一次性读树，绝不能切 frame
    #     （切 frame → 播放器失焦 → 暂停 → play() 被拒 → 视频 0:00 卡死）
    dd.reset_stats()
    js = JsDriver()
    folder_js = dd.capture(js, '视频-JS路径')
    check('JS 路径能抓到快照', bool(folder_js) and os.path.isdir(folder_js), folder_js)
    meta_js = json.load(open(os.path.join(folder_js, 'meta.json'), encoding='utf-8')) \
        if folder_js else {}
    check('JS 路径标记 method=js', meta_js.get('method') == 'js', meta_js.get('method'))
    check('JS 路径抓到 iframe 子文档', len(meta_js.get('frames') or []) >= 2,
          [f.get('path') for f in meta_js.get('frames') or []])
    check('JS 路径记录了跨域帧数量', meta_js.get('cross_origin_frames') == 1,
          meta_js.get('cross_origin_frames'))
    check('抓 DOM 一次 switch_to 都没调用（视频不再被暂停）',
          js.switch_calls == [], js.switch_calls)
    htmls = [f for f in os.listdir(folder_js) if f.endswith('.html')] if folder_js else []
    check('子帧 html 已落盘', len(htmls) >= 2, htmls)

    # 6) 覆盖点：main.py 必须真的调用 record_dom，且覆盖全流程
    main_src = open('main.py', encoding='utf-8').read()
    check('main.py 有统一入口 record_dom', 'def record_dom(' in main_src)
    spots = {
        '登录后首页': "record_dom(driver, '登录后首页'",
        '课程列表': "record_dom(driver, '课程列表'",
        '课程详情': "record_dom(driver, '课程详情_",
        '未识别页面': "'未识别页面_%d'",
        '处理出错': "'处理出错'",
    }
    missing = [k for k, v in spots.items() if v not in main_src]
    check('main.py 关键页面都有记录点', not missing, missing)
    check('run() 里按页面指纹记录',
          "key='page:%s' % fingerprint" in main_src)

    exam_src = open(os.path.join('task', 'exam_standalone.py'), encoding='utf-8').read()
    check('考试列表/确认页/答题页有记录点', "'考试_%s' % PAGE_LABEL" in exam_src)
    work_src = open(os.path.join('task', 'do_work.py'), encoding='utf-8').read()
    check('作业作答页有记录点', "'作业作答页'" in work_src)

    # 7) 日志包不能把 DOM 当配置收进去
    export_src = open(os.path.join('core', 'log_export.py'), encoding='utf-8').read()
    check('日志导出跳过 DOM 目录', '_prune_walk' in export_src
          and "base == 'DOM'" in export_src)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
