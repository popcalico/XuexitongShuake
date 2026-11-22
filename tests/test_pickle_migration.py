# -*- coding: utf-8 -*-
"""任务06 pickle 安全与 JSON 迁移回归（2026-11-16）纯桩测试。

不联网、不开浏览器。覆盖：恶意全局对象拦截、格式错误、缺字段、
历史缓存迁移、迁移回退、Cookie 迁移。

用法：C:/Python315/python.exe tests/test_pickle_migration.py
"""
import json
import os
import pickle
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['XXT_HOME'] = tempfile.mkdtemp(prefix='xxt_pickle_')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import safe_pickle as sp
from core import paths

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


class Evil:
    """恶意 pickle：反序列化时执行 __reduce__ 指向的函数。"""
    def __reduce__(self):
        return (eval, ("open(r'{}/PWNED','w').write('x')".format(
            os.environ['XXT_HOME']),))


def main():
    print('=== 任务06 pickle 安全 ===')

    # 1) 恶意全局对象被拦截且不执行
    evil = pickle.dumps(Evil())
    try:
        sp.safe_load(evil)
        blocked = False
    except sp.UnsafePickleError as e:
        blocked = 'eval' in str(e) or '全局对象' in str(e)
    check('恶意 __reduce__ 被拦截', blocked)
    check('恶意代码未执行', not os.path.exists(os.path.join(os.environ['XXT_HOME'], 'PWNED')))

    # 2) 正常数据结构允许
    good = {'value': ['A', 'B'], 'expire': 0}
    check('正常 dict 通过', sp.safe_load(pickle.dumps(good)) == good)
    check('正常 list 通过', sp.safe_load(pickle.dumps([1, 2, 3])) == [1, 2, 3])

    # 3) 损坏 pickle
    try:
        sp.safe_load(b'not a pickle')
        bad_blocked = False
    except Exception:
        bad_blocked = True
    check('损坏 pickle 抛错', bad_blocked)

    d = tempfile.mkdtemp(prefix='mig_')
    jp = os.path.join(d, 'cache.json')
    pp = os.path.join(d, 'cache.pkl')

    # 4) 历史缓存迁移：旧 pkl -> JSON，备份保留，回读一致
    with open(pp, 'wb') as f:
        pickle.dump(good, f)
    val, src = sp.load_json_or_pickle(
        jp, pp, validator=lambda v: isinstance(v, dict) and 'value' in v)
    check('历史缓存迁移返回原值', val == good and src == 'migrated', src)
    check('迁移后 JSON 存在', os.path.exists(jp))
    check('迁移前 pkl 备份保留', os.path.exists(pp + '.prejson'))
    check('JSON 带格式版本', json.load(open(jp, encoding='utf-8')).get('_fmt') == 1)

    # 5) 二次读取走 JSON，不再碰 pkl
    val2, src2 = sp.load_json_or_pickle(jp, pp, validator=lambda v: isinstance(v, dict))
    check('二次读取走 JSON', val2 == good and src2 == 'json', src2)

    # 6) 缺字段 / 结构非法 -> 拒绝迁移，旧文件保留
    d2 = tempfile.mkdtemp(prefix='bad_')
    jp2, pp2 = os.path.join(d2, 'c.json'), os.path.join(d2, 'c.pkl')
    with open(pp2, 'wb') as f:
        pickle.dump({'wrong': 'shape'}, f)
    try:
        sp.load_json_or_pickle(jp2, pp2,
                               validator=lambda v: isinstance(v, dict) and 'value' in v)
        rejected = False
    except sp.UnsafePickleError:
        rejected = True
    check('结构非法拒绝迁移', rejected)
    check('拒绝迁移时不写 JSON', not os.path.exists(jp2))
    check('拒绝迁移时旧文件保留', os.path.exists(pp2))

    # 7) Cookie 迁移：list[dict] 完整字段保留（含 httpOnly/secure/expiry）
    d3 = tempfile.mkdtemp(prefix='cookie_')
    cookies = [{'name': 'sid', 'value': 'abc', 'domain': '.chaoxing.com',
                'path': '/', 'secure': True, 'httpOnly': True, 'expiry': 123456}]
    pp3, jp3 = os.path.join(d3, 'cookies.pkl'), os.path.join(d3, 'cookies.json')
    with open(pp3, 'wb') as f:
        pickle.dump(cookies, f)
    got, _ = sp.load_json_or_pickle(jp3, pp3, validator=lambda v: isinstance(v, list))
    check('Cookie 全字段迁移保留', got == cookies, got)

    # 8) 源码级：没有普通 pickle.load 入口（只允许 safe_pickle.safe_load）
    for rel in ('task/tool/ai_wen_da.py', 'task/tool/face.py', 'start.py'):
        src = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        # 允许 import pickle 与注释，但不允许 pickle.load( / pickle.dump(
        check('%s 无普通 pickle.load' % rel,
              'pickle.load(' not in src and 'pickle.dump(' not in src)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
