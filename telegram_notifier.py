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

# 频繁限制消息模板
RATE_LIMIT_TEMPLATE = (
    "⚠️ 搜索频繁限制\n"
    "\n"
    "微信号: {wechat_id}\n"
    "限制提示: {reason}\n"
    "检查时间: {timestamp}\n"
    "\n"
    "建议: 暂停检查或切换 IP\n"
    "来自: 微信账号检查工具"
)


# 轮次汇总消息模板
ROUND_SUMMARY_TEMPLATE = "微信检测报告：本轮检查：{round_checked}个，所有微信号正常。"


class TelegramNotifier:
    """Telegram Bot 通知发送器，线程安全"""

    def __init__(self, enabled: bool = False, bot_token: str = "", chat_id: str = "", proxy: str = ""):
        self._enabled = enabled
        self._bot_token = bot_token
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

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @bot_token.setter
    def bot_token(self, value: str):
        self._bot_token = value

    @property
    def chat_id(self) -> str:
        return self._chat_id

    @chat_id.setter
    def chat_id(self, value: str):
        self._chat_id = value

    @property
    def proxy(self) -> str:
        return self._proxy

    @proxy.setter
    def proxy(self, value: str):
        self._proxy = value.strip() if value else ""

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

        timestamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        message = DEFAULT_TEMPLATE.format(
            wechat_id=wechat_id,
            reason=reason,
            timestamp=timestamp,
        )

        # 单锁保护整个"限流检查→发送→更新时间戳"流程，防止竞态并发
        with self._lock:
            elapsed = _time.time() - self._last_send_time
            if elapsed < self._min_interval:
                _time.sleep(self._min_interval - elapsed)

            ok, err = self._send_via_http(message)
            self._last_send_time = _time.time()

        if ok:
            logger.info(f"Telegram 通知已发送: {wechat_id}")
        else:
            logger.error(f"Telegram 通知发送失败 ({wechat_id}): {err}")

        return ok

    def send_rate_limit_notification(self, wechat_id: str, reason: str) -> bool | None:
        """
        发送频繁限制通知。线程安全，可在检查子线程中调用。

        Returns:
            True  发送成功
            False 发送失败
            None  Telegram 功能未启用
        """
        if not self._enabled:
            return None

        timestamp = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        message = RATE_LIMIT_TEMPLATE.format(
            wechat_id=wechat_id,
            reason=reason,
            timestamp=timestamp,
        )

        # 单锁保护整个"限流检查→发送→更新时间戳"流程，防止竞态并发
        with self._lock:
            elapsed = _time.time() - self._last_send_time
            if elapsed < self._min_interval:
                _time.sleep(self._min_interval - elapsed)

            ok, err = self._send_via_http(message)
            self._last_send_time = _time.time()

        if ok:
            logger.info(f"Telegram 频繁限制通知已发送: {wechat_id}")
        else:
            logger.error(f"Telegram 频繁限制通知发送失败 ({wechat_id}): {err}")

        return ok

    def send_round_summary(self, round_checked: int, normal: int = 0,
                           abnormal: int = 0, rate_limit: int = 0, error: int = 0) -> bool | None:
        """
        发送轮次汇总通知。线程安全，可在检查子线程中调用。

        Returns:
            True  发送成功
            False 发送失败
            None  Telegram 功能未启用
        """
        if not self._enabled:
            return None

        message = f"微信检测报告：本轮检查 {round_checked} 个"
        if abnormal > 0 or rate_limit > 0 or error > 0:
            parts = []
            if normal > 0:
                parts.append(f"正常 {normal} 个")
            if abnormal > 0:
                parts.append(f"异常 {abnormal} 个")
            if rate_limit > 0:
                parts.append(f"频繁 {rate_limit} 个")
            if error > 0:
                parts.append(f"出错 {error} 个")
            message += "，" + "，".join(parts) + "。"
        else:
            message += "，所有微信号正常。"

        with self._lock:
            elapsed = _time.time() - self._last_send_time
            if elapsed < self._min_interval:
                _time.sleep(self._min_interval - elapsed)

            ok, err = self._send_via_http(message)
            self._last_send_time = _time.time()

        if ok:
            detail_parts = [f"N{normal}", f"A{abnormal}", f"R{rate_limit}", f"E{error}"]
            logger.info(f"Telegram 轮次汇总通知已发送 (共{round_checked}个, {', '.join(detail_parts)})")
        else:
            logger.error(f"Telegram 轮次汇总通知发送失败: {err}")

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
        if not self._bot_token:
            return False, "Bot Token 未配置（请在 config.json 中设置 telegram_bot_token）"

        if not self._chat_id or not self._chat_id.strip():
            return False, "Chat ID 未配置（请在界面中填写群组/频道 ID）"

        url = _API_URL.format(token=self._bot_token)

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
