"""设置页面 — 配置、账号、功能、高级设置的全部 UI 和交互逻辑"""
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image
from gui.tooltip import ToolTip
from gui import theme
from core import paths

class SettingsPanel:
    def __init__(self, parent, config_manager, change_font,change_text_box,
                 get_font, get_colors, get_theme, show_money, root,_timer,
                 footer=None):
        self.timer_end_time = '03:00:00'
        self.timer_start_time = '04:00:00'
        self.parent = parent
        self.config = config_manager
        self.change_font = change_font
        self.change_text_box=change_text_box
        self._get_font = get_font
        self._get_colors = get_colors
        self._get_theme = get_theme
        self._show_money = show_money
        self.root = root
        self._timer=_timer
        # footer：设置页外面的固定底栏。「保存设置」放这里，滚动时不会跑出视野
        self.footer = footer
        self.is_topmost = True
        self.root.wm_attributes("-topmost", self.is_topmost)
        self.radio_var = tk.IntVar(value=1)
        self.dynamic_rows = []
        self._acc_index = {}
        self.next_row = 4
        self.all_courses = []
        self.data = []
        self.pady = 7
        self._build_ui()

    @property
    def _font(self): return self._get_font()

    @property
    def _colors(self): return self._get_colors()

    @property
    def _theme_colors(self): return self._get_theme()

    def _build_ui(self):
        c = self._colors
        f = self._font
        bg = self._theme_colors[1]

        # 左右两列各自独立堆叠卡片（2026-11-21）。
        # 旧版把所有卡片塞进同一个 grid 并用 rowspan 撑高级设置，
        # 结果右侧越矮的卡片越把左侧「账号信息」挤到很下面，
        # 左列中间就空出一大块 —— 用户看到的「大面积留白」正是它。
        self._left = ctk.CTkFrame(self.parent, fg_color='transparent')
        self._right = ctk.CTkFrame(self.parent, fg_color='transparent')
        for _col in (self._left, self._right):
            _col.columnconfigure(0, weight=1)

        cfg = ctk.CTkFrame(self._left, corner_radius=12, border_width=1,
                           fg_color=theme.palette()['surface'], border_color=theme.palette()['border'])
        cfg.grid(row=0, column=0, sticky="nsew", padx=0, pady=6)
        cfg.columnconfigure(1, weight=1)     # 输入框随窗口拉宽，不再固定 140 像素
        self.cfg_frame = cfg
        ctk.CTkLabel(cfg, text="配置设置", font=theme.font(13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 4))

        ctk.CTkLabel(cfg, text="浏览器:", font=f).grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.browser_entry = ctk.CTkComboBox(
            cfg, values=['不指定浏览器', 'edge', 'chrome'], state='readonly',
            font=f, corner_radius=6, dropdown_fg_color=bg,
            dropdown_hover_color=c[0], button_color=c[0],
            button_hover_color=c[2], command=self._auto_fill_browser_driver)
        self.browser_entry.grid(row=1, column=1, padx=6, pady=4, sticky="ew")

        ctk.CTkLabel(cfg, text="驱动地址:", font=f).grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.browser_driver_entry = ctk.CTkEntry(cfg, corner_radius=6, width=140)
        self.browser_driver_entry.grid(row=2, column=1, padx=6, pady=4, sticky="ew")
        self.open_file_button = ctk.CTkButton(
            cfg, text="选择文件", width=140, fg_color=c[0],
            hover_color=c[2], corner_radius=6, command=self._select_file)
        self.open_file_button.grid(row=3, column=1, padx=6, pady=4, sticky="ew")

        ctk.CTkLabel(cfg, text="并发账号数:", font=f).grid(row=4, column=0, padx=6, pady=4, sticky="w")
        self.concurrency_entry = ctk.CTkComboBox(
            cfg, values=['1', '2', '3', '4', '5'], width=140, state='readonly',
            font=f, corner_radius=6, dropdown_fg_color=bg,
            dropdown_hover_color=c[0], button_color=c[0], button_hover_color=c[2])
        self.concurrency_entry.grid(row=4, column=1, padx=6, pady=4, sticky="ew")

        ui = ctk.CTkFrame(self._left, corner_radius=12, border_width=1,
                          fg_color=theme.palette()['surface'], border_color=theme.palette()['border'])
        ui.grid(row=1, column=0, sticky="nsew", padx=0, pady=6)
        ui.columnconfigure(1, weight=1)
        self.ui_frame = ui
        ctk.CTkLabel(ui, text="界面设置", font=theme.font(13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 4))

        ctk.CTkLabel(ui, text="字体设置:", font=f).grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.font_entry = ctk.CTkComboBox(
            ui, values=["Helvetica", "Microsoft YaHei", '微软雅黑', '宋体', '楷体', '黑体'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], state='readonly',
            font=f, corner_radius=6, button_color=c[0], button_hover_color=c[2])
        self.font_entry.grid(row=1, column=1, padx=6, pady=4, sticky="ew")

        ctk.CTkLabel(ui, text="大小设置:", font=f).grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.size_entry = ctk.CTkComboBox(
            ui, values=['9','10','11','12','13','14','15','16'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], state='readonly',
            font=f, corner_radius=6, button_color=c[0], button_hover_color=c[2])
        self.size_entry.grid(row=2, column=1, padx=6, pady=4, sticky="ew")

        ctk.CTkLabel(ui, text="窗口置顶:", font=f).grid(row=3, column=0, padx=6, pady=4, sticky="w")
        self.topmost_check = ctk.CTkSwitch(ui, text="", command=self._toggle_topmost)
        self.topmost_check.grid(row=3, column=1, padx=6, pady=4, sticky="ew")
        self.topmost_check.select()

        self._build_account_section(bg, c, f)
        self._build_function_section(bg, c, f)
        self._build_detail_section(bg, c, f)

        # 「保存设置」放在滚动区外面（固定底栏），页面再长也不用滚到底才找得到
        self._save_in_footer = self.footer is not None
        parent_for_save = self.footer if self._save_in_footer else self.parent
        self.save_button = ctk.CTkButton(
            parent_for_save, text="保存设置", command=self.save,
            font=theme.font(13, "bold"), height=40,
            corner_radius=8, fg_color=c[0], hover_color=c[2])
        if self._save_in_footer:
            self.save_button.pack(fill='x', padx=6, pady=(2, 0))
        else:
            self.save_button.grid(row=3, column=0, columnspan=2, pady=(12, 18),
                                  padx=12, sticky="ew")
        # 2026-11-21：删掉 `parent.rowconfigure(3, weight=1)` 与
        # `ui.rowconfigure(3, weight=1)`。行权重加在「保存按钮行」和
        # 「窗口置顶行」上，等于让这两行吃掉所有多余高度 ——
        # 表现就是设置页卡片里一大片空白、开关飘在半空（用户截图）。
        self.parent.columnconfigure(0, weight=1)
        self.parent.columnconfigure(1, weight=1)
        # 先按双列摆好，之后再跟着窗口宽度在 1/2 列之间切换
        self._columns = None
        self._apply_layout(2)
        self.parent.bind('<Configure>', self._on_parent_resize, add='+')

    def _toggle_topmost(self):
        if self.topmost_check.get():
            self.is_topmost = False
            self.root.wm_attributes("-topmost", True)
        else:
            self.is_topmost = True
            self.root.wm_attributes("-topmost", False)

    # ---------- 响应式布局（2026-11-21） ----------

    # 双列需要的最小宽度；回差 40px 防止在阈值附近来回抖动
    LAYOUT_2COL_MIN = 900
    LAYOUT_1COL_MAX = 940

    def _on_parent_resize(self, event=None):
        """窗口变窄时自动切成单列：旧版固定两列，窗口一窄右列直接被挤出可视区，
        用户看到的就是「只剩左半边、右边一片空白」。"""
        try:
            width = int(event.width) if event is not None else int(self.parent.winfo_width())
        except Exception:
            return
        if width <= 1:
            return
        # 150% 缩放时 Tk 报的是物理像素（820 逻辑 = 1230 物理），
        # 不折算的话阈值永远够大、单列布局永远不触发。
        try:
            scale = ctk.ScalingTracker.get_window_scaling(self.parent) or 1.0
            width = int(width / scale)
        except Exception:
            pass
        if self._columns == 2 and width < self.LAYOUT_2COL_MIN:
            self._apply_layout(1)
        elif self._columns == 1 and width > self.LAYOUT_1COL_MAX:
            self._apply_layout(2)

    def _apply_layout(self, columns):
        if columns == getattr(self, '_columns', None):
            return
        self._columns = columns
        self._left.grid_forget()
        self._right.grid_forget()
        if columns >= 2:
            self._left.grid(row=0, column=0, sticky='nsew', padx=(6, 3), pady=0)
            self._right.grid(row=0, column=1, sticky='nsew', padx=(3, 6), pady=0)
            self.parent.columnconfigure(0, weight=1)
            self.parent.columnconfigure(1, weight=1)
            save_row, save_span = 1, 2
        else:
            self._left.grid(row=0, column=0, sticky='nsew', padx=6, pady=0)
            self._right.grid(row=1, column=0, sticky='nsew', padx=6, pady=0)
            self.parent.columnconfigure(0, weight=1)
            self.parent.columnconfigure(1, weight=0)
            save_row, save_span = 2, 1
        if not getattr(self, '_save_in_footer', False):
            self.save_button.grid_configure(row=save_row, column=0,
                                            columnspan=save_span,
                                            pady=(12, 18), padx=12, sticky='ew')

    def _build_account_section(self, bg, c, f):
        msg = ctk.CTkFrame(self._left, corner_radius=12, border_width=1,
                           fg_color=theme.palette()['surface'], border_color=theme.palette()['border'])
        msg.grid(row=2, column=0, sticky="nsew", padx=0, pady=6)
        msg.columnconfigure(1, weight=1)      # 手机号/密码/课程名跟着卡片拉宽
        self.msg_frame = msg
        ctk.CTkLabel(msg, text="账号信息", font=theme.font(13, "bold")).grid(
            row=0, column=0, sticky="w", padx=6, pady=(8, 4))

        # 多账号：标题行右侧放账号选择器 + 新增/删除
        sel = ctk.CTkFrame(msg, fg_color='transparent')
        sel.grid(row=0, column=1, columnspan=2, sticky="e", padx=6, pady=(8, 4))
        self.account_select = ctk.CTkComboBox(
            sel, values=[], width=130, font=f, state='readonly',
            dropdown_fg_color=bg, dropdown_hover_color=c[0],
            button_color=c[0], button_hover_color=c[2],
            command=self._switch_account)
        self.account_select.pack(side="left", padx=(0, 4))
        self.add_account_btn = ctk.CTkButton(
            sel, text="新增", width=46, height=26, font=f,
            fg_color=c[0], hover_color=c[2], command=self._add_account)
        self.add_account_btn.pack(side="left", padx=2)
        self.remove_account_btn = ctk.CTkButton(
            sel, text="删除", width=46, height=26, font=f,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self._remove_account)
        self.remove_account_btn.pack(side="left", padx=2)

        ctk.CTkLabel(msg, text="手机号:", font=f).grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.phone_number_entry = ctk.CTkEntry(msg, corner_radius=6, width=140)
        self.phone_number_entry.grid(row=1, column=1, padx=6, pady=4, sticky="ew")

        ctk.CTkLabel(msg, text="密码:", font=f).grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.password_entry = ctk.CTkEntry(msg, show='*', corner_radius=6, width=140)
        self.password_entry.grid(row=2, column=1, padx=(6, 0), pady=4, sticky="ew")
        show_img = ctk.CTkImage(light_image=Image.open(paths.asset('img', 'show.png')), size=(16, 12))
        self.show_pwd_btn = ctk.CTkButton(msg, text="", fg_color='transparent',
                                          image=show_img, command=self._show_password,
                                          font=f, width=5, height=10)
        self.show_pwd_btn.grid(row=2, column=2, padx=0, pady=4, sticky=tk.W)

        ctk.CTkLabel(msg, text="课程名称:", font=f).grid(row=3, column=0, padx=6, pady=4, sticky="w")
        self.cour_entry = ctk.CTkComboBox(
            msg, values=[], state='normal', dropdown_fg_color=bg,
            dropdown_hover_color=c[0], font=f, corner_radius=6, button_color=c[0])
        self.cour_entry.grid(row=3, column=1, padx=6, pady=4, sticky="ew")
        add_img = ctk.CTkImage(light_image=Image.open(paths.asset('img', 'add.png')), size=(18, 19))
        self.add_course_btn = ctk.CTkButton(
            msg, text="", fg_color='transparent', image=add_img,
            command=lambda: self.add_course(''), width=5, height=5)
        self.add_course_btn.grid(row=3, column=2, padx=1, pady=4, sticky=tk.W)
        ToolTip(self.add_course_btn, "添加课程")
        self.message_set_frame = msg

    def _build_function_section(self, bg, c, f):
        func = ctk.CTkFrame(self._right, corner_radius=12, border_width=1,
                            fg_color=theme.palette()['surface'], border_color=theme.palette()['border'])
        func.grid(row=0, column=0, sticky="nsew", padx=0, pady=6)
        ctk.CTkLabel(func, text="功能设置", font=theme.font(13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))
        self.radio_button_1 = ctk.CTkRadioButton(
            func, text="自动学习答题", variable=self.radio_var, value=1,
            command=self.function_choice, font=f)
        self.radio_button_2 = ctk.CTkRadioButton(
            func, text="自动完成作业", variable=self.radio_var, value=2,
            command=self.function_choice, font=f)
        self.radio_button_3 = ctk.CTkRadioButton(
            func, text="自动完成考试", variable=self.radio_var, value=3,
            command=self.function_choice, font=f)
        self.radio_button_1.grid(row=1, column=1, columnspan=3, padx=12, pady=6, sticky="w")
        self.radio_button_2.grid(row=2, column=1, columnspan=3, padx=12, pady=6, sticky="w")
        self.radio_button_3.grid(row=3, column=1, columnspan=3, padx=12, pady=6, sticky="w")
        self.function_set_frame = func

    def _build_detail_section(self, bg, c, f):
        det = ctk.CTkFrame(self._right, corner_radius=12, border_width=1,
                           fg_color=theme.palette()['surface'], border_color=theme.palette()['border'])
        det.grid(row=1, column=0, sticky="nsew", padx=0, pady=6)
        ctk.CTkLabel(det, text="高级设置", font=theme.font(13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))

        self.question_label = ctk.CTkLabel(det, text="章节测验:", font=f)
        self.question_entry = ctk.CTkComboBox(
            det, values=["DeepSeek AI", '随机答题', "不作答"],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], font=f,
            corner_radius=6, state='readonly', button_color=c[0],
            command=self.shua_ti_choice)

        self.after_finish_question = ctk.CTkLabel(det, text="答完题后:", font=f)
        self.after_finish_question_entry = ctk.CTkComboBox(
            det, values=["仅自动保存", '强制自动提交', '搜到60%的题自动提交',
                         '搜到70%的题自动提交', '搜到80%的题自动提交',
                         '搜到90%的题自动提交', '搜到100%的题自动提交'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], font=f,
            corner_radius=6, state='readonly', button_color=c[0])

        self.vido_question_label = ctk.CTkLabel(det, text="视频题目:", font=f)
        # 视频内弹题固定随机作答：平台没有错题校验，选错会提示，继续选下一个总能试到正确，
        # 不需要也不应该调用 AI（历史配置里的 DeepSeek AI 会被 coerce 成随机答题）。
        # 注意：customtkinter 6.0 下 state='disabled' 的 ComboBox 调 set() 不生效（get() 仍为空），
        # 这里必须用 readonly + 单选项，否则保存校验会认为没填。
        self.vido_question_entry = ctk.CTkComboBox(
            det, values=['随机答题'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], font=f,
            corner_radius=6, state='readonly', button_color=c[0])
        self.vido_question_entry.set('随机答题')
        ToolTip(self.vido_question_entry, "视频中弹出的题目固定随机作答\n（选错会提示，继续选下一个直到答对）")

        self.discussion_label = ctk.CTkLabel(det, text="讨论:", font=f)
        self.discussion_entry = ctk.CTkComboBox(
            det, values=["DeepSeek AI", '跳过讨论'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], font=f,
            corner_radius=6, state='readonly', button_color=c[0],
            command=lambda _: self.shua_ti_choice('讨论'))

        self.speed_label = ctk.CTkLabel(det, text="倍速设置:", font=f)
        self.speed_entry = ctk.CTkComboBox(
            det, values=['1','2','3','4','5','6','8','10','16'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], font=f,
            corner_radius=6, state='readonly', command=self._hint, button_color=c[0])

        # API Key 与 AI 地址/模型一起放在下方「AI 接口」子面板里（_build_extra_section 创建），
        # 不再随模式切换在主网格里 grid/grid_forget —— 以前选「随机答题」会把输入框整个隐藏。

        self.pass_face_label = ctk.CTkLabel(det, text="跳过人脸:", font=f)
        self.pass_face_check = ctk.CTkSwitch(det, text="",)
        ToolTip(self.pass_face_check, "只有当你的课程需要人脸认证时才开启")

        self.multi_device_label = ctk.CTkLabel(det, text="多端学习:", font=f)
        self.multi_device_check = ctk.CTkSwitch(det, text="",width=20,command=self._warn)
        ToolTip(self.multi_device_check, "多端学习状态兼容，可同时学习多门课程")

        self.pass_vido_label = ctk.CTkLabel(det, text='秒过视频:', font=f)
        self.pass_vido_check = ctk.CTkSwitch(det, text="", command=self._change_speed,)
        ToolTip(self.pass_vido_check, "只有在视频可以拖动进度条的情况下\n才会有效")

        self.lock_screen_label = ctk.CTkLabel(det, text="防锁屏:", font=f)
        self.lock_screen_check = ctk.CTkSwitch(det, text="",width=20)

        self.timer_label=ctk.CTkLabel(det,text='定时学习:',font=f)
        self.timer_check=ctk.CTkSwitch(det, text="",command=self.open_timer_window,)

        self.homework_label = ctk.CTkLabel(det, text="选择作业:", font=f)
        self.homework_entry = ctk.CTkComboBox(
            det, values=['手动选择', '自动选择'],
            dropdown_fg_color=bg, dropdown_hover_color=c[0], state='readonly',
            font=f, corner_radius=6, command=self._prompt, button_color=c[0])
        self.detail_set_frame = det
        self._build_extra_section(det, bg, c, f)

    def _build_extra_section(self, det, bg, c, f):
        """答题策略 / 讨论 / MeoW 推送：独立子面板占第 13 行，内部自带布局。

        放进独立 frame 是为了不挤动 _show_brush_mode / _show_homework_mode
        里写死的 grid 行号（4~12），切换模式时也不会被 grid_forget 掉。
        """
        box = ctk.CTkFrame(det, fg_color='transparent')
        box.grid(row=13, column=0, columnspan=4, sticky='ew', padx=6, pady=(6, 4))
        box.columnconfigure(1, weight=1)

        ctk.CTkLabel(box, text='答题与讨论', font=theme.font(12, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(2, 4))

        ctk.CTkLabel(box, text='无法处理的题:', font=f).grid(
            row=1, column=0, sticky='w', padx=(0, 6), pady=2)
        self.unanswerable_entry = ctk.CTkComboBox(
            box, values=['AI 尽力完成', '截图跳过'], state='readonly', font=f,
            width=170, dropdown_fg_color=bg, dropdown_hover_color=c[0],
            button_color=c[0])
        self.unanswerable_entry.set('AI 尽力完成')
        self.unanswerable_entry.grid(row=1, column=1, sticky='w', pady=2)
        ToolTip(self.unanswerable_entry,
                '默认 AI 尽力完成：读题干 → AI 作答 → 填进空位。\n'
                '数不出空位 / AI 失败 / 填不进去时自动降级为截图跳过，不卡住。\n'
                '计算题、连线题仍直接跳过。会消耗 API 额度。')

        ctk.CTkLabel(box, text='讨论范围:', font=f).grid(
            row=2, column=0, sticky='w', padx=(0, 6), pady=2)
        self.discussion_scope_entry = ctk.CTkComboBox(
            box, values=['任务点内全部讨论', '全部讨论（需真机校准）'],
            state='readonly', font=f, width=200,
            dropdown_fg_color=bg, dropdown_hover_color=c[0], button_color=c[0])
        self.discussion_scope_entry.set('任务点内全部讨论')
        self.discussion_scope_entry.grid(row=2, column=1, sticky='w', pady=2)
        ToolTip(self.discussion_scope_entry,
                '「任务点内全部讨论」：章节里遇到的讨论逐个完成。\n'
                '「全部讨论」：还要遍历整门课程讨论区，需要真机确认页面结构后再启用。')

        self.discussion_copy_check = ctk.CTkSwitch(
            box, text='抄已有回复（默认关 = 直接用 AI）', font=f)
        self.discussion_copy_check.grid(row=3, column=0, columnspan=3, sticky='w', pady=2)
        ToolTip(self.discussion_copy_check,
                '关闭时每个话题都由 AI 生成一段简短回复（约 30~50 字）；\n'
                '开启且话题已有回复时，改为随机挑一条现成回复抄过来。')

        ctk.CTkLabel(box, text='MeoW 推送', font=theme.font(12, 'bold')).grid(
            row=4, column=0, columnspan=3, sticky='w', pady=(8, 4))
        self.meow_enabled_check = ctk.CTkSwitch(box, text='启用通知', font=f)
        self.meow_enabled_check.grid(row=5, column=0, sticky='w', pady=2)
        # 任务03：设备 URL 属于凭据，界面默认掩码显示（与 API Key 一致）
        self.meow_url_entry = ctk.CTkEntry(
            box, placeholder_text='设备 URL（DPAPI 加密只存本机）', font=f, show='*')
        self.meow_url_entry.grid(row=5, column=1, sticky='ew', padx=5, pady=2)
        self.meow_url_toggle = ctk.CTkButton(
            box, text='👁', width=28, font=f, command=self._toggle_meow_url)
        self.meow_url_toggle.grid(row=5, column=3, sticky='w', padx=2)
        self.meow_test_btn = ctk.CTkButton(
            box, text='发送测试推送', width=100, font=f,
            fg_color=c[0], hover_color=theme.palette()['accent_hover'],
            command=self._test_meow)
        self.meow_test_btn.grid(row=5, column=2, sticky='w', pady=2)

        events_frame = ctk.CTkFrame(box, fg_color='transparent')
        events_frame.grid(row=6, column=0, columnspan=3, sticky='w', pady=(2, 2))
        self.meow_event_checks = {}
        for idx, (key, label) in enumerate([
                ('start_stop', '手动结束'),
                ('account', '账号与课程'),
                ('error', '详细错误'),
                ('sign_export', '签到/导出')]):
            sw = ctk.CTkSwitch(events_frame, text=label, font=f)
            sw.grid(row=0, column=idx, padx=(0, 10), sticky='w')
            sw.select()                      # 默认全开，用户可逐项关闭
            self.meow_event_checks[key] = sw

        # ------------------------------------------------ 课程考试（2026-11-08）
        # 常驻显示但不影响其它模式；只有选中「自动完成考试」时才会被内核读取。
        ctk.CTkLabel(box, text='课程考试（选中「自动完成考试」时生效）',
                     font=theme.font(12, 'bold')).grid(
            row=7, column=0, columnspan=3, sticky='w', pady=(10, 4))

        ctk.CTkLabel(box, text='答题方式:', font=f).grid(
            row=8, column=0, sticky='w', padx=(0, 6), pady=2)
        self.exam_answer_entry = ctk.CTkComboBox(
            box, values=['题库优先+AI兜底', '仅题库', '只识别不作答'],
            state='readonly', font=f, width=180,
            dropdown_fg_color=bg, dropdown_hover_color=c[0], button_color=c[0])
        self.exam_answer_entry.set('题库优先+AI兜底')
        self.exam_answer_entry.grid(row=8, column=1, sticky='w', pady=2)
        ToolTip(self.exam_answer_entry,
                '题库优先+AI兜底：先查免费题库，没命中再问 AI（消耗额度）。\n'
                '仅题库：不联网问 AI。只识别不作答：只解析题目写日志，不点任何选项。')

        self.exam_auto_submit_check = ctk.CTkSwitch(
            box, text='允许按阈值自动交卷（默认关，提交不可逆）', font=f,
            command=self._warn_exam_submit)
        self.exam_auto_submit_check.grid(
            row=9, column=0, columnspan=3, sticky='w', pady=2)
        ToolTip(self.exam_auto_submit_check,
                '考试一旦开始倒计时不停止、离开界面也不暂停，截止会自动提交。\n'
                '开启后程序达到阈值才交卷；提交不可逆。请先用模拟考试演练。')

        ctk.CTkLabel(box, text='交卷阈值:', font=f).grid(
            row=10, column=0, sticky='w', padx=(0, 6), pady=2)
        self.exam_submit_ratio_entry = ctk.CTkComboBox(
            box, values=['搜到100%的题', '搜到90%的题', '搜到80%的题',
                         '搜到70%的题', '搜到60%的题'],
            state='readonly', font=f, width=150,
            dropdown_fg_color=bg, dropdown_hover_color=c[0], button_color=c[0])
        self.exam_submit_ratio_entry.set('搜到100%的题')
        self.exam_submit_ratio_entry.grid(row=10, column=1, sticky='w', pady=2)
        ToolTip(self.exam_submit_ratio_entry,
                '沿用既有「答题率」语义 = 搜到/点到的题比例，不是正确率。')

        ctk.CTkLabel(box, text='交卷前可撤销(秒):', font=f).grid(
            row=11, column=0, sticky='w', padx=(0, 6), pady=2)
        self.exam_revoke_entry = ctk.CTkEntry(box, width=70, font=f)
        self.exam_revoke_entry.insert(0, '15')
        self.exam_revoke_entry.grid(row=11, column=1, sticky='w', pady=2)

        ctk.CTkLabel(box, text='剩余不足则放弃交卷(分):', font=f).grid(
            row=12, column=0, sticky='w', padx=(0, 6), pady=2)
        self.exam_min_remain_entry = ctk.CTkEntry(box, width=70, font=f)
        self.exam_min_remain_entry.insert(0, '10')
        self.exam_min_remain_entry.grid(row=12, column=1, sticky='w', pady=2)

        # -------------------------------------------- OpenAI 兼容 AI 接口（2026-11-10）
        ctk.CTkLabel(box, text='AI 接口（任意 OpenAI 兼容服务）',
                     font=theme.font(12, 'bold')).grid(
            row=13, column=0, columnspan=3, sticky='w', pady=(10, 4))

        # API Key 与地址/模型放在同一组、常驻显示。
        # 原先它挂在主网格 det 上，会随「章节/作业/考试」模式切换被 grid/grid_forget，
        # 选「随机答题」时甚至整个隐藏，导致用户改不了 Key。现在统一下沉到这个独立子面板。
        ctk.CTkLabel(box, text='API Key:', font=f).grid(
            row=14, column=0, sticky='w', padx=(0, 6), pady=2)
        self.API_entry = ctk.CTkEntry(box, show='*', font=f)
        self.API_entry.grid(row=14, column=1, sticky='ew', padx=5, pady=2)
        api_show_img = ctk.CTkImage(
            light_image=Image.open(paths.asset('img', 'show.png')), size=(16, 12))
        self.show_api_btn = ctk.CTkButton(
            box, text='', fg_color='transparent', image=api_show_img,
            command=self._show_api, font=f, width=5, height=10)
        self.show_api_btn.grid(row=14, column=2, sticky='w', pady=2)
        ToolTip(self.API_entry,
                '任意 OpenAI 兼容服务的 API Key（不以 sk- 开头也支持）。\n'
                '与下面的 API 地址、模型名称配套使用；点右侧眼睛可显示明文。')
        # 兼容旧代码里对 self.API_label 的引用（模式切换已不再操作它）
        self.API_label = ctk.CTkLabel(box, text='', font=f)

        ctk.CTkLabel(box, text='API 地址:', font=f).grid(
            row=15, column=0, sticky='w', padx=(0, 6), pady=2)
        self.ai_base_url_entry = ctk.CTkEntry(
            box, font=f, placeholder_text='https://api.deepseek.com')
        self.ai_base_url_entry.grid(row=15, column=1, columnspan=2, sticky='ew', padx=5, pady=2)
        ToolTip(self.ai_base_url_entry,
                'OpenAI 兼容的基地址，SDK 会自动拼 /chat/completions。\n'
                '例：https://api.openai.com/v1、https://api.deepseek.com、\n'
                'http://localhost:11434/v1（本地 Ollama）。留空则默认 DeepSeek。')

        ctk.CTkLabel(box, text='模型名称:', font=f).grid(
            row=16, column=0, sticky='w', padx=(0, 6), pady=2)
        self.ai_model_entry = ctk.CTkEntry(
            box, font=f, placeholder_text='deepseek-v4-flash')
        self.ai_model_entry.grid(row=16, column=1, sticky='ew', padx=5, pady=2)
        ToolTip(self.ai_model_entry,
                '文本答题 / 讨论 / 题库兜底使用的模型，按服务商实际名称填写。\n'
                '例：gpt-4o-mini、qwen-plus、deepseek-chat。留空用 deepseek-v4-flash。')

        ctk.CTkLabel(box, text='视觉模型(选填):', font=f).grid(
            row=17, column=0, sticky='w', padx=(0, 6), pady=2)
        self.ai_vision_entry = ctk.CTkEntry(box, font=f)
        self.ai_vision_entry.grid(row=17, column=1, sticky='ew', padx=5, pady=2)
        ToolTip(self.ai_vision_entry,
                '图片题使用支持视觉输入的模型；留空则复用上面的「模型名称」。\n'
                '文本模型若不支持图片输入，把视觉模型单独填这里即可。')

        ctk.CTkLabel(box, text='超时(秒,0=默认):', font=f).grid(
            row=18, column=0, sticky='w', padx=(0, 6), pady=2)
        self.ai_timeout_entry = ctk.CTkEntry(box, width=70, font=f)
        self.ai_timeout_entry.grid(row=18, column=1, sticky='w', padx=5, pady=2)
        self.ai_test_btn = ctk.CTkButton(
            box, text='测试连接', width=100, font=f,
            fg_color=c[0], hover_color=theme.palette()['accent_hover'],
            command=self._test_ai)
        self.ai_test_btn.grid(row=18, column=2, sticky='w', pady=2)
        ToolTip(self.ai_test_btn,
                '主动点击才会发一次极小的请求，验证地址/模型/Key 是否可用。\n'
                '可能产生极少量费用；不会自动重试。')

        # ------------------------------------------------ 内容处理（2026-11-19 移植）
        ctk.CTkLabel(box, text='内容处理', font=theme.font(12, 'bold')).grid(
            row=19, column=0, columnspan=3, sticky='w', pady=(10, 4))

        self.vote_enabled_check = ctk.CTkSwitch(box, text='自动问卷', font=f)
        self.vote_enabled_check.grid(row=20, column=0, columnspan=2, sticky='w', pady=2)
        self.vote_enabled_check.select()      # 默认开启
        ToolTip(self.vote_enabled_check,
                '开启后：遇到问卷随机选答案（填空题填「无」）并提交。\n'
                '关闭后：问卷仍会被识别为已知页面并跳过，不会被当成「未识别页」累计。')

        self.review_check = ctk.CTkSwitch(box, text='复习模式', font=f)
        self.review_check.grid(row=21, column=0, columnspan=2, sticky='w', pady=2)
        ToolTip(self.review_check,
                '开启后：已完成的视频 / PPT / 阅读 / 音频会从头再刷一遍，'
                '用于刷学习时长与次数。\n'
                '测验、作业、考试绝不会重做（会覆盖已有成绩）。\n'
                '注意：复习时「秒过视频」自动失效，否则没有真实观看时长。')

        ctk.CTkLabel(box, text='复习遍数:', font=f).grid(
            row=22, column=0, sticky='w', padx=(0, 6), pady=2)
        self.review_times_entry = ctk.CTkEntry(box, width=70, font=f,
                                               placeholder_text='1')
        self.review_times_entry.insert(0, '1')
        self.review_times_entry.grid(row=22, column=1, sticky='w', padx=5, pady=2)
        ToolTip(self.review_times_entry,
                '复习模式刷几遍。填 1 表示刷一遍就停；填 0 表示不限制（跑到手动停止）。\n'
                '单遍不足 30 秒会自动判定「没有可刷内容」并停止，避免空转。')

    def _warn_exam_submit(self):
        """用户打开自动交卷时做一次显式二次确认；取消就立刻关回去。"""
        if not self.exam_auto_submit_check.get():
            return
        ok = messagebox.askyesno(
            '自动交卷二次确认',
            '考试一旦开始，倒计时不会停止，离开界面也不暂停，截止会自动提交。\n\n'
            '开启后程序会在达到「搜到题目比例」阈值、且剩余时间充足时自动交卷，'
            '提交不可逆，且作答次数有限。\n\n'
            '请确认已经用不影响成绩的模拟考试演练过，并会自行核对答案。确认开启吗？')
        if not ok:
            self.exam_auto_submit_check.deselect()

    def _exam_config(self) -> dict:
        def _int(entry, default, lo, hi):
            try:
                return max(lo, min(hi, int(str(entry.get()).strip())))
            except Exception:
                return default
        digits = ''.join(ch for ch in self.exam_submit_ratio_entry.get()
                         if ch.isdigit())
        return {
            'exam_answer_mode': self.exam_answer_entry.get() or '题库优先+AI兜底',
            'exam_auto_submit': int(self.exam_auto_submit_check.get()),
            'exam_submit_ratio': int(digits) if digits else 100,
            'exam_revoke_seconds': _int(self.exam_revoke_entry, 15, 0, 120),
            'exam_min_remain_minutes': _int(self.exam_min_remain_entry, 10, 1, 60),
            'exam_max_questions': 200,
        }

    def _meow_events(self) -> dict:
        """把 4 组界面开关展开成 notifier 认识的事件名 → bool。"""
        checked = {k: bool(v.get()) for k, v in self.meow_event_checks.items()}
        groups = {
            'start_stop': ('stop',),
            'account': ('account_finish', 'all_finish', 'course_finish', 'course_abnormal'),
            'error': ('error',),
            'sign_export': ('sign_start', 'sign_stop', 'export_done'),
        }
        out = {}
        for key, events in groups.items():
            for event in events:
                out[event] = checked.get(key, True)
        return out

    def _meow_config(self) -> dict:
        return {
            'meow_enabled': int(self.meow_enabled_check.get()),
            'meow_url': self.meow_url_entry.get().strip(),
            'meow_category_enabled': 1,
            'meow_events': self._meow_events(),
        }

    def _ai_config(self) -> dict:
        """从界面读取 OpenAI 兼容接口配置（测试连接与保存共用）。"""
        try:
            timeout = max(0, min(600, int(str(self.ai_timeout_entry.get()).strip() or 0)))
        except (TypeError, ValueError):
            timeout = 0
        return {
            'ai_base_url': self.ai_base_url_entry.get().strip(),
            'ai_model': self.ai_model_entry.get().strip(),
            'ai_vision_model': self.ai_vision_entry.get().strip(),
            'ai_timeout': timeout,
        }

    def _test_ai(self):
        """后台线程发一次极小请求；主线程用 after 轮询结果，绝不卡界面。"""
        import threading
        from core import ai_config

        api_key = self.API_entry.get().strip()
        overrides = self._ai_config()
        holder = {}
        self.ai_test_btn.configure(state=tk.DISABLED, text='测试中…')

        def worker():
            try:
                holder['result'] = ai_config.test_connection(api_key, overrides)
            except Exception as e:
                holder['result'] = {'ok': False, 'msg': '%s: %s' % (type(e).__name__, e),
                                    'config': '', 'reply': ''}
            holder['done'] = True

        def poll(waited=0):
            # 只有主线程会执行这里，控件操作与弹窗都是安全的
            if not holder.get('done') and waited < 30000:
                self.ai_test_btn.after(200, lambda: poll(waited + 200))
                return
            self.ai_test_btn.configure(state=tk.NORMAL, text='测试连接')
            r = holder.get('result') or {'ok': False,
                                         'msg': '请求超时（30 秒），未重试',
                                         'config': ai_config.describe(ai_config.resolve(overrides)),
                                         'reply': ''}
            detail = '\n'.join(x for x in (r.get('config'), r.get('reply')) if x)
            if r.get('ok'):
                messagebox.showinfo('连接成功', '%s\n%s' % (r['msg'], detail))
            else:
                messagebox.showerror('连接失败', '%s\n%s\n\n请检查 API 地址、模型名称与 Key。'
                                     % (r.get('msg', ''), detail))

        threading.Thread(target=worker, daemon=True).start()
        self.ai_test_btn.after(200, poll)

    def _test_meow(self):
        """用户主动点击才真正发送；遵守 skill「只有明确要求才执行推送」。"""
        from core.notifier import Notifier
        n = Notifier.instance()
        n.configure(self._meow_config())
        result = n.send_test()
        if result.get('ok'):
            messagebox.showinfo('推送成功',
                                'API 已接受：status=%s\n%s'
                                % (result.get('status'), result.get('msg') or ''))
        else:
            messagebox.showerror('推送失败',
                                 'status=%s\n%s\n\n注意：超时或 500 表示送达结果不确定，'
                                 '程序不会自动重试。'
                                 % (result.get('status'), result.get('msg') or ''))

    def open_timer_window(self):
        if self.timer_check.get():
            self._timer.open(self.timer_start_time, self.timer_end_time)
        else:
            self._timer.cancel()

    def _warn(self):
        if self.multi_device_check.get():
            tk.messagebox.showwarning('警告', '请谨慎开启：开启后可能出现课程进度异常，'
                                         '建议一次专注一门课程，相关后果请自行评估')

    def _change_speed(self):
        if self.pass_vido_check.get():
            self.speed_entry.set('1')
            self.speed_entry.configure(state='disabled')
            messagebox.showinfo('提示', '只有在视频可以拖动进度条的情况下才会有效')
        else:
            self.speed_entry.configure(state='normal')

    def add_course(self, cour_name):
        row = self.next_row
        self.next_row += 1
        if row >= 6:
            messagebox.showwarning('警告', '最多只能添加3门课程')
            self.next_row -= 1
            return
        c, f, bg = self._colors, self._font, self._theme_colors[1]
        entry = ctk.CTkComboBox(
            self.message_set_frame, values=self.data, state='normal',
            dropdown_fg_color=bg, dropdown_hover_color=c[0],
            font=f, corner_radius=6, button_color=c[0])
        entry.set(cour_name)
        entry.grid(row=row, column=1, padx=6, pady=4, sticky="w")
        del_btn = ctk.CTkButton(
            self.message_set_frame, text="×", width=20,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=lambda: self._delete_course_row(entry, del_btn, row))
        del_btn.grid(row=row, column=2, padx=2, pady=4, sticky=tk.W)
        tip = ToolTip(del_btn, "删除该课程")
        self.dynamic_rows.append((entry, del_btn, row, tip))
        if row == 5 and len(self.dynamic_rows) >= 2:
            prev_entry, prev_del_btn, prev_row, prev_tip = self.dynamic_rows[-2]
            prev_del_btn.configure(state='disabled')
            prev_tip.set_text("请先删除最后一个课程")

    def _delete_course_row(self, entry, del_btn, row):
        entry.destroy()
        del_btn.destroy()
        for i, (e, d, r, tip) in enumerate(self.dynamic_rows):
            if e == entry:
                self.dynamic_rows.pop(i)
                break
        self.next_row -= 1
        if self.next_row == 5 and len(self.dynamic_rows) >= 1:
            last_entry, last_del_btn, last_row, last_tip = self.dynamic_rows[-1]
            last_del_btn.configure(state='normal')
            last_tip.set_text("删除该课程")

    def function_choice(self):
        if self.radio_var.get() == 1:
            self._show_brush_mode()
        elif self.radio_var.get() == 2:
            self._show_homework_mode()
        elif self.radio_var.get() == 3:
            self._show_exam_mode()
        else:
            # 防御：配置里被写进非法值时回到章节模式，不要悄悄变成作业
            self.radio_var.set(1)
            self.function_choice()

    def _show_brush_mode(self):
        self.question_label.configure(text='章节测验:')
        self.question_label.grid(row=4, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.grid(row=4, column=2, padx=5, pady=self.pady, sticky=tk.W)
        if self.question_entry.get() == 'DeepSeek AI':
            self.after_finish_question.grid(row=5, column=1, padx=5, pady=self.pady, sticky=tk.W)
            self.after_finish_question_entry.grid(row=5, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.after_finish_question_entry.configure(values=[
            "仅自动保存", '强制自动提交', '搜到60%的题自动提交',
            '搜到70%的题自动提交', '搜到80%的题自动提交',
            '搜到90%的题自动提交', '搜到100%的题自动提交'])
        self.vido_question_label.grid(row=6, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.vido_question_entry.grid(row=6, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.discussion_label.grid(row=7, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.discussion_entry.grid(row=7, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.configure(values=['DeepSeek AI', '随机答题', '不作答'])
        # API Key 已常驻下方「AI 接口」子面板，不再随模式切换摆放
        self.speed_label.grid(row=9, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.speed_entry.grid(row=9, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.pass_face_label.grid(row=10, column=1, padx=5, pady=5, sticky=tk.W)
        self.pass_face_check.grid(row=10, column=2, padx=0, pady=5, sticky=tk.W)
        self.multi_device_label.grid(row=10, column=2, padx=5, pady=5, sticky=tk.E)
        self.multi_device_check.grid(row=10, column=3, padx=0, pady=5, sticky=tk.W)
        self.pass_vido_label.grid(row=11, column=1, padx=5, pady=5, sticky=tk.W)
        self.pass_vido_check.grid(row=11, column=2, padx=0, pady=5, sticky=tk.W)
        self.lock_screen_label.grid(row=11, column=2, padx=5, pady=5, sticky=tk.E)
        self.lock_screen_check.grid(row=11, column=3, padx=0, pady=5, sticky=tk.W)
        self.timer_label.grid(row=12, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.timer_check.grid(row=12, column=2, padx=0, pady=5, sticky=tk.W)
        self.homework_label.grid_forget()
        self.homework_entry.grid_forget()

    def _show_homework_mode(self):
        self.question_label.configure(text='作业答题')
        self.question_label.grid(row=4, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.grid(row=4, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.set('DeepSeek AI')
        self.question_entry.configure(values=['DeepSeek AI'])
        self.after_finish_question.grid(row=5, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.after_finish_question_entry.grid(row=5, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.after_finish_question_entry.set('仅自动保存')
        self.after_finish_question_entry.configure(values=['仅自动保存'])
        # API Key 常驻下方「AI 接口」子面板
        self.homework_label.grid(row=9, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.homework_entry.grid(row=9, column=2, padx=5, pady=self.pady, sticky=tk.W)
        for w in [self.speed_label, self.speed_entry, self.lock_screen_label,
                  self.lock_screen_check, self.vido_question_label,
                  self.vido_question_entry, self.discussion_label,
                  self.discussion_entry, self.pass_vido_label, self.pass_vido_check]:
            w.grid_forget()

    def _show_exam_mode(self):
        """考试模式：只保留答题与 API，交卷由下方「课程考试」子面板控制。

        这里刻意不复用 after_finish_question 的「强制自动提交」等值 ——
        考试提交不可逆，必须走 exam_auto_submit 的独立开关与守卫。
        """
        self.question_label.configure(text='考试答题:')
        self.question_label.grid(row=4, column=1, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.grid(row=4, column=2, padx=5, pady=self.pady, sticky=tk.W)
        self.question_entry.configure(values=['DeepSeek AI'])
        self.question_entry.set('DeepSeek AI')
        self.after_finish_question_entry.set('仅自动保存')
        self.after_finish_question_entry.configure(values=['仅自动保存'])
        self.after_finish_question.grid_forget()
        self.after_finish_question_entry.grid_forget()
        # API Key 常驻下方「AI 接口」子面板，考试模式也照样可见可改
        for w in [self.speed_label, self.speed_entry, self.lock_screen_label,
                  self.lock_screen_check, self.vido_question_label,
                  self.vido_question_entry, self.discussion_label,
                  self.discussion_entry, self.pass_vido_label, self.pass_vido_check,
                  self.homework_label, self.homework_entry,
                  self.pass_face_label, self.pass_face_check,
                  self.multi_device_label, self.multi_device_check,
                  self.timer_label, self.timer_check]:
            w.grid_forget()

    def shua_ti_choice(self, event=None):
        # 考试模式固定只答不交卷，别让「答完题后」控件被重新摆出来
        if self.radio_var.get() == 3:
            return
        if self.question_entry.get() == '随机答题' and event != '讨论':
            messagebox.showinfo('提示', '请谨慎选择，只有在章节测验不计入总成绩的情况下才能使用...')
        if self.question_entry.get() != 'DeepSeek AI':
            self.after_finish_question.grid_forget()
            self.after_finish_question_entry.grid_forget()
        else:
            self.after_finish_question.grid(row=5, column=1, padx=5, pady=self.pady, sticky=tk.W)
            self.after_finish_question_entry.grid(row=5, column=2, padx=5, pady=self.pady, sticky=tk.W)
        # API Key 常驻「AI 接口」子面板，不再随答题方式隐藏；
        # 否则选「随机答题」后用户连测连接、改 Key 都做不到。

    def _auto_fill_browser_driver(self, choice):
        self.browser_driver_entry.configure(state='normal')
        self.browser_driver_entry.delete(0, tk.END)
        if choice == "edge":
            self.browser_driver_entry.insert(0, r"edgedriver_win64\msedgedriver.exe")
        elif choice == "不指定浏览器":
            self.browser_driver_entry.insert(0, "无需填写")
            self.browser_driver_entry.configure(state='disabled')

    def _select_file(self):
        path = filedialog.askopenfilename(title="选择文件", filetypes=[("", "*.exe")])
        if path:
            self.browser_driver_entry.delete(0, tk.END)
            self.browser_driver_entry.insert(tk.END, path)

    def _show_password(self):
        self.password_entry.configure(
            show='' if self.password_entry.cget('show') == '*' else '*')

    def _show_api(self):
        self.API_entry.configure(
            show='' if self.API_entry.cget('show') == '*' else '*')

    def _toggle_meow_url(self):
        self.meow_url_entry.configure(
            show='' if self.meow_url_entry.cget('show') == '*' else '*')

    def _hint(self, speed):
        if int(speed) > 2:
            messagebox.showinfo('提示', '倍数过高，已完成的任务点可能会被清空，请谨慎使用')

    def _prompt(self, choice):
        if choice == '自动选择':
            messagebox.showinfo('提示', '默认会从第一个未完成的作业开始处理...')
        else:
            messagebox.showinfo('提示', '到达作业页面后请手动选择要处理的作业...')

    # ========== 多账号管理 ==========

    def refresh_accounts(self):
        accounts = self.config.list_accounts()
        self._acc_index = {}
        values = []
        for acc in accounts:
            label = acc['alias'] or acc['phone'] or '未命名'
            self._acc_index[label] = acc['id']
            values.append(label)
        self.account_select.configure(values=values or ['（无账号）'])
        current = self.config.get_account_by_id(self.config.current_id())
        cur_label = current.get('alias') or current.get('phone_number') or '未命名'
        if cur_label not in self._acc_index and values:
            cur_label = values[0]
        self.account_select.set(cur_label)

    def _collect_form(self) -> dict:
        courses = [self.cour_entry.get().replace('\n', '')]
        courses += [e.get().replace('\n', '') for e, _, _, _ in self.dynamic_rows
                    if e.get().replace('\n', '')]
        self.all_courses = courses
        # “无需填写”只是输入框的占位提示，不能当路径存下来
        driver_path = self.browser_driver_entry.get().strip()
        if driver_path == '无需填写' or not os.path.isfile(driver_path):
            driver_path = ''
        return {
            'browser': self.browser_entry.get(),
            'driver_path': driver_path,
            'phone_number': self.phone_number_entry.get().replace('\n', ''),
            'password': self.password_entry.get().replace('\n', ''),
            'cour': courses,
            'choice': self.question_entry.get(),
            'after_finish_question': self.after_finish_question_entry.get(),
            'video_title_choice': self.vido_question_entry.get(),
            'discussion_choice': self.discussion_entry.get(),
            'API': self.API_entry.get(),
            'speed': self.speed_entry.get(),
            'homework': self.homework_entry.get(),
            'task_type': {1: '章节', 2: '作业', 3: '考试'}.get(
                self.radio_var.get(), '章节'),
            'font_type': self.font_entry.get(),
            'font_size': self.size_entry.get(),
            'pass_face': self.pass_face_check.get(),
            'multi_device': self.multi_device_check.get(),
            'pass_vido': self.pass_vido_check.get(),
            'lock_screen': self.lock_screen_check.get(),
            'radio_var': self.radio_var.get(),
            'max_concurrency': int(self.concurrency_entry.get() or 2),
            'unanswerable_policy': self.unanswerable_entry.get() or 'AI 尽力完成',
            'discussion_scope': self.discussion_scope_entry.get() or '任务点内全部讨论',
            'discussion_copy_reply': int(self.discussion_copy_check.get()),
            'vote_enabled': int(self.vote_enabled_check.get()),
            'review': int(self.review_check.get()),
            'review_times': self._review_times_value(),
            **self._meow_config(),
            **self._exam_config(),
            **self._ai_config(),
        }

    def _review_times_value(self) -> int:
        """复习遍数：空/非法按 1 处理；0 或负数表示不限制（由内核兜底防空转）。"""
        try:
            return int(str(self.review_times_entry.get()).strip() or 1)
        except (TypeError, ValueError):
            return 1

    def _persist_form(self):
        """切换账号前把当前表单写回当前账号（不弹窗、不校验）。"""
        try:
            account = self.config.get_account()
            account.update(self._collect_form())
            self.config.save_account(account)
        except Exception:
            pass

    def _reset_form(self):
        for w in (self.phone_number_entry, self.password_entry, self.API_entry,
                  self.browser_driver_entry):
            w.delete(0, 'end')
        for combo in (self.cour_entry, self.speed_entry, self.question_entry,
                      self.after_finish_question_entry,
                      self.discussion_entry, self.homework_entry):
            combo.set('')
        # 视频题固定随机作答，清空表单后也要还原，否则保存校验会认为没填
        self.vido_question_entry.set('随机答题')
        # 新增项重置为默认值（设备 URL 不填，避免误把凭据带进新账号）
        self.unanswerable_entry.set('AI 尽力完成')
        self.discussion_scope_entry.set('任务点内全部讨论')
        self.discussion_copy_check.deselect()
        self.meow_enabled_check.deselect()
        self.meow_url_entry.delete(0, 'end')
        for sw in self.meow_event_checks.values():
            sw.select()
        # 考试：交卷开关必须回到默认关闭，不能因为切换账号被带上一个账号的授权
        self.exam_answer_entry.set('题库优先+AI兜底')
        self.exam_auto_submit_check.deselect()
        self.exam_submit_ratio_entry.set('搜到100%的题')
        for entry, default in ((self.exam_revoke_entry, '15'),
                               (self.exam_min_remain_entry, '10')):
            entry.delete(0, 'end')
            entry.insert(0, default)
        # AI 接口：重置回默认值（不清空用户已填的地址也行，但清空更安全、语义一致）
        for entry in (self.ai_base_url_entry, self.ai_model_entry, self.ai_vision_entry,
                      self.ai_timeout_entry):
            entry.delete(0, 'end')
        self.ai_base_url_entry.insert(0, 'https://api.deepseek.com')
        self.ai_model_entry.insert(0, 'deepseek-v4-flash')
        self.ai_timeout_entry.insert(0, '0')
        # 复习/问卷：新账号必须回到默认（问卷开、复习关、遍数 1），
        # 否则会把上一个账号的复习授权带过去，导致新账号莫名重刷。
        self.vote_enabled_check.select()
        self.review_check.deselect()
        self.review_times_entry.delete(0, 'end')
        self.review_times_entry.insert(0, '1')
        for entry, btn, _row, _tip in self.dynamic_rows:
            entry.destroy()
            btn.destroy()
        self.dynamic_rows = []
        self.next_row = 4

    def _switch_account(self, label=None):
        target = self._acc_index.get(self.account_select.get())
        if not target or target == self.config.current_id():
            return
        self._persist_form()
        self.config.set_current(target)
        self._reset_form()
        self.load_data(self.data)
        self.change_text_box([], 0)

    def _add_account(self):
        self._persist_form()
        acc = self.config.add_account()
        self._reset_form()
        self.load_data(self.data)
        self.refresh_accounts()
        self.change_text_box([], 0)

    def _remove_account(self):
        if not messagebox.askyesno('确认', '确定删除当前账号吗？该账号的课程配置会一并移除'):
            return
        if not self.config.remove_account(self.config.current_id()):
            messagebox.showwarning('提示', '至少要保留一个账号')
            return
        self._reset_form()
        self.load_data(self.data)
        self.refresh_accounts()
        self.change_text_box([], 0)

    def save(self):
        if not self._validate():
            return
        account = self.config.get_account()
        data = self._collect_form()
        account.update(data)
        account['user_notice'] = 'False'

        if messagebox.askokcancel('确认保存', '你确定要保存吗？\n(使用DeepSeek可支持全题型作答)'):
            if self.config.save_account(account):
                self.config.save_courses(data['phone_number'], data['cour'])
                self.refresh_accounts()
                messagebox.showinfo('', '保存成功')
                self.change_font()
                self.change_text_box(self.all_courses, self.multi_device_check.get())
                self.root.update()
            else:
                messagebox.showerror('警告', '保存失败，请以管理员权限运行')

    def _validate(self):
        checks = [
            (self.browser_entry.get(), '请选择浏览器'),
            (self.browser_driver_entry.get(), '请填写驱动地址'),
            (self.phone_number_entry.get(), '请填写手机号'),
            (self.password_entry.get(), '请填写密码'),
            (self.cour_entry.get(), '请填写课程名称'),
            (self.question_entry.get(), '请完成章节测验答题设置'),
        ]
        for val, msg in checks:
            if not val:
                messagebox.showerror('警告', msg)
                return False
        if self.after_finish_question_entry.get() == '' and self.question_entry.get() == 'DeepSeek AI':
            messagebox.showerror('警告', '请完成答完题后的设置')
            return False
        if self.radio_var.get() == 1:
            if not self.vido_question_entry.get():
                messagebox.showerror('警告', '请完成视频题目设置')
                return False
            if not self.discussion_entry.get():
                messagebox.showerror('警告', '请完成讨论设置')
                return False
            if not self.speed_entry.get():
                messagebox.showerror('警告', '请填写倍数')
                return False
        if self.radio_var.get() == 2 and not self.homework_entry.get():
            messagebox.showerror('警告', '请填写作业选择形式')
            return False
        if self.radio_var.get() == 3:
            # 考试模式不复用「答完题后」的提交策略，单独校验考试设置
            if not self.exam_answer_entry.get():
                messagebox.showerror('警告', '请完成考试答题设置')
                return False
            if (self.exam_answer_entry.get() == '题库优先+AI兜底'
                    and not self.API_entry.get()):
                messagebox.showerror('警告', 'AI 兜底需要填写 API Key')
                return False
            return True
        api = self.API_entry.get()
        if any(e.get() == 'DeepSeek AI' for e in
               [self.question_entry, self.vido_question_entry, self.discussion_entry]):
            if not api:
                messagebox.showerror('警告', '请填写 API Key（任意 OpenAI 兼容服务均可）')
                return False
        return True

    def load_data(self, data):
        self.data = data
        account = self.config.get_account()
        self.cour_entry.configure(values=tuple(data))
        self.browser_entry.set(account.get('browser', ''))
        if account.get('browser') != '不指定浏览器':
            self.browser_driver_entry.insert(0, account.get('driver_path', ''))
        else:
            self.browser_driver_entry.insert(0, '无需填写')
            self.browser_driver_entry.configure(state=tk.DISABLED)
        self.speed_entry.set(account.get('speed', ''))
        self.phone_number_entry.insert(0, account.get('phone_number', ''))
        self.password_entry.insert(0, account.get('password', ''))
        self.cour_entry.set(account.get('cour', [''])[0])
        choice_value = account.get('choice', '')
        # 兼容旧配置里保存的旧叫法
        if choice_value == '\u4e0d\u5237\u9898':
            choice_value = '不作答'
        self.question_entry.set(choice_value)
        self.after_finish_question_entry.set(account.get('after_finish_question', ''))
        self.timer_start_time = account.get('timer_start', '04:00:00')
        self.timer_end_time = account.get('timer_end', '03:00:00')
        # 视频题固定随机作答；旧配置里存过 'DeepSeek AI'，一律按随机答题处理
        self.vido_question_entry.set('随机答题')
        self.discussion_entry.set(account.get('discussion_choice', ''))
        self.homework_entry.set(account.get('homework', ''))
        # 2026-11-06 新增：答题策略 / 讨论范围 / MeoW 推送
        self.unanswerable_entry.set(account.get('unanswerable_policy') or 'AI 尽力完成')
        scope = account.get('discussion_scope') or '任务点内全部讨论'
        if scope not in ('任务点内全部讨论', '全部讨论（需真机校准）'):
            scope = '任务点内全部讨论'
        self.discussion_scope_entry.set(scope)
        (self.discussion_copy_check.select() if account.get('discussion_copy_reply', 0)
         else self.discussion_copy_check.deselect())
        (self.meow_enabled_check.select() if account.get('meow_enabled', 0)
         else self.meow_enabled_check.deselect())
        self.meow_url_entry.delete(0, 'end')
        self.meow_url_entry.insert(0, account.get('meow_url', '') or '')
        events = account.get('meow_events') or {}
        for key, sw in self.meow_event_checks.items():
            (sw.select() if events.get(key, True) else sw.deselect())
        # 2026-11-08 考试设置回填；非法值一律回落到安全默认
        exam_mode = account.get('exam_answer_mode') or '题库优先+AI兜底'
        if exam_mode not in ('题库优先+AI兜底', '仅题库', '只识别不作答'):
            exam_mode = '题库优先+AI兜底'
        self.exam_answer_entry.set(exam_mode)
        (self.exam_auto_submit_check.select() if account.get('exam_auto_submit', 0)
         else self.exam_auto_submit_check.deselect())
        try:
            ratio = int(account.get('exam_submit_ratio', 100) or 100)
        except (TypeError, ValueError):
            ratio = 100
        ratio = min((60, 70, 80, 90, 100), key=lambda x: abs(x - ratio))
        self.exam_submit_ratio_entry.set('搜到%d%%的题' % ratio)
        for entry, key, default in ((self.exam_revoke_entry, 'exam_revoke_seconds', '15'),
                                    (self.exam_min_remain_entry, 'exam_min_remain_minutes', '10')):
            entry.delete(0, 'end')
            entry.insert(0, str(account.get(key, default)))
        # 2026-11-10 AI 接口（OpenAI 兼容）：回填，空值用默认
        for entry, key, default in (
                (self.ai_base_url_entry, 'ai_base_url', 'https://api.deepseek.com'),
                (self.ai_model_entry, 'ai_model', 'deepseek-v4-flash'),
                (self.ai_vision_entry, 'ai_vision_model', ''),
                (self.ai_timeout_entry, 'ai_timeout', '0')):
            entry.delete(0, 'end')
            entry.insert(0, str(account.get(key, default) or default))
        self.radio_var.set(account.get('radio_var', 1))
        # 切换账号后按新模式重排控件（原先只看保存的 choice，考试模式会显示错）
        self.function_choice()
        for attr, key in [('pass_face_check', 'pass_face'), ('multi_device_check', 'multi_device'),
                          ('pass_vido_check', 'pass_vido'), ('lock_screen_check', 'lock_screen'),
                          ('timer_check', 'timer_active')]:
            getattr(self, attr).select() if account.get(key, 0) else getattr(self, attr).deselect()
        # 2026-11-19 移植的两个开关：问卷默认开、复习默认关（与 DEFAULT_ACCOUNT 一致）。
        # 旧配置里没有这两个键，所以默认值必须显式给，不能沿用 0。
        try:
            for attr, key, default in (
                    ('vote_enabled_check', 'vote_enabled', 1),
                    ('review_check', 'review', 0)):
                value = account.get(key, default)
                sw = getattr(self, attr)
                sw.select() if value else sw.deselect()
            self.review_times_entry.delete(0, 'end')
            self.review_times_entry.insert(0, str(account.get('review_times', 1)))
        except Exception:
            pass
        try:
            self.API_entry.insert(0, account.get('API', ''))
            self.font_entry.set(account.get('font_type', ''))
            self.size_entry.set(account.get('font_size', ''))
            self.concurrency_entry.set(str(account.get('max_concurrency', 2)))
            self.change_font()
        except:
            pass
        for name in account.get('cour', [])[1:]:
            self.add_course(name)
        self.refresh_accounts()

    @property
    def comboboxes(self):
        return [self.speed_entry, self.question_entry, self.cour_entry,
                self.concurrency_entry, self.account_select,
                self.size_entry, self.homework_entry, self.font_entry,
                self.browser_entry, self.vido_question_entry,
                self.discussion_entry, self.after_finish_question_entry]

    @property
    def switches(self):
        return [self.topmost_check, self.pass_face_check,self.timer_check,
                self.multi_device_check, self.lock_screen_check, self.pass_vido_check,
                self.vote_enabled_check, self.review_check]

    @property
    def buttons(self):
        # 测试按钮也要参与主题切换，否则换主题后颜色与主色调不一致
        return [self.save_button, self.open_file_button,
                self.meow_test_btn, self.ai_test_btn]