# Copyright (c) 2025 Mortal004
# All rights reserved.
# This software is provided for non-commercial use only.
# For more information, see the LICENSE file in the root directory of this project.

import os
import time
import traceback

from core import paths


def send_error(txt):
    """把错误堆栈追加写入用户数据目录下的 error.log。

    不能用 logging.basicConfig —— 打包后 start.py 在导入阶段已经配置过 root logger，
    basicConfig 变成空操作，error.log 永远不生成。这里直接写文件，
    并且用追加模式，避免多账号并发时互相覆盖。
    路径必须用 paths.root_data()：打包后 exe 旁边可能是只读目录。
    """
    try:
        path = paths.root_data('error.log')
        os.makedirs(paths.DATA_ROOT, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write('%s - ERROR\n%s\n\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), txt))
    except Exception:
        pass
    # 远程通知：所有走 send_error 的异常都推一条（脱敏 + 截断由 notifier 负责）
    # 不重试、不阻塞；推送失败绝不能影响本地写日志或学习主流程
    try:
        from core import digest
        from core.notifier import Notifier, format_message
        n = Notifier.instance()
        if not n.url:
            # 子进程可能还没来得及 configure，从 account_info.json 兜底装载一次
            try:
                import json
                with open(paths.data('tool', 'account_info.json'), 'r', encoding='utf-8') as f:
                    n.configure(json.load(f))
            except Exception:
                pass
        lines = [ln.strip() for ln in (txt or '').splitlines() if ln.strip()]
        first = lines[0] if lines else '未知错误'
        count = n.error_count(first[:80])
        d = digest.build('error')
        head = '运行错误 · 今日第%d次 · %s' % (count, first[:40])
        detail_lines = ['未识别页：%d ｜ 当日错误：%d' % (d['unknown_pages'], d['errors']),
                        '完整堆栈已写入 error.log']
        n.notify('error', format_message(head, detail_lines),
                 title='异常告警 · 运行错误')
    except Exception:
        pass


if __name__ == '__main__':
    try:
        你好
    except Exception:
        send_error(traceback.format_exc())
