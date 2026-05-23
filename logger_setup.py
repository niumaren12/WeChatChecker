"""
日志模块
按天滚动 + 单文件最大5MB，保留 7 天
"""
import os
import sys
import logging
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

    def shouldRollover(self, record):
        """按天滚动 或 文件超过大小上限时触发滚动"""
        if super().shouldRollover(record):
            return True
        # 检查文件大小
        try:
            if os.path.getsize(self.baseFilename) >= self.max_bytes:
                return True
        except OSError:
            pass
        return False


def setup_logger(name="WeChatChecker"):
    """初始化日志系统"""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_path = os.path.join(LOG_DIR, "checker.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

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
