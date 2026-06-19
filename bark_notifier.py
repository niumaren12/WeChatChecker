"""
Bark 推送通知模块
通过 Bark API (https://github.com/Finb/Bark) 向 iOS 设备推送告警
纯标准库实现，无外部依赖
"""
import urllib.request
import urllib.parse
from logger_setup import logger

# Bark 设备 Key（从 Bark App 获取）
BARK_KEY = "ChxMrr6chpC5axRmKtsc5P"
BARK_BASE = f"https://api.day.app/{BARK_KEY}"


class BarkNotifier:
    """Bark 推送通知器，线程安全，静默失败"""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def _send(self, title: str, body: str):
        """HTTP GET 推送，静默失败不抛异常"""
        if not self.enabled:
            return
        try:
            url = (
                f"{BARK_BASE}/{urllib.parse.quote(title)}"
                f"/{urllib.parse.quote(body)}"
                "?level=timeSensitive&sound=horn&call=1"
            )
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass  # 静默失败，不中断检查流程

    def send_abnormal(self, wechat_id: str, reason: str):
        """异常账号告警推送"""
        self._send("🚨 异常账号告警", f"微信号: {wechat_id}\n异常原因: {reason}")

    def send_rate_limit(self, wechat_id: str, reason: str):
        """搜索频繁限制告警推送"""
        self._send("⚠️ 搜索频繁限制", f"微信号: {wechat_id}\n限制提示: {reason}")

    def send_test(self) -> tuple[bool, str]:
        """测试推送，返回 (成功与否, 消息)"""
        try:
            url = (
                f"{BARK_BASE}/{urllib.parse.quote('✅ 测试消息')}"
                f"/{urllib.parse.quote('微信账号检查工具通知正常')}"
            )
            urllib.request.urlopen(url, timeout=10)
            return True, "发送成功"
        except Exception as e:
            return False, str(e)
