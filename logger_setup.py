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

    # 文件 Handler：按天 or 5MB 滚动，保留 7 天
    file_handler = TimedSizeRotatingFileHandler(
        log_path, max_bytes=MAX_BYTES,
        when="midnight", interval=1, backupCount=7, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# 全局日志实例
logger = setup_logger()
