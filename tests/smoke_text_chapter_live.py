"""真机冒烟：纯文本章节的「快速识别」与「直接调用翻页函数」。

页面结构**照抄**用户 2026-11-22 导出的真实 DOM 快照
（`record/DOM/20261122/001812_未识别页面_1`）：

  <div class="prev_title" title="第二节 华夏初立">第二节 华夏初立</div>
  <ul class="prev_ul clearfix"><li class="active" cardid="..." id="dct1">...</li></ul>
  <iframe id="iframe" src="cards?...">
  <div id="prevNextFocusNext" class="... prev_next next fr ..."
       onclick="PCount.next('1','1211178756','265852000','155480978','',true)">下一节</div>

要点：`#prevNextFocusNext` 是 **div + onclick**，没有 href，
所以「点不动」时唯一的可靠推进方式就是直接执行那段 onclick。

用本地 HTTP 服务而不是 file://：Chrome 把每个本地文件当独立源，
iframe 读不到 contentDocument，测不到真实结构。
"""

import os
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_text_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import main as core

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> %s' % detail) if detail else ''))
    if not ok:
        FAILS.append(name)


# 纯文本章节的卡片帧：真实页面里由 uParse(".ans-cc") 渲染出大片正文
CARDS = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
         '<div class="ans-cc"><p>第二节 华夏初立。中华民族的形成是一个漫长的历史过程，'
         '在长期的生产生活与交往交流中，各民族文化互鉴融通，逐渐形成了多元一体的格局。'
         '这一段用于模拟真实的纯文本章节正文，长度足够让页面被判定为已经渲染完成。</p></div>'
         '</body></html>')

EMPTY_CARDS = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
               '<body><div id="player-host"></div></body></html>')

TEXT_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>学生学习页面</title>
<script>
window.PCount = {
  calls: 0, last: null,
  next: function(){
    window.PCount.calls++;
    window.PCount.last = Array.prototype.slice.call(arguments);
    document.title = 'PCOUNT_NEXT_CALLED';
  }
};
</script></head>
<body>
<div class="prev_title_pos" tabindex="0" role="option">
  <div class="prev_title" title="第二节 华夏初立">第二节 华夏初立
    <span class="markTag" id="markTag_1211178756" style="display: none"></span>
  </div>
</div>
<div class="prev_list" id="prev_tab" style="display: none">
  <ul class="prev_ul clearfix">
    <li class="active" title="第二节 华夏初立" cardid="1209675773"
        onclick="changeDisplayContent(1,1)" tabindex="0" role="option" id="dct1">
      <div class="prev_white"><span class="num">1</span><span class="spanText">第二节 华...</span></div>
    </li>
  </ul>
</div>
<div style="min-width:1010px" class="course_main" id="qqqq">
  <iframe height="500px" id="iframe" info="card" src="cards.html"></iframe>
</div>
<div id="prevNextFocus" class="clearfix" tabindex="-1">
  <div id="prevNextFocusNext" class="jb_btn jb_btn_92 fs14 prev_next next fr btnWidAuto"
       tabindex="-1" role="button"
       onclick="PCount.next('1','1211178756','265852000','155480978','',true)">下一节</div>
  <div id="prevNextFocusPrev" class="btnBlue btn_92 fs14 prev_next marginRight30 prev fr btnWidAuto"
       role="button" onclick="PCount.previous('1','1','1','1','')">上一节</div>
</div>
</body></html>
"""

# 章节内有 3 个卡片标签（真实结构：dct1 正文 / dct2 视频 / dct3 ppt）
MULTI_CARD_PAGE = TEXT_PAGE.replace(
    """    </li>
  </ul>""",
    """    </li>
    <li title="视频" cardid="1209675774" id="dct2"
        onclick="changeDisplayContent(2,3,'1211178694','265852000','155480978','');"><div class="prev_white"><span class="num">2</span><span class="spanText">视频</span></div></li>
    <li title="ppt" cardid="1209675775" id="dct3"
        onclick="changeDisplayContent(3,3,'1211178694','265852000','155480978','');"><div class="prev_white"><span class="num">3</span><span class="spanText">ppt</span></div></li>
  </ul>""").replace(
    "window.PCount = {",
    """window.changeDisplayContent = function(num, total){
      var lis = document.querySelectorAll('.prev_ul > li');
      for (var i = 0; i < lis.length; i++) {
        var n = parseInt(String(lis[i].id || '').replace('dct',''), 10);
        lis[i].className = (n === num) ? 'active' : ('c' + n);
      }
      var f = document.getElementById('iframe');
      if (f) { f.src = 'cards.html?num=' + (num - 1); }
      window.__cardNum = num;
    };
window.PCount = {""")

# 卡片帧里带真任务点（视频）
VIDEO_CARDS = """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div class="ans-attach-online ans-insertvideo-online" id="video-1"></div>
</body></html>"""


def build_pages(base):
    pages = {'text': TEXT_PAGE, 'multi': MULTI_CARD_PAGE, 'video': TEXT_PAGE}
    for name, html in pages.items():
        with open(os.path.join(base, name + '.html'), 'w', encoding='utf-8') as f:
            f.write(html)
    with open(os.path.join(base, 'cards.html'), 'w', encoding='utf-8') as f:
        f.write(CARDS)
    # 播放器还没插进 DOM 的卡片帧：没正文、没媒体
    with open(os.path.join(base, 'empty_cards.html'), 'w', encoding='utf-8') as f:
        f.write(EMPTY_CARDS)
    with open(os.path.join(base, 'video_cards.html'), 'w', encoding='utf-8') as f:
        f.write(VIDEO_CARDS)
    # 视频页：卡片帧换成带任务点的
    with open(os.path.join(base, 'video.html'), 'w', encoding='utf-8') as f:
        f.write(TEXT_PAGE.replace('src="cards.html"', 'src="video_cards.html"'))
    # 慢加载页：卡片帧延迟 1.8 秒才返回
    with open(os.path.join(base, 'slow.html'), 'w', encoding='utf-8') as f:
        f.write(TEXT_PAGE.replace('src="cards.html"', 'src="empty_cards.html"'))
    return pages


def build_long_page(base, lines=12000):
    body = '<br>'.join('这是第 %d 行正文内容，用于模拟超长章节页面。' % i for i in range(lines))
    html = TEXT_PAGE.replace('<body>', '<body><div class="ans-cc">' + body + '</div>')
    with open(os.path.join(base, 'long.html'), 'w', encoding='utf-8') as f:
        f.write(html)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # /slow* 故意延迟：模拟「iframe 还在加载、播放器还没插进 DOM」的瞬间。
        # 这一刻页面看起来「什么都没有」，绝不能被判成纯文本。
        if self.path.startswith('/slow'):
            time.sleep(1.8)
        if '?' in self.path:
            self.path = self.path.split('?')[0]
        return super().do_GET()

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError):
            pass


def serve(base):
    handler = lambda *a, **kw: QuietHandler(*a, directory=base, **kw)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1]


def main():
    print('=== 纯文本章节：快速识别 + 翻页函数兜底（真实 Edge）===')
    base = tempfile.mkdtemp(prefix='xxt_text_pages_')
    build_pages(base)
    build_long_page(base)
    httpd, root = serve(base)

    driver = None
    try:
        driver = core.start_browser('edge', '', '1')
        check('浏览器启动成功', driver is not None)
        if not driver:
            return 1

        # 1) 单卡片纯文本页：一次 JS 就该认出来
        driver.get(root + 'text.html')
        time.sleep(1.2)
        t0 = time.perf_counter()
        is_text = core._is_text_chapter_fast(driver)
        cost = time.perf_counter() - t0
        check('单卡片纯文本页判定为文本章节', is_text is True, is_text)
        check('判定耗时 < 1 秒', cost < 1.0, '%.3fs' % cost)

        msg = core.page_message(driver)
        check("page_message 直接返回 {'文本'}", list(msg.keys()) == ['文本'], msg)

        # 2) 直接调用翻页函数：必须真的执行到平台自己的 PCount.next
        ok_force, detail = core._force_next_chapter(driver)
        time.sleep(0.4)
        calls = driver.execute_script('return window.PCount.calls')
        check('_force_next_chapter 返回成功', ok_force is True, detail)
        check('平台翻页函数被真的调用（PCount.next）', int(calls or 0) == 1, 'calls=%s' % calls)
        args = driver.execute_script('return window.PCount.last')
        check('传给翻页函数的参数来自 onclick（含 chapterId）',
              bool(args) and '1211178756' in [str(a) for a in args], args)

        # 3) 有多个卡片标签 → 绝不能当纯文本（可能有视频/ppt 任务点）
        driver.get(root + 'multi.html')
        time.sleep(1.0)
        check('多卡片章节不判为纯文本', core._is_text_chapter_fast(driver) is False)

        # 4) 卡片帧里有 ans-attach-online → 绝不能当纯文本
        driver.get(root + 'video.html')
        time.sleep(1.0)
        check('带任务点的页面不判为纯文本', core._is_text_chapter_fast(driver) is False)

        # 5) 超长正文也不会变慢（旧实现每页要 5~6 秒）
        driver.get(root + 'long.html')
        time.sleep(1.5)
        t0 = time.perf_counter()
        ok_long = core._is_text_chapter_fast(driver)
        cost_long = time.perf_counter() - t0
        check('超长正文页仍能认出是文本章节', ok_long is True)
        check('超长正文页耗时 < 1 秒', cost_long < 1.0, '%.3fs' % cost_long)

        # 6) iframe 还在加载：绝不能判成纯文本（否则会把视频/ppt 整页跳过）
        # 注意：driver.get 会等 iframe 一起加载完，所以这里改成运行时把
        # iframe 的 src 换掉，才能真的抓到「旧文档已卸载、新文档还在下载」的瞬间
        driver.get(root + 'text.html')
        time.sleep(1.0)
        driver.execute_script("document.getElementById('iframe').src='empty_cards.html'")
        time.sleep(0.2)
        check('卡片帧里没正文没媒体（播放器还没插入）时不判为纯文本',
              core._is_text_chapter_fast(driver) is False)
        time.sleep(2.2)
        driver.execute_script("document.getElementById('iframe').src='cards.html'")
        time.sleep(1.2)
        check('换成有正文的卡片帧后才判为纯文本',
              core._is_text_chapter_fast(driver) is True)

        # 7) 卡片还没渲染时，page_message 必须返回空（让上层重试），
        #    绝不能返回「文本」直接翻页 —— 那会跳过刚切出来的视频/ppt
        driver.get(root + 'text.html')
        time.sleep(1.0)
        driver.execute_script("document.getElementById('iframe').src='empty_cards.html'")
        time.sleep(0.3)
        check('卡片未渲染时 page_message 返回空（等待重试）',
              core.page_message(driver) == {}, core.page_message(driver))

        # 8) 多卡片章节：必须逐张切换，不能只处理当前激活的那一张
        driver.get(root + 'multi.html')
        time.sleep(1.2)
        core._CARD_SEEN.clear()
        core._CARD_TRIES.clear()
        seq = []
        for _ in range(4):
            if not core._advance_card(driver, 'ch1'):
                break
            seq.append(driver.execute_script('return window.__cardNum'))
        check('多卡片章节按顺序切到第 2、3 张', seq == [2, 3], seq)
        check('全部卡片处理完后返回 False（可以翻页）',
              core._advance_card(driver, 'ch1') is False)

        # 单卡片章节不应该被「切卡片」逻辑拦住
        driver.get(root + 'text.html')
        time.sleep(1.0)
        core._CARD_SEEN.clear()
        core._CARD_TRIES.clear()
        check('单卡片章节直接返回 False（正常翻页）',
              core._advance_card(driver, 'single') is False)

        # 9) 没有「下一节」元素时不能崩
        driver.get(root + 'cards.html')
        time.sleep(0.6)
        ok_none, why = core._force_next_chapter(driver)
        check('没有翻页元素时返回 False 且不抛异常', ok_none is False, why)
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
