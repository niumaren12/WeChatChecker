"""
异常通知面板 Mixin
提供异常面板的 UI 构建和所有相关回调方法
"""
import tkinter as tk
from tkinter import ttk
import time as _time

from logger_setup import logger


class AbnormalPanelMixin:
    """异常通知面板 — 混入 WeChatCheckerApp"""

    # ==================== 初始化 ====================

    def _init_abnormal_panel_vars(self):
        """初始化异常面板相关的实例变量（由主类 __init__ 调用）"""
        self._sound_muted = False
        self._beep_after_id = None
        self._beep_stopped = False
        self._paused = False

    # ==================== UI 构建 ====================

    def _build_abnormal_panel(self, parent):
        """构建异常通知面板（右栏，IP面板下方）"""
        self.abnormal_frame = ttk.LabelFrame(
            parent, text="⚠ 异常账号 (0)", padding=4
        )
        self.abnormal_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

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

        self.abnormal_inner = ttk.Frame(self.abnormal_canvas)
        self.abnormal_inner_id = self.abnormal_canvas.create_window(
            (0, 0), window=self.abnormal_inner, anchor=tk.NW
        )

        def _on_abnormal_canvas_configure(event):
            self.abnormal_canvas.itemconfig(
                self.abnormal_inner_id, width=event.width
            )
        self.abnormal_canvas.bind("<Configure>", _on_abnormal_canvas_configure)

        def _on_abnormal_inner_configure(event):
            self.abnormal_canvas.configure(
                scrollregion=self.abnormal_canvas.bbox("all")
            )
        self.abnormal_inner.bind("<Configure>", _on_abnormal_inner_configure)

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

    # ==================== 异常面板刷新 ====================

    def _refresh_abnormal_panel(self):
        """重建异常通知面板内容（必须在主线程调用）"""
        for widget in self.abnormal_inner.winfo_children():
            widget.destroy()

        with self._abnormal_lock:
            entries = list(self._abnormal_dict.values())
            count = len(entries)

        self.abnormal_frame.configure(text=f"⚠ 异常账号 ({count})")

        if count == 0:
            self.abnormal_count_label.configure(text="")
            self.abnormal_canvas.configure(bg="#f0f0f0")
            self.stop_sound_btn.configure(state=tk.DISABLED, text="🔇 停止声音")
            self._stop_beep()
            return

        self.abnormal_canvas.configure(bg="#fff0f0")
        self.abnormal_count_label.configure(text=f"共 {count} 个异常账号待处理")

        entries.sort(key=lambda e: e.timestamp, reverse=True)

        for entry in entries:
            row_frame = tk.Frame(
                self.abnormal_inner,
                bg="#ffe0e0",
                relief=tk.GROOVE,
                borderwidth=1,
            )
            row_frame.pack(fill=tk.X, pady=1, padx=2)

            info_label = tk.Label(
                row_frame,
                text=f"⚠ {entry.wechat_id}  —  {entry.reason}",
                bg="#ffe0e0",
                fg="#cc0000",
                font=("微软雅黑", 9, "bold"),
                anchor=tk.W,
            )
            info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4), pady=2)

            if entry.telegram_sent is True:
                tg_text, tg_fg = "✅ 已通知", "#2e7d32"
            elif entry.telegram_sent is False:
                tg_text, tg_fg = "❌ 发送失败", "#c62828"
            else:
                tg_text, tg_fg = "—", "#999999"
            tg_label = tk.Label(
                row_frame, text=tg_text, bg="#ffe0e0",
                fg=tg_fg, font=("微软雅黑", 8),
            )
            tg_label.pack(side=tk.LEFT, padx=(0, 4), pady=2)

            fix_btn = tk.Button(
                row_frame, text="已修复",
                bg="#4caf50", fg="white",
                font=("微软雅黑", 8),
                relief=tk.RAISED, borderwidth=1, padx=8,
                command=lambda wid=entry.wechat_id: self._on_mark_fixed(wid),
            )
            fix_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=2)

        self.abnormal_inner.update_idletasks()
        self.abnormal_canvas.configure(
            scrollregion=self.abnormal_canvas.bbox("all")
        )

    def _on_mark_fixed(self, wechat_id):
        """用户点击'已修复'按钮，清除该异常通知"""
        with self._abnormal_lock:
            self._abnormal_dict.pop(wechat_id, None)
        self._refresh_abnormal_panel()
        logger.info(f"用户标记 {wechat_id} 已修复")

    # ==================== 声音警报 ====================

    def _ensure_beeping(self):
        """确保声音警报正在播放（必须在主线程调用）"""
        if not self.sound_enabled_var.get():
            return
        if self._sound_muted:
            return
        self.stop_sound_btn.configure(state=tk.NORMAL, text="🔇 停止声音")
        if self._beep_after_id is not None:
            return
        self._beep_stopped = False
        self._beep_loop()

    def _beep_loop(self):
        """播放一声短促警报，然后调度下一次（必须在主线程调用）"""
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
            winsound.Beep(1000, 200)
        except Exception:
            pass

        if self._beep_stopped:
            self._beep_after_id = None
            return
        self._beep_after_id = self.root.after(1500, self._beep_loop)

    def _stop_beep(self):
        """停止声音警报循环"""
        self._beep_stopped = True
        if self._beep_after_id is not None:
            self.root.after_cancel(self._beep_after_id)
            self._beep_after_id = None

    def _on_stop_sound(self):
        """用户点击'停止声音'按钮"""
        self._sound_muted = True
        self._stop_beep()
        self.stop_sound_btn.configure(state=tk.DISABLED, text="🔇 声音已停")
