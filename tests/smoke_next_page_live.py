# -*- coding: utf-8 -*-
"""真实浏览器冒烟：超长文本页 + 下一页在最底部 + 按钮在嵌套 iframe 里。

背景（2026-11-21 用户现场）：
  「这个页面中有非常非常多的文本，下一页按钮在最下方，然后程序就卡在这里了」。
  日志证据：每次「跳转下一页」都卡 55~56 秒才打印「暂时没找到」，
  等于 6 条 XPath × 5 个上下文 × 全局 implicit_wait(2)；
  而且长文本页上 `//*[contains(normalize-space(.), "下一页")]` 会先命中
  一串祖先 div，真正的 <a> 排在后面，旧代码只看前 4 个节点 → 永远找不到。

这个脚本用 file:// 造出同构的页面（长正文 + 祖先 div 也含「下一页」+ 按钮在
最底部 + 按钮在第二层 iframe 里），用真实 Edge 跑 `_click_next_by_js`，验证：

  1) 能找到并点到最底部的「下一页」（含嵌套帧）
  2) 不误点祖先容器 / 不误点文档自己的翻页按钮
  3) 耗时 < 3 秒（旧实现是 56 秒）
  4) 没有按钮时快速返回 False，不空等

用法（会弹出真实 Edge 窗口，约 20~40 秒）：
    C:/Python315/python.exe tests/smoke_next_page_live.py

退出码 0 = 全部通过。
"""
import os
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_next_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


LONG_TEXT = ''.join(
    '<p>第%d段：这是一段很长的正文，用来把页面撑到几千像素高，'
    '模拟学习通的长文本章节内容。正文里当然也会顺带提到下一页这个词。</p>' % i
    for i in range(1, 1201))


def build_pages(base):
    """造三个页面：outer（长文+嵌套帧）、inner（长文+底部按钮）、empty（无按钮）。"""
    inner = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>INNER</title></head><body>
%s
<div class="pager" style="padding:40px 0">
  <a href="javascript:void(0)" id="bottomNext"
     onclick="window.__clicked='inner-bottom';document.title='CLICKED'">下一页</a>
</div>
</body></html>""" % LONG_TEXT

    outer = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>OUTER</title></head><body>
<div class="content">
%s
<iframe src="inner.html" style="width:900px;height:600px;border:1px solid #ccc"></iframe>
<p>页脚：正文结束，这里还有一个「下一页」的字样，但它是纯文本，不是按钮。</p>
</div>
</body></html>""" % LONG_TEXT

    empty = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>EMPTY</title></head><body>
%s
<p>这一页没有任何翻页按钮。</p>
</body></html>""" % LONG_TEXT

    for name, html in (('inner', inner), ('outer', outer), ('empty', empty)):
        with open(os.path.join(base, name + '.html'), 'w', encoding='utf-8') as f:
            f.write(html)
    return {name: name + '.html' for name in ('inner', 'outer', 'empty')}


def serve(base):
    """起一个临时 HTTP 服务：file:// 下每个文件是独立源，iframe 读不到
    contentDocument（真机学习通的帧是同源的），必须用 http 才能如实复现。"""
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except (ConnectionResetError, ConnectionAbortedError):
                pass   # 浏览器关窗时的正常断连，不往输出里刷堆栈

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=base))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1]


def build_priority_page(base):
    """同一帧里同时有：底部 class=next 的 pager、顶部 #prevNextFocusNext。

    必须点 #prevNextFocusNext —— 否则会翻文档而不是翻章节。
    """
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>PRIORITY</title></head><body>
<a href="javascript:void(0)" id="prevNextFocusNext"
   onclick="window.__clicked='chapter';document.title='CHAPTER'">下一页</a>
%s
<div class="next-pager" style="padding:40px 0">
  <a href="javascript:void(0)"
     onclick="window.__clicked='doc-pager';document.title='DOCPAGER'">下一页</a>
</div>
</body></html>""" % LONG_TEXT
    path = os.path.join(base, 'priority.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return 'priority.html'


def main():
    print('=== 「下一页在最底部」真机冒烟 ===')
    base = tempfile.mkdtemp(prefix='xxt_next_pages_')
    pages = build_pages(base)
    priority_page = build_priority_page(base)

    httpd, root_url = serve(base)
    print('本地站点: %s' % root_url)
    priority_url = root_url + priority_page

    driver = None
    try:
        driver = core.start_browser('edge', '', '1')
        check('浏览器启动成功', driver is not None)
        if not driver:
            return 1

        # ---- 1) 长文本 + 按钮在第二层 iframe 最底部 ----
        driver.get(root_url + pages['outer'])
        time.sleep(1.5)
        h = driver.execute_script('return document.body.scrollHeight')
        check('页面确实很长（>5000px）', (h or 0) > 5000, h)

        t0 = time.perf_counter()
        ok, detail = core._click_next_by_js(driver)
        cost = time.perf_counter() - t0
        check('嵌套帧最底部的「下一页」能找到并点到', ok, detail)
        check('耗时 < 3 秒（旧实现 56 秒）', cost < 3, '%.2fs' % cost)

        clicked = None
        try:
            clicked = driver.execute_script(
                "var f=document.querySelector('iframe');"
                "return f && f.contentWindow ? f.contentWindow.__clicked : null;")
        except Exception as e:
            clicked = 'JS 读取失败(%s)' % type(e).__name__
        check('点的是 iframe 里的真按钮（不是祖先 div）', clicked == 'inner-bottom', clicked)

        # ---- 2) #prevNextFocusNext 优先级高于文档 pager ----
        driver.get(priority_url)
        time.sleep(1.5)
        ok2, detail2 = core._click_next_by_js(driver)
        picked = driver.execute_script('return window.__clicked || null')
        check('有章节导航时优先点 #prevNextFocusNext', ok2 and picked == 'chapter',
              '%s / %s' % (detail2, picked))

        # ---- 3) 没有按钮：快速返回 False，不空等 ----
        driver.get(root_url + pages['empty'])
        time.sleep(1.5)
        t0 = time.perf_counter()
        ok3, detail3 = core._click_next_by_js(driver)
        cost3 = time.perf_counter() - t0
        check('没有按钮时返回 False', ok3 is False, detail3)
        check('没有按钮时也很快（< 3 秒）', cost3 < 3, '%.2fs' % cost3)
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        try:
            httpd.shutdown()
        except Exception:
            pass

    print()
    if FAILS:
        print('FAILED: %s' % FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
