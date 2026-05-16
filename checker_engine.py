"""
检查引擎模块
管理检查循环、批次调度、进度追踪
"""
import threading
import time
import random

from logger_setup import logger
from config_manager import ConfigManager
from wechat_controller import WeChatController
from telegram_notifier import TelegramNotifier


class CheckerEngine:
    """
    检查引擎

    状态流转:
        idle -> running -> (异常停止 / 用户停止) -> idle
    """

    def __init__(self, config_mgr: ConfigManager):
        self.config = config_mgr
        self.wechat = WeChatController(config_mgr.get("wechat_path", ""))
        self.wechat._gui_log = self._emit_log      # 注入 GUI 日志回调
        self._running = False
        self._stop_event = threading.Event()
        self.wechat.set_stop_event(self._stop_event)  # 注入停止信号

        # Telegram 通知器
        self._telegram_notifier = TelegramNotifier(
            enabled=config_mgr.get("telegram_enabled", False)
        )

        # 回调函数（供 GUI 使用）
        self.on_log = None           # func(msg)
        self.on_status = None        # func(status_text)
        self.on_progress = None      # func(current, total, batch_info)
        self.on_abnormal = None      # func(wechat_id, reason, telegram_sent)
        self.on_countdown = None     # func(remaining_seconds, label)

        # 当前检查进度
        self.current_batch = 0
        self.total_batches = 0
        self.current_account = 0
        self.total_accounts = 0
        self.checked_accounts = []   # 已检查列表

    @property
    def is_running(self):
        return self._running

    def _emit_log(self, msg, level="info"):
        """向 GUI 发送日志"""
        if level == "info":
            logger.info(msg)
        elif level == "warn":
            logger.warning(msg)
        elif level == "error":
            logger.error(msg)
        if self.on_log:
            self.on_log(msg)

    def _emit_status(self, text):
        if self.on_status:
            self.on_status(text)

    def _emit_progress(self, current, total, batch_info=""):
        if self.on_progress:
            self.on_progress(current, total, batch_info)

    def _build_config_snapshot(self):
        """快照所有配置参数，避免子线程读取时被主线程并发修改"""
        return {
            "batch_size": self.config.get("batch_size", 9),
            "batch_interval_min": self.config.get("batch_interval_min", 30),
            "batch_interval_max": self.config.get("batch_interval_max", 50),
            "account_interval_min": self.config.get("account_interval_min", 3),
            "account_interval_max": self.config.get("account_interval_max", 5),
            "max_rounds": self.config.get("max_rounds", 100),
        }

    def stop(self):
        """请求停止检查"""
        self._stop_event.set()
        self._emit_log("用户请求停止检查", "warn")

    def start(self, ids_file=None):
        """
        启动检查线程

        Args:
            ids_file: 微信号列表文件路径，None 则使用配置文件中的路径
        """
        if self._running:
            self._emit_log("检查已在运行中", "warn")
            return

        # 读取微信号列表
        filepath = ids_file or self.config.get("ids_file", "wechat_ids.txt")
        ids, err = ConfigManager.load_ids(filepath)
        if err:
            self._emit_log(f"读取微信号失败: {err}", "error")
            return
        if not ids:
            self._emit_log("微信号列表为空", "error")
            return

        self._running = True
        self._stop_event.clear()
        self.total_accounts = len(ids)
        self.checked_accounts = []

        config_snapshot = self._build_config_snapshot()
        self._telegram_notifier.enabled = self.config.get("telegram_enabled", False)

        # 在子线程中运行检查
        try:
            thread = threading.Thread(
                target=self._run_check_loop,
                args=(ids, config_snapshot),
                daemon=True,
                name="CheckerThread",
            )
            thread.start()
        except Exception as e:
            self._running = False
            self._emit_log(f"启动检查线程失败: {e}", "error")
            return

        self._emit_log(f"检查启动，共 {len(ids)} 个微信号")

    def start_with_ids(self, ids_list):
        """
        用微信号列表直接启动检查（不读文件）

        Args:
            ids_list: 微信号字符串列表
        """
        if self._running:
            self._emit_log("检查已在运行中", "warn")
            return
        if not ids_list:
            self._emit_log("微信号列表为空", "error")
            return

        self._running = True
        self._stop_event.clear()
        self.total_accounts = len(ids_list)
        self.checked_accounts = []

        config_snapshot = self._build_config_snapshot()
        self._telegram_notifier.enabled = self.config.get("telegram_enabled", False)

        try:
            thread = threading.Thread(
                target=self._run_check_loop,
                args=(ids_list, config_snapshot),
                daemon=True,
                name="CheckerThread",
            )
            thread.start()
        except Exception as e:
            self._running = False
            self._emit_log(f"启动检查线程失败: {e}", "error")
            return

        self._emit_log(f"检查启动，共 {len(ids_list)} 个微信号")

    def _run_check_loop(self, all_ids, cfg):
        """
        主检查循环（在子线程中运行）
        支持多轮循环，每批最多 batch_size 个

        Args:
            all_ids: 微信号列表
            cfg: 配置快照字典，避免与主线程并发读写
        """
        batch_size = cfg["batch_size"]
        bi_min = cfg["batch_interval_min"]
        bi_max = cfg["batch_interval_max"]
        ai_min = cfg["account_interval_min"]
        ai_max = cfg["account_interval_max"]
        max_rounds = cfg["max_rounds"]

        # 初始化 COM（uiautomation 依赖，子线程必须手动初始化）
        try:
            import ctypes
            hr = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
            if hr < 0:
                self._emit_log(f"COM 初始化失败: 0x{hr & 0xFFFFFFFF:08X}", "error")
        except Exception:
            pass

        round_num = 0

        try:
            while not self._stop_event.is_set() and round_num < max_rounds:
                round_num += 1
                self._emit_log(f"====== 第 {round_num} 轮检查开始 ======")
                self._emit_status(f"检查中 - 第{round_num}轮")

                # 检查微信是否在运行
                if not self.wechat.is_wechat_running():
                    self._emit_log("微信未运行，请手动打开微信并登录", "error")
                    self._emit_status("微信未运行")
                    # 系统级错误，直接停止，不触发异常弹窗
                    break

                # 将列表分批
                batches = []
                for i in range(0, len(all_ids), batch_size):
                    batches.append(all_ids[i:i + batch_size])

                self.total_batches = len(batches)
                total_checked = 0

                for batch_idx, batch in enumerate(batches):
                    if self._stop_event.is_set():
                        break

                    self.current_batch = batch_idx + 1
                    self._emit_log(
                        f"--- 第 {batch_idx+1}/{len(batches)} 批 "
                        f"(共 {len(batch)} 个号) ---"
                    )

                    for acc_idx, wechat_id in enumerate(batch):
                        if self._stop_event.is_set():
                            break

                        self.current_account = total_checked + acc_idx + 1
                        self._emit_progress(
                            self.current_account,
                            self.total_accounts,
                            f"第{round_num}轮 第{batch_idx+1}批"
                        )

                        # 检查单个微信号（COM 已在父线程初始化，直接用）
                        if self._stop_event.is_set():
                            break
                        status, detail = self.wechat.check_single_account(wechat_id)

                        self.checked_accounts.append({
                            "id": wechat_id,
                            "status": status,
                            "detail": detail,
                        })

                        # 停止信号检查：OCR 等阻塞操作期间用户可能点了停止
                        if self._stop_event.is_set():
                            break

                        if status == "abnormal":
                            self._emit_log(
                                f"[异常] {wechat_id}: {detail}", "warn"
                            )
                            # 发送 Telegram 通知
                            telegram_sent = self._telegram_notifier.send_abnormal_notification(
                                wechat_id, detail
                            )
                            # 非阻塞通知 GUI，含通知状态
                            if self.on_abnormal:
                                self.on_abnormal(wechat_id, detail, telegram_sent)
                        elif status == "success":
                            self._emit_log(
                                f"[正常] {wechat_id}: {detail}", "info"
                            )
                        else:
                            self._emit_log(
                                f"[错误] {wechat_id}: {detail}", "error"
                            )

                        # 账号间等待（随机间隔）。操作出错时跳过，直接查下一个
                        if acc_idx < len(batch) - 1 and not self._stop_event.is_set():
                            if status == "error":
                                self._emit_log("操作出错，跳过等待直接检查下一个...")
                            else:
                                wait_time = random.uniform(ai_min, ai_max)
                                self._emit_log(f"等待 {wait_time:.1f} 秒后检查下一个...")
                                self._wait_with_stop(wait_time, "account")

                    total_checked += len(batch)

                    # 批次间等待
                    if batch_idx < len(batches) - 1 and not self._stop_event.is_set():
                        wait_min = random.uniform(bi_min, bi_max)
                        self._emit_log(
                            f"本批完成，等待 {wait_min:.1f} 分钟后检查下一批..."
                        )
                        self._emit_status(
                            f"等待中 - 第{round_num}轮 下一批 "
                            f"({wait_min:.0f}分钟后)"
                        )
                        self._wait_with_stop(wait_min * 60, f"batch_r{round_num}")

                # 一轮完成
                if not self._stop_event.is_set():
                    self._emit_log(
                        f"====== 第 {round_num} 轮完成，共检查 "
                        f"{len(self.checked_accounts)} 个号 ======"
                    )

                    # 最后一轮不等待，直接结束
                    if round_num >= max_rounds:
                        self._emit_log("已达最大轮数，检查结束")
                        break

                    # 进入下一轮前等待
                    wait_min = random.uniform(bi_min, bi_max)
                    self._emit_log(
                        f"所有号已完成，等待 {wait_min:.1f} 分钟后开始下一轮..."
                    )
                    self._emit_status(
                        f"等待中 - 下一轮 ({wait_min:.0f}分钟后)"
                    )
                    self._wait_with_stop(wait_min * 60, f"round_r{round_num}")

        except Exception as e:
            self._emit_log(f"检查循环异常: {e}", "error")
            import traceback
            self._emit_log(traceback.format_exc(), "error")

        finally:
            self._running = False
            self._emit_status("已停止" if self._stop_event.is_set() else "已完成")
            self._emit_log("检查已停止")

    def _wait_with_stop(self, seconds, countdown_label=""):
        """等待指定秒数，期间可被停止信号打断，每秒发倒计时回调"""
        interval = 0.5  # 每 0.5 秒检查一次停止信号
        elapsed = 0
        last_reported = -1  # 只在秒数变化时发回调，减少 GUI 负担
        while elapsed < seconds and not self._stop_event.is_set():
            time.sleep(min(interval, seconds - elapsed))
            elapsed += interval
            remaining = max(0, seconds - elapsed)
            if self.on_countdown and countdown_label and int(remaining) != last_reported:
                self.on_countdown(remaining, countdown_label)
                last_reported = int(remaining)
            elapsed += interval
