# -*- coding: utf-8 -*-
"""视频「点了但没播」的恢复逻辑回归（2026-11-19）。

真机事故：播放器 DOM 里
    <video preload="none">  +  <span class="vjs-duration-display">0:00</span>
    <button class="vjs-play-control vjs-control vjs-button">播放</button>
即视频**从未开始加载**，而控制条上**没有 `vjs-paused` 类**。
旧代码只按 `[class*="vjs-paused"]` 找播放按钮 → 永远点不到 → 整页没有一次
成功手势 → `play()` 被自动播放策略拒绝 → 一直 0:00，8 次后抛「视频播放异常」。

本测试锁死新的逐级降级链（真实点击 → play() → 静音 play() → JS 点按钮），
并禁止源码再退回只认 `vjs-paused` 的写法。

用法：C:/Python315/python.exe tests/test_video_force_play.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_forceplay_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task import watch_vido as wv

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Node:
    def __init__(self, driver):
        self.d = driver

    def click(self):
        self.d.clicked = True
        if self.d.click_works:
            self.d.paused = False


class Switch:
    def default_content(self):
        pass

    def frame(self, x):
        pass


class PlayerDriver:
    """模拟播放器：按脚本内容区分「读状态 / play / 点击按钮」。"""

    def __init__(self, click_works=False, js_play_works=False, mute_play_works=False,
                 js_click_works=False, no_button=False):
        self.paused = True
        self.clicked = False
        self.click_works = click_works
        self.js_play_works = js_play_works
        self.mute_play_works = mute_play_works
        self.js_click_works = js_click_works
        self.no_button = no_button
        self.switch_to = Switch()

    def find_element(self, by, value):
        if self.no_button:
            raise RuntimeError('no element')
        return Node(self)

    def execute_script(self, script, *args):
        s = script
        if 'return window.__xxt_play_err' in s:
            return ''
        if "document.querySelector('video');if(!v){return {video:false};}" in s:
            return {'video': True, 'paused': self.paused, 'muted': False,
                    'ready': 0 if self.paused else 4, 'net': 1, 'dur': 0,
                    'cur': 0, 'srcLen': 80, 'preload': 'none' if self.paused else 'auto',
                    'err': 0}
        if "n.click(); return true" in s:
            if self.js_click_works:
                self.paused = False
                return True
            return True
        if 'window.__xxt_play_err' in s and 'v.play()' in s:
            if 'v.muted=true' in s or 'v.muted = true' in s:
                if self.mute_play_works:
                    self.paused = False
            elif self.js_play_works:
                self.paused = False
            return None
        if 'play()' in s:
            if self.js_play_works:
                self.paused = False
            return None
        return None


def main():
    print('=== 视频播放恢复（_force_play）===')
    _sleep = wv.time.sleep
    wv.time.sleep = lambda *a, **k: None      # 测试不需要真等

    try:
        # 1) 真实点击就能播
        d = PlayerDriver(click_works=True)
        ok, detail = wv._force_play(d)
        check('点播放按钮后能播', ok and not d.paused, detail)

        # 2) 点击无效，JS play() 有效
        d = PlayerDriver(js_play_works=True)
        ok, detail = wv._force_play(d)
        check('点击无效时 JS play() 兜底', ok, detail)

        # 3) 前两步都无效，静音 play() 生效（浏览器策略只放行静音自动播放）
        d = PlayerDriver(mute_play_works=True)
        ok, detail = wv._force_play(d)
        check('静音 play() 作为第三级降级', ok, detail)

        # 4) 全部无效：返回 False，且诊断里带上播放器真实状态
        d = PlayerDriver()
        ok, detail = wv._force_play(d)
        check('全都无效时返回 False', ok is False, detail)
        check('诊断里带 preload/ready 等现场状态',
              'preload=' in detail and 'ready=' in detail, detail)

        # 5) 连播放按钮都不存在时，不能崩
        d = PlayerDriver(no_button=True, js_play_works=True)
        ok, detail = wv._force_play(d)
        check('播放按钮不存在也能靠 play() 起播', ok, detail)

        # 5b) 真机 21:40 事故回归：视频本来就在播，程序不能去点播放按钮
        #     （vjs 播放键是 toggle，点一下 = 暂停，于是「正常却一直报暂停」）
        d = PlayerDriver()
        d.paused = False
        ok, detail = wv._force_play(d)
        check('已在播放时 _force_play 直接返回、不点按钮',
              ok is True and d.clicked is False, detail)

        wv.b = 0
        wv.pause_start_time = 0
        d2 = PlayerDriver()
        d2.paused = False
        wv.check_vido_play(d2, 0, 5.0, '1', False, None, paused=False)
        check('paused=False 时 check_vido_play 不碰播放按钮',
              d2.clicked is False, d2.clicked)
        check('paused=False 时不会累积暂停计数', wv.b == 0, wv.b)

        # 6) 源码红线：不许再依赖 vjs-paused（注释里说明历史可以，代码里不行）
        src = open(os.path.join('task', 'watch_vido.py'), encoding='utf-8').read()
        code_lines = [line.split('#')[0] for line in src.splitlines()]
        offenders = [line.strip() for line in code_lines if 'vjs-paused' in line]
        check('代码里不再按 vjs-paused 找播放按钮', not offenders, offenders[:2])
        check('恢复播放走 _force_play', src.count('_force_play(') >= 3, src.count('_force_play('))
        check('播放启动后会打印是否成功', "'播放已启动（%s）'" in src or '播放已启动' in src)

        # 7) 时长读不到时不能只给 600 秒（13 分钟的视频会被判失败）
        check('时长未知时预算提高到 1800 秒以上', 'max(budget, 1800)' in src)
    finally:
        wv.time.sleep = _sleep

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
