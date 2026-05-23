"""
IP 切换模块的单元测试（纯逻辑，无需网络）
"""
import os
import sys
import re
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIPRegex(unittest.TestCase):
    """IP 地址正则验证"""

    def setUp(self):
        # 与 ip_switcher.py 中相同的正则
        self.ip_pattern = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')

    def test_valid_ips(self):
        valid = ["1.1.1.1", "255.255.255.255", "0.0.0.0", "192.168.1.1",
                 "10.0.0.1", "172.16.0.1", "127.0.0.1"]
        for ip in valid:
            with self.subTest(ip=ip):
                self.assertTrue(self.ip_pattern.match(ip), f"应该匹配: {ip}")

    def test_invalid_ips(self):
        # 注意：正则只验证格式（数字+点），不验证每段是否 ≤255
        invalid = ["1.2.3.4.5", "abc.def.ghi.jkl",
                   "", "192.168.1", "hello world",
                   "192.168.1.", ".192.168.1.1", "192..168.1.1"]
        for ip in invalid:
            with self.subTest(ip=ip):
                self.assertFalse(self.ip_pattern.match(ip), f"不应匹配: {ip}")


class TestNodeFiltering(unittest.TestCase):
    """Clash 节点筛选逻辑测试"""

    def _filter_candidates(self, all_nodes, current_node):
        """复制 ip_switcher.py 中的筛选逻辑"""
        return [
            n for n in all_nodes
            if n != current_node
            and n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")
            and not n.startswith("GLOBAL")
        ]

    def test_excludes_current_node(self):
        candidates = self._filter_candidates(["NodeA", "NodeB", "NodeC"], "NodeA")
        self.assertNotIn("NodeA", candidates)
        self.assertEqual(set(candidates), {"NodeB", "NodeC"})

    def test_excludes_special_types(self):
        nodes = ["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE",
                 "GLOBAL-TCP", "NodeA", "NodeB"]
        candidates = self._filter_candidates(nodes, "NodeB")
        for excluded in ["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL-TCP"]:
            self.assertNotIn(excluded, candidates, f"应排除: {excluded}")
        self.assertIn("NodeA", candidates)

    def test_empty_when_only_current_and_special(self):
        candidates = self._filter_candidates(["DIRECT", "REJECT", "NodeA"], "NodeA")
        self.assertEqual(candidates, [])

    def test_handles_empty_list(self):
        candidates = self._filter_candidates([], "AnyNode")
        self.assertEqual(candidates, [])


class TestResultsSorting(unittest.TestCase):
    """测速结果排序测试"""

    def test_sort_by_delay_ascending(self):
        results = [("NodeC", 300), ("NodeA", 50), ("NodeB", 100)]
        results.sort(key=lambda x: x[1])
        self.assertEqual(results, [("NodeA", 50), ("NodeB", 100), ("NodeC", 300)])

    def test_filter_out_failed(self):
        """测速失败的节点（delay=-1）应被过滤"""
        results = [("NodeA", 50), ("NodeB", -1), ("NodeC", 100)]
        valid = [(n, d) for n, d in results if d > 0]
        self.assertEqual(valid, [("NodeA", 50), ("NodeC", 100)])


if __name__ == "__main__":
    unittest.main()
