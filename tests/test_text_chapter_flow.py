"""守卫：纯文本章节必须被认出来，且「在学习页就绝不回课程页」。

背景（2026-11-22 用户现场，导出的 1122 日志 + 真实 DOM 快照）：
  00:47:50 该页面无法识别（第 1 次）    chapterId=1211178773
  00:48:14 该页面无法识别（第 3 次）    chapterId=1211178886   ← 一直在正常翻页
  ...
  00:49:45 连续 10 页都无法识别，结束本课程  ← 其实每一页都在往前翻
  00:49:51 已进入「我学的课」列表            ← 其实还停在学习页
  00:57:04 回不到课程页，结束本课程

根因：这些页是**单卡片纯文本章节**（DOM 里只有 <li id="dct1">，正文由
uParse(".ans-cc") 渲染，没有任何 ans-attach-online），旧代码一个选择器都匹配不上，
就累加「无法识别」，凑满 10 页判死。

用户原话：「为什么要回课程页，就是就是在学习页面，但是它无法继续学习了，
即使无法识别也应该下一节才对啊」
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> %s' % detail) if detail else ''))
    if not ok:
        FAILS.append(name)


def body_of(src, func):
    """截出顶层函数的函数体。"""
    start = src.index('def %s(' % func)
    m = re.search(r'\ndef ', src[start + 5:])
    return src[start:start + 5 + m.start()] if m else src[start:]


def main():
    src = open('main.py', encoding='utf-8').read()

    print('=== 1. 纯文本章节：快速识别必须在扫选择器之前 ===')
    pm = body_of(src, 'page_message')
    check('page_message 调用了 _is_text_chapter_fast',
          '_is_text_chapter_fast(driver)' in pm)
    check('快速判定在逐帧扫描之前',
          pm.index('_is_text_chapter_fast(driver)') < pm.index('_scan_content_types('))
    check('识别成「文本」而不是空字典', "{'文本': [True]}" in pm)
    check('未渲染时返回空让上层重试', '_page_rendered(driver)' in pm
          and 'return {}' in pm)
    check('渲染就绪判断也在扫选择器之前',
          pm.index('_page_rendered(driver)') < pm.index('_scan_content_types('))
    check('有 _page_rendered 函数', 'def _page_rendered(' in src)

    print()
    print('=== 2. 快速判定必须保守：不许吞掉真任务点 ===')
    q = body_of(src, '_is_text_chapter_fast')
    check('检查 ans-attach-online（有任务点就不算文本）',
          "info.get('job')" in q and '> 0' in q)
    check('检查卡片数（多 tab 章节不算文本）',
          "info.get('cards')" in q and '> 1' in q)
    check('要求有章节标题栏', "info.get('title')" in q)
    js = src.split('_QUICK_TEXT_JS = r"""')[1].split('"""')[0]
    check('JS 里查了 ans-attach-online', 'ans-attach-online' in js)
    check('JS 里查了 prev_ul / dct 卡片', 'prev_ul' in js and 'dct' in js)
    check('JS 递归了 iframe contentDocument', 'contentDocument' in js)
    check('要求卡片帧里有正文（没渲染完就不判文本）',
          "info.get('text')" in q and '< 30' in q)
    check('要求页面没有播放器/附件元素', "info.get('media')" in q and '> 0' in q)
    # iframe 不能算「媒体」：学习页顶层天生就有 #iframe，算进去纯文本页永远认不出
    media_sel = js[js.index('out.media = doc.querySelectorAll('):]
    media_sel = media_sel[:media_sel.index(').length')]
    check('media 判据里不含 iframe', 'iframe' not in media_sel, media_sel[:80])

    print()
    print('=== 3. 点不到时先直接调平台翻页函数，不许立刻判卡死 ===')
    cn = body_of(src, 'click_next_page')
    check('click_next_page 调用 _force_next_chapter',
          '_force_next_chapter(driver)' in cn)
    check('先 force 再累加 error_num',
          cn.index('_force_next_chapter(driver)') < cn.index('error_num += 1'))
    force = body_of(src, '_force_next_chapter')
    check('_force_next_chapter 用 execute_script', 'execute_script' in force)
    fjs = src.split('_FORCE_NEXT_JS = r"""')[1].split('"""')[0]
    check('JS 取 prevNextFocusNext 的 onclick', 'prevNextFocusNext' in fjs and 'onclick' in fjs)
    check('JS 直接执行 onclick 里的代码', 'new Function' in fjs)

    print()
    print('=== 4. 同一页认不出：先 force，force 不动才算卡住 ===')
    _start = src.index('if unknown_same >= UNKNOWN_SKIP_LIMIT')
    unknown_block = src[_start:src.index('跳过该页，直接点下一节', _start)]
    check('同页上限分支里有 _force_next_chapter',
          '_force_next_chapter(driver)' in unknown_block)
    check('force 成功就 continue，不 return lost', 'continue' in unknown_block)
    check('只有 force 失败才 return lost', unknown_block.count("return 'lost'") == 1)

    print()
    print('=== 5. 在学习页上绝不回课程页 ===')
    check('有 _is_study_page', 'def _is_study_page(' in src)
    sp = body_of(src, '_is_study_page')
    check('判据含 studentstudy', 'studentstudy' in sp)
    _s = src.index("while status == 'lost' and retry < 3")
    # 注意：'stay_retry += 1' 里也含 'retry += 1'，必须带缩进精确匹配
    lost_block = src[_s:src.index('\n                retry += 1')]
    check('lost 分支先判「还在学习页」', '_is_study_page(driver)' in lost_block)
    check('学习页上直接 continue 继续学', 'continue' in lost_block)
    # 真正的判据：在「调 _reenter_course 回课程页」之前，必须先判过 _is_study_page
    _s = src.index("while status == 'lost'")
    _e = src.index('_reenter_course(driver', _s)
    check('学习页判断在回课程页之前',
          '_is_study_page(driver)' in src[_s:_e])

    print()
    print('=== 6. 章节内多卡片：必须逐张处理，不能只处理当前激活的那张 ===')
    check('有 _advance_card', 'def _advance_card(' in src)
    adv = body_of(src, '_advance_card')
    check('_advance_card 用 active 标记当前卡', "item.get('active')" in adv)
    check('每张卡片最多试 2 次（防死循环）', 'tried >= 2' in adv)
    info_fn = body_of(src, '_cards_info')
    check('单卡片章节直接返回 None（不切卡）', 'len(items) <= 1' in info_fn)
    check('状态只保留最近 3 个章节', 'len(_CARD_SEEN) >= 3' in adv)
    cjs = src.split('_CARDS_JS = r"""')[1].split('"""')[0]
    check('卡片 JS 用 .prev_ul > li[id^=dct]', 'prev_ul > li[id^="dct"]' in cjs)
    swjs = src.split('_SWITCH_CARD_JS = r"""')[1].split('"""')[0]
    check('切卡片执行的是 onclick', 'onclick' in swjs and 'new Function' in swjs)
    # run() 里必须在 dispatch 之后调用，且不在 repeat_page 时切
    check('run() 调用了 _advance_card', '_advance_card(driver, identity)' in src)
    check('repeat_page 时不重复切卡', 'not repeat_page and _advance_card' in src)

    print()
    print('=== 7. 真机冒烟脚本存在（真实浏览器验证过） ===')
    check('有 tests/smoke_text_chapter_live.py',
          os.path.exists(os.path.join('tests', 'smoke_text_chapter_live.py')))

    print()
    if FAILS:
        print('FAILED: %s' % FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
