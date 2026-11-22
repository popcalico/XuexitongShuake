# -*- coding: utf-8 -*-
"""子进程运行时策略（只读配置快照）。

新增设置项如果沿 main() → run() → dispatch_page() → Quiz/Discussion 一层层透传，
要改 6 处函数签名，任何一处漏掉都会静默失效。这里改成启动时装载一次，
各模块按需读取，纯桩测试也能直接 configure() 造场景。

注意：只在**进程内**有效。主进程（GUI）与每个账号子进程各自装载自己的副本；
子进程由 main.run_main() 从 account_info.json 装载。
"""
from __future__ import annotations

import time

DEFAULTS = {
    # 无法处理的题：'AI 尽力完成'（默认，失败自动降级截图跳过） / '截图跳过'
    'unanswerable_policy': 'AI 尽力完成',
    # 讨论范围：'任务点内全部讨论' / '全部讨论（需真机校准）'
    'discussion_scope': '任务点内全部讨论',
    # 是否抄已有回复：0=直接用 DeepSeek（默认），1=有回复时随机抄一条
    'discussion_copy_reply': 0,
    # MeoW 推送（子进程也要用，用于课程结束/错误事件）
    'meow_enabled': 0,
    'meow_url': '',
    'meow_msg_type': 'text',
    'meow_events': {},
    # 课程考试（2026-11-08）：全部有默认值，未配置时等价于「只答题、绝不交卷」
    'exam_answer_mode': '题库优先+AI兜底',
    'exam_auto_submit': 0,
    'exam_submit_ratio': 100,
    'exam_revoke_seconds': 15,
    'exam_min_remain_minutes': 10,
    'exam_max_questions': 200,
    # 整卷题数：0=从答题卡探测；交卷分母必须用它，绝不用本轮遍历题数（2026-11-16 任务01）
    'exam_paper_total': 0,
    # OpenAI 兼容 AI 接口（2026-11-10）：子进程从 account_info.json 装载，
    # core.ai_config.resolve() 会读取这些值构造客户端
    'ai_base_url': 'https://api.deepseek.com',
    'ai_model': 'deepseek-v4-flash',
    'ai_vision_model': '',
    'ai_timeout': 0,
    # 任务04：默认不关闭浏览器同源策略；仅真机验证确有需要时显式开启
    'browser_disable_web_security': 0,
    # ---- 2026-11-19 从上游 26.11.18 移植 ----
    # 复习模式：1=所有任务点从头再刷一遍（用于刷学习时长/次数）；0=只做未完成的
    'review': 0,
    # 复习遍数：1=刷一遍就停；0 或负数=不限制（跑到手动停止）
    'review_times': 1,
    # 自动问卷：1=遇到问卷随机作答并提交；0=识别但跳过（不再计入「未识别页」）
    'vote_enabled': 1,
}

_STATE = dict(DEFAULTS)


def configure(cfg: dict) -> dict:
    """从扁平配置装载；只认 DEFAULTS 里的键，返回当前快照。"""
    if isinstance(cfg, dict):
        for key in DEFAULTS:
            if key in cfg and cfg[key] is not None:
                _STATE[key] = cfg[key]
    return dict(_STATE)


def get(key: str, default=None):
    return _STATE.get(key, DEFAULTS.get(key, default))


def state() -> dict:
    return dict(_STATE)


def reset() -> dict:
    """测试用：恢复默认值。"""
    _STATE.clear()
    _STATE.update(DEFAULTS)
    return dict(_STATE)


def ai_effort_enabled() -> bool:
    return str(get('unanswerable_policy')) == 'AI 尽力完成'


def _as_int(value, default=0) -> int:
    """配置里可能是 '1' / 1 / True / '' 各种形态，统一转 int，绝不抛异常。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def review_enabled() -> bool:
    """复习模式：所有任务点（含已完成的）从头再刷一遍。

    只影响「看/听」类内容（视频 / PPT / 阅读 / 音频），由各模块的
    `REVIEWABLE` 与自身逻辑决定；测验 / 作业 / 考试**永不重做**。
    """
    return _as_int(get('review')) > 0


def review_times() -> int:
    """复习要刷的遍数：>0 刷够即停；<=0 表示不限制（跑到手动停止）。"""
    return _as_int(get('review_times'), 1)


def vote_enabled() -> bool:
    """是否自动作答问卷。关闭时仍会被识别为已知页面，只跳过不处理。"""
    return _as_int(get('vote_enabled'), 1) > 0


def copy_existing_reply() -> bool:
    return bool(get('discussion_copy_reply'))


# ---------------------------------------------------------------- 运行统计
_STATS = {
    'started_at': 0,
    'quiz_answered': 0,
    'quiz_skipped': 0,
    'discussion_ok': 0,
    'discussion_failed': 0,
    'videos_done': 0,
    'courses_done': 0,
}


def bump(key: str, value: int = 1) -> None:
    """累加一个进程内计数；key 不存在时自动建立。"""
    try:
        _STATS[key] = int(_STATS.get(key, 0) or 0) + int(value)
    except Exception:
        pass


def mark_started() -> None:
    _STATS['started_at'] = time.time()


def stats() -> dict:
    return dict(_STATS)


def reset_stats() -> dict:
    for key in _STATS:
        _STATS[key] = 0
    return dict(_STATS)
