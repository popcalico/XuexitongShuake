# -*- coding: utf-8 -*-
"""守卫：「找下一页」不许再退回 56 秒空等 / 长文本页找不到按钮。

背景（2026-11-21 真机日志）：
    21:34:22 跳转下一页
    21:35:18 暂时没找到「下一页」按钮    ← 56 秒
    21:35:27 跳转下一页
    21:36:22 暂时没找到「下一页」按钮    ← 55 秒
  算术对得上：全局 implicit_wait(2) × 6 条 XPath × 5 个上下文（顶层 + 4 个 iframe）
  ≈ 60 秒。用户描述的场景是「文本非常多、下一页在最下方」，
  这种页面上 `//*[contains(normalize-space(.), "下一页")]` 会先命中一串祖先 div，
  真正的 <a> 排在第 5 个以后，而旧代码只看前 4 个 → 永远找不到。

本测试锁死修复点，任何一条被改回去都会失败。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def func_body(src, name):
    start = src.index('def %s(' % name)
    m = re.search(r'\ndef ', src[start + 10:])
    return src[start:start + 10 + m.start()] if m else src[start:]


def main():
    src = open('main.py', encoding='utf-8').read()

    print('=== 1. 兜底查找必须走 probe_mode（不许付 2 秒隐式等待）===')
    body = func_body(src, '_find_next_button')
    check('_find_next_button 包在 probe_mode 里', 'nav.probe_mode(driver)' in body)
    check('_find_next_button 不再只检查前 4 个节点', 'nodes[:4]' not in body)
    check('_find_next_button 扫描上限放宽到 40', 'nodes[:40]' in body)

    print()
    print('=== 2. 首选路径是「一次 JS 扫全帧树」===')
    check('存在 _NEXT_SEARCH_JS', '_NEXT_SEARCH_JS' in src)
    check('存在 _click_next_by_js', 'def _click_next_by_js(' in src)
    js = src.split('_NEXT_SEARCH_JS = r"""')[1].split('"""')[0]
    check('JS 用 return 把结果交回 Python（否则恒为 None）',
          js.lstrip().startswith('return (function'), js.lstrip()[:40])
    check('JS 递归 contentDocument（能钻进多层 iframe）',
          'contentDocument' in js)
    check('JS 只扫交互元素，不对所有元素求子树文本',
          'a, button' in js and 'normalize-space' not in js)
    check('JS 有候选数量上限（长文本页不 O(n²)）', 'CAP = 3000' in js)
    check('JS 优先 #prevNextFocusNext（章节导航，不是文档 pager）',
          "el.id === 'prevNextFocusNext'" in js and 'score = 5' in js)
    check('JS 同分取页面最下方（按钮常在底部）', 'return b.top - a.top' in js)
    check('JS 点击前先 scrollIntoView', 'scrollIntoView' in js)

    print()
    print('=== 3. click_next_page 先 JS 后兜底 ===')
    cbody = func_body(src, 'click_next_page')
    check('先调 _click_next_by_js', 'ok_js, detail = _click_next_by_js(driver)' in cbody)
    # 注意：_try_click() 是函数内嵌套定义，出现位置在前，不能拿它比先后。
    # 真正的顺序证据是「JS 结果赋值 -> if result == 'none' -> 才调 _try_click()」。
    check('JS 失败才走 Selenium',
          cbody.index("ok_js, detail = _click_next_by_js(driver)")
          < cbody.index("if _try_click() == 'clicked':"))
    check('找不到时打印耗时与扫描面', '耗时 %.1fs' in cbody and '%s' in cbody)
    check('慢路径（>2s）单独警告', '[耗时] 找「下一页」' in cbody)
    check('找不到按钮仍不判课程完结（返回 True 继续等）',
          cbody.index("result == 'none'") < cbody.index('return True'))
    check('放弃时打 _NEXT_GAVE_UP 标记', '_NEXT_GAVE_UP = True' in cbody)

    print()
    print('=== 4. 死亡分支已清理（避免测试给死代码背书）===')
    check('不再有永远走不到的 finished 分支', "result == 'finished'" not in cbody)
    check('「撒花」死代码已删', '🎉 🎉 该课程全部已完结' not in src)

    print()
    if FAILS:
        print('FAILED=%d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
