# -*- coding: utf-8 -*-
"""签到结果解析回归（2026-11-19 审计 P0-10）。

核心：任何含否定词的文案都不得判成功。旧实现 `"成功" in b` 会把
「签到已结束，本次未成功」「操作不成功」判成功并永久 mark_signed。

纯函数测试，不联网。

用法：C:/Python315/python.exe tests/test_sign_result_parse.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_signparse_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from task.xxt.sign import SignResult

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 签到结果解析 ===')

    for body in ('success', '签到成功', '签到成功！', '{"msg":"签到成功"}'):
        r = SignResult.parse(body)
        check('成功文案判成功: %s' % body, r.success is True, (r.success, r.text))

    for body in ('签到已结束，本次未成功', '操作不成功，请重试', '签到失败',
                 '未签到，请重新签到', 'error: invalid request', 'ERROR'):
        r = SignResult.parse(body)
        check('否定文案不判成功: %s' % body, r.success is False, (r.success, r.text))

    r = SignResult.parse('签到已结束，本次未成功')
    check('「已结束/未成功」归为已截止（不重试也不误报）',
          r.ended is True and r.success is False, (r.ended, r.success))

    r = SignResult.parse('您已签到过了')
    check('已签到 -> already', r.already is True and r.success is False, r.already)

    r = SignResult.parse('服务器已锁定该签到, 请约 1 小时后再试')
    check('锁定 -> locked', r.locked is True, r.locked)

    r = SignResult.parse('距离签到地点 1234 米')
    check('距离文案不算成功', r.success is False and r.distance == 1234.0,
          (r.success, r.distance))

    r = SignResult.parse('完全无法识别的返回')
    check('未知文案不判成功', r.success is False, r.success)

    print()
    if FAILS:
        print('FAILED: %d %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
