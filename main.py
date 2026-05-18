"""
微信账号状态检查工具 - 主入口
基于 tkinter 的 GUI 界面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import sys
import time as _time
from dataclasses import dataclass

from config_manager import ConfigManager
from checker_engine import CheckerEngine
from logger_setup import logger
import wechat_controller  # 确保 PyInstaller 打包此模块


@dataclass
class AbnormalEntry:
    """异常账号记录，用于通知面板展示"""
    wechat_id: str
    reason: str
    timestamp: float
    telegram_sent: bool | None = None  # True=通知成功, False=通知失败, None=未启用


class WeChatCheckerApp:
    """主应用窗口"""

    APP_NAME = "微信账号状态检查工具"
    APP_VERSION = "v1.2"

    def __init__(self):
        self.config = ConfigManager()
        self.engine = CheckerEngine(self.config)

        # 绑定引擎回调
        self.engine.on_log = self._on_engine_log
        self.engine.on_status = self._on_engine_status
        self.engine.on_progress = self._on_engine_progress
        self.engine.on_abnormal = self._on_engine_abnormal
        self.engine.on_countdown = self._on_engine_countdown
        self.engine.on_ip_changed = self._on_ip_changed

        # 异常账号追踪（线程安全）
        self._abnormal_lock = threading.Lock()
        self._abnormal_dict: dict[str, AbnormalEntry] = {}
        self._sound_muted = False
        self._beep_after_id: str | None = None
        self._beep_stopped = False  # 防止竞态：_stop_beep 后 _beep_loop 重新注册 after
        self._paused = False        # 暂停状态
        self._drag_data = {"index": -1, "y": 0, "dragging": False}

        # IP 切换相关
        self._ip_switch_records = []  # 切换记录列表，最多50条
        self._ip_switch_in_progress = False  # 切换进行中标志
        self._fetching_ip = False  # 防重入：正在获取IP信息
        self._ip_color_after_id = None  # IP颜色恢复的after ID

        # 创建主窗口
        self.root = tk.Tk()
        self.root.title(f"{self.APP_NAME} {self.APP_VERSION}")
        self.root.geometry("820x780")
        self.root.minsize(640, 700)

        # 设置图标（内置 Base64 图标）
        self._set_icon()

        # 窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 日志缓冲区（线程安全）
        self._log_buffer = []
        self._log_buffer_lock = threading.Lock()

        # 初始化界面
        self._build_ui()

        # 加载配置到界面
        self._load_config_to_ui()

        # 启动日志刷新定时器
        self._start_log_timer()

        # 窗口居中
        self._center_window()

        # 延迟检查运行环境（窗口显示后执行）
        self.root.after(500, self._check_runtime_env)
        # 启动后获取当前IP信息
        self.root.after(1000, self._fetch_current_ip_info)

    # ==================== 图标 ====================

    def _set_icon(self):
        """设置窗口图标（内置简易图标，Base64 编码）"""
        try:
            # 用 tkinter 内置方式生成一个简单图标
            img = tk.PhotoImage(width=32, height=32)
            # 画一个简单的绿色盾牌图标
            colors = {
                "bg": "#2ecc71",
                "fg": "#ffffff",
            }
            for x in range(32):
                for y in range(32):
                    # 盾牌形状
                    dx, dy = x - 16, y - 16
                    if (dx * dx + dy * dy) < 200 and y > 6 and y < 28:
                        img.put(colors["bg"], (x, y))
                    elif 6 <= y <= 28 and 6 <= x <= 26:
                        img.put(colors["bg"], (x, y))
            self.root.iconphoto(True, img)
        except Exception:
            pass  # 图标设置失败不阻塞

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建界面元素"""
        # ---- 滚动容器（Canvas + Scrollbar） ----
        canvas = tk.Canvas(self.root, highlightthickness=0, bg="#f0f0f0")
        scrollbar = ttk.Scrollbar(self.root, orient=tk.VERTICAL, command=canvas.yview)

        # 主框架（内边距由 main_frame 自身管理）
        main_frame = ttk.Frame(canvas, padding=12)

        # 将 main_frame 放入 Canvas
        main_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        self._main_window_id = canvas.create_window(
            (0, 0), window=main_frame, anchor=tk.NW, tags=("main",)
        )

        def _on_canvas_resize(event):
            """Canvas 宽度变化时，同步 main_frame 宽度"""
            canvas.itemconfig("main", width=event.width)

        canvas.bind("<Configure>", _on_canvas_resize)
        canvas.configure(yscrollcommand=scrollbar.set)

        # 鼠标滚轮支持（canvas + root 双重绑定，覆盖所有区域）
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        self.root.bind("<MouseWheel>", _on_mousewheel)  # 全窗口滚轮

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- 第一行：标题 ----
        title_label = ttk.Label(
            main_frame,
            text=f"{self.APP_NAME} {self.APP_VERSION}",
            font=("微软雅黑", 14, "bold"),
        )
        title_label.pack(anchor=tk.W, pady=(0, 10))

        # ---- 配置区域（使用 LabelFrame） ----
        config_frame = ttk.LabelFrame(main_frame, text="配置", padding=10)
        config_frame.pack(fill=tk.X, pady=(0, 8))

        # 微信路径
        path_row = ttk.Frame(config_frame)
        path_row.pack(fill=tk.X, pady=3)
        ttk.Label(path_row, text="微信路径:", width=12).pack(side=tk.LEFT)
        self.wechat_path_var = tk.StringVar()
        path_entry = ttk.Entry(path_row, textvariable=self.wechat_path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(path_row, text="浏览", width=6,
                   command=self._browse_wechat_path).pack(side=tk.RIGHT)

        # ---- 左右分栏主区域：微信号列表(左) | IP切换+Telegram(右) ----
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        left_col = ttk.Frame(content_frame)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_col = ttk.Frame(content_frame)
        right_col.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        # ---- IP自动切换面板（右栏） ----
        ip_frame = ttk.LabelFrame(right_col, text="IP自动切换", padding=8)
        ip_frame.pack(fill=tk.X, pady=(0, 6))

        # ① 顶部 — 实时IP显示条
        ip_top = ttk.Frame(ip_frame)
        ip_top.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(ip_top, text="当前IP:", font=("微软雅黑", 9, "bold")).pack(side=tk.LEFT)
        self.ip_current_label = ttk.Label(
            ip_top, text="--", font=("Consolas", 10),
            foreground="#1976d2"
        )
        self.ip_current_label.pack(side=tk.LEFT, padx=(4, 15))

        ttk.Label(ip_top, text="节点:", font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.ip_node_label = ttk.Label(
            ip_top, text="--", font=("微软雅黑", 9),
            foreground="#666666"
        )
        self.ip_node_label.pack(side=tk.LEFT, padx=(4, 10))

        self.ip_refresh_btn = ttk.Button(
            ip_top, text="刷新", width=6,
            command=self._fetch_current_ip_info
        )
        self.ip_refresh_btn.pack(side=tk.LEFT)

        # ② 中部 — 配置区
        ip_config = ttk.Frame(ip_frame)
        ip_config.pack(fill=tk.X)

        # 第一行：启用开关 + 方式选择
        ip_row1 = ttk.Frame(ip_config)
        ip_row1.pack(fill=tk.X, pady=(0, 4))

        self.ip_switch_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            ip_row1, text="启用IP自动切换（优先选延迟最低的节点）",
            variable=self.ip_switch_enabled_var,
        ).pack(side=tk.LEFT)

        # 方式选择
        ttk.Label(ip_row1, text="  方式:").pack(side=tk.LEFT)
        self.ip_switch_method_var = tk.StringVar(value="clash")
        ttk.Radiobutton(
            ip_row1, text="Clash API", variable=self.ip_switch_method_var,
            value="clash", command=self._on_ip_method_changed
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Radiobutton(
            ip_row1, text="自定义命令", variable=self.ip_switch_method_var,
            value="command", command=self._on_ip_method_changed
        ).pack(side=tk.LEFT, padx=(4, 0))

        # Clash 配置行
        self.ip_clash_row = ttk.Frame(ip_config)
        self.ip_clash_row.pack(fill=tk.X, pady=(0, 3))

        ttk.Label(self.ip_clash_row, text="Clash地址:").pack(side=tk.LEFT)
        self.ip_clash_url_var = tk.StringVar(value="http://127.0.0.1:9090")
        ttk.Entry(
            self.ip_clash_row, textvariable=self.ip_clash_url_var, width=18
        ).pack(side=tk.LEFT, padx=(4, 10))

        ttk.Label(self.ip_clash_row, text="代理组:").pack(side=tk.LEFT)
        self.ip_clash_group_var = tk.StringVar(value="Proxy")
        ttk.Entry(
            self.ip_clash_row, textvariable=self.ip_clash_group_var, width=10
        ).pack(side=tk.LEFT, padx=(4, 0))

        # 命令配置行
        self.ip_cmd_row = ttk.Frame(ip_config)

        ttk.Label(self.ip_cmd_row, text="切换命令:").pack(side=tk.LEFT)
        self.ip_command_var = tk.StringVar()
        ttk.Entry(
            self.ip_cmd_row, textvariable=self.ip_command_var, width=30
        ).pack(side=tk.LEFT, padx=(4, 6))
        ttk.Button(
            self.ip_cmd_row, text="查看模板", width=8,
            command=self._show_ip_templates
        ).pack(side=tk.LEFT)

        # 参数行
        ip_param = ttk.Frame(ip_config)
        ip_param.pack(fill=tk.X, pady=(3, 0))

        ttk.Label(ip_param, text="每").pack(side=tk.LEFT)
        self.ip_batch_count_var = tk.StringVar(value="3")
        ttk.Spinbox(
            ip_param, from_=1, to=999, textvariable=self.ip_batch_count_var, width=4
        ).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(ip_param, text="批后切换  ").pack(side=tk.LEFT)

        ttk.Label(ip_param, text="超时:").pack(side=tk.LEFT)
        self.ip_timeout_var = tk.StringVar(value="30")
        ttk.Spinbox(
            ip_param, from_=5, to=600, textvariable=self.ip_timeout_var, width=5
        ).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(ip_param, text="秒  ").pack(side=tk.LEFT)

        ttk.Label(ip_param, text="提前:").pack(side=tk.LEFT)
        self.ip_advance_var = tk.StringVar(value="300")
        ttk.Spinbox(
            ip_param, from_=30, to=1800, textvariable=self.ip_advance_var, width=5
        ).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(ip_param, text="秒测速  ").pack(side=tk.LEFT)

        ttk.Label(ip_param, text="验证地址:").pack(side=tk.LEFT)
        self.ip_verify_url_var = tk.StringVar(value="https://api.ipify.org")
        ttk.Entry(
            ip_param, textvariable=self.ip_verify_url_var, width=20
        ).pack(side=tk.LEFT, padx=(4, 0))

        # 测试按钮 + 状态
        ip_action = ttk.Frame(ip_config)
        ip_action.pack(fill=tk.X, pady=(4, 0))

        self.ip_test_btn = ttk.Button(
            ip_action, text="立即切换(测试)", width=14,
            command=self._on_test_ip_switch
        )
        self.ip_test_btn.pack(side=tk.LEFT)

        self.ip_status_label = ttk.Label(
            ip_action, text="", foreground="#666666", font=("微软雅黑", 8)
        )
        self.ip_status_label.pack(side=tk.LEFT, padx=(10, 0))

        # ③ 底部 — 切换记录列表
        ip_history_frame = ttk.LabelFrame(ip_frame, text="切换记录", padding=2)
        ip_history_frame.pack(fill=tk.X, pady=(6, 0))

        ip_history_container = ttk.Frame(ip_history_frame)
        ip_history_container.pack(fill=tk.BOTH, expand=True)

        self.ip_history_canvas = tk.Canvas(
            ip_history_container, height=60,
            bg="#f5f5f5", highlightthickness=0,
        )
        ip_history_scrollbar = ttk.Scrollbar(
            ip_history_container, orient=tk.VERTICAL,
            command=self.ip_history_canvas.yview,
        )
        self.ip_history_canvas.configure(yscrollcommand=ip_history_scrollbar.set)
        self.ip_history_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ip_history_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.ip_history_inner = ttk.Frame(self.ip_history_canvas)
        self.ip_history_inner_id = self.ip_history_canvas.create_window(
            (0, 0), window=self.ip_history_inner, anchor=tk.NW
        )

        def _on_ip_history_configure(event):
            self.ip_history_canvas.itemconfig(
                self.ip_history_inner_id, width=event.width
            )
        self.ip_history_canvas.bind("<Configure>", _on_ip_history_configure)

        def _on_ip_history_inner_configure(event):
            self.ip_history_canvas.configure(
                scrollregion=self.ip_history_canvas.bbox("all")
            )
        self.ip_history_inner.bind("<Configure>", _on_ip_history_inner_configure)

        def _on_ip_history_wheel(event):
            self.ip_history_canvas.yview_scroll(
                int(-1 * (event.delta / 120)), "units"
            )
        self.ip_history_canvas.bind("<MouseWheel>", _on_ip_history_wheel)

        # 初始显示：命令配置行默认隐藏（clash模式）
        self.ip_cmd_row.pack_forget()

        # ---- 左栏：微信号列表 ----
        ids_frame = ttk.LabelFrame(left_col, text="微信号列表", padding=6)
        ids_frame.pack(fill=tk.BOTH, expand=True)

        # ---- 右栏：Telegram 通知配置（IP面板下方） ----
        telegram_frame = ttk.LabelFrame(right_col, text="📨 Telegram 通知", padding=8)
        telegram_frame.pack(fill=tk.X, pady=(6, 0))

        self.telegram_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            telegram_frame, text="启用通知",
            variable=self.telegram_enabled_var,
        ).pack(anchor=tk.W)

        ttk.Label(telegram_frame, text="群组/频道 ID:", font=("微软雅黑", 8)).pack(anchor=tk.W, pady=(8, 2))
        self.telegram_chatid_var = tk.StringVar()
        ttk.Entry(
            telegram_frame, textvariable=self.telegram_chatid_var, width=22
        ).pack(fill=tk.X)

        ttk.Label(telegram_frame, text="代理地址 (选填):", font=("微软雅黑", 8)).pack(anchor=tk.W, pady=(8, 2))
        self.telegram_proxy_var = tk.StringVar()
        ttk.Entry(
            telegram_frame, textvariable=self.telegram_proxy_var, width=22
        ).pack(fill=tk.X)

        self.telegram_test_btn = ttk.Button(
            telegram_frame, text="🔄 发送测试消息", width=14,
            command=self._on_test_telegram,
        )
        self.telegram_test_btn.pack(pady=(8, 4))
        self.telegram_status_label = ttk.Label(
            telegram_frame, text="", foreground="#666666", font=("微软雅黑", 8)
        )
        self.telegram_status_label.pack()

        # Listbox + 滚动条
        listbox_frame = ttk.Frame(ids_frame)
        listbox_frame.pack(fill=tk.BOTH, expand=True)

        self.ids_listbox = tk.Listbox(
            listbox_frame, height=8, selectmode=tk.EXTENDED,
            font=("Consolas", 9), bg="#f5f5f5", relief=tk.SUNKEN,
        )
        # 拖拽排序绑定
        self.ids_listbox.bind("<Button-1>", self._on_drag_start)
        self.ids_listbox.bind("<B1-Motion>", self._on_drag_motion)
        self.ids_listbox.bind("<ButtonRelease-1>", self._on_drag_drop)
        ids_scrollbar = ttk.Scrollbar(listbox_frame, command=self.ids_listbox.yview)
        self.ids_listbox.config(yscrollcommand=ids_scrollbar.set)
        self.ids_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ids_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 添加行：输入框 + 添加按钮
        add_row = ttk.Frame(ids_frame)
        add_row.pack(fill=tk.X, pady=(4, 2))

        self.new_id_var = tk.StringVar()
        add_entry = ttk.Entry(add_row, textvariable=self.new_id_var)
        add_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        add_entry.bind("<Return>", lambda e: self._add_wechat_id())
        ttk.Button(add_row, text="添加", width=6,
                   command=self._add_wechat_id).pack(side=tk.RIGHT)

        # 操作按钮行
        btn_row = ttk.Frame(ids_frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="删除选中", width=10,
                   command=self._delete_selected_id).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="从文件导入...", width=12,
                   command=self._import_ids_from_file).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="清空", width=6,
                   command=self._clear_all_ids).pack(side=tk.LEFT)

        # 参数行
        param_frame = ttk.Frame(config_frame)
        param_frame.pack(fill=tk.X, pady=3)

        # 每批数量
        ttk.Label(param_frame, text="每批数量:").pack(side=tk.LEFT)
        self.batch_size_var = tk.StringVar(value="9")
        ttk.Spinbox(
            param_frame, from_=1, to=50,
            textvariable=self.batch_size_var, width=4
        ).pack(side=tk.LEFT, padx=(0, 15))

        # 批次间隔
        ttk.Label(param_frame, text="批次间隔(分钟):").pack(side=tk.LEFT)
        self.bi_min_var = tk.StringVar(value="30")
        ttk.Spinbox(
            param_frame, from_=1, to=999,
            textvariable=self.bi_min_var, width=5
        ).pack(side=tk.LEFT)
        ttk.Label(param_frame, text=" - ").pack(side=tk.LEFT)
        self.bi_max_var = tk.StringVar(value="50")
        ttk.Spinbox(
            param_frame, from_=1, to=999,
            textvariable=self.bi_max_var, width=5
        ).pack(side=tk.LEFT, padx=(0, 15))

        # 账号间隔
        ttk.Label(param_frame, text="账号间隔(秒):").pack(side=tk.LEFT)
        self.ai_min_var = tk.StringVar(value="3")
        ttk.Spinbox(
            param_frame, from_=0, to=30,
            textvariable=self.ai_min_var, width=4
        ).pack(side=tk.LEFT)
        ttk.Label(param_frame, text=" - ").pack(side=tk.LEFT)
        self.ai_max_var = tk.StringVar(value="5")
        ttk.Spinbox(
            param_frame, from_=0, to=30,
            textvariable=self.ai_max_var, width=4
        ).pack(side=tk.LEFT, padx=(0, 15))

        # 最大轮数
        ttk.Label(param_frame, text="最大轮数:").pack(side=tk.LEFT)
        self.max_rounds_var = tk.StringVar(value="100")
        ttk.Spinbox(
            param_frame, from_=1, to=9999,
            textvariable=self.max_rounds_var, width=6
        ).pack(side=tk.LEFT)

        # 声音提醒开关（独立一行，保证可见）
        sound_row = ttk.Frame(config_frame)
        sound_row.pack(fill=tk.X, pady=(4, 0))
        self.sound_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            sound_row, text="🔊 异常时声音播报", variable=self.sound_enabled_var
        ).pack(side=tk.LEFT)

        # ---- 控制按钮 + 进度条（同行） ----
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 6))

        self.start_btn = ttk.Button(
            control_frame, text="▶ 开始检查", width=14,
            command=self._start_check
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.pause_btn = ttk.Button(
            control_frame, text="⏸ 暂停", width=14,
            command=self._on_pause_resume, state=tk.DISABLED
        )
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ttk.Button(
            control_frame, text="■ 停止检查", width=14,
            command=self._stop_check, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT)

        # 进度条（按钮右侧）
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            control_frame, variable=self.progress_var,
            maximum=100, length=300
        )
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 8))

        self.progress_label = ttk.Label(control_frame, text="", width=16)
        self.progress_label.pack(side=tk.LEFT)

        # 状态标签（最右）
        self.status_label = ttk.Label(
            control_frame, text="状态: 空闲", font=("微软雅黑", 10),
            foreground="#666666"
        )
        self.status_label.pack(side=tk.RIGHT)

        # ---- 异常通知面板 ----
        self.abnormal_frame = ttk.LabelFrame(
            main_frame, text="⚠ 异常账号 (0)", padding=4
        )
        self.abnormal_frame.pack(fill=tk.X, pady=(0, 6))

        abnormal_canvas_frame = ttk.Frame(self.abnormal_frame)
        abnormal_canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.abnormal_canvas = tk.Canvas(
            abnormal_canvas_frame,
            height=80,
            bg="#f0f0f0",
            highlightthickness=0,
        )
        abnormal_scrollbar = ttk.Scrollbar(
            abnormal_canvas_frame,
            orient=tk.VERTICAL,
            command=self.abnormal_canvas.yview,
        )
        self.abnormal_canvas.configure(yscrollcommand=abnormal_scrollbar.set)
        self.abnormal_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        abnormal_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 内部容器帧（所有异常条目在此帧内）
        self.abnormal_inner = ttk.Frame(self.abnormal_canvas)
        self.abnormal_inner_id = self.abnormal_canvas.create_window(
            (0, 0), window=self.abnormal_inner, anchor=tk.NW
        )

        # 让内部帧宽度跟随 Canvas
        def _on_abnormal_canvas_configure(event):
            self.abnormal_canvas.itemconfig(
                self.abnormal_inner_id, width=event.width
            )
        self.abnormal_canvas.bind("<Configure>", _on_abnormal_canvas_configure)

        # 更新 scrollregion
        def _on_abnormal_inner_configure(event):
            self.abnormal_canvas.configure(
                scrollregion=self.abnormal_canvas.bbox("all")
            )
        self.abnormal_inner.bind("<Configure>", _on_abnormal_inner_configure)

        # 鼠标滚轮支持
        def _on_abnormal_mousewheel(event):
            self.abnormal_canvas.yview_scroll(
                int(-1 * (event.delta / 120)), "units"
            )
        self.abnormal_canvas.bind("<MouseWheel>", _on_abnormal_mousewheel)

        # 底部控制栏
        abnormal_ctrl = ttk.Frame(self.abnormal_frame)
        abnormal_ctrl.pack(fill=tk.X, pady=(4, 0))

        self.stop_sound_btn = ttk.Button(
            abnormal_ctrl, text="🔇 停止声音", width=14,
            command=self._on_stop_sound, state=tk.DISABLED,
        )
        self.stop_sound_btn.pack(side=tk.LEFT)

        self.abnormal_count_label = ttk.Label(
            abnormal_ctrl, text="", foreground="#cc3333",
            font=("微软雅黑", 9, "bold"),
        )
        self.abnormal_count_label.pack(side=tk.RIGHT)

        # ---- 日志区域 ----
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_text_frame = ttk.Frame(log_frame)
        log_text_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_text_frame,
            height=10,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state=tk.DISABLED,
            relief=tk.SUNKEN,
            borderwidth=1,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 滚动条
        scrollbar = ttk.Scrollbar(log_text_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # 底部提示
        footer = ttk.Label(
            main_frame,
            text="提示: 运行前请确保微信已登录且未最小化（可被遮挡）",
            font=("微软雅黑", 8),
            foreground="#999999",
        )
        footer.pack(anchor=tk.W, pady=(4, 0))

    # ==================== 配置加载/保存 ====================

    def _load_config_to_ui(self):
        """将配置文件内容加载到界面控件"""
        self.wechat_path_var.set(self.config.get("wechat_path", ""))
        self.batch_size_var.set(str(self.config.get("batch_size", 9)))
        self.bi_min_var.set(str(self.config.get("batch_interval_min", 30)))
        self.bi_max_var.set(str(self.config.get("batch_interval_max", 50)))
        self.ai_min_var.set(str(self.config.get("account_interval_min", 3)))
        self.ai_max_var.set(str(self.config.get("account_interval_max", 5)))
        self.max_rounds_var.set(str(self.config.get("max_rounds", 100)))
        self.sound_enabled_var.set(self.config.get("sound_enabled", True))
        self.telegram_enabled_var.set(self.config.get("telegram_enabled", False))
        self.telegram_chatid_var.set(self.config.get("telegram_chat_id", ""))
        self.telegram_proxy_var.set(self.config.get("telegram_proxy", ""))
        # IP 切换配置
        self.ip_switch_enabled_var.set(self.config.get("ip_switch_enabled", False))
        self.ip_switch_method_var.set(self.config.get("ip_switch_method", "clash"))
        self.ip_clash_url_var.set(self.config.get("ip_switch_clash_url", "http://127.0.0.1:9090"))
        self.ip_clash_group_var.set(self.config.get("ip_switch_clash_group", "Proxy"))
        self.ip_command_var.set(self.config.get("ip_switch_command", ""))
        self.ip_batch_count_var.set(str(self.config.get("ip_switch_batch_count", 3)))
        self.ip_timeout_var.set(str(self.config.get("ip_switch_timeout", 30)))
        self.ip_verify_url_var.set(self.config.get("ip_switch_verify_url", "https://api.ipify.org"))
        self.ip_advance_var.set(str(self.config.get("ip_switch_advance_seconds", 300)))
        self._on_ip_method_changed()  # 根据方式显示/隐藏对应行
        # 从文件加载微信号列表到界面
        self._load_ids_to_listbox()

    def _save_ui_to_config(self):
        """将界面值写入配置文件"""
        self.config.set("wechat_path", self.wechat_path_var.get().strip())

        try:
            self.config.set("batch_size", int(self.batch_size_var.get()))
        except ValueError:
            pass

        try:
            self.config.set("batch_interval_min", int(self.bi_min_var.get()))
            self.config.set("batch_interval_max", int(self.bi_max_var.get()))
        except ValueError:
            pass

        try:
            self.config.set("account_interval_min", int(self.ai_min_var.get()))
            self.config.set("account_interval_max", int(self.ai_max_var.get()))
        except ValueError:
            pass

        try:
            self.config.set("max_rounds", int(self.max_rounds_var.get()))
        except ValueError:
            pass

        self.config.set("sound_enabled", self.sound_enabled_var.get())
        self.config.set("telegram_enabled", self.telegram_enabled_var.get())
        self.config.set("telegram_chat_id", self.telegram_chatid_var.get().strip())
        self.config.set("telegram_proxy", self.telegram_proxy_var.get().strip())
        # IP 切换配置
        self.config.set("ip_switch_enabled", self.ip_switch_enabled_var.get())
        self.config.set("ip_switch_method", self.ip_switch_method_var.get())
        self.config.set("ip_switch_clash_url", self.ip_clash_url_var.get().strip())
        self.config.set("ip_switch_clash_group", self.ip_clash_group_var.get().strip())
        self.config.set("ip_switch_command", self.ip_command_var.get().strip())
        try:
            self.config.set("ip_switch_batch_count", int(self.ip_batch_count_var.get()))
        except ValueError:
            pass
        try:
            self.config.set("ip_switch_timeout", int(self.ip_timeout_var.get()))
        except ValueError:
            pass
        self.config.set("ip_switch_verify_url", self.ip_verify_url_var.get().strip())
        try:
            self.config.set("ip_switch_advance_seconds", int(self.ip_advance_var.get()))
        except ValueError:
            pass

    def _browse_wechat_path(self):
        """浏览选择微信可执行文件"""
        path = filedialog.askopenfilename(
            title="选择微信主程序",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if path:
            self.wechat_path_var.set(path)
            self._save_ui_to_config()

    # ==================== 微信号列表管理 ====================

    def _load_ids_to_listbox(self):
        """从 wechat_ids.txt 加载微信号到 Listbox"""
        ids_file = self.config.get("ids_file", "wechat_ids.txt")
        ids, _ = ConfigManager.load_ids(ids_file)
        self.ids_listbox.delete(0, tk.END)
        for wid in ids:
            self.ids_listbox.insert(tk.END, wid)

    def _save_ids_to_file(self):
        """将 Listbox 中的微信号保存到文件"""
        ids = list(self.ids_listbox.get(0, tk.END))
        ids_file = self.config.get("ids_file", "wechat_ids.txt")
        try:
            with open(ids_file, "w", encoding="utf-8") as f:
                f.write("\n".join(ids))
        except IOError as e:
            logger.error(f"保存微信号列表失败: {e}")

    def _add_wechat_id(self):
        """添加新微信号到列表"""
        new_id = self.new_id_var.get().strip()
        if not new_id:
            return
        # 去重
        current_ids = list(self.ids_listbox.get(0, tk.END))
        if new_id in current_ids:
            messagebox.showinfo("提示", f"微信号 {new_id} 已存在", parent=self.root)
            return
        self.ids_listbox.insert(tk.END, new_id)
        self.new_id_var.set("")
        self._save_ids_to_file()

    def _delete_selected_id(self):
        """删除选中的微信号"""
        selected = self.ids_listbox.curselection()
        if not selected:
            return
        # 从后往前删，避免索引偏移
        for idx in reversed(selected):
            self.ids_listbox.delete(idx)
        self._save_ids_to_file()

    def _clear_all_ids(self):
        """清空全部微信号"""
        if not self.ids_listbox.size():
            return
        if not messagebox.askyesno(
            "确认清空",
            "确定要清空所有微信号吗？",
            parent=self.root,
        ):
            return
        self.ids_listbox.delete(0, tk.END)
        self._save_ids_to_file()

    def _import_ids_from_file(self):
        """从文件导入微信号（合并去重）"""
        path = filedialog.askopenfilename(
            title="选择微信号列表文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not path:
            return

        ids, err = ConfigManager.load_ids(path)
        if err:
            messagebox.showwarning("导入失败", err, parent=self.root)
            return
        if not ids:
            messagebox.showinfo("提示", "文件中没有有效的微信号", parent=self.root)
            return

        current = set(self.ids_listbox.get(0, tk.END))
        added = 0
        for wid in ids:
            if wid not in current:
                self.ids_listbox.insert(tk.END, wid)
                current.add(wid)
                added += 1

        self._save_ids_to_file()
        logger.info(f"从 {path} 导入 {added} 个新微信号，跳过 {len(ids) - added} 个重复")

    def _get_ids_list(self):
        """获取当前列表中的微信号"""
        return list(self.ids_listbox.get(0, tk.END))

    # ---- 拖拽排序 ----

    def _on_drag_start(self, event):
        """记录拖拽起始位置"""
        idx = self.ids_listbox.nearest(event.y)
        if idx < 0:
            return
        self._drag_data["index"] = idx
        self._drag_data["y"] = event.y
        self._drag_data["dragging"] = False
        # 选中该项
        self.ids_listbox.selection_clear(0, tk.END)
        self.ids_listbox.selection_set(idx)

    def _on_drag_motion(self, event):
        """拖拽移动：超过阈值后进入拖拽模式"""
        if self._drag_data["index"] < 0:
            return
        dy = abs(event.y - self._drag_data["y"])
        if dy < 5 and not self._drag_data["dragging"]:
            return  # 未达到拖拽阈值
        if not self._drag_data["dragging"]:
            self._drag_data["dragging"] = True
            self.ids_listbox.configure(cursor="fleur")
        # 高亮目标位置
        target = self.ids_listbox.nearest(event.y)
        self.ids_listbox.selection_clear(0, tk.END)
        self.ids_listbox.selection_set(target)

    def _on_drag_drop(self, event):
        """松开鼠标：完成拖拽"""
        self.ids_listbox.configure(cursor="")
        if not self._drag_data["dragging"]:
            self._drag_data["index"] = -1
            return
        src = self._drag_data["index"]
        dst = self.ids_listbox.nearest(event.y)
        self._drag_data["index"] = -1
        self._drag_data["dragging"] = False
        if dst < 0 or dst == src:
            return
        # 取出源项，插入目标位置
        item = self.ids_listbox.get(src)
        self.ids_listbox.delete(src)
        self.ids_listbox.insert(dst, item)
        self.ids_listbox.selection_clear(0, tk.END)
        self.ids_listbox.selection_set(dst)
        self._save_ids_to_file()

    # ==================== 引擎回调 ====================

    def _on_engine_log(self, msg):
        """引擎日志回调（在子线程中调用）"""
        with self._log_buffer_lock:
            self._log_buffer.append(msg)

    def _on_engine_status(self, text):
        """引擎状态回调"""
        self.root.after(0, self._update_status, text)
        # 引擎完成/停止时重置按钮
        if text in ("已完成", "已停止"):
            self.root.after(0, self._on_engine_finished)

    def _on_engine_finished(self):
        """引擎完成/停止后的按钮重置"""
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="⏸ 暂停")
        self.stop_btn.config(state=tk.DISABLED)
        self._paused = False
        self.ip_test_btn.config(state=tk.NORMAL)  # 恢复手动测试按钮

    def _on_engine_countdown(self, remaining, label):
        """引擎倒计时回调（在子线程中调用）"""
        if remaining < 60:
            time_str = f"{int(remaining)}秒"
        else:
            m = int(remaining // 60)
            s = int(remaining % 60)
            time_str = f"{m}分{s:02d}秒"

        if label == "account":
            text = f"等待中 - {time_str}后检查下一个"
        elif label.startswith("batch_r"):
            r = label.split("_r")[1]
            text = f"等待中 - 第{r}轮 下一批 ({time_str}后)"
        elif label.startswith("round_r"):
            r = label.split("_r")[1]
            text = f"等待中 - 下一轮 ({time_str}后)"
        else:
            text = f"等待中 - {time_str}后"

        self.root.after(0, self._update_status, text)

    def _on_engine_progress(self, current, total, batch_info):
        """引擎进度回调"""
        def update():
            if total > 0:
                pct = (current / total) * 100
                self.progress_var.set(pct)
                self.progress_label.config(
                    text=f"{current}/{total}  {batch_info}"
                )
        self.root.after(0, update)

    def _on_engine_abnormal(self, wechat_id, reason, telegram_sent=None):
        """
        引擎异常回调（在检查线程中被调用）
        非阻塞：注册异常信息，调度 GUI 更新和声音警报，检查继续。
        """
        entry = AbnormalEntry(
            wechat_id=wechat_id,
            reason=reason,
            timestamp=_time.time(),
            telegram_sent=telegram_sent,
        )
        with self._abnormal_lock:
            self._abnormal_dict[wechat_id] = entry

        self._sound_muted = False  # 新异常出现，恢复声音警报
        self.root.after(0, self._refresh_abnormal_panel)
        self.root.after(0, self._ensure_beeping)

    # ==================== 异常通知面板方法 ====================

    def _refresh_abnormal_panel(self):
        """重建异常通知面板内容（必须在主线程调用）。"""
        for widget in self.abnormal_inner.winfo_children():
            widget.destroy()

        with self._abnormal_lock:
            entries = list(self._abnormal_dict.values())
            count = len(entries)

        self.abnormal_frame.configure(text=f"⚠ 异常账号 ({count})")

        if count == 0:
            self.abnormal_count_label.configure(text="")
            self.abnormal_canvas.configure(bg="#f0f0f0")
            self.stop_sound_btn.configure(
                state=tk.DISABLED, text="🔇 停止声音"
            )
            self._stop_beep()
            return

        self.abnormal_canvas.configure(bg="#fff0f0")
        self.abnormal_count_label.configure(
            text=f"共 {count} 个异常账号待处理"
        )

        # 按时间倒序（最新的在前）
        entries.sort(key=lambda e: e.timestamp, reverse=True)

        for entry in entries:
            row_frame = tk.Frame(
                self.abnormal_inner,
                bg="#ffe0e0",
                relief=tk.GROOVE,
                borderwidth=1,
            )
            row_frame.pack(fill=tk.X, pady=1, padx=2)

            # 左侧：异常图标 + 微信号 + 原因
            info_label = tk.Label(
                row_frame,
                text=f"⚠ {entry.wechat_id}  —  {entry.reason}",
                bg="#ffe0e0",
                fg="#cc0000",
                font=("微软雅黑", 9, "bold"),
                anchor=tk.W,
            )
            info_label.pack(
                side=tk.LEFT, fill=tk.X, expand=True,
                padx=(6, 4), pady=2
            )

            # 通知状态标签
            if entry.telegram_sent is True:
                tg_text = "✅ 已通知"
                tg_fg = "#2e7d32"
            elif entry.telegram_sent is False:
                tg_text = "❌ 发送失败"
                tg_fg = "#c62828"
            else:
                tg_text = "—"
                tg_fg = "#999999"
            tg_label = tk.Label(
                row_frame,
                text=tg_text,
                bg="#ffe0e0",
                fg=tg_fg,
                font=("微软雅黑", 8),
            )
            tg_label.pack(side=tk.LEFT, padx=(0, 4), pady=2)

            # 右侧：已修复按钮
            fix_btn = tk.Button(
                row_frame,
                text="已修复",
                bg="#4caf50",
                fg="white",
                font=("微软雅黑", 8),
                relief=tk.RAISED,
                borderwidth=1,
                padx=8,
                command=lambda wid=entry.wechat_id: self._on_mark_fixed(wid),
            )
            fix_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=2)

        # 更新 Canvas 滚动区域
        self.abnormal_inner.update_idletasks()
        self.abnormal_canvas.configure(
            scrollregion=self.abnormal_canvas.bbox("all")
        )

    def _on_mark_fixed(self, wechat_id):
        """用户点击'已修复'按钮，清除该异常通知。"""
        with self._abnormal_lock:
            self._abnormal_dict.pop(wechat_id, None)

        self._refresh_abnormal_panel()
        logger.info(f"用户标记 {wechat_id} 已修复")

    def _ensure_beeping(self):
        """确保声音警报正在播放（必须在主线程调用）。"""
        if not self.sound_enabled_var.get():
            return
        if self._sound_muted:
            return
        self.stop_sound_btn.configure(state=tk.NORMAL, text="🔇 停止声音")
        if self._beep_after_id is not None:
            return  # 已在蜂鸣中
        self._beep_stopped = False
        self._beep_loop()

    def _beep_loop(self):
        """播放一声短促警报，然后调度下一次（必须在主线程调用）。"""
        if self._sound_muted:
            self._beep_after_id = None
            return

        with self._abnormal_lock:
            has_abnormal = len(self._abnormal_dict) > 0

        if not has_abnormal:
            self._beep_after_id = None
            return

        try:
            import winsound
            winsound.Beep(1000, 200)  # 1000Hz, 200ms
        except Exception:
            pass

        # 防止竞态：_stop_beep 可能在 Beep 期间被调用
        if self._beep_stopped:
            self._beep_after_id = None
            return
        self._beep_after_id = self.root.after(1500, self._beep_loop)

    def _stop_beep(self):
        """停止声音警报循环。"""
        self._beep_stopped = True
        if self._beep_after_id is not None:
            self.root.after_cancel(self._beep_after_id)
            self._beep_after_id = None

    def _on_stop_sound(self):
        """用户点击'停止声音'按钮。"""
        self._sound_muted = True
        self._stop_beep()
        self.stop_sound_btn.configure(
            state=tk.DISABLED, text="🔇 声音已停"
        )

    def _on_test_telegram(self):
        """发送 Telegram 测试消息，验证 Bot Token 和 Chat ID 配置。"""
        self.telegram_test_btn.configure(state=tk.DISABLED, text="...")
        self.telegram_status_label.configure(text="发送中...", foreground="#666666")

        def _do_test():
            from telegram_notifier import TelegramNotifier
            chat_id = self.telegram_chatid_var.get().strip()
            proxy = self.telegram_proxy_var.get().strip()
            notifier = TelegramNotifier(enabled=True, chat_id=chat_id, proxy=proxy)
            ok, msg = notifier.send_test_notification()

            def _update_ui():
                if ok:
                    self.telegram_status_label.configure(
                        text="✓ 发送成功", foreground="#2e7d32"
                    )
                else:
                    self.telegram_status_label.configure(
                        text=f"✗ {msg}", foreground="#c62828"
                    )
                self.telegram_test_btn.configure(
                    state=tk.NORMAL, text="🔄 测试"
                )
            self.root.after(0, _update_ui)

        threading.Thread(target=_do_test, daemon=True).start()

    def _append_log_immediate(self, msg):
        """立即写入日志区（绕过缓冲区，用于关键错误提示）"""
        def _write():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _write)

    # ==================== UI 更新方法 ====================

    def _update_status(self, text):
        """更新状态标签"""
        self.status_label.config(text=f"状态: {text}")

    def _start_log_timer(self):
        """启动定时器，定期从缓冲区刷新日志到界面"""
        def flush():
            with self._log_buffer_lock:
                if self._log_buffer:
                    self.log_text.config(state=tk.NORMAL)
                    for msg in self._log_buffer:
                        self.log_text.insert(tk.END, msg + "\n")
                    self.log_text.see(tk.END)
                    self.log_text.config(state=tk.DISABLED)
                    self._log_buffer.clear()
            self.root.after(200, flush)  # 每 200ms 刷新一次
        self.root.after(200, flush)

    # ==================== 控制方法 ====================

    def _start_check(self):
        """开始检查"""
        # 保存当前界面的配置
        self._save_ui_to_config()

        # 验证配置
        valid, err = self.config.validate()
        if not valid:
            messagebox.showwarning("配置错误", err, parent=self.root)
            return

        # 获取微信号列表
        ids = self._get_ids_list()
        if not ids:
            messagebox.showwarning(
                "列表为空",
                "微信号列表为空，请先添加微信号。",
                parent=self.root,
            )
            return

        # 切换按钮状态
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="⏸ 暂停")
        self.stop_btn.config(state=tk.NORMAL)
        self._paused = False
        self.ip_test_btn.config(state=tk.DISABLED)  # 检查中禁止手动测试

        # 清空上一次的日志，立即写入启动提示
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.insert(tk.END, "正在启动检查...\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="")

        # 重置异常通知状态
        with self._abnormal_lock:
            self._abnormal_dict.clear()
        self._sound_muted = False
        self.stop_sound_btn.configure(state=tk.DISABLED, text="🔇 停止声音")
        self._refresh_abnormal_panel()

        # 启动检查
        logger.info("用户点击开始检查")
        try:
            self.engine.start_with_ids(ids)
        except Exception as e:
            import traceback
            err_msg = f"启动检查失败: {e}\n{traceback.format_exc()}"
            logger.error(err_msg)
            self._append_log_immediate(f"[错误] {err_msg}")
            self.start_btn.config(state=tk.NORMAL)
            self.pause_btn.config(state=tk.DISABLED, text="⏸ 暂停")
            self.stop_btn.config(state=tk.DISABLED)
            self._paused = False

    def _stop_check(self):
        """停止检查"""
        self.engine.stop()
        self._update_status("正在停止...（等待当前操作完成）")
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="⏸ 暂停")
        self.stop_btn.config(state=tk.DISABLED)
        self._paused = False

    def _on_pause_resume(self):
        """暂停/继续切换"""
        if self._paused:
            # 继续：重新读取配置和微信号
            self._save_ui_to_config()
            ids = self._get_ids_list()
            if not ids:
                messagebox.showwarning("列表为空", "微信号列表为空", parent=self.root)
                return

            cfg = self.engine._build_config_snapshot()

            # 刷新 Telegram 配置
            self.engine._telegram_notifier.enabled = self.config.get("telegram_enabled", False)
            self.engine._telegram_notifier.chat_id = self.config.get("telegram_chat_id", "")
            self.engine._telegram_notifier.proxy = self.config.get("telegram_proxy", "")

            self.engine.resume(ids=ids, cfg=cfg)
            self._paused = False
            self.pause_btn.config(text="⏸ 暂停")
            self._update_status("检查中...")
        else:
            # 暂停
            self.engine.pause()
            self._paused = True
            self.pause_btn.config(text="▶ 继续")
            self._update_status("已暂停")

    def _on_close(self):
        """窗口关闭事件"""
        self._stop_beep()  # 停止声音警报
        if self.engine.is_running:
            if not messagebox.askyesno(
                "确认退出",
                "检查正在进行中，确定要退出吗？",
                parent=self.root,
            ):
                return
            self.engine.stop()
        self.root.destroy()

    def _center_window(self):
        """将窗口居中显示"""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _check_runtime_env(self):
        """检查运行环境：Tesseract OCR 是否可用"""
        try:
            from wechat_controller import _get_tesseract_path
            import subprocess, os
            tesseract = _get_tesseract_path()
            result = subprocess.run(
                [tesseract, "--version"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            if result.returncode != 0:
                raise OSError(f"tesseract 返回码 {result.returncode}")
            logger.info(f"Tesseract OCR 可用: {result.stdout.split(chr(10))[0]}")
        except Exception as e:
            logger.warning(f"Tesseract OCR 自检失败: {e}")
            self._append_log_immediate(
                "[警告] Tesseract OCR 不可用，下拉菜单识别和弹窗检测将无法工作！\n"
                "       请确保 tesseract 已安装并添加到 PATH，或使用正式打包的 exe。"
            )

    # ==================== IP切换相关方法 ====================

    def _on_ip_changed(self, old_ip, new_ip, node_name, delay, success):
        """引擎IP切换回调（在子线程中调用）"""
        def _update():
            # 取消上一次的颜色恢复定时器
            if self._ip_color_after_id is not None:
                self.root.after_cancel(self._ip_color_after_id)
                self._ip_color_after_id = None

            if success:
                # 更新顶部IP显示（绿色高亮）
                self.ip_current_label.config(
                    text=new_ip, foreground="#2e7d32"
                )
                node_text = f"{node_name} ({delay}ms)" if node_name else "--"
                self.ip_node_label.config(text=node_text, foreground="#2e7d32")
                self.ip_status_label.config(
                    text=f"上次切换: 成功 {_time.strftime('%H:%M:%S')}",
                    foreground="#2e7d32"
                )
                # 1.5秒后恢复常态颜色
                self._ip_color_after_id = self.root.after(1500, lambda: (
                    self.ip_current_label.config(foreground="#1976d2"),
                    self.ip_node_label.config(foreground="#666666"),
                    setattr(self, '_ip_color_after_id', None),
                ))
            else:
                self.ip_status_label.config(
                    text=f"上次切换: 失败 {_time.strftime('%H:%M:%S')}",
                    foreground="#c62828"
                )

            # 追加切换记录
            time_str = _time.strftime("%H:%M")
            record = {
                "time": time_str,
                "old_ip": old_ip,
                "new_ip": new_ip if success else "(无变化)",
                "node": f"{node_name} ({delay}ms)" if node_name else "--",
                "success": success,
            }
            self._ip_switch_records.insert(0, record)
            if len(self._ip_switch_records) > 50:
                self._ip_switch_records = self._ip_switch_records[:50]
            self._refresh_ip_history()

        self.root.after(0, _update)

    def _fetch_current_ip_info(self):
        """获取当前IP和Clash节点信息，更新顶部显示（必须在主线程调用）"""
        if self._fetching_ip:
            return  # 防重入
        self._fetching_ip = True
        self.ip_current_label.config(text="查询中...", foreground="#999999")
        self.ip_node_label.config(text="...", foreground="#999999")

        def _do_fetch():
            from ip_switcher import IPSwitcher
            method = self.ip_switch_method_var.get()
            if method == "clash":
                sw = IPSwitcher(
                    method="clash",
                    clash_url=self.ip_clash_url_var.get().strip(),
                    proxy_group=self.ip_clash_group_var.get().strip(),
                    verify_url=self.ip_verify_url_var.get().strip(),
                )
            else:
                sw = IPSwitcher(
                    method="command",
                    verify_url=self.ip_verify_url_var.get().strip(),
                )

            # 获取IP
            ip, ip_err = sw.get_current_ip()

            # 获取Clash节点信息
            node_name = None
            if method == "clash":
                current, _, clash_err = sw.get_clash_info()
                if not clash_err:
                    node_name = current

            def _update_ui():
                self._fetching_ip = False
                if ip:
                    self.ip_current_label.config(text=ip, foreground="#1976d2")
                else:
                    # 网络不通时用中性提示，不显示红色报错（api.ipify.org 国内需代理）
                    err_text = "需代理" if ip_err and "网络错误" in str(ip_err) else (f"获取失败: {ip_err}" if ip_err else "未知")
                    fg = "#999999" if "网络错误" in str(ip_err or "") else "#c62828"
                    self.ip_current_label.config(text=err_text, foreground=fg)
                if node_name:
                    self.ip_node_label.config(text=node_name, foreground="#666666")
                else:
                    self.ip_node_label.config(text="--", foreground="#999999")

            self.root.after(0, _update_ui)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _refresh_ip_history(self):
        """刷新切换记录列表"""
        for widget in self.ip_history_inner.winfo_children():
            widget.destroy()

        if not self._ip_switch_records:
            placeholder = ttk.Label(
                self.ip_history_inner, text="暂无切换记录",
                foreground="#999999", font=("微软雅黑", 8)
            )
            placeholder.pack(pady=4)
        else:
            for rec in self._ip_switch_records:
                row = tk.Frame(self.ip_history_inner, bg="#f5f5f5")
                row.pack(fill=tk.X, pady=1)

                # 状态图标
                icon = "✅" if rec["success"] else "❌"
                icon_label = tk.Label(
                    row, text=icon, bg="#f5f5f5", font=("微软雅黑", 8)
                )
                icon_label.pack(side=tk.LEFT, padx=(4, 2))

                # 详情文本
                color = "#2e7d32" if rec["success"] else "#c62828"
                detail = (
                    f"{rec['time']}  {rec['old_ip']} → {rec['new_ip']}"
                    f"  |  {rec['node']}"
                )
                detail_label = tk.Label(
                    row, text=detail, bg="#f5f5f5",
                    fg=color, font=("Consolas", 8), anchor=tk.W
                )
                detail_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.ip_history_inner.update_idletasks()
        self.ip_history_canvas.configure(
            scrollregion=self.ip_history_canvas.bbox("all")
        )

    def _on_test_ip_switch(self):
        """手动测试IP切换（在主线程中触发）"""
        self.ip_test_btn.config(state=tk.DISABLED, text="切换中...")
        self.ip_status_label.config(text="正在测速切换...", foreground="#ff8f00")

        def _do_test():
            from ip_switcher import IPSwitcher
            method = self.ip_switch_method_var.get()
            if method == "clash":
                sw = IPSwitcher(
                    method="clash",
                    clash_url=self.ip_clash_url_var.get().strip(),
                    proxy_group=self.ip_clash_group_var.get().strip(),
                    verify_url=self.ip_verify_url_var.get().strip(),
                    timeout=int(self.ip_timeout_var.get() or 30),
                )
            else:
                sw = IPSwitcher(
                    method="command",
                    command=self.ip_command_var.get().strip(),
                    verify_url=self.ip_verify_url_var.get().strip(),
                    timeout=int(self.ip_timeout_var.get() or 30),
                )

            ok, msg, old_ip, new_ip, node_name, delay = sw.switch_ip()

            def _update_ui():
                if ok:
                    self._on_ip_changed(old_ip, new_ip, node_name, delay, True)
                else:
                    # 失败时直接设置状态标签（含具体错误），追加记录
                    self.ip_status_label.config(
                        text=f"切换失败: {msg}", foreground="#c62828"
                    )
                    record = {
                        "time": _time.strftime("%H:%M"),
                        "old_ip": old_ip or "--",
                        "new_ip": "(无变化)",
                        "node": "--",
                        "success": False,
                    }
                    self._ip_switch_records.insert(0, record)
                    if len(self._ip_switch_records) > 50:
                        self._ip_switch_records = self._ip_switch_records[:50]
                    self._refresh_ip_history()
                self.ip_test_btn.config(state=tk.NORMAL, text="立即切换(测试)")

            self.root.after(0, _update_ui)

        threading.Thread(target=_do_test, daemon=True).start()

    def _on_ip_method_changed(self):
        """切换Clash/自定义命令模式的UI显示"""
        method = self.ip_switch_method_var.get()
        if method == "clash":
            self.ip_cmd_row.pack_forget()
            self.ip_clash_row.pack(fill=tk.X, pady=(0, 3))
        else:
            self.ip_clash_row.pack_forget()
            self.ip_cmd_row.pack(fill=tk.X, pady=(0, 3))

    def _show_ip_templates(self):
        """显示IP切换命令模板弹窗"""
        templates = (
            "【Clash API 方式】（推荐，无需配置命令）\n"
            "  程序直接通过Clash API切换节点，填好地址和代理组即可。\n"
            "  默认地址: http://127.0.0.1:9090\n"
            "  常见代理组名: Proxy / GLOBAL / 自动选择\n"
            "\n"
            "【自定义命令 - 常见路由器重启命令】\n"
            "  华为4G移动路由:\n"
            "    curl -X POST \"http://192.168.8.1/api/device/control\" \\\n"
            "      -H \"Content-Type: application/json\" \\\n"
            "      -d '{\"action\":\"reboot\"}'\n"
            "\n"
            "  中兴CPE:\n"
            "    curl \"http://192.168.0.1/goform/goform_set_cmd_process\" \\\n"
            "      -d \"goformId=REBOOT_DEVICE&isTest=false\"\n"
            "\n"
            "  TP-Link 4G路由器:\n"
            "    curl -u admin:密码 \\\n"
            "      \"http://192.168.0.1/admin/reboot\"\n"
            "\n"
            "  通用方式（调用外部脚本）:\n"
            "    C:\\scripts\\change_ip.bat\n"
            "    python C:\\scripts\\reboot_router.py\n"
            "\n"
            "  提示: 不同型号API不同，请搜索\"路由器型号 + API重启\"。\n"
            "  如使用curl，需先下载: https://curl.se/windows/"
        )

        top = tk.Toplevel(self.root)
        top.title("IP切换命令模板")
        top.geometry("580x460")
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()

        frame = ttk.Frame(top, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(
            frame, font=("Consolas", 9), wrap=tk.WORD,
            bg="#fafafa", relief=tk.SUNKEN, borderwidth=1,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, templates)
        text.config(state=tk.DISABLED)

        ttk.Button(
            frame, text="关闭", command=top.destroy
        ).pack(pady=(8, 0))

        # 居中
        top.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 580) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 460) // 2
        top.geometry(f"+{x}+{y}")

    # ==================== 运行 ====================

    def run(self):
        """启动主循环"""
        self.root.mainloop()


# ==================== 入口 ====================
if __name__ == "__main__":
    # 平台检查：非 Windows 环境下 uiautomation 不可用
    if sys.platform != "win32":
        import tkinter.messagebox as _mb
        _mb.showwarning(
            "平台不兼容",
            "此工具仅支持 Windows 系统。\n"
            "当前系统不是 Windows，微信自动化功能无法使用。\n"
            "程序将以预览模式启动，但无法实际操作微信。",
        )
    app = WeChatCheckerApp()
    app.run()
