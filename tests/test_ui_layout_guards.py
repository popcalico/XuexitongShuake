# -*- coding: utf-8 -*-
"""守卫：UI 不许再退回「固定两列 + 行权重错行 + 按钮留空列」。

背景（2026-11-21 用户反馈）：
  · 设置页卡片里一大片空白、「窗口置顶」开关飘在半空
  · 窗口一窄右列直接被挤出可视区，只剩左半边
  · 运行中工具栏右侧空出一整列，按钮之间一条大缝
  · 各页面文本框固定宽高，窗口拉大也不跟着长

根因分别写在下面每条断言旁边。这些改动很容易在后续「顺手重构」时被改回去，
所以用静态扫描钉住。
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


def code_only(src):
    """去掉注释行，避免「文档里写了 xxx」被当成「代码里还在用 xxx」。"""
    return '\n'.join(line for line in src.splitlines()
                     if not line.lstrip().startswith('#'))


def main():
    start = open('start.py', encoding='utf-8').read()
    raw_panel = open(os.path.join('gui', 'setting_panel.py'), encoding='utf-8').read()
    panel = code_only(raw_panel)

    print('=== 1. 设置页：不许再出现「把某一行拉满」的权重 ===')
    # ui.rowconfigure(3, weight=1) 会让「窗口置顶」那一行吃掉所有多余高度
    check('界面设置卡片不再给第 3 行加权重', 'ui.rowconfigure(3, weight=1)' not in panel)
    check('页面根不再给保存按钮行加权重', 'self.parent.rowconfigure(3, weight=1)' not in panel)

    print()
    print('=== 2. 设置页：宽度不够要自动切单列 ===')
    check('有响应式阈值', 'LAYOUT_2COL_MIN' in panel and 'LAYOUT_1COL_MAX' in panel)
    check('有 _on_parent_resize', 'def _on_parent_resize(' in panel)
    check('有 _apply_layout', 'def _apply_layout(' in panel)
    check('绑定了 <Configure>', "self.parent.bind('<Configure>'" in panel)
    check('左右两列是独立容器（不再用 rowspan 把左列挤空）',
          'self._left = ctk.CTkFrame(' in panel and 'self._right = ctk.CTkFrame(' in panel
          and 'rowspan=2' not in panel)
    check('单列时两列上下叠放', "self._right.grid(row=1, column=0" in panel)
    check('保存按钮跟着换行', 'self.save_button.grid_configure(' in panel)
    check('回差 > 0（防抖动）',
          int(re.search(r'LAYOUT_2COL_MIN = (\d+)', panel).group(1))
          < int(re.search(r'LAYOUT_1COL_MAX = (\d+)', panel).group(1)))

    print()
    print('=== 3. 工具栏：按可见按钮均分，不留空列 ===')
    check('有 _sync_toolbar', 'def _sync_toolbar(' in start)
    check('_sync_toolbar 按可见按钮重排', "winfo_manager() == 'grid'" in start)
    check('_sync_toolbar 会改列权重', 'columnconfigure(' in start
          and 'len(visible)' in start)
    check('_sync_toolbar 会改日志区跨列', 'text_box_frame.grid_configure(columnspan=' in start)
    check('运行态/空闲态都调用了重排', start.count('self._sync_toolbar()') >= 4,
          start.count('self._sync_toolbar()'))
    check('按钮全部 sticky=ew（不再居中留缝）',
          start.count('padx=5, pady=5, sticky="ew")') >= 4)

    print()
    print('=== 4. 各页文本框要在自己那一行长高 ===')
    check('学习记录页权重给第 2 行（文本框）',
          'vido_frame.rowconfigure(2, weight=1)' in start)
    check('成绩页权重给第 1 行（文本框），不是第 0 行',
          'score_frame.rowconfigure(1, weight=1)' in start
          and 'score_frame.rowconfigure(0, weight=1)' not in start)
    check('题库页文本框占满', 'question_bank_frame.rowconfigure(1, weight=1)' in start)
    check('帮助页文本框占满', 'help_frame.rowconfigure(0, weight=1)' in start)
    check('报错日志页文本框占满', 'error_frame.rowconfigure(0, weight=1)' in start)

    print()
    print('=== 5. 窗口：下限跟屏幕走，且记住用户拉的大小 ===')
    check('minsize 按屏幕比例算（不再写死 860x560）',
          'self.root.minsize(' in start and '_sw * 0.50' in start
          and '860, 560' not in start)
    check('下限（820）明显小于双列阈值（900），否则永远拉不到单列',
          'min(820' in start and 820 < 900)
    check('geometry 用的是屏幕 3/4 而不是控件最小需求',
          'scr_w * 0.74' in start and 'scr_h * 0.80' in start)
    check('窗口不许超出屏幕', 'scr_w - 16' in start and 'scr_h - 56' in start)
    check('有 _load/_save_window_geometry',
          'def _load_window_geometry(' in start and 'def _save_window_geometry(' in start)
    check('关闭时保存尺寸', '_save_window_geometry()' in start)

    print()
    print('=== 6. 窗口尺寸真的存得下、读得回（不依赖 GUI）===')
    import tempfile
    os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_ui_geo_')
    import start as app_mod

    class FakeRoot:
        def winfo_width(self):
            return 1280

        def winfo_height(self):
            return 816

    app = app_mod.Start.__new__(app_mod.Start)
    app.root = FakeRoot()
    app._save_window_geometry()
    check('尺寸写进了文件', os.path.exists(app._window_state_path()),
          app._window_state_path())
    check('读回来是同一组宽高', app._load_window_geometry() == (1280, 816),
          app._load_window_geometry())

    print()
    if FAILS:
        print('FAILED=%d %s' % (len(FAILS), FAILS))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
