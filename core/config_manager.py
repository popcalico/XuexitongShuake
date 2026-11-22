"""多账号配置管理。

存储结构 accounts.json：
    {
      "version": 2,
      "global":   { 浏览器、驱动、API Key、字体、并发数、定时 ... },
      "accounts": [ { 手机号、密码、课程、倍速、答题策略 ... }, ... ]
    }

对外仍保留旧的扁平接口（load_account / save_account / get_account），
把「global + 当前账号」合并成一个 dict，这样 GUI 侧几乎不用改。
"""
import json
import os
import uuid

from core import paths

# 本地凭据保护（2026-11-16 任务03）：敏感字段只以引用形式落盘 JSON。
# 每条记录的 name 带作用域/账号 id，保证账号隔离。
try:
    from core import credential_store as secrets
except Exception:      # 极端情况下模块缺失也不能让配置完全不可用
    secrets = None

SECRET_REF_PREFIX = 'secret_ref:'

# (作用域, 字段名) -> 凭据名模板。global 字段不带账号；account 字段带 id。
_SECRET_FIELDS = [
    ('global', 'API', 'global.API'),          # AI API Key
    ('global', 'meow_url', 'global.meow_url'),  # MeoW 设备 URL（含 token）
    ('account', 'password', 'account.{id}.password'),
    ('account', 'phone_number', 'account.{id}.phone'),
]


GLOBAL_KEYS = {
    'browser', 'driver_path', 'API', 'font_type', 'font_size', 'user_notice',
    'max_concurrency', 'timer_active', 'timer_start', 'timer_end',
    'file_name', 'current_account',
    # MeoW 推送（2026-11-06）：设备 URL 属于凭据，绝不写进 example/仓库
    'meow_enabled', 'meow_url', 'meow_category_enabled', 'meow_events',
    # OpenAI 兼容 AI 接口（2026-11-10）：地址/模型/超时不是凭据，随全局配置走
    'ai_base_url', 'ai_model', 'ai_vision_model', 'ai_timeout',
    'browser_disable_web_security',
}

ACCOUNT_KEYS = {
    'alias', 'enabled', 'phone_number', 'password', 'cour', 'choice',
    'after_finish_question', 'video_title_choice', 'discussion_choice',
    'homework', 'task_type', 'speed', 'pass_face', 'pass_vido',
    'lock_screen', 'multi_device', 'radio_var',
    # 无法处理的题策略 / 讨论范围与作答方式（2026-11-06）
    'unanswerable_policy', 'discussion_scope', 'discussion_copy_reply',
    # 课程考试（2026-11-08）。交卷开关默认关闭，必须用户显式开启才算授权。
    'exam_answer_mode', 'exam_auto_submit', 'exam_submit_ratio',
    'exam_revoke_seconds', 'exam_min_remain_minutes', 'exam_max_questions',
    'exam_paper_total',  # 整卷题数(0=自动探测答题卡)；交卷分母必须用它，禁止用本轮遍历数
    # 2026-11-19 从上游 26.11.18 移植：复习模式 / 复习遍数 / 自动问卷。
    # 这三个键必须与 runtime.DEFAULTS 同名，否则子进程读不到（配置快照按名字装载）。
    'review', 'review_times', 'vote_enabled',
}

DEFAULT_GLOBAL = {
    'browser': '不指定浏览器',
    'driver_path': '',
    'API': '',
    'font_type': 'Microsoft YaHei UI',
    'font_size': '12',
    'user_notice': 'True',
    'max_concurrency': 2,
    'timer_active': 0,
    'timer_start': '04:00:00',
    'timer_end': '03:00:00',
    'file_name': ['python', 'main.py'],
    'current_account': '',
    # 通知默认关闭；设备 URL 留空由用户在本机填写（不要写进任何模板文件）
    'meow_enabled': 0,
    'meow_url': '',
    'meow_category_enabled': 1,
    'meow_events': {},
    # OpenAI 兼容 AI 接口：默认仍指向 DeepSeek，老配置不填新字段时行为不变
    'ai_base_url': 'https://api.deepseek.com',
    'ai_model': 'deepseek-v4-flash',
    'ai_vision_model': '',
    'ai_timeout': 0,
    'browser_disable_web_security': 0,
}

DEFAULT_ACCOUNT = {
    'alias': '',
    'enabled': 1,
    'phone_number': '',
    'password': '',
    'cour': [''],
    'choice': '',
    'after_finish_question': '',
    'video_title_choice': '',
    'discussion_choice': '',
    'homework': '',
    'task_type': '章节',
    'speed': '',
    'pass_face': 0,
    'pass_vido': 0,
    'lock_screen': 0,
    'multi_device': 0,
    'radio_var': 1,
    # 默认：AI 尽力完成（处理不了自动降级截图跳过）、任务点内全部讨论、直接 DeepSeek 不抄回复
    'unanswerable_policy': 'AI 尽力完成',
    'discussion_scope': '任务点内全部讨论',
    'discussion_copy_reply': 0,
    # 考试：只答不交卷；阈值沿用「搜到/点到的题」比例，不是正确率
    'exam_answer_mode': '题库优先+AI兜底',
    'exam_auto_submit': 0,
    'exam_submit_ratio': 100,
    'exam_revoke_seconds': 15,
    'exam_min_remain_minutes': 10,
    'exam_max_questions': 200,
    'exam_paper_total': 0,
    # 2026-11-19：复习模式默认关闭（不改变老用户行为）；遍数默认 1；
    # 问卷默认自动作答（问卷通常不计分，但漏做会一直卡在「未识别页」）。
    'review': 0,
    'review_times': 1,
    'vote_enabled': 1,
}

MAX_CONCURRENCY_LIMIT = 5


def _read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def new_account_id() -> str:
    return 'acc_' + uuid.uuid4().hex[:8]


class ConfigManager:
    def __init__(self):
        self._account_info = {}
        self._data = None
        paths.ensure_data_dirs()

    # ================= 路径（延迟求值，保证 XXT_HOME 生效后再算） =================
    @property
    def accounts_path(self) -> str:
        return paths.data('tool', 'accounts.json')

    @property
    def legacy_account_path(self) -> str:
        return paths.data('tool', 'account_info.json')

    @property
    def course_path(self) -> str:
        return paths.data('tool', 'course_name.json')

    @property
    def version_path(self) -> str:
        return paths.asset('tool', 'version_info')

    @property
    def help_path(self) -> str:
        return paths.asset('tool', 'Help.txt')

    @property
    def course_process_path(self) -> str:
        return paths.data('tool', 'course_process.json')

    # ================= 账号集合 =================
    def load_accounts(self) -> dict:
        """读取 accounts.json；不存在时从旧 account_info.json 迁移。"""
        if self._data is not None:
            return self._data

        data = _read_json(self.accounts_path, None)
        if not isinstance(data, dict) or 'accounts' not in data:
            data = self._migrate_legacy()

        # 凭据迁移（2026-11-16 任务03）：旧 JSON 里的明文敏感字段
        # 先备份原文件 -> 加密入库 -> 回读验证 -> 换成引用。
        # 注意顺序：先迁移（把明文换 ref 并落盘），最后统一 deserialize 还原内存明文；
        # 否则「还原后的明文」会被 migrate 再次当成明文写回 ref，覆盖内存值。
        if secrets and os.path.exists(self.accounts_path):
            ok, changed, note = self._migrate_secrets(data)
            # 2026-11-19 性能修复：只在「确实迁到了明文」时才备份 + 重新加密落盘。
            # 旧实现只要 ok 就无条件 _write_json(_serialize_for_disk())，而后者会
            # 把所有敏感字段重新 DPAPI 加密 + 写盘 + 调 icacls —— 于是每次启动都
            # 白做一轮（多次黑窗 + 明显启动延迟），哪怕配置压根没变。
            if ok and changed:
                try:
                    import shutil
                    shutil.copy2(self.accounts_path, self.accounts_path + '.precred')
                except OSError:
                    pass
                try:
                    _write_json(self.accounts_path, self._serialize_for_disk(data))
                except Exception:
                    pass
            elif not ok:
                print('[凭据] 旧配置迁移未完成（%s），保留原始配置' % note)
        elif secrets and data:
            # 新建/legacy 迁移出来的结构：直接把明文入库
            ok, _changed, _ = self._migrate_secrets(data)
            if not ok:
                print('[凭据] 新配置凭据入库失败，拒绝继续使用明文')
        # 最后统一把 ref 还原成内存明文，供 GUI / 子进程使用
        if secrets:
            self._deserialize_from_disk(data)

        data.setdefault('version', 2)
        data.setdefault('global', {})
        data.setdefault('accounts', [])
        for k, v in DEFAULT_GLOBAL.items():
            data['global'].setdefault(k, v)
        for acc in data['accounts']:
            for k, v in DEFAULT_ACCOUNT.items():
                acc.setdefault(k, v)
            acc.setdefault('id', new_account_id())

        if not data['accounts']:
            data['accounts'].append(self._blank_account('账号1'))

        cur = data['global'].get('current_account')
        if not cur or not any(a['id'] == cur for a in data['accounts']):
            data['global']['current_account'] = data['accounts'][0]['id']

        self._data = data
        # 首次运行（或迁移后）立即落盘，否则每次启动都会重新生成账号 id，
        # 导致 task/run/ 下堆积一堆空的账号工作目录
        if not os.path.exists(self.accounts_path):
            self.save_accounts(data)
        return data

    # ================= 凭据保护（2026-11-16 任务03） =================
    @staticmethod
    def _secret_name(scope, field, tmpl, acc):
        return tmpl.format(id=acc.get('id', '')) if scope == 'account' else tmpl

    def _serialize_for_disk(self, data):
        """返回落盘副本：敏感字段加密入库，JSON 里只留 `secret_ref:` 引用。

        原 data（内存态）保持明文不变，供 GUI / 子进程正常使用。
        凭据库失败时抛 CredentialError —— 上层 save_accounts 会据此拒绝写盘，
        绝不把明文落进 JSON。
        """
        import copy
        out = copy.deepcopy(data)
        g = out.get('global', {})
        for scope, field, tmpl in _SECRET_FIELDS:
            bucket = g if scope == 'global' else None
            targets = []
            if scope == 'global':
                targets = [(g, bucket)]
            else:
                targets = [(acc, acc) for acc in out.get('accounts', [])]
            for obj, _ in targets:
                val = obj.get(field, '')
                if val is None or val == '' or str(val).startswith(SECRET_REF_PREFIX):
                    continue
                name = self._secret_name(scope, field, tmpl, obj)
                secrets.save_secret(name, val)
                obj[field] = SECRET_REF_PREFIX + name
        return out

    def _deserialize_from_disk(self, data):
        """把 JSON 里的 `secret_ref:` 还原为明文（内存态），失败保留空占位。"""
        g = data.get('global', {})
        for scope, field, tmpl in _SECRET_FIELDS:
            if scope == 'global':
                targets = [g]
            else:
                targets = list(data.get('accounts', []))
            for obj in targets:
                val = obj.get(field, '')
                if not (isinstance(val, str) and val.startswith(SECRET_REF_PREFIX)):
                    continue
                name = val[len(SECRET_REF_PREFIX):]
                try:
                    obj[field] = secrets.load_secret(name, '') if secrets else ''
                except Exception:
                    # 凭据丢失/损坏：保留引用占位，上层会给出空值，但绝不读明文
                    obj[field] = ''

    def _migrate_secrets(self, data):
        """旧配置里残留明文敏感字段时：加密入库 -> 回读验证 -> 换引用。

        返回 `(ok, changed, note)`：
        * `ok=False` 表示迁移失败（调用方保留原 JSON 与内存明文，绝不写半截）；
        * `changed=True` 才表示**真的动过数据**，调用方据此决定是否重新落盘。
          这个标志是 2026-11-19 加的性能门：没有它，每次启动都会把所有凭据
          重新加密一遍（多次黑窗 + 启动变慢），即使配置完全没变化。

        全部成功才继续；任何一步失败都保留原 JSON 与内存明文（由调用方决定是否阻断）。
        """
        if secrets is None:
            return False, False, '凭据模块不可用'
        changed = False
        g = data.get('global', {})
        for scope, field, tmpl in _SECRET_FIELDS:
            targets = [g] if scope == 'global' else list(data.get('accounts', []))
            for obj in targets:
                val = obj.get(field, '')
                if not val or (isinstance(val, str) and val.startswith(SECRET_REF_PREFIX)):
                    continue
                name = self._secret_name(scope, field, tmpl, obj)
                try:
                    secrets.save_secret(name, val)          # 加密写入
                    back = secrets.load_secret(name, None)  # 回读验证
                    if back != val:
                        return False, changed, '%s 回读校验失败' % name
                except Exception as e:
                    return False, changed, '%s 加密失败：%s' % (name, e)
                obj[field] = SECRET_REF_PREFIX + name
                changed = True
        return True, changed, ('已迁移' if changed else '无需迁移')

    def save_accounts(self, data: dict = None) -> bool:
        data = data or self._data
        if not data:
            return False
        try:
            # 敏感字段加密入库，JSON 只写引用；失败拒绝写盘（不降级明文）
            disk = self._serialize_for_disk(data) if secrets else data
            _write_json(self.accounts_path, disk)
            self._data = data     # 内存态保持明文
            return True
        except Exception as e:
            print('[凭据] 配置保存失败（未写明文）：%s' % e)
            return False

    def _blank_account(self, alias='') -> dict:
        acc = dict(DEFAULT_ACCOUNT)
        acc['id'] = new_account_id()
        acc['alias'] = alias
        acc['cour'] = ['']
        return acc

    def _migrate_legacy(self) -> dict:
        """把旧的单账号 account_info.json 迁移成新结构，旧文件保留不删。"""
        legacy = _read_json(self.legacy_account_path, {}) or {}
        g = {}
        for k in GLOBAL_KEYS:
            if k in legacy:
                g[k] = legacy[k]
        acc = self._blank_account(legacy.get('phone_number') or '账号1')
        # 子进程 HOME 里的 account_info.json 带着 runner 写入的真实 account_id，
        # 迁移时沿用它，避免「日志 id=acc_xxx 但 HOME=acc_yyy」这种看着像串号的假象
        if legacy.get('account_id'):
            acc['id'] = str(legacy['account_id'])
        for k in ACCOUNT_KEYS:
            if k in legacy:
                acc[k] = legacy[k]
        if not acc.get('alias'):
            acc['alias'] = acc.get('phone_number') or '账号1'
        g['current_account'] = acc['id']
        return {'version': 2, 'global': g, 'accounts': [acc]}

    # ================= 当前账号 =================
    def list_accounts(self) -> list:
        """给 GUI 用的精简列表。"""
        data = self.load_accounts()
        out = []
        for acc in data['accounts']:
            out.append({
                'id': acc['id'],
                'alias': acc.get('alias') or acc.get('phone_number') or '未命名',
                'phone': acc.get('phone_number', ''),
                'enabled': int(acc.get('enabled', 1)),
                'course_count': len([c for c in acc.get('cour', []) if c]),
            })
        return out

    def current_id(self) -> str:
        return self.load_accounts()['global'].get('current_account', '')

    def set_current(self, account_id: str) -> None:
        data = self.load_accounts()
        if any(a['id'] == account_id for a in data['accounts']):
            data['global']['current_account'] = account_id
            self.save_accounts(data)
            self._account_info = {}

    def get_account_by_id(self, account_id: str) -> dict:
        for acc in self.load_accounts()['accounts']:
            if acc['id'] == account_id:
                return acc
        return {}

    def add_account(self, alias='', phone='', password='') -> dict:
        data = self.load_accounts()
        acc = self._blank_account(alias or phone or f'账号{len(data["accounts"]) + 1}')
        acc['phone_number'] = phone
        acc['password'] = password
        data['accounts'].append(acc)
        data['global']['current_account'] = acc['id']
        self.save_accounts(data)
        self._account_info = {}
        return acc

    def remove_account(self, account_id: str) -> bool:
        data = self.load_accounts()
        if len(data['accounts']) <= 1:
            return False
        data['accounts'] = [a for a in data['accounts'] if a['id'] != account_id]
        if data['global'].get('current_account') == account_id:
            data['global']['current_account'] = data['accounts'][0]['id']
        self.save_accounts(data)
        self._account_info = {}
        return True

    # ================= 扁平接口（兼容旧调用） =================
    def load_account(self) -> dict:
        data = self.load_accounts()
        acc = self.get_account_by_id(data['global']['current_account'])
        flat = {}
        flat.update(data['global'])
        flat.update(acc)
        flat['account_id'] = acc.get('id', '')
        self._account_info = flat
        return flat

    def save_account(self, account_info: dict) -> bool:
        data = self.load_accounts()
        acc_id = account_info.get('account_id') or data['global']['current_account']
        acc = self.get_account_by_id(acc_id)
        if not acc:
            return False
        for k, v in account_info.items():
            if k in GLOBAL_KEYS:
                data['global'][k] = v
            elif k in ACCOUNT_KEYS:
                acc[k] = v
        data['global']['current_account'] = acc_id
        ok = self.save_accounts(data)
        if ok:
            self._account_info = account_info
        return ok

    def get_account(self) -> dict:
        if not self._account_info:
            self.load_account()
        return self._account_info

    # ================= 课程 =================
    def load_courses(self, phone_number: str) -> list:
        """当前账号配置里的课程 + 子进程学习过程中新发现的课程。"""
        acc = self.get_account_by_id(self.current_id())
        courses = [c for c in acc.get('cour', []) if c]
        discovered = _read_json(self.course_path, {}) or {}
        for name in discovered.get(phone_number, []) or []:
            if name and name not in courses:
                courses.append(name)
        return courses

    def save_courses(self, phone_number: str, courses: list) -> None:
        data = self.load_accounts()
        acc = self.get_account_by_id(data['global']['current_account'])
        merged = []
        for name in list(courses) + list(acc.get('cour', [])):
            if name and name not in merged:
                merged.append(name)
        acc['cour'] = merged or ['']
        self.save_accounts(data)

    # ================= 便捷读取 =================
    def get_version(self) -> str:
        try:
            with open(self.version_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            return '0.0.0'

    def get_help_text(self) -> str:
        try:
            with open(self.help_path, 'r', encoding='utf-8') as f:
                return f.read()
        except OSError:
            return '帮助文档未找到'

    def get_course_process(self) -> dict:
        """当前账号的课程进度；账号工作目录没有时回落到共享目录。"""
        acc_id = self.current_id()
        candidates = [
            os.path.join(paths.account_home(acc_id), 'tool', 'course_process.json'),
            self.course_process_path,
        ]
        for path in candidates:
            data = _read_json(path, None)
            if isinstance(data, dict):
                return data
        return {}

    def count_video_records(self) -> int:
        record_dir = paths.record()
        if not os.path.isdir(record_dir):
            return 0
        total = 0
        for fname in os.listdir(record_dir):
            # 兼容旧版本留下的记录文件名（转义写法）
            if fname.endswith('.txt') and ('学习记录' in fname or '\u5237\u8bfe\u8bb0\u5f55' in fname):
                try:
                    with open(os.path.join(record_dir, fname), 'r', encoding='utf-8') as f:
                        total += f.read().count('视频')
                except OSError:
                    pass
        return total

    def get_popup_count(self) -> int:
        return self.load_account().get('popup_count', 0)

    def increment_popup_count(self, video_count: int):
        account = self.load_account()
        account['popup_count'] = video_count
        self.save_account(account)

    # ================= 子进程配置 =================
    def write_account_runtime(self, account_id: str) -> str:
        """把某个账号扁平化成旧格式 account_info.json，写进它的工作目录。

        子进程通过 XXT_HOME 指向该目录，main.py 的读取逻辑无需改动。
        """
        data = self.load_accounts()
        acc = self.get_account_by_id(account_id)
        if not acc:
            return ''

        home = paths.account_home(account_id)
        tool_dir = os.path.join(home, 'tool')
        os.makedirs(tool_dir, exist_ok=True)
        os.makedirs(os.path.join(home, 'record'), exist_ok=True)

        flat = {}
        flat.update(data['global'])
        flat.update(acc)
        flat.pop('id', None)
        flat.pop('alias', None)
        flat['account_id'] = account_id
        # 让子进程知道一共开了几个账号：单账号时浏览器已有登录态就可以直接复用，
        # 不必清 Cookie 重新登录（多账号才需要严格防串号）
        flat['account_count'] = len(data['accounts'])
        # 多账号模式下多端学习会叠乘浏览器数量，强制关闭
        if len(data['accounts']) > 1:
            flat['multi_device'] = 0

        target = os.path.join(tool_dir, 'account_info.json')
        _write_json(target, flat)

        # 首次运行时补一份默认课程进度文件，避免子进程读不到而报错
        process_file = os.path.join(tool_dir, 'course_process.json')
        if not os.path.exists(process_file):
            _write_json(process_file, {})

        return home
