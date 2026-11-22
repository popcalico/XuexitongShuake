# -*- coding: utf-8 -*-
"""任务04 浏览器安全参数回归（2026-11-16）纯桩测试。

不启动浏览器。验证 build_argument_list 的输出、main.py 不再硬编码
--disable-web-security，以及参数审查表落盘。

用法：C:/Python315/python.exe tests/test_browser_security_options.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_browser_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import runtime, browser_options as bo

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 任务04 浏览器安全参数 ===')
    runtime.reset()

    # 1) 默认参数不含 --disable-web-security
    args = bo.build_argument_list(speed='1')
    check('默认不含 --disable-web-security',
          '--disable-web-security' not in args, args)
    check('保留 AutomationControlled 反爬参数',
          '--disable-blink-features=AutomationControlled' in args, args)

    # 2) 倍速/答题扩展都加载时 --enable-extensions 只出现一次
    args = bo.build_argument_list(speed='2')
    check('--enable-extensions 去重', args.count('--enable-extensions') == 1, args)

    # 3) 显式开关开启才加入（真机验证后的逃生口）
    runtime.configure({'browser_disable_web_security': 1})
    args_on = bo.build_argument_list(speed='1')
    check('显式开启含 --disable-web-security',
          '--disable-web-security' in args_on, args_on)
    runtime.reset()
    args_off = bo.build_argument_list(speed='1')
    check('reset 后默认关闭',
          '--disable-web-security' not in args_off, args_off)

    # 4) main.py 源码不再硬编码 --disable-web-security
    main_src = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    check('main.py 无硬编码 disable-web-security 参数',
          'add_argument("--disable-web-security")' not in main_src
          and "add_argument('--disable-web-security')" not in main_src)
    check('main.py 调用集中生成器',
          'browser_options.build_argument_list' in main_src)

    # 5) 参数审查表存在且逐项说明
    review = os.path.join(ROOT, 'docs', '浏览器安全参数审查表.md')
    check('参数审查表已落盘', os.path.exists(review))
    if os.path.exists(review):
        txt = open(review, encoding='utf-8').read()
        for kw in ('disable-web-security', 'enable-extensions',
                   'AutomationControlled', '风险', '处理结果'):
            check('审查表含关键词 %s' % kw, kw in txt)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
