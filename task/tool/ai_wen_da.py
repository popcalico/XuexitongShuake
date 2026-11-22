import re

import requests
import time
import hashlib
import random
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import pickle
import os
import asyncio
import ast
from task.tool.DeepSeekAsk import DeepSeekAsk
from task.tool import quota
from core import paths, ai_config
from core import bounded_executor


# 判断题的否定词表：命中任意一个就判「错」。
# 必须比肯定词先判断——「不正确」里含「正确」，顺序反了就会把答案答反。
_JUDGE_FALSE_WORDS = ('错误', '不正确', '错', '否', 'false', 'no', '×', '✗')


@dataclass
class Question:
    """题目数据结构"""
    type: str  # 题目类型: 0-单选, 1-多选, 2-填空, 3-判断, 9-简答
    question: str
    options: List[str]
    html: str = ""
    work_type: str = ""
    page_type: str = ""
    API: str = ""


@dataclass
class AnswerResult:
    """答案结果"""
    form: str  # 答案来源
    answer: List[str]  # 答案列表
    duration: int = 0  # 耗时
    msg: str = ""  # 消息
    error: Optional[Exception] = None
    cacheable: bool = True  # 是否允许写入答案缓存（AI 兜底答案不可缓存）

class Cache:
    """缓存管理类"""

    @staticmethod
    def set(key: str, value: Any, expire: int = 0) -> Any:
        """设置缓存"""
        cache_data = {
            'value': value,
            'expire': int(time.time()) + expire if expire > 0 else 0
        }
        try:
            # 任务06：新缓存写 JSON（带格式版本），旧 pkl 只读不写
            import json as _json
            jp = paths.record(f'cache_{key}.json')
            tmp = jp + '.tmp'
            os.makedirs(os.path.dirname(jp) or '.', exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                _json.dump({'_fmt': 1, 'data': cache_data}, f, ensure_ascii=False)
            os.replace(tmp, jp)
        except Exception as e:
            print(f"缓存设置失败: {e}")
        return value

    @staticmethod
    def delete(key: str) -> None:
        """删除一条缓存（清理脏答案用；不存在时静默）。"""
        for suffix in ('.json', '.pkl'):
            try:
                os.remove(paths.record(f'cache_{key}{suffix}'))
            except OSError:
                pass

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """获取缓存（优先 JSON，旧 pkl 受限读取后迁移）"""
        try:
            from core import safe_pickle
            cache_data, _ = safe_pickle.load_json_or_pickle(
                paths.record(f'cache_{key}.json'),
                paths.record(f'cache_{key}.pkl'),
                validator=lambda v: isinstance(v, dict) and 'value' in v and 'expire' in v)
            if not cache_data:
                return default
            if cache_data['expire'] > 0 and cache_data['expire'] < time.time():
                # 过期：只删 JSON（旧 pkl 已迁移备份，保留不误删用户数据）
                try:
                    os.remove(paths.record(f'cache_{key}.json'))
                except OSError:
                    pass
                return default
            return cache_data['value']
        except FileNotFoundError:
            return default
        except Exception as e:
            print(f"缓存获取失败: {e}")
            return default


class AnswerAPI:
    """答题API类"""

    # 题库接口并发上限（任务08）：避免同时打太多请求触发风控
    BANK_MAX_CONCURRENCY = 3
    BANK_TASK_TIMEOUT = 25  # 单接口最长等待秒数，超时只影响该接口

    def __init__(self):
        self.cache = Cache()
        self.api_scores = {}  # API评分记录

    async def _gather_bounded(self, coros, forms):
        """并发执行题库接口：限制并发数 + 单接口超时隔离 + 结果顺序稳定。

        任务08：任一接口超时/异常只返回一个空 AnswerResult，不影响其它接口；
        保持传入顺序（与既有匹配顺序一致），不把 AI 答案写缓存。
        """
        # V5.1 第25节：把默认执行器换成进程内共享的**有界线程池**，
        # 连续超时时底层同步 HTTP 线程不会无限堆积。
        # 真实取消边界：asyncio.wait_for 只取消协程，**不会终止底层线程**；
        # 线程靠 requests 的 connect/read timeout 自己结束，池子限制其总数与排队长度。
        bounded_executor.install()
        # 局部信号量：每次调用在当前事件循环内创建，兼容每题 asyncio.run 的现状
        sem = asyncio.Semaphore(self.BANK_MAX_CONCURRENCY)
        batch = bounded_executor.next_batch()

        async def one(coro, form):
            async with sem:
                bounded_executor.record_request()
                try:
                    result = await asyncio.wait_for(coro, timeout=self.BANK_TASK_TIMEOUT)
                    # 迟到响应隔离：只采信本批次协程自己返回的 AnswerResult；
                    # 被取消的协程结果无处写入，不会串到别的接口或下一道题。
                    if not isinstance(result, AnswerResult):
                        return AnswerResult(form=form, answer=[],
                                            msg='结果类型异常(批次%d)' % batch)
                    return result
                except asyncio.TimeoutError:
                    bounded_executor.record_timeout()
                    return AnswerResult(form=form, answer=[],
                                        msg='单接口超时(批次%d)' % batch,
                                        duration=int(self.BANK_TASK_TIMEOUT * 1000))
                except Exception as e:
                    return AnswerResult(form=form, answer=[], error=e,
                                        msg='单接口失败(批次%d)' % batch)

        return await asyncio.gather(*(one(c, f) for c, f in zip(coros, forms)))

    @staticmethod
    def bank_executor_stats():
        """供性能报告/压力测试读取线程池上限证据。"""
        return bounded_executor.stats()


    def score(self, api_name: str, score: int):
        """记录API评分"""
        current_score = self.cache.get(f"api_{api_name}", {'score': 0})
        current_score['score'] += score
        self.cache.set(f"api_{api_name}", current_score, 600)

    def get_timestamp(self) -> int:
        """获取时间戳"""
        return int(time.time())

    def question_hash(self, question: Question) -> str:
        """生成题目哈希值"""
        content = f"{question.question}{''.join(question.options)}"
        # print(content,flush=True)
        # print('生成题目哈希值:',hashlib.md5(content.encode()).hexdigest(),flush=True)
        return hashlib.md5(content.encode()).hexdigest()

    async def get_free_answers(self, question: Question) -> List[AnswerResult]:
        """获取所有答案源（受额度闸门约束：超出就返回空结果，不联网）"""
        if not quota.acquire('', '题库'):
            return [AnswerResult(form='quota', answer=[], duration=0)]
        tasks = [self.get_answer4(question), self.get_answer3(question)]
        return await self._gather_bounded(tasks, ['免费题库', '题库3'])

    async def get_answers_free(self, question: Question) -> List[AnswerResult]:
        """免费答案获取（受额度闸门约束）"""
        if not quota.acquire('', '题库'):
            return [AnswerResult(form='quota', answer=[], duration=0)]
        tasks = [self.get_main_answer(question), self.get_answer1(question),
                 self.get_answer2(question)]
        return await self._gather_bounded(tasks, ['主题库', '一之题库', 'muketool'])

    async def get_answer1(self, question: Question) -> AnswerResult:
        """从一之题库获取答案"""
        headers = self._generate_random_headers()
        data = {"question": question.question}

        try:
            start_time = time.time()
            # 任务08：同步网络请求放到线程，避免阻塞事件循环（真实并发）
            response = await asyncio.to_thread(
                requests.post, "http://cx.icodef.com/wyn-nb?v=4",
                json=data, headers=headers, timeout=10)
            duration = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 1:
                    answer_text = result.get('data', '')
                    # 过滤无效答案
                    invalid_keywords = ["傀儡", "公众号", "李恒雅", "一之"]
                    if not any(keyword in answer_text for keyword in invalid_keywords):
                        answers = answer_text.split('#')
                        # print(answers,flush=True)
                        return AnswerResult(
                            form="一之题库",
                            answer=answers,
                            duration=duration
                        )

            return AnswerResult(
                form="一之题库",
                answer=[],
                duration=duration,
                msg="未找到答案"
            )

        except requests.exceptions.Timeout:
            self.score("icodef", -1)
            return AnswerResult(
                form="一之题库",
                answer=[],
                msg="timeout",
                duration=5000
            )
        except Exception as e:
            return AnswerResult(
                form="一之题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )

    async def get_answer2(self, question: Question) -> AnswerResult:
        """从Muketool题库获取答案"""
        if question.type not in ['0', '1', '2']:
            return AnswerResult(
                form="muketool",
                answer=[],
                duration=0,
                msg="不支持的题型"
            )

        data = {
            "question": question.question,
            "type": int(question.type)
        }

        try:
            start_time = time.time()
            response = await asyncio.to_thread(
                requests.post, "https://api.muketool.com/cx/v2/query",
                json=data, timeout=10)
            duration = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 1:
                    answers = result.get('data', '').split('#')
                    # print(2,answers,flush=True)
                    return AnswerResult(
                        form="muketool",
                        answer=answers,
                        duration=duration
                    )

            return AnswerResult(
                form="muketool",
                answer=[],
                duration=duration
            )

        except requests.exceptions.Timeout:
            self.score("muketool", -1)
            return AnswerResult(form="muketool", answer=[])
        except Exception as e:
            return AnswerResult(form="muketool", answer=[])

    async def get_main_answer(self, question: Question) -> AnswerResult:
        """从爱问答题库获取答案"""
        data = {
            "type": question.type,
            "question": question.question,
            "options": question.options.copy(),
            "html": question.html,
            "workType": question.work_type,
            "pageType": question.page_type
        }

        try:
            start_time = time.time()
            response = await asyncio.to_thread(
                requests.post, "https://aiask.wk66.top/api/search",
                json=data, timeout=10)
            duration = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 200:
                    # print(result.get('data', {}).get('answer', []),flush=True)
                    return AnswerResult(
                        form="爱问答题库",
                        answer=result.get('data', {}).get('answer', []),
                        duration=duration,
                        msg=result.get('msg', '')
                    )

            return AnswerResult(
                form="爱问答题库",
                answer=[],
                duration=duration,
                msg=result.get('msg', '') if response.status_code == 200 else '请求失败'
            )

        except Exception as e:
            return AnswerResult(
                form="爱问答题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )

    async def get_answer3(self,question: Question)-> AnswerResult:
        """
        请求 wkexam API 获取答案
        :param question: 你要查询的问题字符串
        :param your_token: 你的 API Token
        :return: 解析后的 JSON 数据或错误信息
        """
        # API 的基础地址
        base_url = "https://webapi.zaizhexue.top/search"

        # 准备请求参数
        params = {
            "query": question.question,
            "type": "题目",
            "page": 1,
            "pageSize": 8,
            "sort": "newest"
        }

        try:
            start_time = time.time()

            # 发送 GET 请求
            response = await asyncio.to_thread(
                requests.get, base_url, params=params, timeout=10)

            # 检查 HTTP 状态码
            response.raise_for_status()

            # 解析返回的 JSON 数据
            data = response.json()
            duration = int((time.time() - start_time) * 1000)

            # 根据返回的 code 判断请求是否成功
            if response.status_code == 200:
                answer = data["data"]["answer"]
                # 去掉首尾的空白字符（比如 \n 和空格）
                answer = re.split(r'#', answer.strip())
                # print(answer,flush=True)
                if not answer:
                    return AnswerResult(form="免费题库",
                                        answer=[],
                                        duration=10,
                                        msg="无答案"
                                    )
                return AnswerResult(
                    form="免费题库3",
                    answer=answer,
                    duration=duration,
                    msg=data.get('msg', '')
                )
            else:
                return AnswerResult(
                    form="免费题库",
                    answer=[],
                    duration=10,
                    msg="请求失败"
                )

        except requests.exceptions.Timeout:
            print("请求超时，请检查网络或稍后重试。")
            return AnswerResult(
                form="免费题库",
                answer=[],
                duration=10,
                msg="请求失败"
            )
        except requests.exceptions.RequestException as e:
            print(f"请求过程中发生错误：{e}")
            return AnswerResult(
                form="免费题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )
        except ValueError as e:
            print(f"解析返回数据失败，返回内容可能不是有效的 JSON。错误：{e}")
            return AnswerResult(
                form="免费题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )

    async def get_answer4(self,question: Question)-> AnswerResult:
        url = 'https://cx.icodef.com/wyn-nb?v=4'

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': 'd4UgUg49pkJauTLr'
        }

        data = {
            'question': question.question  # 将 ${title} 替换为实际的问题内容
        }
        start_time = time.time()
        try:
            # 任务07：补显式超时；任务08：to_thread 真实并发
            response = await asyncio.to_thread(
                requests.post, url, headers=headers, data=data, timeout=(5, 15))
            response.raise_for_status()  # 检查HTTP错误

            result = response.json()

            # 处理返回结果（对应 handler 的逻辑）
            if result.get('code') == 1:
                answer=result.get('data')
                answer = re.split(r'#', answer.strip())
                # print(answer,flush=True)
                duration = int((time.time() - start_time) * 1000)
                if not answer:
                    return AnswerResult(form="免费题库",
                                        answer=[],
                                        duration=10,
                                        msg="无答案"
                                        )
                return AnswerResult(
                    form="免费题库4",
                    answer=answer,
                    duration=duration,
                    msg=data.get('msg', '')
                )
            else:
                return AnswerResult(
                    form="免费题库",
                    answer=[],
                    duration=10,
                    msg="请求失败"
                )

        except requests.exceptions.Timeout:
            print("请求超时，请检查网络或稍后重试。",flush=True)
            return AnswerResult(
                form="免费题库",
                answer=[],
                duration=10,
                msg="请求失败"
            )
        except requests.exceptions.RequestException as e:
            print(f"请求过程中发生错误：{e}",flush=True)
            return AnswerResult(
                form="免费题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )
        except ValueError as e:
            print(f"解析返回数据失败，返回内容可能不是有效的 JSON。错误：{e}")
            return AnswerResult(
                form="免费题库",
                answer=[],
                error=e,
                duration=10,
                msg="请求失败"
            )

    def cache_answer(self, question: Question, answer: List[str]):
        """缓存答案"""
        cache_data = {
            'type': question.type,
            'question': question.question,
            'options': question.options,
            'answer': answer,
            'createTime': self.get_timestamp()
        }
        cache_key = f"ques1_{self.question_hash(question)}"
        self.cache.set(cache_key, cache_data)

    def drop_cache_answer(self, question: Question) -> None:
        """删掉某道题的缓存。

        用于清理「答案与题型根本不匹配」的脏缓存（例如判断题里存着
        `['科学社会主义']`）。留着它每次都会白查一遍再被判无答案。
        """
        try:
            self.cache.delete(f"ques1_{self.question_hash(question)}")
        except Exception:
            pass

    async def get_cache_answer(self, question: Question) -> AnswerResult:
        """从缓存获取答案"""
        cache_key = f"ques1_{self.question_hash(question)}"
        cached_data = self.cache.get(cache_key)

        if cached_data:
            return AnswerResult(
                form="本地缓存",
                answer=cached_data.get('answer', []),
                duration=10
            )
        else:
            return AnswerResult(
                form="本地缓存",
                answer=[],
                duration=10,
                msg="未找到缓存"
            )

    def _generate_random_headers(self) -> Dict[str, str]:
        """生成随机请求头"""
        ip = ".".join(str(random.randint(0, 255)) for _ in range(4))
        return {
            "X-Forwarded-For": ip,
            "X-Real-IP": ip,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }


class AnswerMatcher:
    """答案匹配器"""

    @staticmethod
    def is_true(text: str) -> bool:
        """判断答案是否表示「正确」。

        2026-11-19 收紧：旧实现是 `any(kw in text for kw in [...'是'...])`，
        只要答案里出现一个「是」或「对」就算正确 —— 于是
        「这是错误的」「不正确」「是国家的、民族的…」这类答案全会被判成「对」。
        现在规则：

        1. **否定词优先**（「错误 / 不正确 / 否 / false / no」直接判否，
           否则「不正确」里的「正确」会先把结果定成真）；
        2. 单字母/符号：只有**整个答案**就是 A/T/√/对/是 时才算；
        3. 长文本只认明确的肯定词（正确 / true / yes），不认零散的「是」「对」。
        """
        s = str(text or '').strip()
        if not s:
            return False
        low = s.lower()
        if any(kw in low for kw in _JUDGE_FALSE_WORDS):
            return False
        if s in ('A', 'a', 'T', 't', '√', '✓', '对', '是', '正确', 'true', 'yes'):
            return True
        return any(kw in low for kw in ('正确', 'true', 'yes'))

    @staticmethod
    def is_false(text: str) -> bool:
        """判断答案是否表示「错误」（否定优先，避免「不正确」被当成肯定）。"""
        s = str(text or '').strip()
        if not s:
            return False
        low = s.lower()
        if s in ('B', 'b', 'F', 'f', '×', '✗', '错', '否', '错误', 'false', 'no'):
            return True
        return any(kw in low for kw in ('错误', '不正确', 'false'))

    @staticmethod
    def remove_html(html: str) -> str:
        """去除HTML标签"""
        import re
        return re.sub('<[^<]+?>', '', html)

    def match_answer(self, answers: List[str], question: Question) -> List[int]:
        """匹配答案和选项"""
        options=question.options
        finally_options = []
        if len(answers) == 1 and question.type == '1':
            answers = answers[0]
        for answer in answers:
            matched_indices=None
            option_scores = []
            num=len(options)
            if answer in ['A', 'B', 'C', 'D', 'E', 'F', 'G','H'][:num]:
                dit = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7}
                matched_indices = dit.get(answer, None)
            else:
                for i, option in enumerate(options):
                    similarity = self._calculate_similarity(answer, option)
                    # print(i, option, similarity,flush=True)
                    if similarity >= 0.4:
                        option_scores.append((i, similarity))
                    if answer.lower() in option or option in answer.lower():
                        matched_indices=i
                        break
                if matched_indices is None:
                    if not option_scores:
                      break
                    option_scores.sort(key=lambda x: x[1], reverse=True)
                    matched_indices = option_scores[0][0]
            finally_options.append(matched_indices)
        return finally_options


    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """计算字符串相似度"""
        if not str1 or not str2:
            return 0.0

        # 简单的相似度计算：共同字符比例
        set1 = set(str1)
        set2 = set(str2)
        intersection = set1.intersection(set2)
        union = set1.union(set2)

        if not union:

            return 0.0

        return len(intersection) / len(union)
    def api_answer_match(self, answer_results: List[AnswerResult], question: Question) -> Dict[str, Any]:
        """API答案匹配逻辑"""
        if not answer_results:
            return {'res': answer_results, 'haveAnswer': False}

        question_type = question.type
        answers_to_use = []

        # 过滤有效答案
        for result in answer_results:
            if result.answer!=[''] and result.answer!=[]:
                answers_to_use.append(result)

        if not answers_to_use:
            return {'res': answer_results, 'haveAnswer': False}

        # 根据题目类型进行匹配
        if question_type in ['0', '1']:  # 单选/多选
            return self._match_choice_questions(answers_to_use, question)
        elif question_type == '3':  # 判断
            return self._match_judgment_questions(answers_to_use, question)
        elif question_type in ['2', '9', '4', '5', '6', '7']:  # 填空/简答等
            return self._match_text_questions(answers_to_use, question)
        else:
            return {'res': answer_results, 'haveAnswer': False}

    def _match_choice_questions(self, answers: List[AnswerResult], question: Question) -> Dict[str, Any]:
        """匹配选择题"""
        best_answer = None

        answer_result = answers[0]
        matched_indices = self.match_answer(answer_result.answer, question)
        best_answer = answer_result

        if len(matched_indices) > 0:
            return {
                'res': answers,
                'form': best_answer,
                'haveAnswer': True,
                'matchedIndices': matched_indices,
                'answerType': 'choice'
            }

        return {'res': answers, 'haveAnswer': False}

    def _match_judgment_questions(self, answers: List[AnswerResult], question: Question) -> Dict[str, Any]:
        """匹配判断题"""
        for answer_result in answers:
            if answer_result.answer:
                answer_text = answer_result.answer[0] if isinstance(answer_result.answer,
                                                                    list) else answer_result.answer
                if self.is_true(answer_text):
                    for option in question.options:
                        for keyword in   ["正确", "对", "是", "true", "yes","A"]:
                            if keyword in option:
                                return {
                                    'res': answers,
                                    'form': answer_result,
                                    'haveAnswer': True,
                                    'answer': option,
                                    'answerType': 'judgment'
                                }
                elif self.is_false(answer_text):
                    for option in question.options:
                        for keyword in   ["错误", "错", "否", "false", "no","B"]:
                            if keyword in option:
                                return {
                                    'res': answers,
                                    'form': answer_result,
                                    'haveAnswer': True,
                                    'answer': option,
                                    'answerType': 'judgment'
                                }

        return {'res': answers, 'haveAnswer': False}

    def _match_text_questions(self, answers: List[AnswerResult], question: Question) -> Dict[str, Any]:
        """匹配文本题（填空/简答）"""

        def has_letters(text):
            """检测文本中是否含有字母"""
            return bool(re.search(r'[a-zA-Z]', text))
        for answer_result in answers:
            if answer_result.answer[0] in ["正确", "对", "是", "true", "yes","错误", "错", "否", "false", "no",'答案:',''] :
                continue
            if answer_result.answer:
                return {
                    'res': answers,
                    'form': answer_result,
                    'haveAnswer': True,
                    'answer': answer_result.answer,
                    'answerType': 'text'
                }

        return {'res': answers, 'haveAnswer': False}


class AutoAnswer:
    """自动答题器"""

    def __init__(self):
        self.api = AnswerAPI()
        self.matcher = AnswerMatcher()
    def use_deepseek(self, question: Question,api) :
        """使用DeepSeek API获取答案"""
        start_time = time.time()
        typ_dict = {'0': '单选题', '1': '多选题', '2': '填空题', '3': '判断题', '9': '简答题'}
        if question.type  in ['2','9']:
            title="【" + typ_dict[question.type] + "】" + question.question
        else:
            title = "【" + typ_dict[question.type] + "】" + question.question + str(question.options)
        answer = DeepSeekAsk(api, title,typ_dict[question.type])
        if type(answer) is str:
            try:
                answer = ast.literal_eval(answer)  # 转换为列表
            except:
                answer = [answer]

        duration = int((time.time() - start_time) * 1000)
        return [AnswerResult(
            form="Deepseek",
            answer=answer,
            duration=duration,
            # AI 答案不写缓存：猜错一次会被永久固化，之后遇到同题直接拿错答案。
            # 缓存只保留题库/接口命中的结果（见 auto_answer_question 里的过滤）。
            cacheable=False
        )]
    async def auto_answer_question(self, question: Question) -> Dict[str, Any]:
        """自动回答单个题目"""
        global status
        from core import runtime
        # 「截图跳过」策略下：填空/简答直接跳过，免得白耗额度。
        # 默认「AI 尽力完成」策略下放行，让题库/AI 兜底给出文本答案，
        # 再由 quiz_ai 的 _write_answer 填进作答区（AI 结果仍不写缓存）。
        if str(question.type) in ('2', '9') and not runtime.ai_effort_enabled():
            print(f'⏭️ 题型 {question.type}（填空/简答）无法自动作答，跳过', flush=True)
            return {'res': '', 'haveAnswer': False}
        print(f"\n🔍 正在搜索题目答案...",flush=True)
        print(f"题目类型: {question.type} | 题目:{question.question[:50]}...",flush=True)

        # 1. 尝试从缓存获取答案
        cache_result = await self.api.get_cache_answer(question)
        cache_usable = False
        if cache_result.answer:
            # 缓存里可能躺着脏答案（旧版本缓存的 AI 结果、题库串题等）。
            # 例如判断题存了 `['科学社会主义']`——它永远匹配不到「对/错」两个选项，
            # 旧逻辑仍把它当命中，于是跳过网络查询、直接判「所有来源均未找到答案」，
            # 白费一次机会后才去问 AI。现在先验证能不能真的匹配本题。
            probe = self.matcher.api_answer_match([cache_result], question)
            cache_usable = bool(probe.get('haveAnswer'))
            if not cache_usable:
                print(f"⚠️ 缓存答案 {cache_result.answer} 与本题型不匹配（脏数据），"
                      f"已删除并重新查询", flush=True)
                self.api.drop_cache_answer(question)
        print(f"📦 缓存查询结果: {'找到' if cache_usable else '未找到'}",flush=True)

        # 2. 如果缓存没有（或不可用），从API获取
        if not cache_usable:
            if ai_config.is_key_valid(question.API):
                print("🌐 开始从网络API获取答案...",flush=True)
                time.sleep(1)
                api_results = await self.api.get_answers_free(question)
                # 显示每个API的结果
                for i, result in enumerate(api_results, 1):
                    status = "✅ 找到答案" if result.answer else "❌ 未找到"
                    # print(f"   {status} | 耗时: {result.duration}ms",flush=True)
                    if result.answer:
                        print(result.answer,flush=True)
                match_result = self.matcher.api_answer_match(api_results, question)
                if not match_result.get('haveAnswer') and question.type not in ['2','9']:
                    print('稍侯', flush=True)
                    api_results = await self.api.get_free_answers(question)
                    match_result = self.matcher.api_answer_match(api_results, question)
                if not match_result.get('haveAnswer'):
                    print('正在使用deepseek搜索中...',flush=True)
                    api_results =self.use_deepseek(question,question.API)
            else:
                print('API错误',flush=True)
                return {'res': '', 'haveAnswer': False}
        else:
            api_results = [cache_result]
            print(f"🎯 使用缓存答案",flush=True)
            print(cache_result.answer,flush=True)
            # 3. 匹配答案
        match_result = self.matcher.api_answer_match(api_results, question)
        # 添加详细的来源信息输出
        if match_result.get('haveAnswer'):
            # 只缓存题库 / 接口命中的答案；AI 兜底答案（cacheable=False）不进缓存，
            # 否则一次猜错会被永久固化，之后同题直接拿错答案、也不再查题库。
            for result in api_results:
                if result.answer and getattr(result, 'cacheable', True):
                    self.api.cache_answer(question, result.answer)
                    break
            answer_source = match_result.get('form')
            if answer_source:
                # print(f"🎉 答案来源: {answer_source.form}",flush=True)
                print(f"📝 答案内容: {answer_source.answer}",flush=True)
                print(f"⏱️ 查询耗时: {answer_source.duration}ms",flush=True)
                if answer_source.msg:
                    print(f"💬 附加信息: {answer_source.msg}",flush=True)
            else:
                print("⚠️ 找到答案但无法确定具体来源",flush=True)
        else:
            print("😔 所有来源均未找到答案",flush=True)

        return match_result


# 使用示例
async def main(typ, question, options,api):
        """主函数 - 添加详细的来源信息输出"""
        typ_dict = {'单选题': 0, '多选题': 1, '填空题': 2, '判断题': 3, '简答题': 9,'论述题':9, '名词解释':9}
    # 创建题目实例
        question_obj = Question(
        type=str(typ_dict[typ]),  # 题目类型
        question=question,
        options=options,
        API=api)

    # 创建自动答题器
        auto_answer = AutoAnswer()

    # 自动答题
        result = await auto_answer.auto_answer_question(question_obj)

        if result['haveAnswer']:
            answer_source = result.get('form')
            if answer_source:
                # 根据题目类型显示具体答案
                answer_type = result.get('answerType')
                if answer_type == 'choice' and 'matchedIndices' in result:
                    indices = result['matchedIndices']
                    selected_options = [question_obj.options[i] for i in indices]
                    # print(f"   选项: {selected_options}",flush=True)
                    return selected_options
                elif answer_type == 'judgment':
                    # print(f"   判断: {[result.get('answer')]}",flush=True)
                    return [result.get('answer')]
                elif answer_type == 'text':
                    # print(f"   文本答案: {answer_source.answer}",flush=True)
                    return answer_source.answer
            return result['form'].answer if result.get('form') else []
        else:
            print("未找到答案",flush=True)
            return []
