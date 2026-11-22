# -*- coding: utf-8 -*-
"""DOM 全量落盘（2026-11-19 新增功能）回归。

纯桩 fake driver + 临时 HOME，不联网、不开浏览器。
覆盖：顶层 + iframe 抓取、meta.json、截断标记、异常不抛、driver 回到 default_content。

用法：C:/Python315/python.exe tests/test_dom_dump.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_domdump_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import dom_dump as dd

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class SwitchStub:
    def __init__(self, driver):
        self.d = driver

    def default_content(self):
        self.d.ctx = 'top'

    def frame(self, el):
        if isinstance(el, dict) and el.get('dead'):
            raise RuntimeError('frame detached')
        self.d.ctx = 'child'

    def parent_frame(self):
        self.d.ctx = 'top'


class FakeDriver:
    """顶层文档里有一个 iframe；只实现本模块用到的四个接口。"""

    def __init__(self, explode=False):
        self.ctx = 'top'
        self.switch_to = SwitchStub(self)
        self.explode = explode
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append(('js', self.ctx))
        if self.explode:
            raise RuntimeError('boom')
        if self.ctx == 'top':
            return {'h': '<html><body><div>TOP</div><iframe src="a.html"></iframe></body></html>',
                    'u': 'https://mooc1.chaoxing.com/study', 't': '学习页', 'r': 'complete'}
        return {'h': '<html><body><p>CHILD</p></body></html>',
                'u': 'https://mooc1.chaoxing.com/frame', 't': '子帧', 'r': 'complete'}

    def find_elements(self, by, value):
        self.calls.append(('find', self.ctx))
        if self.ctx == 'top':
            return [{'id': 'f1'}]
        return []

    @property
    def page_source(self):
        if self.explode:
            raise RuntimeError('no source')
        return '<html>fallback</html>'


def main():
    print('=== DOM 全量落盘 ===')
    drv = FakeDriver()
    folder = dd.capture(drv, '视频+测验', {'course': '测试课', 'url': 'https://x'})
    check('返回快照目录', bool(folder) and os.path.isdir(folder), folder)
    files = sorted(os.listdir(folder)) if folder else []
    check('含 meta.json', 'meta.json' in files, files)
    check('顶层文档已落盘', any(f.startswith('frame_') for f in files), files)
    check('iframe 文档也落盘（>=2 个 html）',
          len([f for f in files if f.endswith('.html')]) >= 2, files)
    check('抓完回到 default_content', drv.ctx == 'top', drv.ctx)

    meta = json.load(open(os.path.join(folder, 'meta.json'), encoding='utf-8')) if folder else {}
    check('meta 记录标签', meta.get('tag') == '视频+测验', meta.get('tag'))
    check('meta 记录 frame 数量', len(meta.get('frames') or []) >= 2, len(meta.get('frames') or []))
    check('meta 保留 url/title',
          any('study' in (f.get('url') or '') for f in (meta.get('frames') or [])),
          [f.get('url') for f in (meta.get('frames') or [])])
    check('meta 保留调用方附加信息',
          (meta.get('extra') or {}).get('course') == '测试课', meta.get('extra'))

    # 截断：把单文档上限压到 20 字符
    old = dd.MAX_DOC_CHARS
    dd.MAX_DOC_CHARS = 20
    try:
        folder2 = dd.capture(drv, '截断')
        meta2 = json.load(open(os.path.join(folder2, 'meta.json'), encoding='utf-8'))
        check('超长文档被标记 truncated',
              any(f.get('truncated') for f in meta2.get('frames') or []),
              meta2.get('frames'))
        check('截断后字符数不超过上限',
              all(f.get('chars', 0) <= 20 for f in meta2.get('frames') or []),
              [f.get('chars') for f in meta2.get('frames') or []])
    finally:
        dd.MAX_DOC_CHARS = old

    # 异常路径：不得抛异常，也不得把 driver 留在子帧
    bad = FakeDriver(explode=True)
    try:
        folder3 = dd.capture(bad, '异常页')
        ok = True
    except Exception as e:
        ok = False
        folder3 = 'raised %r' % e
    check('抓取异常不外抛', ok, folder3)
    check('异常后仍回到 default_content', bad.ctx == 'top', bad.ctx)

    # 空 driver
    check('driver 为 None 返回空串', dd.capture(None, 'x') == '')

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
