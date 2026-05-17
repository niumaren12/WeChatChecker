"""
Telegram 通知模块
通过 Telegram Bot API 发送异常账号通知到指定频道/群组
无外部依赖，使用标准库 urllib.request
"""
import json
import urllib.request
import urllib.error
import threading
import time as _time
from logger_setup import logger

# ==================== 配置常量（按需修改） ====================
# Bot Token: 从 @BotFather 获取，格式 "123456:ABC-DEF1234ghikl"
TELEGRAM_BOT_TOKEN = "8627831778:AAFZ04aMuyDCox3Npg4ZRpBRJBotqo6Vw48"

# Telegram Bot API 端点
_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# 默认消息模板
DEFAULT_TEMPLATE = (
    "🚨 异常账号告警\n"
    "\n"
    "微信号: {wechat_id}\n"
    "异常原因: {reason}\n"
    "检查时间: {timestamp}\n"
    "\n"
    "来自: 微信账号检查工具"
)


class TelegramNotifier:
    """Telegram Bot 通知发送器，线程安全"""

    def __init__(self, enabled: bool = False, chat_id: str = "", proxy: str = ""):
        self._enabled = enabled
        self._chat_id = chat_id
        self._proxy = proxy.strip() if proxy else ""
        self._lock = threading.Lock()
        self._last_send_time: float = 0.0
        self._min_interval: float = 1.0  # 两次发送最小间隔（秒）

    # ---- 公共属性 ----

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    # ---- 公共方法 ----

    def send_abnormal_notification(self, wechat_id: str, reason: str) -> bool | None:
        """
        发送异常通知。线程安全，可在检查子线程中调用。

        Returns:
            True  发送成功
            False 发送失败
            None  Telegram 功能未启用
        """
        if not self._enabled:
            return None

        # 限流检查
        with self._lock:
            elapsed = _time.time() - self._last_send_time
            wait = self._min_interval - elapsed if elapsed < self._min_interval else 0

        if wait > 0:
            _time.sleep(wait)

        timestamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        message = DEFAULT_TEMPLATE.format(
            wechat_id=wechat_id,
            reason=reason,
            timestamp=timestamp,
        )

        ok, err = self._send_via_http(message)

        with self._lock:
            self._last_send_time = _time.time()

        if ok:
            logger.info(f"Telegram 通知已发送: {wechat_id}")
        else:
            logger.error(f"Telegram 通知发送失败 ({wechat_id}): {err}")

        return ok

    def send_test_notification(self) -> tuple[bool, str]:
        """
        发送测试消息，验证配置是否有效。

        Returns:
            (True, "发送成功")
            (False, "错误描述")
        """
        timestamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        message = (
            f"✅ 微信账号检查工具 - 测试消息\n"
            f"\n"
            f"Telegram 通知配置正常！\n"
            f"发送时间: {timestamp}"
        )
        return self._send_via_http(message)

    # ---- 内部方法 ----

    def _send_via_http(self, message: str) -> tuple[bool, str]:
        """通过 urllib 发送 HTTP POST 到 Telegram Bot API"""
        if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            return False, "Bot Token 未配置（请修改 telegram_notifier.py 中的 TELEGRAM_BOT_TOKEN）"

        if not self._chat_id or not self._chat_id.strip():
            return False, "Chat ID 未配置（请在界面中填写群组/频道 ID）"

        url = _API_URL.format(token=TELEGRAM_BOT_TOKEN)

        payload = json.dumps({
            "chat_id": self._chat_id.strip(),
            "text": message,
            "disable_web_page_preview": True,
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "WeChatChecker/1.2",
            },
            method="POST",
        )

        try:
            if self._proxy:
                proxy_handler = urllib.request.ProxyHandler({"https": self._proxy})
                opener = urllib.request.build_opener(proxy_handler)
                resp = opener.open(req, timeout=10)
            else:
                resp = urllib.request.urlopen(req, timeout=10)
            with resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                if data.get("ok"):
                    return True, ""
                return False, f"API 返回: {data.get('description', body)}"
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = ""
            return False, f"HTTP {e.code}: {err_body}"
        except Exception as e:
            return False, f"网络错误: {e}"
