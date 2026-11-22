"""守卫：已经作答的题不再查答案；填空题不重复写、不串空。

三个真机问题（2026-11-22 22:09 日志 + 用户截图）：

1. **未知原因退出**：`run()` 里裸调 `driver.switch_to.default_content()`，
   抛 `TimeoutException: Timed out receiving message from renderer: 45.000`，
   异常一路冒到 `run_main()`，整个账号进程退出（退出前刚答到第 19 题）。
2. **已作答的题还去查答案**：`use_ai_wen_da()` 一上来就遍历所有题查题库/问 AI，
   「是否已回答」的判断在后面的 `_click_option()` 里 —— 那时额度已经花掉了。
3. **填空题重复写 / 答案串空**：`_fill_blank()` 的候选 iframe 用了 `frames[index:]`
   （本题后面**所有** iframe），前一个候选回读失败就会把同一个答案往后面几个空里写；
   而且从不判断「这个空本来就有内容」。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('XXT_HOME', os.path.join(os.environ.get('TEMP', '.'), 'xxt_ans'))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  -> %s' % detail) if detail else ''))
    if not ok:
        FAILS.append(name)


def body_of(src, func):
    start = src.index('def %s(' % func)
    m = re.search(r'\n    def ', src[start + 5:])
    return src[start:start + 5 + m.start()] if m else src[start:]


def main():
    qsrc = open(os.path.join('task', 'quiz_ai.py'), encoding='utf-8').read()
    msrc = open('main.py', encoding='utf-8').read()

    print('=== 1. 已作答的选择题不再查答案（省额度） ===')
    check('有 _option_already_checked', 'def _option_already_checked(' in qsrc)
    loop = body_of(qsrc, 'use_ai_wen_da')
    check('use_ai_wen_da 循环开头就判已作答',
          '_option_already_checked(i)' in loop)
    first_branch = loop[loop.index('for i in self.all_title_dit.keys():'):]
    check('判断在 image / needs_ai / 题库搜索之前',
          first_branch.index('_option_already_checked(i)')
          < first_branch.index('_search_bank'))
    check('已作答的不再调 AI', '跳过搜索（省额度）' in qsrc)

    print()
    print('=== 2. 填空题不再串空 ===')
    fill = body_of(qsrc, '_fill_blank')
    # 注释里会保留 "旧实现是 frames[index:]" 的说明，只看代码行
    code_lines = [ln for ln in fill.splitlines() if not ln.strip().startswith('#')]
    code_only = '\n'.join(code_lines)
    check('代码里不再用 frames[index:]', 'frames[index:]' not in code_only)
    check('只取对应的那一个 iframe', 'candidates.append(frames[index])' in code_only)
    check('写之前判断该空是否已有内容', '_node_any_text(' in fill)
    check('已有相同答案 → 跳过不重写', '_same_answer(' in fill
          and '跳过不重写' in fill)
    check('已有其它内容 → 保留不覆盖', '保留不覆盖' in fill)
    check('优先真实点击（能触发 focus，编辑器才展开）',
          fill.index('blank_el.click()') < fill.index('execute_script'))
    check('点击后等编辑器挂载', 'time.sleep(0.25)' in fill)
    check('点击后检测元素是否失效', 'blank_el.tag_name' in fill)

    print()
    print('=== 3. 占位提示不能被当成已填 ===')
    node_fn = body_of(qsrc, '_node_any_text')
    check('过滤占位提示词', '请输入' in node_fn and 'hints' in node_fn)
    check('长度限制（只滤短提示）', 'len(compact) <= 8' in node_fn)

    print()
    print('=== 4. 答案等价判断（纯逻辑实测） ===')
    from task.quiz_ai import Answer
    check('完全相同判等价', Answer._same_answer('2', '2') is True)
    check('前后空格/括号差异判等价', Answer._same_answer(' ( X > 2 0 ) ', '(X>20)') is True)
    check('短答案不被长串误命中（1 vs 11）',
          Answer._same_answer('11', '1') is False, Answer._same_answer('11', '1'))
    check('中文逗号/顿号会被去掉',
          Answer._same_answer('优先级，结合性', '优先级') is True)
    check('完全不同判不等价', Answer._same_answer('优先级', '结合性') is False)
    check('空值判不等价', Answer._same_answer('', '2') is False)

    print()
    print('=== 5. 进程不再因浏览器超时退出 ===')
    check('有 _reset_to_default', 'def _reset_to_default(' in msrc)
    rfn = body_of(msrc, '_reset_to_default')
    check('失败后先刷新自愈', 'driver.refresh()' in rfn)
    run_src = msrc[msrc.index('def run(driver'):]
    run_src = run_src[:run_src.index('\ndef ')]
    check('run() 不再裸调 switch_to.default_content',
          '_reset_to_default(driver)' in run_src)
    check('恢复失败只结束本课程', "return 'lost'" in run_src)
    lost = msrc[msrc.index("while status == 'lost' and retry < 3"):]
    lost = lost[:lost.index('\n            # 复习模式')] if '\n            # 复习模式' in lost else lost[:3000]
    check('lost 循环里 _is_study_page 有 try 保护',
          'in_study = _is_study_page(driver)' in lost)
    check('lost 循环里 _reenter_course 有 try 保护',
          'reentered = _reenter_course(' in lost)

    print()
    if FAILS:
        print('FAILED: %s' % FAILS)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
