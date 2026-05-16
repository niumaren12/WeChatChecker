"""
微信账号状态检查工具 - 主入口
基于 tkinter 的 GUI 界面
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import sys

from config_manager import ConfigManager
from checker_engine import CheckerEngine
from logger_setup import logger
import wechat_controller  # 确保 PyInstaller 打包此模块


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

        # 创建主窗口
        self.root = tk.Tk()
        self.root.title(f"{self.APP_NAME} {self.APP_VERSION}")
        self.root.geometry("720x720")
        self.root.minsize(640, 660)

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
        # 主框架 + 内边距
        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

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

        # ---- 微信号列表区域 ----
        ids_frame = ttk.LabelFrame(main_frame, text="微信号列表", padding=6)
        ids_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Listbox + 滚动条
        listbox_frame = ttk.Frame(ids_frame)
        listbox_frame.pack(fill=tk.BOTH, expand=True)

        self.ids_listbox = tk.Listbox(
            listbox_frame, height=6, selectmode=tk.EXTENDED,
            font=("Consolas", 9), bg="#f5f5f5", relief=tk.SUNKEN,
        )
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

        # ---- 控制按钮区域 ----
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        self.start_btn = ttk.Button(
            btn_frame, text="▶ 开始检查", width=14,
            command=self._start_check
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ttk.Button(
            btn_frame, text="■ 停止检查", width=14,
            command=self._stop_check, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT)

        # 状态标签
        self.status_label = ttk.Label(
            btn_frame, text="状态: 空闲", font=("微软雅黑", 10),
            foreground="#666666"
        )
        self.status_label.pack(side=tk.RIGHT)

        # 进度条
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 6))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var,
            maximum=100, length=600
        )
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_label = ttk.Label(progress_frame, text="", width=16)
        self.progress_label.pack(side=tk.RIGHT, padx=(8, 0))

        # ---- 日志区域 ----
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_text_frame = ttk.Frame(log_frame)
        log_text_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_text_frame,
            height=14,
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

    def _browse_wechat_path(self):
        """浏览选择微信可执行文件"""
        path = filedialog.askopenfilename(
            title="选择微信主程序",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
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

    # ==================== 引擎回调 ====================

    def _on_engine_log(self, msg):
        """引擎日志回调（在子线程中调用）"""
        with self._log_buffer_lock:
            self._log_buffer.append(msg)

    def _on_engine_status(self, text):
        """引擎状态回调"""
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

    def _on_engine_abnormal(self, wechat_id, reason):
        """
        引擎异常回调（在检查线程中被调用）
        弹窗提示用户，超时 60 秒无人操作则自动停止
        返回 True 表示停止检查
        """
        event = threading.Event()

        def show_alert():
            msg = (
                f"检测到异常微信号！\n\n"
                f"微信号: {wechat_id}\n"
                f"异常原因: {reason}\n\n"
                f"请及时处理！"
            )
            # 播放警告音
            try:
                import winsound; winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass

            messagebox.showerror(
                "⚠ 账号异常警告",
                msg,
                parent=self.root,
            )
            event.set()

        self.root.after(0, show_alert)
        event.wait()  # 阻塞等待用户手动关闭弹窗，确保人看到异常
        return True  # 异常时总是停止检查

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
        self.stop_btn.config(state=tk.NORMAL)

        # 清空上一次的日志
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="")

        # 启动检查
        logger.info("用户点击开始检查")
        self.engine.start_with_ids(ids)

    def _stop_check(self):
        """停止检查"""
        self.engine.stop()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def _on_close(self):
        """窗口关闭事件"""
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
