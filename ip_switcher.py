"""
IP自动切换模块
支持 Clash API 实时测速切换 + 自定义命令两种方式
仅使用 Python 标准库，无外部依赖
"""
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from logger_setup import logger


class IPSwitcher:
    """IP切换器，支持Clash API和自定义命令两种方式"""

    def __init__(self, method="clash", clash_url="http://127.0.0.1:9090",
                 proxy_group="Proxy", command="",
                 verify_url="https://api.ipify.org", timeout=30):
        self.method = method          # "clash" 或 "command"
        self.clash_url = clash_url.rstrip('/')
        self.proxy_group = proxy_group
        self.command = command
        self.verify_url = verify_url
        self.timeout = timeout
        self._lock = threading.Lock()  # 防止并发切换

    # ==================== 公网IP查询 ====================

    def get_current_ip(self):
        """
        查询当前公网出口IP
        返回 (ip_str, err_msg)，成功时 err_msg 为 None
        """
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.verify_url)
                req.add_header("User-Agent", "WeChatChecker/1.2")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    text = resp.read().decode("utf-8").strip()
                # 验证返回值像是IP地址
                if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', text):
                    return (text, None)
                else:
                    logger.warning(f"IP验证服务返回非IP格式: {text[:50]}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(3)
                else:
                    return (None, f"网络错误: {e}")
            except Exception as e:
                if attempt < 2:
                    time.sleep(3)
                else:
                    return (None, f"获取IP失败: {e}")
        return (None, "获取IP失败: 多次重试后仍失败")

    # ==================== Clash API 操作 ====================

    def _clash_request(self, path, method="GET", body=None):
        """
        调用Clash API
        返回 (status_code, response_data_dict) 或 (0, error_msg)
        """
        url = f"{self.clash_url}/{path.lstrip('/')}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return (resp.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            return (e.code, {"error": f"HTTP {e.code}"})
        except Exception as e:
            return (0, {"error": str(e)})

    def get_clash_info(self):
        """
        获取Clash当前状态信息
        返回 (current_node, node_list, error_msg)
        """
        status, data = self._clash_request("/proxies")
        if status != 200:
            return (None, [], f"Clash API不可达 (状态码:{status})")

        proxies = data.get("proxies", {})
        group = proxies.get(self.proxy_group)
        if not group:
            return (None, [], f"代理组 '{self.proxy_group}' 不存在")

        current = group.get("now", "")
        all_nodes = group.get("all", [])
        return (current, all_nodes, None)

    def test_node_delay(self, node_name, test_url="https://www.gstatic.com/generate_204", timeout=5000):
        """
        通过Clash API实时测试单个节点延迟
        返回 (node_name, delay_ms)，失败时 delay_ms 为 -1
        """
        # URL编码节点名（中文等特殊字符）
        import urllib.parse
        encoded = urllib.parse.quote(node_name, safe='')
        path = f"/proxies/{encoded}/delay"
        url = f"{self.clash_url}{path}?url={urllib.parse.quote(test_url, safe='')}&timeout={timeout}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout / 1000 + 5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                delay = data.get("delay", -1)
                return (node_name, delay)
        except Exception as e:
            logger.debug(f"节点 {node_name} 测速失败: {e}")
            return (node_name, -1)

    def _test_all_nodes(self, candidates, stop_event=None):
        """
        并发测速所有候选节点
        返回按延迟升序的列表 [(node_name, delay_ms), ...]
        """
        results = []
        # 用线程池并发测试，最多同时测10个
        max_workers = min(10, len(candidates)) if candidates else 1
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(self.test_node_delay, name): name for name in candidates}
        try:
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    # 取消未开始的任务，关闭线程池（不等已运行的完成）
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    executor.shutdown(wait=False)
                    break
                try:
                    name, delay = future.result()
                    if delay > 0:
                        results.append((name, delay))
                except Exception:
                    pass
        finally:
            executor.shutdown(wait=False)

        results.sort(key=lambda x: x[1])  # 按延迟升序
        return results

    # ==================== IP切换入口 ====================

    def switch_ip(self, stop_event=None):
        """
        切换IP主入口
        返回 (ok, msg, old_ip, new_ip, node_name, node_delay)
         - node_name/node_delay 仅在Clash方式下有意义，否则为 None/0
        """
        with self._lock:
            if self.method == "clash":
                return self._switch_via_clash(stop_event)
            else:
                ok, msg, old_ip, new_ip = self._switch_via_command(stop_event)
                return (ok, msg, old_ip, new_ip, None, 0)

    def _switch_via_clash(self, stop_event):
        """
        通过Clash API测速并切换节点
        流程: 记录旧IP → 获取节点列表 → 并发测速 → 选最快 → 切换 → 验证
        """
        # 1. 记录旧IP
        old_ip, ip_err = self.get_current_ip()
        if ip_err:
            logger.warning(f"切换前获取IP失败: {ip_err}")

        # 2. 获取代理组信息
        if stop_event and stop_event.is_set():
            return (False, "已停止", old_ip, None, None, 0)

        current_node, all_nodes, err = self.get_clash_info()
        if err:
            return (False, err, old_ip, None, None, 0)

        # 3. 筛选候选节点：排除当前节点、DIRECT、REJECT、特殊类型
        candidates = [
            n for n in all_nodes
            if n != current_node
            and n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")
            and not n.startswith("GLOBAL")
        ]
        if not candidates:
            return (False, "无可切换节点（代理组只有当前一个可用节点）", old_ip, None, None, 0)

        logger.info(f"候选节点: {len(candidates)} 个，开始实时测速...")

        # 4. 并发测速
        if stop_event and stop_event.is_set():
            return (False, "已停止", old_ip, None, None, 0)

        ranked = self._test_all_nodes(candidates, stop_event)
        if not ranked:
            return (False, "所有节点测速均失败，无法选择", old_ip, None, None, 0)

        logger.info(f"测速完成，最快节点: {ranked[0][0]} ({ranked[0][1]}ms)")

        # 5. 逐个尝试切换（从最快开始），直到IP真的变了
        new_ip = None
        for attempt, (node_name, delay) in enumerate(ranked[:min(len(ranked), 5)]):
            if stop_event and stop_event.is_set():
                return (False, "已停止", old_ip, None, None, 0)

            logger.info(f"切换节点 [{attempt+1}]: {node_name} ({delay}ms)")

            # 切换节点
            status, _ = self._clash_request(
                f"/proxies/{self.proxy_group}",
                method="PUT",
                body={"name": node_name}
            )
            if status not in (200, 204):
                logger.warning(f"切换节点API返回状态码 {status}，重试下一个")
                continue

            # 等待连接建立（可中断）
            for _ in range(4):
                if stop_event and stop_event.is_set():
                    return (False, "已停止", old_ip, None, None, 0)
                time.sleep(0.5)

            # 验证IP是否变化
            new_ip, ip_err = self.get_current_ip()
            if ip_err:
                logger.warning(f"切换后获取IP失败: {ip_err}")
                continue

            if new_ip and old_ip and new_ip != old_ip:
                logger.info(f"IP切换成功: {old_ip} → {new_ip} (节点: {node_name}, {delay}ms)")
                return (True, f"IP已切换: {old_ip} → {new_ip}", old_ip, new_ip, node_name, delay)
            else:
                logger.warning(f"IP未变化 ({node_name})，尝试下一个节点")

        # 所有尝试失败
        return (False, "IP未变化: 已尝试所有节点但出口IP均相同", old_ip, new_ip, None, 0)

    def _switch_via_command(self, stop_event):
        """
        通过自定义命令切换IP
        流程: 记录旧IP → 执行命令 → 等待 → 验证新IP
        """
        if not self.command:
            return (False, "切换命令为空", None, None)

        # 1. 记录旧IP
        old_ip, ip_err = self.get_current_ip()
        if ip_err:
            logger.warning(f"切换前获取IP失败: {ip_err}")

        if stop_event and stop_event.is_set():
            return (False, "已停止", old_ip, None)

        # 2. 执行切换命令
        logger.info(f"执行切换命令: {self.command}")
        try:
            result = subprocess.run(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            if result.returncode != 0:
                err_detail = result.stderr.strip() or result.stdout.strip() or "无输出"
                return (False, f"命令执行失败(退出码{result.returncode}): {err_detail[:100]}", old_ip, None)
        except subprocess.TimeoutExpired:
            return (False, f"命令执行超时({self.timeout}秒)", old_ip, None)
        except Exception as e:
            return (False, f"命令执行异常: {e}", old_ip, None)

        if stop_event and stop_event.is_set():
            return (False, "已停止", old_ip, None)

        # 3. 等待后验证新IP（可中断等待5秒）
        for _ in range(10):
            if stop_event and stop_event.is_set():
                return (False, "已停止", old_ip, None)
            time.sleep(0.5)

        new_ip = None
        for attempt in range(3):
            if stop_event and stop_event.is_set():
                return (False, "已停止", old_ip, None)

            new_ip, ip_err = self.get_current_ip()
            if ip_err:
                if attempt < 2:
                    for _ in range(10):
                        if stop_event and stop_event.is_set():
                            return (False, "已停止", old_ip, None)
                        time.sleep(0.5)
                    continue
                return (False, f"验证IP失败: {ip_err}", old_ip, None)

            if new_ip and old_ip and new_ip != old_ip:
                logger.info(f"IP切换成功: {old_ip} → {new_ip}")
                return (True, f"IP已切换: {old_ip} → {new_ip}", old_ip, new_ip)

            if attempt < 2:
                logger.info(f"IP未变化，等待重试 ({attempt+2}/3)...")
                time.sleep(5)

        return (False, "IP未变化: 命令已执行但出口IP未改变", old_ip, new_ip)
