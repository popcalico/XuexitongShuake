from selenium.webdriver.common.by import By
from task.tool import color
from core import paths
from core import runtime
import os
import re
import time
import ast
import json


def save_diagnostic_screenshot(driver, tag='诊断'):
    """把当前窗口截图存到 record/诊断截图/，返回路径（失败返回空串）。

    用户要求：遇到识别不了 / 处理出错的页面，都留一张截图当诊断日志，
    事后不用再靠口头描述复现。截图按 `标签_年月日_时分秒.png` 命名，不会互相覆盖。
    """
    if driver is None:
        return ''
    try:
        folder = paths.record('诊断截图')
        os.makedirs(folder, exist_ok=True)
        safe_tag = re.sub(r'[\\/:*?"<>|\s]+', '_', str(tag or '诊断')).strip('_')[:20] or '诊断'
        name = '%s_%s.png' % (safe_tag, time.strftime('%Y%m%d_%H%M%S'))
        path = os.path.join(folder, name)
        driver.save_screenshot(path)
        print(color.yellow(f'诊断截图已保存: record/诊断截图/{name}'), flush=True)
        return path
    except Exception as e:
        print(color.yellow(f'诊断截图保存失败: {e}'), flush=True)
        return ''


class Common:
    # 复习模式是否作用于本类型。只有「看/听」类任务点（视频 / PPT / 阅读 / 音频）
    # 设为 True；测验 / 作业 / 考试必须保持 False —— 重做会覆盖已有成绩。
    REVIEWABLE = False

    def __init__(self,driver,iframe_element,type,course_name,require_mission=True):
        self.course_name = course_name
        self.type=type
        self.driver=driver
        # 是否要求「任务点」图标存在。视频/PPT 等仍按任务点处理；
        # 测验/考试传 False —— 平台上的考试经常不算任务点，旧逻辑会把它整段跳过。
        self.require_mission = require_mission
        # 记住选择器与序号：测验/作业提交后页面会整页刷新，之前定位到的
        # iframe WebElement 会变成 stale，必须能按同样的选择器重新拿到它
        # （2026-11-19：提交成功却被判「平台未确认完成」的根因）。
        self.iframe_selector = iframe_element
        self.iframe_index = 0
        self.driver.switch_to.default_content()
        self.driver.switch_to.frame("iframe")
        self.iframes = self.driver.find_elements(By.CSS_SELECTOR,iframe_element)
        num = len(self.iframes)
        print(color.green(f'{self.type}数量：{num}'),flush=True)

    def _relocate_iframe(self):
        """按「选择器 + 序号」重新定位 iframe。

        真机场景：章节测验点「提交」后整个 `#iframe` 文档会重载，元素句柄全部失效。
        旧实现把提交前拿到的元素存进 `self.iframe` 一直复用，重载后
        `find_element('..')` 必然抛 stale，于是 `check_audio_finished()` 返回 False，
        上层打印「平台未确认完成，不写学习记录」——**提交明明成功了却当成没完成**。
        """
        selector = getattr(self, 'iframe_selector', '') or ''
        if not selector:
            return None
        try:
            index = int(getattr(self, 'iframe_index', 0))
        except (TypeError, ValueError):
            index = 0
        try:
            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            return None
        if 0 <= index < len(elements):
            self.iframe = elements[index]
            print(color.yellow(f'{self.type}页面已刷新，重新定位到第{index + 1}个任务点'), flush=True)
            return self.iframe
        return None

    def _iframe_parent_class(self):
        """取当前任务点 iframe 的父容器 class；旧句柄 stale 时自动重新定位一次。

        不用 `get_attribute` 之类的「探活」技巧，直接拿父节点——拿得到就是活的，
        拿不到才重新定位。这样对测试桩和真机都成立。
        """
        candidate = getattr(self, 'iframe', None)
        if candidate is not None:
            try:
                parent = candidate.find_element(By.XPATH, "..")
                return parent.get_attribute("class") or ""
            except Exception:
                pass
        candidate = self._relocate_iframe()
        if candidate is None:
            return ''
        try:
            parent = candidate.find_element(By.XPATH, "..")
            return parent.get_attribute("class") or ""
        except Exception:
            return ''

    def check_audio_finished(self):
        """该模块是否已完成：父容器 class 含 ans-job-finished 即视为已完成。

        旧实现用 `== 'ans-attach-ct ans-job-finished'` 精确比较，class 顺序/多一个空格
        就判不出来；取父元素失败时还会直接抛异常把整页带崩。这里改为包含匹配并兜底。
        """
        parent_element_class = self._iframe_parent_class()
        if 'ans-job-finished' in parent_element_class:
            print(color.green(f"该{self.type}已完成（父容器标记 ans-job-finished），跳过"), flush=True)
            return True
        return False

    def check_exist_mission(self):
        # 定位到该元素的所有兄弟元素（旧句柄 stale 时重新定位一次）
        xpath = "preceding-sibling::* | following-sibling::*"
        all_elements = None
        candidate = getattr(self, 'iframe', None)
        if candidate is not None:
            try:
                all_elements = candidate.find_elements(By.XPATH, xpath)
            except Exception:
                all_elements = None      # 旧句柄失效，下面重新定位
        if all_elements is None:
            candidate = self._relocate_iframe()
            all_elements = []
            if candidate is not None:
                try:
                    all_elements = candidate.find_elements(By.XPATH, xpath)
                except Exception:
                    all_elements = []
        for element in all_elements:
            class_attr = element.get_attribute("class") or ""
            if 'ans-job-icon ' in class_attr:
                print(color.green(f"该{self.type}存在任务点"), flush=True)
                return True
        print(color.green(f"该{self.type}不存在任务点"), flush=True)
        return False
    def verify_finished(self):
        """start() 之后确认平台是否**真的**把任务点标记完成。

        2026-11-19：默认值由 `True` 改为「按平台任务点打勾判定」。
        旧默认值让 PPT / 阅读 / 讨论三个没有覆写的子类无条件打印「已完成」并写
        学习记录（审计 P0-1），而它们其实只做了「滚到底 / 睡 10 秒」这类动作。
        子类可以继续覆写做更严格的检查（音频、直播已经这么做了）。
        """
        try:
            return bool(self.platform_finished())
        except Exception:
            # 取不到任务点状态一律按「未确认」处理（fail-closed）。
            # 旧行为是默认 True，等于把「读不到」当成「已完成」。
            return False

    def platform_finished(self):
        """回到任务点所在文档，按父容器是否含 ans-job-finished 判定完成。

        必须重切帧：`start()` 往往把 driver 留在更深层的 iframe 里，
        直接读 `self.iframe` 的父节点会拿到错误文档的节点。

        2026-11-19：提交后平台把任务点打成「已完成」是**异步**的，第一次读不到
        就等 1.5 秒再读一次，避免「刚提交完就判未完成」。
        """
        for attempt in (0, 1):
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame("iframe")
            except Exception:
                return False
            if self.check_audio_finished():
                return True
            if attempt == 0:
                time.sleep(1.5)
        return False

    def main(self):
        num=0
        for iframe in self.iframes:
            self.driver.switch_to.default_content()
            self.driver.switch_to.frame("iframe")
            self.iframe = iframe
            num+=1
            # 记住序号：提交/刷新后 `_alive_iframe()` 靠它重新定位同一个模块
            self.iframe_index = num - 1

            # 检测是否存在任务点（测验/考试不要求任务点，见 require_mission）
            if self.require_mission and not self.check_exist_mission():
                print(color.yellow(f'第{num}个{self.type}没有任务点标记，跳过'), flush=True)
                continue
            # 检查任务点是否已完成。
            # 复习模式：已完成也照做（刷学习时长/次数）。只有 REVIEWABLE 的
            # 「看/听」类会走到这里；测验/作业/考试永远是「已完成就跳过」。
            already_done = self.check_audio_finished()
            if already_done and not (self.REVIEWABLE and runtime.review_enabled()):
                continue
            if already_done:
                print(color.yellow(
                    f'复习模式：第{num}个{self.type}已完成，仍按要求重刷一遍'), flush=True)
            # 滚动到任务点：有的模块（尤其不算任务点的考试）前面没有兄弟 div，
            # 取不到就不滚动，绝不能因此把整场测验带崩。
            try:
                anchor = self.iframe.find_element(By.XPATH, "preceding-sibling::div[1]")
                self.driver.execute_script("arguments[0].scrollIntoView();", anchor)
            except Exception:
                pass
            print(color.green(f'开始第{num}个{self.type}'), flush=True)
            try:
                self.start()
            except Exception as e:
                print(color.red(f'第{num}个{self.type}处理出错，跳过：{e}'), flush=True)
                continue
            try:
                confirmed = self.verify_finished()
            except Exception as e:
                confirmed = False
                print(color.yellow(f'第{num}个{self.type}完成校验出错：{e}'), flush=True)
            if not confirmed:
                print(color.yellow(
                    f'第{num}个{self.type}平台未确认完成，不写学习记录'), flush=True)
                continue
            print(color.green(f'第{num}个{self.type}已完成'), flush=True)
            save_record(self.driver, self.course_name, time, iframe, self.type)
    def start(self):
        pass

def turn_page(driver,page_name,timeout=3):
    """切到标题含 page_name 的窗口，返回 bool（有界等待）。

    旧实现只遍历一遍就返回：新标签页的 document.title 还没落定时必然判失败，
    而调用方几乎都不检查返回值，于是在旧窗口里找元素 → 判「处理失败」。
    另外成功路径返回字符串 `'True'`、失败返回 `False`，类型不一致（审计 T1）。
    现在统一 `bool` + 有界轮询。

    2026-11-19 性能：默认超时从 8 秒降到 3 秒。真正的页面就绪由各任务自己的
    有界等待判断；这里只是「切窗口」，等太久会把整体拖慢。
    """
    deadline = time.time() + max(0.5, float(timeout or 0))
    while True:
        try:
            if page_name in (driver.title or ''):
                return True
        except Exception:
            pass
        try:
            handles = list(driver.window_handles or [])
        except Exception:
            handles = []
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if page_name in (driver.title or ''):
                    return True
            except Exception:
                continue
        if time.time() >= deadline:
            return False
        time.sleep(0.5)
def save_record(driver, course_name, time, iframe, typ):
    save_process(course_name)
    if typ == '测验':
        return
    # 2026-11-21：旧实现把取证和写文件全塞进一个裸 `except:`，
    # 页面结构一变（`prev_title` 改名 / iframe 的 data 不是 JSON）就只会打
    # 「保存进度失败」，看不出到底哪一步挂了。这里分步取证、分步兜底：
    # 章节标题/任务点名取不到也照样写记录，绝不因为"题目信息不完整"就整条丢失。
    title = ''
    name = ''
    try:
        driver.switch_to.default_content()
        element = driver.find_element(By.CLASS_NAME, 'prev_title')
        title = element.get_attribute('title') or ''
    except Exception:
        title = ''
    try:
        driver.switch_to.frame(driver.find_element(By.CSS_SELECTOR, '[id="iframe"]'))
        info = json.loads(iframe.get_attribute("data") or '{}')
        name = (info.get('name') or info.get('title')
                or info.get('bookname') or '')
    except Exception:
        name = ''
    try:
        with open(paths.record(f'《{course_name}》的学习记录.txt'), 'a',
                  encoding='utf-8') as f:
            f.write(
                f'已完成:《{title}》章节中的{typ}:<{name}>，完成时间：'
                f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))}\n\n')
        print(color.green('已保存进度'), flush=True)
    except Exception as e:
        print(color.red('保存进度失败（%s: %s）' % (type(e).__name__, e)), flush=True)
def save_process(course_name,done=0,total=0):
    with open(paths.data('tool', 'course_process.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)
        num_list=data.get(course_name,'')
        if num_list!='' and done==0:
            data[course_name]=[num_list[0]+1,num_list[1]]
        elif total!=0:
            data[course_name]=[done,total]
            print(color.green(f'已保存进度：{done}/{total}'), flush=True)
        with open(paths.data('tool', 'course_process.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

