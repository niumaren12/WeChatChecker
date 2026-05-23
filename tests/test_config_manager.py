"""
配置文件解析和验证的单元测试
"""
import os
import sys
import json
import tempfile
import unittest

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_manager import ConfigManager, DEFAULT_CONFIG


class TestLoadIDs(unittest.TestCase):
    """微信号文件加载测试"""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )

    def tearDown(self):
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def _write(self, content):
        self.tmpfile.write(content)
        self.tmpfile.flush()

    def test_normal_ids(self):
        """正常微信号列表"""
        self._write("wxid_123\nwxid_456\nwxid_789\n")
        ids, err = ConfigManager.load_ids(self.tmpfile.name)
        self.assertEqual(err, "")
        self.assertEqual(ids, ["wxid_123", "wxid_456", "wxid_789"])

    def test_skip_empty_and_comment_lines(self):
        """跳过空行和 # 注释行"""
        self._write("wxid_aaa\n\n# 这是注释\nwxid_bbb\n  \n# 另一条注释\n")
        ids, err = ConfigManager.load_ids(self.tmpfile.name)
        self.assertEqual(err, "")
        self.assertEqual(ids, ["wxid_aaa", "wxid_bbb"])

    def test_deduplication_preserves_order(self):
        """去重保持首次出现顺序"""
        self._write("wxid_c\nwxid_a\nwxid_b\nwxid_a\nwxid_c\n")
        ids, err = ConfigManager.load_ids(self.tmpfile.name)
        self.assertEqual(ids, ["wxid_c", "wxid_a", "wxid_b"])

    def test_empty_file(self):
        """空文件返回空列表和提示"""
        self._write("")
        ids, err = ConfigManager.load_ids(self.tmpfile.name)
        self.assertEqual(ids, [])
        self.assertIn("没有有效的微信号", err)

    def test_file_not_found(self):
        """不存在的文件返回错误"""
        ids, err = ConfigManager.load_ids("/nonexistent/path.txt")
        self.assertEqual(ids, [])
        self.assertIn("不存在", err)

    def test_strips_whitespace(self):
        """自动去除首尾空白"""
        self._write("  wxid_space  \n\twxid_tab\t\n")
        ids, err = ConfigManager.load_ids(self.tmpfile.name)
        self.assertEqual(ids, ["wxid_space", "wxid_tab"])


class TestConfigDefaults(unittest.TestCase):
    """默认配置完整性测试"""

    def test_all_required_keys_present(self):
        """验证所有必需配置项都有默认值"""
        required_keys = [
            "wechat_path", "ids_file", "batch_size",
            "batch_interval_min", "batch_interval_max",
            "account_interval_min", "account_interval_max",
            "max_rounds", "sound_enabled",
            "telegram_enabled", "telegram_bot_token",
            "telegram_chat_id", "telegram_proxy",
            "ip_switch_enabled", "ip_switch_method",
            "ip_switch_clash_url", "ip_switch_clash_group",
            "ip_switch_command", "ip_switch_batch_count",
            "ip_switch_timeout", "ip_switch_verify_url",
            "ip_switch_advance_seconds",
        ]
        for key in required_keys:
            with self.subTest(key=key):
                self.assertIn(key, DEFAULT_CONFIG, f"缺少默认配置项: {key}")


class TestConfigValidation(unittest.TestCase):
    """配置验证逻辑测试"""

    def setUp(self):
        self.cm = ConfigManager()

    def test_valid_default_config(self):
        """默认配置应该通过验证"""
        self.cm.config = DEFAULT_CONFIG.copy()
        valid, err = self.cm.validate()
        self.assertTrue(valid, f"默认配置验证失败: {err}")

    def test_empty_wechat_path(self):
        """空微信路径不通过验证"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["wechat_path"] = ""
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("微信路径", err)

    def test_invalid_batch_size(self):
        """无效的批次大小"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["batch_size"] = 0
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("每批数量", err)

    def test_batch_interval_max_less_than_min(self):
        """批次间隔最大值小于最小值"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["batch_interval_min"] = 50
        self.cm.config["batch_interval_max"] = 10
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("批次间隔最大值", err)

    def test_ip_switch_clash_missing_url(self):
        """Clash 方式但未填 API 地址"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["ip_switch_enabled"] = True
        self.cm.config["ip_switch_method"] = "clash"
        self.cm.config["ip_switch_clash_url"] = ""
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("Clash API地址", err)

    def test_ip_switch_command_empty(self):
        """自定义命令方式但命令为空"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["ip_switch_enabled"] = True
        self.cm.config["ip_switch_method"] = "command"
        self.cm.config["ip_switch_command"] = ""
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("IP切换命令", err)

    def test_ip_switch_invalid_verify_url(self):
        """验证地址不合法"""
        self.cm.config = DEFAULT_CONFIG.copy()
        self.cm.config["ip_switch_enabled"] = True
        self.cm.config["ip_switch_verify_url"] = "ftp://invalid"
        valid, err = self.cm.validate()
        self.assertFalse(valid)
        self.assertIn("验证地址", err)


if __name__ == "__main__":
    unittest.main()
