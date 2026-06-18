"""
配置管理模块
读写 config.json，提供默认值、验证、保存功能
"""
import json
import os
import sys
from logger_setup import logger

# 兼容 PyInstaller 打包：exe 运行时用 exe 所在目录，源码运行时用脚本目录
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(_APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    "ids_file": "wechat_ids.txt",
    "batch_size": 9,
    "batch_interval_min": 20,
    "batch_interval_max": 30,
    "account_interval_min": 3,
    "account_interval_max": 5,
    "max_rounds": 100,
    "sound_enabled": True,

    # IP 自动切换配置
    "ip_switch_enabled": False,
    "ip_switch_method": "clash",              # "clash" 或 "command"
    "ip_switch_clash_url": "http://127.0.0.1:9097",
    "ip_switch_clash_group": "Proxy",         # Clash 代理组名
    "ip_switch_clash_secret": "",             # Clash API 密钥（Clash Verge 设置中可查看，默认空=不认证）
    "ip_switch_command": "",                  # 自定义命令（method=command时使用）
    "ip_switch_batch_count": 3,               # 每N批后切换
    "ip_switch_timeout": 30,                  # 切换超时(秒)
    "ip_switch_verify_url": "https://api.ipify.org",
    "ip_switch_advance_seconds": 300,         # 提前多少秒开始测速+切换（默认5分钟）

    # Telegram 通知配置
    "telegram_enabled": True,
    "telegram_bot_token": "",                 # Bot Token，从 @BotFather 获取
    "telegram_chat_id": "",
    "telegram_proxy": "",
    "telegram_round_notification_enabled": True,   # 每轮完成后发送汇总通知

    # Bark 推送通知配置
    "bark_enabled": True,                          # 异常时 Bark 推送（iOS）

    "telegram_message_template": (
        "🚨 异常账号告警\n"
        "\n"
        "微信号: {wechat_id}\n"
        "异常原因: {reason}\n"
        "检查时间: {timestamp}\n"
        "\n"
        "来自: 微信账号检查工具"
    ),
}


class ConfigManager:
    """配置管理器，负责读写 config.json"""

    def __init__(self):
        self.config = {}
        self.load()

    # 从内嵌配置中取值优先的关键字段（首次部署即有值，不应为空）
    _CRITICAL_FIELDS = ["telegram_bot_token", "telegram_chat_id"]

    def load(self):
        """加载配置。已有文件则读取；不存在或无关键字段则从内嵌数据合并。"""
        embedded = self._find_embedded_config()

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                # 补全缺失的默认字段
                for key, val in DEFAULT_CONFIG.items():
                    if key not in self.config:
                        self.config[key] = val
                # 关键字段为空时，从内嵌配置合并
                if embedded:
                    self._merge_embedded(embedded)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"读取配置文件失败: {e}，使用默认配置")
                self.config = DEFAULT_CONFIG.copy()
        else:
            if embedded and os.path.exists(embedded):
                import shutil
                try:
                    shutil.copy(embedded, CONFIG_FILE)
                    logger.info(f"已从内嵌数据创建配置文件: {CONFIG_FILE}")
                    return self.load()
                except IOError as e:
                    logger.error(f"拷贝内嵌配置失败: {e}")
            self.config = DEFAULT_CONFIG.copy()
            self.save()

    def _merge_embedded(self, embedded_path):
        """内嵌配置中的关键字段覆盖当前配置的空值，并写回文件"""
        try:
            with open(embedded_path, "r", encoding="utf-8") as f:
                embedded_cfg = json.load(f)
            changed = False
            for key in self._CRITICAL_FIELDS:
                current = self.config.get(key, "")
                embedded_val = embedded_cfg.get(key, "")
                if (not current or not str(current).strip()) and embedded_val:
                    self.config[key] = embedded_val
                    changed = True
            if changed:
                self.save()
                logger.info("已从内嵌配置补充关键字段（telegram_bot_token/chat_id）")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"读取内嵌配置失败: {e}")

    @staticmethod
    def _find_embedded_config():
        """PyInstaller 打包模式下，查找内嵌的 config.json 路径"""
        if getattr(sys, 'frozen', False):
            meipass = getattr(sys, '_MEIPASS', None)
            if meipass:
                path = os.path.join(meipass, 'config.json')
                if os.path.exists(path):
                    return path
        return None

    def save(self):
        """保存配置到文件"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"写入配置文件失败: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def validate(self):
        """
        验证配置合法性
        返回 (is_valid, error_msg)
        """
        errs = []

        # 微信路径
        wp = self.get("wechat_path", "")
        if not wp or not isinstance(wp, str):
            errs.append("微信路径不能为空")

        # 微信号文件
        ids_file = self.get("ids_file", "")
        if not ids_file:
            errs.append("微信号列表文件不能为空")

        # 批次参数
        batch_size = self.get("batch_size", 9)
        if not isinstance(batch_size, int) or batch_size < 1:
            errs.append("每批数量必须为大于0的整数")

        bi_min = self.get("batch_interval_min", 30)
        bi_max = self.get("batch_interval_max", 50)
        if not isinstance(bi_min, (int, float)) or bi_min < 1:
            errs.append("批次间隔最小值必须为正数")
        if not isinstance(bi_max, (int, float)) or bi_max < bi_min:
            errs.append("批次间隔最大值必须大于等于最小值")

        ai_min = self.get("account_interval_min", 3)
        ai_max = self.get("account_interval_max", 5)
        if not isinstance(ai_min, (int, float)) or ai_min < 0:
            errs.append("账号间隔最小值不能为负数")
        if not isinstance(ai_max, (int, float)) or ai_max < ai_min:
            errs.append("账号间隔最大值必须大于等于最小值")

        # IP 切换配置校验（仅启用时）
        if self.get("ip_switch_enabled", False):
            method = self.get("ip_switch_method", "clash")
            if method == "clash":
                clash_url = self.get("ip_switch_clash_url", "")
                if not clash_url or not isinstance(clash_url, str):
                    errs.append("Clash API地址不能为空")
                clash_group = self.get("ip_switch_clash_group", "")
                if not clash_group or not isinstance(clash_group, str):
                    errs.append("Clash代理组名不能为空")
            elif method == "command":
                cmd = self.get("ip_switch_command", "")
                if not cmd or not isinstance(cmd, str):
                    errs.append("IP切换命令不能为空")
            else:
                errs.append(f"未知的IP切换方式: {method}")

            batch_count = self.get("ip_switch_batch_count", 3)
            if not isinstance(batch_count, int) or batch_count < 1:
                errs.append("IP切换批次间隔必须为大于0的整数")

            timeout = self.get("ip_switch_timeout", 30)
            if not isinstance(timeout, (int, float)) or timeout < 5:
                errs.append("IP切换超时时间必须 >= 5秒")

            verify_url = self.get("ip_switch_verify_url", "")
            if not isinstance(verify_url, str) or not verify_url.startswith(("http://", "https://")):
                errs.append("IP验证地址必须以 http:// 或 https:// 开头")

            advance = self.get("ip_switch_advance_seconds", 300)
            if not isinstance(advance, (int, float)) or advance < 10:
                errs.append("IP切换提前测速时间必须 >= 10秒")

        if errs:
            return False, "；".join(errs)
        return True, ""

    @staticmethod
    def load_ids(filepath):
        """
        读取微信号列表文件
        每行一个微信号，跳过空行和 # 注释行，自动去重（保持顺序）
        返回 (微信号列表, 错误信息)
        """
        ids = []
        seen = set()
        total_lines = 0
        if not os.path.exists(filepath):
            return ids, f"文件不存在: {filepath}"
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        total_lines += 1
                        if line not in seen:
                            seen.add(line)
                            ids.append(line)
            if not ids:
                return ids, "文件中没有有效的微信号"
            dup_count = total_lines - len(ids)
            if dup_count > 0:
                logger.info(f"微信号列表已去重，跳过 {dup_count} 个重复项")
            return ids, ""
        except IOError as e:
            return ids, f"读取文件失败: {e}"
