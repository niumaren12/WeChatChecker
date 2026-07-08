"""
日志模块
按天滚动 + 单文件最大5MB，保留 7 天
"""
import os
import sys
import logging
import time
from logging.handlers import TimedRotatingFileHandler

# 兼容 PyInstaller 打包
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(_APP_DIR, "logs")
MAX_BYTES = 5 * 1024 * 1024  # 单文件最大 5MB


class TimedSizeRotatingFileHandler(TimedRotatingFileHandler):
    """同时支持按天 + 按大小滚动的日志 handler"""

    def __init__(self, filename, max_bytes=MAX_BYTES, **kwargs):
        super().__init__(filename, **kwargs)
        self.max_bytes = max_bytes
        self._rollover_count = 0  # 同一天内大小滚动的序号

    def shouldRollover(self, record):
        """按天滚动 或 文件超过大小上限时触发滚动"""
        if super().shouldRollover(record):
            self._rollover_count = 0  # 新的一天，重置序号
            return True
        # 检查文件大小
        try:
            if os.path.getsize(self.baseFilename) >= self.max_bytes:
                return True
        except OSError:
            pass
        return False

    def doRollover(self):
        """滚动时生成带时间戳+序号的备份文件名，避免覆盖"""
        if self.stream:
            self.stream.close()
            self.stream = None

        # 检查是否是按天滚动（时间变化）还是按大小滚动
        current_time = time.time()
        current_date = time.strftime("%Y-%m-%d", time.localtime(current_time))
        last_date = time.strftime("%Y-%m-%d", time.localtime(self.lastRolloverTime))

        if current_date != last_date:
            # 天滚动，重置序号
            self._rollover_count = 0
            dfn = self.rotation_filename(self.baseFilename + "." + current_date)
        else:
            # 按大小滚动，增加序号
            self._rollover_count += 1
            ts = int(current_time)
            dfn = self.rotation_filename(f"{self.baseFilename}.{current_date}_{ts}_{self._rollover_count}")

        if os.path.exists(dfn):
            os.remove(dfn)
        os.rename(self.baseFilename, dfn)

        # 清理旧日志（超过 backupCount）
        self._cleanup_old_logs()

        self.stream = self._open()
        self.lastRolloverTime = current_time

    def _cleanup_old_logs(self):
        """清理超过保留天数的日志文件"""
        if self.backupCount <= 0:
            return
        cutoff_time = time.time() - self.backupCount * 86400
        for f in os.listdir(LOG_DIR):
            if f.startswith("checker.log"):
                filepath = os.path.join(LOG_DIR, f)
                try:
                    if os.path.getmtime(filepath) < cutoff_time:
                        os.remove(filepath)
                except OSError:
                    pass


def _make_file_handler(log_path):
    """创建文件 handler，并立即自检能否真正写入。

    根因防护：若 checker.log 因上次进程被强杀处于损坏/只读/占用状态，
    handler 构造或 emit 会失败（构造抛异常，emit 则被 Handler.handleError
    静默吞掉），导致本进程所有日志全丢、进程却继续跑。这里把构造 + 试写
    一起包进 try，任一失败就把坏文件改名隔离，用全新文件重建 handler。
    """
    try:
        handler = TimedSizeRotatingFileHandler(
            log_path, max_bytes=MAX_BYTES,
            when="midnight", interval=1, backupCount=7, encoding="utf-8",
        )
        # 试写 + 强制刷盘，验证文件确实可写（构造成功不代表 emit 能成）
        handler.stream.write("# log self-check\n")
        handler.stream.flush()
        return handler
    except Exception:
        # 坏文件：改名隔离后重建。改名失败则改用带后缀的新路径，绝不静默丢日志
        bad_path = f"{log_path}.bad_{int(time.time())}"
        try:
            if os.path.exists(log_path):
                # 先尝试恢复可写权限再改名（只读文件 os.replace 会失败）
                try:
                    os.chmod(log_path, 0o666)
                except OSError:
                    pass
                os.replace(log_path, bad_path)
        except OSError:
            # 连改名都失败（占用等），退到带后缀的新文件，保证本进程能写日志
            log_path = f"{log_path}.{int(time.time())}"
        # 重建；若仍失败，兜底用纯 StreamHandler(stdout)，至少不丢日志
        try:
            handler = TimedSizeRotatingFileHandler(
                log_path, max_bytes=MAX_BYTES,
                when="midnight", interval=1, backupCount=7, encoding="utf-8",
            )
            return handler
        except Exception:
            return logging.StreamHandler()


def setup_logger(name="WeChatChecker"):
    """初始化日志系统，防重复 handler"""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_path = os.path.join(LOG_DIR, "checker.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 防止重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 Handler：按天 or 5MB 滚动，保留 7 天，立即刷新每条日志
    # 启动自检：坏文件自动隔离重建，避免日志静默丢失
    file_handler = _make_file_handler(log_path)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # 强制立即刷新：每条日志直接写入磁盘，防止程序卡死时日志丢失
    class _FlushFilter(logging.Filter):
        """每条日志立即刷盘"""
        def __init__(self, handler):
            super().__init__()
            self._handler = handler
        def filter(self, record):
            try:
                self._handler.flush()
            except Exception:
                pass
            return True

    file_handler.addFilter(_FlushFilter(file_handler))

    return logger


# 全局日志实例
logger = setup_logger()
