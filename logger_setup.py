"""
日志模块
自动滚动保留 7 天的日志文件
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


def setup_logger(name="WeChatChecker"):
    """初始化日志系统，按天滚动，保留 7 天"""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_path = os.path.join(LOG_DIR, "checker.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 日志格式：时间 - 级别 - 消息
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 Handler：按天滚动，保留 7 天
    file_handler = TimedRotatingFileHandler(
        log_path, when="midnight", interval=1, backupCount=7, encoding="utf-8"
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
