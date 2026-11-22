# -*- coding: utf-8 -*-
"""真实渲染抓图：主页 / 设置页（宽）/ 设置页（窄），用于人工核对布局。

会短暂弹出一个真实窗口（约 6~10 秒），截完自动关闭。窗口是 -topmost，
不想被挡就先别切别的窗口。

用法：
    C:/Python315/python.exe tests/capture_ui.py
输出：dist/ui_*.png
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_ui_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

OUT = os.path.join(ROOT, 'dist')
os.makedirs(OUT, exist_ok=True)


def grab(root, name):
    """截窗口区域；失败返回 None。"""
    from PIL import ImageGrab
    root.update_idletasks()
    root.update()
    time.sleep(0.45)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    path = os.path.join(OUT, name)
    img.save(path)
    print('已保存 %s  (%dx%d)' % (path, w, h))
    return path


def main():
    import start as app_mod
    # 不要在这个脚本里联网检查更新（会弹窗打断抓图）
    app_mod.Start.check_update = lambda self, *a, **k: None

    app = app_mod.Start()
    root = app.root
    root.resizable(True, True)

    try:
        root.geometry('1280x800+40+40')
        grab(root, 'ui_main.png')

        app.show_set()
        grab(root, 'ui_settings_wide.png')

        root.geometry('760x900+40+40')
        grab(root, 'ui_settings_narrow.png')

        app.show_help()
        grab(root, 'ui_help.png')

        app.show_record('学习')
        grab(root, 'ui_record.png')
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    print('done')


if __name__ == '__main__':
    main()
