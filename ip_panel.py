"""
IP 自动切换面板 Mixin
提供 IP 面板的 UI 构建和所有相关回调方法
"""
import tkinter as tk
from tkinter import ttk
import threading
import time as _time

from logger_setup import logger


class IPPanelMixin:
    """IP 自动切换面板 — 混入 WeChatCheckerApp"""

    # ==================== 初始化 ====================

    def _init_ip_panel_vars(self):
        """初始化 IP 面板相关的实例变量（由主类 __init__ 调用）"""
        self._ip_switch_records = []
        self._ip_switch_in_progress = False
        self._fetching_ip = False
        self._ip_color_after_id = None

    # ==================== UI 构建 ====================

    def _build_ip_panel(self, parent):
        """构建 IP 自动切换面板（右栏顶部）"""
        ip_frame = ttk.LabelFrame(parent, text="IP自动切换", padding=8)
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
        self.ip_clash_url_var = tk.StringVar(value="http://127.0.0.1:9097")
        ttk.Entry(
            self.ip_clash_row, textvariable=self.ip_clash_url_var, width=18
        ).pack(side=tk.LEFT, padx=(4, 10))

        ttk.Label(self.ip_clash_row, text="代理组:").pack(side=tk.LEFT)
        self.ip_clash_group_var = tk.StringVar(value="Proxy")
        ttk.Entry(
            self.ip_clash_row, textvariable=self.ip_clash_group_var, width=10
        ).pack(side=tk.LEFT, padx=(4, 0))

        # API 密钥行（Clash Verge 需要）
        ip_secret_row = ttk.Frame(ip_config)
        ip_secret_row.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(ip_secret_row, text="API密钥:", font=("微软雅黑", 8)).pack(side=tk.LEFT)
        self.ip_clash_secret_var = tk.StringVar()
        ttk.Entry(
            ip_secret_row, textvariable=self.ip_clash_secret_var, width=22
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            ip_secret_row, text="(选填，Clash Verge设置→外部控制中查看)",
            font=("微软雅黑", 7), foreground="#999999"
        ).pack(side=tk.LEFT, padx=(6, 0))

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

        # 初始隐藏命令配置行（clash 模式默认）
        self.ip_cmd_row.pack_forget()

    # ==================== 配置加载/保存 ====================

    def _load_ip_config_to_ui(self):
        """将 IP 配置加载到界面控件"""
        self.ip_switch_enabled_var.set(self.config.get("ip_switch_enabled", False))
        self.ip_switch_method_var.set(self.config.get("ip_switch_method", "clash"))
        self.ip_clash_url_var.set(self.config.get("ip_switch_clash_url", "http://127.0.0.1:9090"))
        self.ip_clash_group_var.set(self.config.get("ip_switch_clash_group", "Proxy"))
        self.ip_clash_secret_var.set(self.config.get("ip_switch_clash_secret", ""))
        self.ip_command_var.set(self.config.get("ip_switch_command", ""))
        self.ip_batch_count_var.set(str(self.config.get("ip_switch_batch_count", 3)))
        self.ip_timeout_var.set(str(self.config.get("ip_switch_timeout", 30)))
        self.ip_verify_url_var.set(self.config.get("ip_switch_verify_url", "https://api.ipify.org"))
        self.ip_advance_var.set(str(self.config.get("ip_switch_advance_seconds", 300)))
        self._on_ip_method_changed()

    def _save_ip_ui_to_config(self):
        """将 IP 界面值写入配置"""
        self.config.set("ip_switch_enabled", self.ip_switch_enabled_var.get())
        self.config.set("ip_switch_method", self.ip_switch_method_var.get())
        self.config.set("ip_switch_clash_url", self.ip_clash_url_var.get().strip())
        self.config.set("ip_switch_clash_group", self.ip_clash_group_var.get().strip())
        self.config.set("ip_switch_clash_secret", self.ip_clash_secret_var.get().strip())
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

    # ==================== IP 切换回调 ====================

    def _on_ip_changed(self, old_ip, new_ip, node_name, delay, success):
        """引擎IP切换回调（在子线程中调用）"""
        def _update():
            if self._ip_color_after_id is not None:
                self.root.after_cancel(self._ip_color_after_id)
                self._ip_color_after_id = None

            if success:
                self.ip_current_label.config(text=new_ip, foreground="#2e7d32")
                node_text = f"{node_name} ({delay}ms)" if node_name else "--"
                self.ip_node_label.config(text=node_text, foreground="#2e7d32")
                self.ip_status_label.config(
                    text=f"上次切换: 成功 {_time.strftime('%H:%M:%S')}",
                    foreground="#2e7d32"
                )
                # 1.5秒后恢复常态颜色
                def _restore_ip_color():
                    self.ip_current_label.config(foreground="#1976d2")
                    self.ip_node_label.config(foreground="#666666")
                    self._ip_color_after_id = None
                self._ip_color_after_id = self.root.after(1500, _restore_ip_color)
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
            return
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
                    secret=self.ip_clash_secret_var.get().strip(),
                    verify_url=self.ip_verify_url_var.get().strip(),
                )
            else:
                sw = IPSwitcher(
                    method="command",
                    verify_url=self.ip_verify_url_var.get().strip(),
                )

            ip, ip_err = sw.get_current_ip()
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

                icon = "✅" if rec["success"] else "❌"
                icon_label = tk.Label(
                    row, text=icon, bg="#f5f5f5", font=("微软雅黑", 8)
                )
                icon_label.pack(side=tk.LEFT, padx=(4, 2))

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
                    secret=self.ip_clash_secret_var.get().strip(),
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
        """切换 Clash / 自定义命令模式的 UI 显示"""
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

        ttk.Button(frame, text="关闭", command=top.destroy).pack(pady=(8, 0))

        top.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 580) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 460) // 2
        top.geometry(f"+{x}+{y}")
