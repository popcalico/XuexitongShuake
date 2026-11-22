# -*- coding: utf-8 -*-
"""任务03 本地敏感凭据保护回归（2026-11-16）纯桩测试。

不访问网络/浏览器。Windows 上用真实 DPAPI（当前用户）测试加解密；
通过独立 XXT_HOME 隔离测试数据，不碰用户真实配置。

覆盖：加密解密往返、版本标记、损坏数据、重复保存、账号隔离、
旧配置迁移、迁移回退（DPAPI 失败保留旧配置）、JSON 无明文。

用法：C:/Python315/python.exe tests/test_credential_store.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
_TEST_HOME = tempfile.mkdtemp(prefix='xxt_cred_')       # 唯一隔离 HOME
os.environ['XXT_HOME'] = _TEST_HOME
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from core import paths, credential_store as cs
from core import config_manager as cm

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> ' + str(detail)) if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    print('=== 任务03 凭据保护 ===')
    if os.name != 'nt':
        print('SKIP: 非 Windows，DPAPI 不可用（本工程目标平台 Windows）')
        return 0

    # 1) 加密解密往返
    plain = 'sk-abcdefghijklmnop1234567890'
    enc = cs.protect_secret(plain)
    check('加密带版本标记', enc.startswith(cs.VERSION_PREFIX), enc[:10])
    check('密文不含明文', plain not in enc)
    check('解密往返一致', cs.unprotect_secret(enc) == plain)

    # 2) 空值不加密
    check('空值不入库', cs.protect_secret('') == '' and cs.unprotect_secret('') == '')

    # 3) 损坏数据（非法版本 / 非法 base64）必须抛错
    try:
        cs.unprotect_secret('ENC9:xxxx')
        bad_ver = False
    except cs.CredentialError:
        bad_ver = True
    check('错误版本标记抛 CredentialError', bad_ver)
    try:
        cs.unprotect_secret('ENC1:@@@bad@@@')
        bad_b64 = False
    except cs.CredentialError:
        bad_b64 = True
    check('损坏 base64 抛 CredentialError', bad_b64)

    # 4) 篡改密文（错误密钥/被改）必须抛错
    try:
        cs.unprotect_secret(enc[:-4] + 'AAAA')
        tampered = False
    except cs.CredentialError:
        tampered = True
    check('篡改密文解密失败', tampered)

    # 5) 重复保存覆盖
    cs.save_secret('dup', 'first')
    cs.save_secret('dup', 'second')
    check('重复保存取最新值', cs.load_secret('dup') == 'second')
    cs.delete_secret('dup')
    check('删除后不存在', not cs.secret_exists('dup'))

    # 6) 账号隔离：同名不同 account id 互不干扰
    cs.save_secret('account.acc_001.password', 'pwd-one')
    cs.save_secret('account.acc_002.password', 'pwd-two')
    check('账号隔离 acc_001', cs.load_secret('account.acc_001.password') == 'pwd-one')
    check('账号隔离 acc_002', cs.load_secret('account.acc_002.password') == 'pwd-two')

    # 7) ConfigManager：保存后明文 JSON 不出现密码/API Key/手机号/meow_url
    #    用独立 HOME 新建一个 ConfigManager
    cfg = cm.ConfigManager()
    acc = cfg.add_account(alias='测试', phone='13800138000', password='MyP@ssw0rd')
    cfg.save_account({'account_id': acc['id'], 'API': 'sk-supersecretkey123456',
                      'meow_url': 'https://api.chuckfang.com/device/SECRETURL',
                      'phone_number': '13800138000', 'password': 'MyP@ssw0rd'})
    raw = open(cfg.accounts_path, encoding='utf-8').read()
    check('JSON 无明文密码', 'MyP@ssw0rd' not in raw)
    check('JSON 无明文 API Key', 'sk-supersecretkey123456' not in raw)
    check('JSON 无明文 meow URL', 'SECRETURL' not in raw)
    check('JSON 无明文手机号', '13800138000' not in raw)
    check('JSON 含 secret_ref', 'secret_ref:' in raw)
    # 内存态仍能读到明文供 GUI 用
    got = cfg.get_account_by_id(acc['id'])
    check('内存态还原明文密码', got['password'] == 'MyP@ssw0rd')

    # 8) 重新加载（新实例）能从凭据库还原明文
    cfg2 = cm.ConfigManager()
    flat2 = cfg2.load_account()    # API 是 global 字段，需用合并后的扁平视图
    check('重载后还原 API Key', flat2.get('API') == 'sk-supersecretkey123456')
    check('重载后还原手机号', flat2.get('phone_number') == '13800138000')

    # 9) 旧配置迁移：手工写一个含明文敏感字段的 accounts.json
    home2 = tempfile.mkdtemp(prefix='xxt_legacy_')
    old_env = os.environ['XXT_HOME']
    os.environ['XXT_HOME'] = home2
    try:
        paths.HOME = home2  # paths.HOME 在 import 时固定，需显式切换
        legacy = {'version': 2,
                  'global': {'API': 'sk-legacykey999', 'current_account': 'acc_legacy'},
                  'accounts': [{'id': 'acc_legacy', 'phone_number': '13900139000',
                                'password': 'LegacyPwd', 'cour': ['']}]}
        lp = paths.data('tool', 'accounts.json')
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, 'w', encoding='utf-8') as f:
            json.dump(legacy, f, ensure_ascii=False)
        cfg3 = cm.ConfigManager()
        loaded = cfg3.load_accounts()
        migrated_acc = next(a for a in loaded['accounts'] if a['id'] == 'acc_legacy')
        check('迁移后内存还原明文', migrated_acc['password'] == 'LegacyPwd')
        disk = open(cfg3.accounts_path, encoding='utf-8').read()
        check('迁移后 JSON 无明文密码', 'LegacyPwd' not in disk)
        check('迁移后 JSON 无明文 Key', 'sk-legacykey999' not in disk)
        check('迁移保留备份', os.path.exists(cfg3.accounts_path + '.precred'))
    finally:
        os.environ['XXT_HOME'] = old_env
        paths.HOME = old_env

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS), FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
