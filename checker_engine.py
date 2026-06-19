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
from bark_notifier import BarkNotifier


class CheckerEngine:
    """
    检查引擎

    状态流转:
        idle -> running -> (异常停止 / 用户停止) -> idle
    """

    def __init__(self, config_mgr: ConfigManager):
        self.config = config_mgr
        self.wechat = WeChatController(config_mgr.get("wechat_path", ""))
        self.wechat._gui_log = self._forward_to_gui  # controller日志仅转发GUI，不重复写logger
        self._running = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()   # 暂停信号
        self._new_ids = None    # 恢复时的微信号列表（None=不更新）
        self._new_cfg = None    # 恢复时的配置快照（None=不更新）
        self.wechat.set_stop_event(self._stop_event)  # 注入停止信号
        self.wechat.set_pause_event(self._pause_event) # 注入暂停信号

        # 心跳文件路径 — 供单实例锁检测旧实例卡死
        import sys as _sys, os as _os
        if getattr(_sys, 'frozen', False):
            _app_dir = _os.path.dirname(_sys.executable)
        else:
            _app_dir = _os.path.dirname(_os.path.abspath(__file__))
        self._heartbeat_path = _os.path.join(_app_dir, ".instance.heartbeat")

        # Telegram 通知器
        self._telegram_notifier = TelegramNotifier(
            enabled=config_mgr.get("telegram_enabled", False),
            bot_token=config_mgr.get("telegram_bot_token", ""),
            chat_id=config_mgr.get("telegram_chat_id", ""),
            proxy=config_mgr.get("telegram_proxy", ""),
        )

        # Bark 推送通知器
        self._bark_notifier = BarkNotifier(
            enabled=config_mgr.get("bark_enabled", True),
        )

        # 回调函数（供 GUI 使用）
        self.on_log = None           # func(msg)
        self.on_status = None        # func(status_text)
        self.on_progress = None      # func(current, total, batch_info)
        self.on_abnormal = None      # func(wechat_id, reason, telegram_sent)
        self.on_rate_limit = None    # func(wechat_id, reason, telegram_sent) — 频繁限制单独回调
        self.on_countdown = None     # func(remaining_seconds, label)
        self.on_ip_changed = None    # func(old_ip, new_ip, node_name, delay, success)

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
        """引擎自身日志：同时写 logger 和 GUI"""
        if level == "info":
            logger.info(msg)
        elif level == "warn":
            logger.warning(msg)
        elif level == "error":
            logger.error(msg)
        if self.on_log:
            self.on_log(msg)

    def _forward_to_gui(self, msg):
        """仅转发到 GUI（供 WeChatController 回调，避免重复写 logger）"""
        if self.on_log:
            self.on_log(msg)

    def _update_heartbeat(self):
        """写入心跳文件，供单实例锁检测旧实例是否卡死"""
        try:
            with open(self._heartbeat_path, 'w') as f:
                f.write(str(int(time.time())))
        except OSError:
            pass

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
            # IP 切换配置
            "ip_switch_enabled": self.config.get("ip_switch_enabled", False),
            "ip_switch_method": self.config.get("ip_switch_method", "clash"),
            "ip_switch_clash_url": self.config.get("ip_switch_clash_url", "http://127.0.0.1:9097"),
            "ip_switch_clash_group": self.config.get("ip_switch_clash_group", "Proxy"),
            "ip_switch_clash_secret": self.config.get("ip_switch_clash_secret", ""),
            "ip_switch_command": self.config.get("ip_switch_command", ""),
            "ip_switch_batch_count": self.config.get("ip_switch_batch_count", 3),
            "ip_switch_timeout": self.config.get("ip_switch_timeout", 30),
            "ip_switch_verify_url": self.config.get("ip_switch_verify_url", "https://api.ipify.org"),
            "ip_switch_advance_seconds": self.config.get("ip_switch_advance_seconds", 300),
            # Telegram 通知配置
            "telegram_round_notification_enabled": self.config.get("telegram_round_notification_enabled", True),
        }

    def _create_ip_switcher(self, cfg):
        """根据配置快照创建IP切换器实例，未启用返回None"""
        if not cfg.get("ip_switch_enabled"):
            return None
        try:
            from ip_switcher import IPSwitcher
            return IPSwitcher(
                method=cfg.get("ip_switch_method", "clash"),
                clash_url=cfg.get("ip_switch_clash_url", "http://127.0.0.1:9097"),
                proxy_group=cfg.get("ip_switch_clash_group", "Proxy"),
                secret=cfg.get("ip_switch_clash_secret", ""),
                command=cfg.get("ip_switch_command", ""),
                verify_url=cfg.get("ip_switch_verify_url", "https://api.ipify.org"),
                timeout=cfg.get("ip_switch_timeout", 30),
            )
        except Exception as e:
            self._emit_log(f"创建IP切换器失败: {e}", "error")
            return None

    def stop(self):
        """请求停止检查"""
        self._stop_event.set()
        self._emit_log("用户请求停止检查", "warn")

    def pause(self):
        """暂停检查（冻结倒计时）"""
        self._pause_event.set()
        self._emit_log(f"===== 用户暂停 at {time.strftime('%H:%M:%S')} =====")
        self._emit_status("已暂停")

    def resume(self, ids=None, cfg=None):
        """
        继续检查，可选传入新的ID列表和配置快照。

        Args:
            ids: 微信号列表，None 则不更新
            cfg: 配置快照字典，None 则不更新
        """
        self._new_ids = ids
        self._new_cfg = cfg
        self._pause_event.clear()
        if ids is not None:
            self.total_accounts = len(ids)
            self._emit_log(f"继续检查 at {time.strftime('%H:%M:%S')}，微信号列表已刷新（{len(ids)} 个）")
        if cfg is not None:
            self._emit_log(f"继续检查 at {time.strftime('%H:%M:%S')}，配置已刷新")
        self._emit_status("检查中...")

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
        self._telegram_notifier.bot_token = self.config.get("telegram_bot_token", "")
        self._telegram_notifier.chat_id = self.config.get("telegram_chat_id", "")
        self._telegram_notifier.proxy = self.config.get("telegram_proxy", "")
        self._bark_notifier.enabled = self.config.get("bark_enabled", True)

        # 创建 IP 切换器（如果启用）
        ip_switcher = self._create_ip_switcher(config_snapshot)

        # 在子线程中运行检查
        try:
            thread = threading.Thread(
                target=self._run_check_loop,
                args=(ids, config_snapshot, ip_switcher),
                daemon=True,
                name="CheckerThread",
            )
            thread.start()
        except Exception as e:
            self._running = False
            self._emit_log(f"启动检查线程失败: {e}", "error")
            return

        self._emit_log(f"检查启动，共 {len(ids)} 个微信号")
        self._emit_log(
            f"配置: 每批{cfg['batch_size']}个 | "
            f"账号间隔{cfg['account_interval_min']}-{cfg['account_interval_max']}秒 | "
            f"批次间隔{cfg['batch_interval_min']}-{cfg['batch_interval_max']}分 | "
            f"最大{cfg['max_rounds']}轮 | "
            f"IP切换:{'开' if cfg.get('ip_switch_enabled') else '关'} | "
            f"Telegram:{'开' if self._telegram_notifier.enabled else '关'}"
        )

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
        self._telegram_notifier.bot_token = self.config.get("telegram_bot_token", "")
        self._telegram_notifier.chat_id = self.config.get("telegram_chat_id", "")
        self._telegram_notifier.proxy = self.config.get("telegram_proxy", "")

        # 创建 IP 切换器（如果启用）
        ip_switcher = self._create_ip_switcher(config_snapshot)

        try:
            thread = threading.Thread(
                target=self._run_check_loop,
                args=(ids_list, config_snapshot, ip_switcher),
                daemon=True,
                name="CheckerThread",
            )
            thread.start()
        except Exception as e:
            self._running = False
            self._emit_log(f"启动检查线程失败: {e}", "error")
            return

        self._emit_log(f"检查启动，共 {len(ids_list)} 个微信号")
        self._emit_log(
            f"配置: 每批{config_snapshot['batch_size']}个 | "
            f"账号间隔{config_snapshot['account_interval_min']}-{config_snapshot['account_interval_max']}秒 | "
            f"批次间隔{config_snapshot['batch_interval_min']}-{config_snapshot['batch_interval_max']}分 | "
            f"最大{config_snapshot['max_rounds']}轮 | "
            f"IP切换:{'开' if config_snapshot.get('ip_switch_enabled') else '关'} | "
            f"Telegram:{'开' if self._telegram_notifier.enabled else '关'}"
        )

    def _run_check_loop(self, all_ids, cfg, ip_switcher=None):
        """
        主检查循环（在子线程中运行）
        支持多轮循环，每批最多 batch_size 个

        Args:
            all_ids: 微信号列表
            cfg: 配置快照字典，避免与主线程并发读写
            ip_switcher: IP切换器实例，None 表示不启用
        """
        batch_size = cfg["batch_size"]
        bi_min = cfg["batch_interval_min"]
        bi_max = cfg["batch_interval_max"]
        ai_min = cfg["account_interval_min"]
        ai_max = cfg["account_interval_max"]
        max_rounds = cfg["max_rounds"]

        # IP 切换参数
        ip_switch_batch_count = cfg.get("ip_switch_batch_count", 3)
        ip_switch_advance = cfg.get("ip_switch_advance_seconds", 300)

        # 初始化 COM（uiautomation 依赖，子线程必须手动初始化）
        _com_ctypes = None
        try:
            import ctypes
            _com_ctypes = ctypes
            hr = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
            if hr < 0:
                self._emit_log(f"COM 初始化失败: 0x{hr & 0xFFFFFFFF:08X}", "error")
        except Exception:
            pass

        round_num = 0
        batch_counter = 0  # 累计批次数，跨轮次递增

        try:
            while not self._stop_event.is_set() and round_num < max_rounds:
                round_num += 1
                round_start_idx = len(self.checked_accounts)  # 本轮开始前的累计数，用于轮次统计
                self._update_heartbeat()
                self.wechat.wechat_window = None  # 每轮强制刷新 UIA 缓存，避免脏控件树

                # 恢复时刷新ID列表和配置
                if self._new_ids is not None:
                    all_ids = self._new_ids
                    self.total_accounts = len(all_ids)
                    self._new_ids = None
                if self._new_cfg is not None:
                    cfg = self._new_cfg
                    batch_size = cfg["batch_size"]
                    bi_min = cfg["batch_interval_min"]
                    bi_max = cfg["batch_interval_max"]
                    ai_min = cfg["account_interval_min"]
                    ai_max = cfg["account_interval_max"]
                    max_rounds = cfg["max_rounds"]
                    ip_switch_batch_count = cfg.get("ip_switch_batch_count", 3)
                    ip_switch_advance = cfg.get("ip_switch_advance_seconds", 300)
                    # 重新创建IP切换器
                    ip_switcher = self._create_ip_switcher(cfg)
                    self._new_cfg = None

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
                        t_start = time.time()
                        status, detail = self.wechat.check_single_account(wechat_id)
                        t_elapsed = time.time() - t_start

                        # 用户暂停：不记录结果，等待继续后重查当前号
                        if status == "paused":
                            self._emit_log(f"===== 已暂停 at {time.strftime('%H:%M:%S')}，当前号 {wechat_id} 将重新检查 =====")
                            self._emit_status("已暂停")
                            # 等待继续或停止
                            while self._pause_event.is_set() and not self._stop_event.is_set():
                                self._pause_event.wait(timeout=0.5)
                            if self._stop_event.is_set():
                                break
                            self._emit_log(f"===== 继续检查 at {time.strftime('%H:%M:%S')}，重查 {wechat_id} =====")
                            self._emit_status("检查中...")
                            # 暂停期间微信窗口可能失焦，重新激活
                            self.wechat.activate_window()
                            self.wechat._sleep(0.5)
                            # 重查同一个号，重新计时
                            t_start = time.time()
                            status, detail = self.wechat.check_single_account(wechat_id)
                            t_elapsed = time.time() - t_start

                        self.checked_accounts.append({
                            "id": wechat_id,
                            "status": status,
                            "detail": detail,
                        })

                        # 停止信号检查：OCR 等阻塞操作期间用户可能点了停止
                        if self._stop_event.is_set():
                            break

                        if status == "rate_limit":
                            self._emit_log(
                                f"[频繁] {wechat_id}: {detail} (耗时{t_elapsed:.1f}秒) — 搜索被限制，建议暂停或换IP", "warn"
                            )
                            # Bark 推送通知
                            self._bark_notifier.send_rate_limit(wechat_id, detail)
                            if self.on_rate_limit:
                                self.on_rate_limit(wechat_id, detail, None)
                        elif status == "abnormal":
                            self._emit_log(
                                f"[异常] {wechat_id}: {detail} (耗时{t_elapsed:.1f}秒)", "warn"
                            )
                            # Bark 推送通知
                            self._bark_notifier.send_abnormal(wechat_id, detail)
                            if self.on_abnormal:
                                self.on_abnormal(wechat_id, detail, None)
                        elif status == "success":
                            self._emit_log(
                                f"[正常] {wechat_id}: {detail} (耗时{t_elapsed:.1f}秒)", "info"
                            )
                        else:
                            self._emit_log(
                                f"[错误] {wechat_id}: {detail} (耗时{t_elapsed:.1f}秒)", "error"
                            )

                        # 异常后延时 5 秒，给人反应时间（关闭声音等）
                        if status in ("abnormal", "rate_limit") and not self._stop_event.is_set():
                            self._emit_log("异常账号，等待 5 秒后继续...")
                            self._wait_with_stop(5.0, "abnormal_pause")

                        # 账号间等待（随机间隔）。异常/操作出错时跳过
                        if acc_idx < len(batch) - 1 and not self._stop_event.is_set():
                            if status == "error":
                                self._emit_log("操作出错，跳过等待直接检查下一个...")
                            elif status in ("abnormal", "rate_limit"):
                                pass  # 已在上面延时5秒，跳过随机等待
                            else:
                                wait_time = random.uniform(ai_min, ai_max)
                                self._emit_log(f"等待 {wait_time:.1f} 秒后检查下一个...")
                                self._wait_with_stop(wait_time, "account")

                    total_checked += len(batch)
                    batch_counter += 1
                    self._update_heartbeat()

                    # 批次间等待（含IP切换）—— 仅非最后一批
                    if batch_idx < len(batches) - 1 and not self._stop_event.is_set():
                        self._batch_wait_with_ip_switch(
                            batch_counter, ip_switcher, ip_switch_batch_count,
                            ip_switch_advance, bi_min, bi_max, round_num
                        )

                # ========== 一轮所有批次完成 ==========
                # 检查是否需要在此轮最后一批后切换IP（修复：最后一批之前被跳过）
                if not self._stop_event.is_set() and round_num < max_rounds:
                    need_ip_switch = (
                        ip_switcher is not None
                        and batch_counter % ip_switch_batch_count == 0
                    )
                    if need_ip_switch:
                        self._emit_log("本批(本轮最后一批)完成后触发IP切换...")
                        self._do_ip_switch(ip_switcher)

                # 一轮完成
                if not self._stop_event.is_set():
                    self._emit_log(
                        f"====== 第 {round_num} 轮完成，共检查 "
                        f"{len(self.checked_accounts)} 个号 ======"
                    )

                    # 统计本轮各状态数量，发送轮次汇总通知
                    round_checked = len(self.checked_accounts) - round_start_idx
                    if round_checked > 0 and cfg.get("telegram_round_notification_enabled", True):
                        round_normal = 0
                        round_abnormal = 0
                        round_rate_limit = 0
                        round_error = 0
                        for entry in self.checked_accounts[round_start_idx:]:
                            st = entry.get("status", "error")
                            if st == "success":
                                round_normal += 1
                            elif st == "abnormal":
                                round_abnormal += 1
                            elif st == "rate_limit":
                                round_rate_limit += 1
                            else:
                                round_error += 1
                        self._telegram_notifier.send_round_summary(
                            round_checked=round_checked,
                            normal=round_normal,
                            abnormal=round_abnormal,
                            rate_limit=round_rate_limit,
                            error=round_error,
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
            # 删除心跳文件
            try:
                os.remove(self._heartbeat_path)
            except OSError:
                pass
            # 释放 COM
            if _com_ctypes is not None:
                try:
                    _com_ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
            self._running = False
            self._emit_status("已停止" if self._stop_event.is_set() else "已完成")
            self._emit_log("检查已停止")

    def _do_ip_switch(self, ip_switcher):
        """执行一次IP切换，记录日志并回调GUI。返回切换耗时(秒)"""
        self._emit_log("开始实时测速所有节点...")
        self._emit_status("正在测速节点并切换IP...")
        t_start = time.time()
        ok, msg, old_ip, new_ip, node_name, delay = ip_switcher.switch_ip(
            self._stop_event
        )
        t_elapsed = time.time() - t_start

        if ok:
            self._emit_log(
                f"IP切换成功: {old_ip} → {new_ip} "
                f"(节点: {node_name}, {delay}ms, 耗时{t_elapsed:.0f}秒)"
            )
            if self.on_ip_changed:
                self.on_ip_changed(old_ip, new_ip, node_name, delay, True)
        else:
            self._emit_log(f"IP切换失败: {msg}", "warn")
            if self.on_ip_changed:
                self.on_ip_changed(old_ip if old_ip else "", None, None, 0, False)

        return t_elapsed

    def _batch_wait_with_ip_switch(self, batch_counter, ip_switcher,
                                     ip_switch_batch_count, ip_switch_advance,
                                     bi_min, bi_max, round_num):
        """批次间等待，如满足条件则触发IP切换（三阶段：预等→测速切换→剩余等待）"""
        wait_min = random.uniform(bi_min, bi_max)
        wait_sec = wait_min * 60

        need_ip_switch = (
            ip_switcher is not None
            and batch_counter % ip_switch_batch_count == 0
        )

        if need_ip_switch:
            pre_wait = max(0, wait_sec - ip_switch_advance)
            if pre_wait > 0:
                self._emit_log(
                    f"本批完成，等待 {pre_wait/60:.1f} 分钟后开始测速切换IP..."
                )
                self._wait_with_stop(pre_wait, f"batch_r{round_num}")

            if self._stop_event.is_set():
                return

            t_elapsed = self._do_ip_switch(ip_switcher)

            remaining = max(0, wait_sec - pre_wait - t_elapsed)
            if remaining > 0:
                self._emit_log(
                    f"IP切换完成，再等 {remaining/60:.1f} 分钟后继续..."
                )
                self._wait_with_stop(remaining, f"ip_rest_r{round_num}")
        else:
            self._emit_log(
                f"本批完成，等待 {wait_min:.1f} 分钟后检查下一批..."
            )
            self._emit_status(
                f"等待中 - 第{round_num}轮 下一批 "
                f"({wait_min:.0f}分钟后)"
            )
            self._wait_with_stop(wait_sec, f"batch_r{round_num}")

    def _wait_with_stop(self, seconds, countdown_label=""):
        """等待指定秒数，可被停止/暂停打断。暂停时倒计时冻结，继续后从断点恢复。"""
        interval = 0.1
        elapsed = 0.0
        last_reported = -1
        while elapsed < seconds and not self._stop_event.is_set():
            # 暂停中：冻结倒计时
            if self._pause_event.is_set():
                remaining = max(0, seconds - elapsed)
                self._emit_status(f"已暂停（剩余 {int(remaining)}秒）")
                time.sleep(0.2)  # Event已set，wait()不阻塞，用sleep防空转
                continue

            time.sleep(interval)
            elapsed += interval
            remaining = max(0, seconds - elapsed)
            if self.on_countdown and countdown_label and int(remaining) != last_reported:
                self.on_countdown(remaining, countdown_label)
                last_reported = int(remaining)
