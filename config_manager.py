"""
配置管理模块
读写 config.json，提供默认值、验证、保存功能
"""
import json
import os
from logger_setup import logger

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    "ids_file": "wechat_ids.txt",
    "batch_size": 9,
    "batch_interval_min": 30,
    "batch_interval_max": 50,
    "account_interval_min": 3,
    "account_interval_max": 5,
    "max_rounds": 100,
}


class ConfigManager:
    """配置管理器，负责读写 config.json"""

    def __init__(self):
        self.config = {}
        self.load()

    def load(self):
        """加载配置，文件不存在则创建默认配置"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                # 补全缺失的默认字段
                for key, val in DEFAULT_CONFIG.items():
                    if key not in self.config:
                        self.config[key] = val
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"读取配置文件失败: {e}，使用默认配置")
                self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = DEFAULT_CONFIG.copy()
            self.save()

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
