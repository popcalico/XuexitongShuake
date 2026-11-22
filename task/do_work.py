# -*- coding: utf-8 -*-
"""作业处理（V5.1 第22节）：窗口等待有超时、结果状态真实、不误报完成。

* `wait_for_homework_window()`：有上限、可取消、有心跳的新窗口条件等待；
* 统一结果状态 `core.task_control.TaskStatus`，`auto_choice_homework_question()`
  与 `get_answer_list()` 的返回值都由调用者检查；
* 只有平台确认「暂时保存」按钮点击成功才允许报完成，且措辞明确为
  「已保存（未提交，需人工确认后再提交）」—— 本模式策略就是仅自动保存；
* 切换窗口/翻页后重新定位 WebElement，绝不复用跨页面缓存的元素。
"""
import time
from selenium.webdriver.common.by import By

from core import task_control as tc
from core import ai_config
from task.tool import color
from task.quiz_ai import Answer

HOMEWORK_WINDOW_TIMEOUT = 180   # 用户手动打开作业窗口的最长等待（秒）


def pick_pending_homework(elements, homework_label=''):
    """从筛选 radio 列表里选出「未完成/待做」项，返回元素；无法判断返回 None。

    任务05：绝不硬编码 `elements[2]`。判定依据（按优先级）：
      1. aria-label / text 含「未完成 / 待完成 / 待做 / 未提交」；
      2. 用户显式指定了 homework_label 时按标签文本精确/包含匹配；
      3. 都没有就不猜（返回 None），由调用方安全退出并打印日志。
    """
    if not elements:
        return None
    kw = ('未完成', '待完成', '待做', '未提交', '未完成作业')
    for el in elements:
        try:
            label = (el.get_attribute('aria-label') or el.text or '').strip()
        except Exception:
            continue
        if any(k in label for k in kw):
            return el
    if homework_label:
        for el in elements:
            try:
                label = (el.get_attribute('aria-label') or el.text or '').strip()
            except Exception:
                continue
            if label and (label == homework_label or homework_label in label):
                return el
    return None


def wait_for_homework_window(driver, baseline_count, timeout=None,
                             stop_event=None, poll_interval=0.5):
    """有超时、可取消、有心跳的新窗口条件等待（V5.1 第22节）。

    返回 (TaskStatus, detail)。用户没打开窗口时按策略返回 TIMEOUT 并提示跳过，
    绝不无限等待，也绝不把超时/停止当成作业完成。
    """
    limit = HOMEWORK_WINDOW_TIMEOUT if timeout is None else timeout

    def _check():
        try:
            n = len(driver.window_handles)
        except Exception as e:
            # 保留原始异常文本，便于有界等待识别「driver 失效」并立即终止
            raise RuntimeError('窗口句柄读取失败 %s: %s' % (type(e).__name__, e))
        return (n > baseline_count, '窗口数 %d（基线 %d）' % (n, baseline_count))

    status, detail, elapsed = tc.bounded_wait(
        '作业新窗口出现', _check, timeout=limit,
        poll_interval=poll_interval, heartbeat=30.0, stop_event=stop_event,
        diagnose=lambda: tc.page_snapshot(driver))
    if status == tc.TaskStatus.SUCCESS:
        return status, detail
    if status == tc.TaskStatus.TIMEOUT:
        print(color.yellow('等待作业窗口超时（%.0f 秒）：未检测到新窗口，'
                           '跳过本次手动选择，不判定为完成' % elapsed), flush=True)
    elif status == tc.TaskStatus.CANCELLED:
        print(color.yellow('用户停止，放弃等待作业窗口'), flush=True)
    else:
        print(color.red('等待作业窗口失败（%s）· %s' % (status, detail)), flush=True)
    return status, detail


class do_work(Answer):
    def __init__(self, driver, course_name, homework, API_KEY):
        Answer.__init__(self, driver, test_frame=None, course_name=course_name,
                        api=API_KEY, work_choice=None,
                        after_finish_question='仅自动保存')

        self.homework = homework
        self.driver = driver
        self.course_name = course_name
        # 作业页没有 iframe 链，题目直接铺在「作业作答」窗口里
        self._context_mode = 'window'
        # V5.1：结果状态显式化，绝不无条件打印「已完成作业」
        self.result_status = tc.TaskStatus.FAILED
        self.result_detail = '未开始'

        if self.homework == '自动选择':
            status = self.auto_choice_homework_question()
            self.result_status = status
            self.result_detail = '自动选择'
        elif self.homework == '手动选择':
            print(color.green('请手动选择要处理的作业，点开即可'), flush=True)
            try:
                baseline = len(driver.window_handles)
            except Exception:
                self.result_status = tc.TaskStatus.DRIVER_ERROR
                self.result_detail = '窗口句柄不可读'
                print(color.red('窗口句柄不可读，作业流程已停止'), flush=True)
                return
            status, detail = wait_for_homework_window(driver, baseline)
            self.result_status, self.result_detail = status, detail
            if status != tc.TaskStatus.SUCCESS:
                return
            # 保留 2 秒：新窗口渲染/标题稳定缓冲，属于平台动画缓冲而非任务识别等待
            print(color.yellow('[等待] 新作业窗口渲染缓冲 2 秒'), flush=True)
            time.sleep(2)
            saved = self.get_answer_list()
            self.result_status = (tc.TaskStatus.SUCCESS if saved
                                  else tc.TaskStatus.FAILED)
            self.result_detail = '仅自动保存' if saved else '保存失败'
        else:
            self.result_status = tc.TaskStatus.SKIPPED
            self.result_detail = '未选择作业处理模式'
            print(color.yellow('未选择作业处理模式，跳过'), flush=True)
            return

        if self.result_status == tc.TaskStatus.SUCCESS:
            # 措辞明确：本模式只自动保存，绝不声称「已完成作业」
            print(color.green('作业作答已保存（未提交，需人工确认后再提交），'
                              '15 秒后自动关闭窗口'), flush=True)
            time.sleep(15)
        else:
            print(color.yellow('作业未确认完成（%s / %s），不输出「已完成作业」'
                               % (self.result_status, self.result_detail)), flush=True)

    def auto_choice_homework_question(self):
        """自动选择并处理作业，返回 TaskStatus（调用者必须检查）。"""
        elements = self.driver.find_elements(By.CSS_SELECTOR, '[name="group-radio"]')
        target = pick_pending_homework(elements, getattr(self, 'homework', ''))
        if target is None:
            print(color.red(
                '无法从作业筛选里识别出「未完成」项（共 %d 个选项），'
                '为避免误选已提交作业，本次安全退出' % len(elements)), flush=True)
            return tc.TaskStatus.FAILED
        try:
            self.driver.execute_script('arguments[0].click();', target)
        except Exception:
            try:
                target.click()
            except Exception as e:
                print(color.red('作业筛选项点击失败（%s）' % type(e).__name__),
                      flush=True)
                return tc.TaskStatus.FAILED
        time.sleep(2)
        try:
            element = self.driver.find_element(By.CLASS_NAME, 'bottomList')
        except Exception:
            print(color.red('未检测到作业列表，本次安全退出'), flush=True)
            return tc.TaskStatus.FAILED

        homework_list = element.find_elements(By.TAG_NAME, 'li')
        total = len(homework_list)
        print(color.green(f'已检测到{total}个作业'), flush=True)
        if total == 0:
            return tc.TaskStatus.FAILED
        saved_count = 0
        failed = 0
        expected_names = []
        for item in homework_list:
            try:
                expected_names.append(item.get_attribute('aria-label'))
            except Exception:
                expected_names.append(None)

        for i in range(total):
            if i != 0:
                # 翻页/切窗口后必须重新定位，绝不复用上一页的 WebElement
                try:
                    self.turn_page(self.driver, self.course_name)
                    self.driver.switch_to.frame(
                        self.driver.find_element(By.TAG_NAME, 'iframe'))
                    element = self.driver.find_element(By.CLASS_NAME, 'bottomList')
                    fresh = element.find_elements(By.TAG_NAME, 'li')
                    if len(fresh) <= i:
                        raise RuntimeError('作业列表在翻页后变短')
                    homework = fresh[i]
                    # 校验任务点身份没有变化（不依赖会变化的数组位置）
                    name = homework.get_attribute('aria-label')
                    if expected_names[i] and name and name != expected_names[i]:
                        raise RuntimeError('作业项身份变化，拒绝误点其它作业')
                except Exception as e:
                    print(color.red('第%d个作业重新定位失败（%s），跳过该项'
                                    % (i + 1, type(e).__name__)), flush=True)
                    failed += 1
                    continue
            else:
                homework = homework_list[i]

            try:
                homework_name = homework.get_attribute('aria-label')
            except Exception as e:
                print(color.red('第%d个作业元素失效（%s），跳过该项'
                                % (i + 1, type(e).__name__)), flush=True)
                failed += 1
                continue
            print(color.green(f'开始处理第{i + 1}个作业：{homework_name}'), flush=True)
            try:
                homework.click()
            except Exception as e:
                print(color.red('第%d个作业点击失败（%s），跳过该项'
                                % (i + 1, type(e).__name__)), flush=True)
                failed += 1
                continue
            time.sleep(1)
            saved = self.get_answer_list()
            if saved:
                saved_count += 1
                print(color.green(f'第{i + 1}个作业已保存（未提交）'), flush=True)
            else:
                failed += 1
                print(color.red(f'第{i + 1}个作业保存失败，不计为完成'), flush=True)
            time.sleep(1)
            try:
                self.driver.close()
            except Exception:
                pass

        if failed == 0 and saved_count == total:
            return tc.TaskStatus.SUCCESS
        print(color.yellow('作业批处理结果：成功 %d/%d，失败 %d，'
                           '不报告全部完成' % (saved_count, total, failed)), flush=True)
        return tc.TaskStatus.FAILED

    def get_answer_list(self):
        """作答并保存；返回是否得到平台「暂时保存」按钮的明确确认。"""
        self.no_answer_dit = {}
        self.num_answer_dit = {}
        self.answer_list = []
        self.num_option_dit = {}
        self.all_title_dit = {}
        self.all_optionWebElementList = []
        self.optionWebElementList = []
        self.questionType_list = []
        self.only_title_text = []
        self.answer_num = []
        self.image_questions = set()
        # 切换到作业窗口
        self.turn_page(self.driver, '作业作答')
        # 作业作答页留档：题目/选项/「暂时保存」选择器和测验页完全不同，
        # 平台一改版就只能靠这份 DOM 离线定位（2026-11-19 用户要求）
        try:
            from core import dom_dump
            dom_dump.record(self.driver, '作业作答页',
                            {'course': getattr(self, 'course_name', '')})
        except Exception:
            pass
        self.questionList0 = self.driver.find_elements(By.CSS_SELECTOR,
                                                           '[class="padBom50 questionLi fontLabel singleQuesId"]')
        # 作业页的题目/选项定位器与测验页不同，记下来供 _relocate() 重定位
        self._q_selector = '[class="padBom50 questionLi fontLabel singleQuesId"]'
        self._opt_by = By.CSS_SELECTOR
        self._opt_value = '[class*="clearfix answerBg"]'
        print(color.green(f'作业题目数量：{len(self.questionList0)}'), flush=True)
        known_types = ['填空题', '判断题', '单选题', '多选题', '简答题', '名词解释', '论述题']
        for i in range(len(self.questionList0)):
            self.title_num = i + 1
            self.option_text_list = []
            self.optionWebElementList = []
            self.title_and_option_element=self.questionList0[i]
            self.title_and_option_text = self.title_and_option_element.text or ''
            self.questionType = self.title_and_option_element.get_attribute('typename')
            if self.questionType not in known_types:
                # 题型都读不出来，多半是整题图片；占位登记后跳过。
                # 这里绝不能 continue：一旦跳过一个题号，后面所有题的四个结构都会错位。
                self._register_unanswerable(i, f'题型为{self.questionType}')
                continue
            try:
                self.title = self.title_and_option_element.find_element(
                    By.CSS_SELECTOR, '[class="mark_name colorDeep fontLabel workTextWrap"]').text
            except Exception:
                self.title = ''
            self.title_text=self.title [self.title.find(")") + 1:]
            if not self.title_text.strip():
                self._register_unanswerable(i, '未检测到题目内容')
                continue
            self.only_title_text.append(self.title_text)
            self.questionType_list.append(self.questionType)
            self.optionWebElementList = self.title_and_option_element.find_elements(
                By.CSS_SELECTOR, '[class*="clearfix answerBg"]')
            self.all_optionWebElementList.append(self.optionWebElementList)
            for option_element in self.optionWebElementList:
                label = option_element.get_attribute('aria-label') or ''
                self.option_text_list.append(label[2:] if len(label) > 2 else label)
            self.all_title_dit[i] = self.title_and_option_text
            self.num_option_dit[i] = self.option_text_list
        print(color.red('正在搜索中，请耐心等待...'))
        has_ai = ai_config.is_key_valid(self.API_KEY)
        # 与章节测验同一处修正：题库查询不依赖 AI Key。未配 Key 时仍然查题库，
        # 只是不做 AI 兜底（旧实现直接建成「全部无答案」，整场作业必然空答）。
        self.use_ai_wen_da()
        if has_ai:
            for i, answer in self.num_answer_dit.items():
                if not answer and i not in self.image_questions:
                    self.no_answer_dit[i] = self.all_title_dit[i]
            self.use_deepseek()
        else:
            print(color.yellow('未配置有效的 AI Key：本轮作业只用题库作答，不做 AI 兜底'), flush=True)
        self.turn_page(self.driver, '作业作答')

        print(color.green('开始答题'), flush=True)
        for title_num in list(self.num_answer_dit.keys()):
            self.finish_title(title_num)
            time.sleep(0.5)
        self.save_elements = []
        try:
            self.save_elements = self.driver.find_element(By.ID, 'submitFocus').find_elements(By.TAG_NAME, 'a')
            print(color.red('暂时保存，AI答题不一定完全正确，请自行确认后再提交'), flush=True)
        except Exception:
            print(color.red('未找到保存区，平台无法确认保存，15 秒后继续'), flush=True)
            time.sleep(15)
            return False
        clicked = 0
        for save_element in self.save_elements:
            try:
                if save_element.text == '暂时保存':
                    save_element.click()
                    clicked += 1
            except Exception:
                pass
        if clicked == 0:
            print(color.red('未能点击「暂时保存」，平台未确认保存，不计为完成'),
                  flush=True)
            return False
        return True

    def _register_unanswerable(self, index, reason):
        """登记一道不准备作答的题（图片题/未知题型），保证题号与其他结构对齐。"""
        self.only_title_text.append('')
        self.questionType_list.append(self.questionType or '未知')
        self.all_optionWebElementList.append(None)
        self.image_questions.add(index)
        self.image_unanswered.add(index)
        self.all_title_dit[index] = self.title_and_option_text
        self.num_option_dit[index] = ['']
        print(color.yellow(f'第{index + 1}题{reason}，已跳过自动作答'), flush=True)
