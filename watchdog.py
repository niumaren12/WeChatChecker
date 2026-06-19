# -*- coding: utf-8 -*-
"""
微信检查工具 - 守护进程 (watchdog)
==================================
24 小时无人值守运行保障。复用主程序已有的心跳文件 (.instance.heartbeat)
做三态判断，自动拉起崩溃/卡死的检查进程。

三态判断（每 --interval 秒巡检一次）：
  1. 进程不存在            → 拉起 python main.py
  2. 进程在 + 心跳新鲜      → 正常，不动
  3. 进程在 + 心跳过期       → COM 死锁/卡死 → 杀进程树 → 拉起

为什么不用 Windows 服务/NSSM：
  主程序是 tkinter + uiautomation 的 GUI 自动化，必须在"交互桌面会话"运行。
  Windows 服务跑在 Session 0 没有桌面，uiautomation 找不到微信窗口直接废。
  因此用「登录时启动 watchdog + watchdog 在会话内拉主程序」的架构。

心跳阈值说明：
  主程序每轮开始 + 每批结束更新心跳，但批次间等待 20-30 分钟期间不更新。
  因此 --stale 必须 > batch_interval_max + 余量，默认 3600 秒(60分钟)，
  否则会在批次等待时误判卡死而杀掉正常运行的进程。

部署：见 setup_keepalive.ps1（建"登录时启动"任务计划程序）
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from logging.handlers import RotatingFileHandler

try:
    import psutil
except ImportError:
    # watchdog 自身依赖 psutil，主程序已装；若缺失给出明确提示
    print("[watchdog] 缺少 psutil，请先 pip install psutil", file=sys.stderr)
    sys.exit(1)


# ========== 路径与常量 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "main.py")
HEARTBEAT_PATH = os.path.join(SCRIPT_DIR, ".instance.heartbeat")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "watchdog.log")

# Windows 进程创建标志
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


# ========== 日志 ==========
def setup_logger():
    """配置日志：文件滚动 + 控制台输出"""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:  # 避免重复添加（--once 多次调用场景）
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


log = setup_logger()


# ========== 配置读取（仅用于 Telegram 通知） ==========
def load_telegram_config():
    """从 config.json 读取 Telegram 配置，供 watchdog 发事件通知"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {
            "enabled": cfg.get("telegram_enabled", False),
            "bot_token": cfg.get("telegram_bot_token", ""),
            "chat_id": cfg.get("telegram_chat_id", ""),
            "proxy": cfg.get("telegram_proxy", ""),
        }
    except Exception as e:
        log.warning(f"读取 config.json 失败，watchdog 通知功能不可用: {e}")
        return {"enabled": False, "bot_token": "", "chat_id": "", "proxy": ""}


def send_telegram(text: str):
    """发送 Telegram 通知，静默失败不中断守护"""
    cfg = load_telegram_config()
    if not cfg["enabled"] or not cfg["bot_token"] or not cfg["chat_id"]:
        return
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    data = json.dumps({"chat_id": cfg["chat_id"], "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    # 代理：仅支持 http/https 代理（urllib 限制），socks 代理需装 PySocks，这里不处理
    proxy = cfg["proxy"].strip()
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        opener.open(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram 通知发送失败（不影响守护）: {e}")


# ========== 主程序进程管理 ==========
def find_main_process():
    """
    查找正在运行的主程序进程。
    匹配规则：命令行参数含 'main.py'（源码模式）或 'WeChatChecker.exe'（打包模式）。
    返回 psutil.Process 或 None。AccessDenied 等异常吞掉，进程不可见视为不存在。
    """
    me_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            if proc.info["pid"] == me_pid:
                continue
            cmdline = proc.info["cmdline"] or []
            cmdline_str = " ".join(cmdline).lower()
            # 源码模式：python ... main.py ；打包模式：WeChatChecker.exe
            if "main.py" in cmdline_str or "wechatchecker.exe" in (proc.info["name"] or "").lower():
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def read_heartbeat_age():
    """
    读取心跳文件，返回心跳距现在的秒数。
    文件不存在或内容损坏 → 返回 None（表示无心跳，调用方需结合进程状态判断）。
    """
    try:
        with open(HEARTBEAT_PATH, "r") as f:
            ts = int(f.read().strip())
        return time.time() - ts
    except (FileNotFoundError, ValueError, OSError):
        return None


def kill_process_tree(proc):
    """杀掉进程及其所有子进程（检查线程是 daemon 子线程，杀树最彻底）"""
    try:
        children = proc.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        proc.kill()
        proc.wait(timeout=10)
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        log.warning(f"杀进程树时异常: {e}")


def get_pythonw():
    """
    获取启动 GUI 主程序用的解释器：优先 pythonw.exe（无控制台黑窗），
    回退到当前解释器。仅 Windows 适用。
    """
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        return exe
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    return exe


def start_main():
    """
    以分离进程方式拉起主程序 python main.py。
    Windows 上用 DETACHED_PROCESS 让主程序与 watchdog 解耦，watchdog 退出不影响主程序；
    CREATE_NO_WINDOW 避免弹出额外控制台窗口（pythonw 本身无窗，双保险）。
    非 Windows（如 Mac 调试）用普通 Popen，忽略 creationflags。
    """
    pythonw = get_pythonw()
    try:
        popen_kwargs = {"cwd": SCRIPT_DIR, "close_fds": True}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW
            )
        subprocess.Popen([pythonw, MAIN_SCRIPT], **popen_kwargs)
        log.info(f"已拉起主程序: {pythonw} {MAIN_SCRIPT}")
        return True
    except Exception as e:
        log.error(f"拉起主程序失败: {e}")
        send_telegram(f"⚠️ watchdog 拉起主程序失败: {e}")
        return False


# ========== 守护主循环 ==========
class Watchdog:
    def __init__(self, args):
        self.interval = args.interval
        self.stale = args.stale
        self.cooldown = args.cooldown
        self.max_restarts_per_hour = args.max_restarts_per_hour
        self.last_start_time = 0.0           # 上次拉起时间，用于冷却
        self.recent_restarts = []            # 最近重启时间戳列表，用于防风暴

    def _record_restart(self):
        """记录一次重启，并清理 1 小时外的记录"""
        now = time.time()
        self.recent_restarts.append(now)
        self.recent_restarts = [t for t in self.recent_restarts if now - t < 3600]

    def _in_cooldown(self):
        """是否在拉起后的冷却期内（冷却期内只判进程在不在，不判心跳）"""
        return (time.time() - self.last_start_time) < self.cooldown

    def _storm_triggered(self):
        """是否触发崩溃风暴保护（1 小时内重启次数超限）"""
        return len(self.recent_restarts) >= self.max_restarts_per_hour

    def inspect_once(self):
        """
        单次巡检，返回本次动作描述。核心三态判断在此。
        """
        proc = find_main_process()
        now = time.time()

        # 状态 1：进程不存在 → 拉起
        if proc is None:
            if self._storm_triggered():
                log.error(f"崩溃风暴保护：1小时内已重启 {len(self.recent_restarts)} 次，停止自动拉起，需人工介入")
                send_telegram(
                    f"🚨 崩溃风暴保护触发：1小时内主程序重启 {len(self.recent_restarts)} 次，"
                    f"watchdog 已停止自动拉起，请人工登录检查！"
                )
                # 风暴期间降低巡检频率，避免刷屏；靠人工介入恢复
                return "storm_protect"
            log.warning("主程序未运行，尝试拉起...")
            if start_main():
                self.last_start_time = now
                self._record_restart()
                send_telegram(f"🔄 主程序未运行，watchdog 已自动拉起（第 {len(self.recent_restarts)} 次/小时）")
            return "start"

        # 进程在。冷却期内只确认存活，不判心跳（给刚启动的程序写心跳的时间）
        if self._in_cooldown():
            return f"running(cooldown, {int(self.cooldown - (now - self.last_start_time))}s)"

        # 状态 2/3：根据心跳判断
        age = read_heartbeat_age()
        if age is None:
            # 进程在但无心跳文件：可能是刚启动还没到写心跳的代码点，宽限一轮
            return "running(no_heartbeat, grace)"

        if age < self.stale:
            return f"running(heartbeat {int(age)}s)"

        # 状态 3：心跳过期 → 判定卡死（COM 死锁等），杀 + 拉起
        log.warning(f"主程序卡死（心跳 {int(age)}s 未更新，阈值 {self.stale}s），杀进程树并重启")
        if self._storm_triggered():
            log.error("卡死频繁，触发风暴保护，不再自动重启")
            send_telegram("🚨 主程序反复卡死，风暴保护触发，需人工介入！")
            return "storm_protect"
        send_telegram(f"💀 主程序卡死（心跳 {int(age)}s 未更新），watchdog 自动重启中")
        kill_process_tree(proc)
        time.sleep(3)  # 等待资源释放，避免端口/文件锁冲突
        if start_main():
            self.last_start_time = time.time()
            self._record_restart()
        return "restart_stuck"

    def run_forever(self):
        log.info(
            f"watchdog 启动 | 巡检 {self.interval}s | 卡死阈值 {self.stale}s | "
            f"冷却 {self.cooldown}s | 风暴上限 {self.max_restarts_per_hour}/h"
        )
        log.info(f"主程序: {MAIN_SCRIPT}")
        log.info(f"心跳: {HEARTBEAT_PATH}")
        send_telegram("✅ watchdog 已启动，开始守护微信检查进程")
        while True:
            try:
                action = self.inspect_once()
                if action != "storm_protect":
                    log.info(f"巡检完成: {action}")
            except Exception as e:
                # 任何巡检异常都不能让 watchdog 自己挂掉
                log.exception(f"巡检异常（已吞掉，继续守护）: {e}")
            time.sleep(self.interval)


def main():
    parser = argparse.ArgumentParser(description="微信检查工具守护进程")
    parser.add_argument("--interval", type=int, default=60, help="巡检间隔秒数（默认 60）")
    parser.add_argument("--stale", type=int, default=3600, help="心跳过期阈值秒，超此判卡死（默认 3600=60分钟，须>批次间隔30分钟）")
    parser.add_argument("--cooldown", type=int, default=60, help="拉起后冷却秒，期间只判存活不判心跳（默认 60）")
    parser.add_argument("--max-restarts-per-hour", type=int, default=10, help="每小时最大重启次数，超此触发风暴保护（默认 10）")
    parser.add_argument("--once", action="store_true", help="只巡检一次并退出（测试用）")
    args = parser.parse_args()

    wd = Watchdog(args)
    if args.once:
        action = wd.inspect_once()
        log.info(f"单次巡检: {action}")
    else:
        wd.run_forever()


if __name__ == "__main__":
    main()
