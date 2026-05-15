"""
微信自动化操作模块
基于 uiautomation 操控微信 PC 客户端
"""
import time
import random
import subprocess
import os

from logger_setup import logger


# ---------- 尝试导入 uiautomation ----------
UIA_AVAILABLE = False
try:
    import uiautomation as auto
    UIA_AVAILABLE = True
except ImportError:
    logger.warning("uiautomation 未安装，将使用模拟方案（仅用于开发环境测试）")


class WeChatController:
    """微信控制器，封装所有自动化操作"""

    # 微信进程名
    WECHAT_PROCESS = "WeChat.exe"
    # 微信主窗口标题可能包含的文字
    WECHAT_WINDOW_TITLE = "微信"

    def __init__(self, wechat_path):
        self.wechat_path = wechat_path
        self.wechat_window = None
        self.main_control = None

    # ==================== 窗口管理 ====================

    def is_wechat_running(self):
        """检查微信是否在运行"""
        if not UIA_AVAILABLE:
            # 降级：通过进程名检查
            try:
                import psutil
                for proc in psutil.process_iter(["name"]):
                    if proc.info["name"] == self.WECHAT_PROCESS:
                        return True
                return False
            except ImportError:
                # 用 tasklist 检查
                try:
                    result = subprocess.run(
                        ["tasklist", "/FI", f"IMAGENAME eq {self.WECHAT_PROCESS}"],
                        capture_output=True, text=True, timeout=5
                    )
                    return self.WECHAT_PROCESS in result.stdout
                except Exception:
                    logger.warning("无法检查微信进程状态")
                    return False
        else:
            try:
                window = auto.WindowControl(
                    searchDepth=1, ClassName="WeChatMainWndForPC"
                )
                return window.Exists(maxSearchSeconds=0)
            except Exception:
                return False

    def prompt_open_wechat(self):
        """
        提示用户手动打开微信
        返回 False 表示取消操作
        """
        logger.warning("微信未运行，请手动打开微信并登录后再点击开始")
        return False

    def activate_window(self):
        """
        将微信窗口置前激活
        返回 True 表示成功
        """
        if not UIA_AVAILABLE:
            logger.error("uiautomation 不可用，无法操作微信窗口")
            return False

        try:
            # 查找微信主窗口
            window = auto.WindowControl(
                searchDepth=1, ClassName="WeChatMainWndForPC"
            )
            if not window.Exists(maxSearchSeconds=2):
                logger.error("找不到微信主窗口")
                return False

            window.SetActive()
            time.sleep(0.5)
            window.SetTopmost(True)
            time.sleep(0.3)
            window.SetTopmost(False)
            self.wechat_window = window
            logger.info("微信窗口已激活")
            return True

        except Exception as e:
            logger.error(f"激活微信窗口失败: {e}")
            return False

    # ==================== 搜索操作 ====================

    def focus_search_box(self):
        """
        按 Ctrl+F 激活搜索框
        返回 True 表示成功
        """
        if not UIA_AVAILABLE:
            logger.error("uiautomation 不可用")
            return False

        try:
            auto.SendKeys("{Ctrl}f")
            time.sleep(0.5)
            logger.debug("已按下 Ctrl+F 激活搜索框")
            return True
        except Exception as e:
            logger.error(f"激活搜索框失败: {e}")
            return False

    def input_wechat_id(self, wechat_id):
        """
        在搜索框中输入微信号
        先清空已有内容再输入
        """
        if not UIA_AVAILABLE:
            return False

        try:
            # 全选 + 删除已有内容
            auto.SendKeys("{Ctrl}a")
            time.sleep(0.2)
            auto.SendKeys("{Delete}")
            time.sleep(0.3)

            # 输入微信号
            auto.SendKeys(wechat_id)
            time.sleep(1.0)  # 等待搜索结果加载
            logger.debug(f"已输入微信号: {wechat_id}")
            return True
        except Exception as e:
            logger.error(f"输入微信号失败: {e}")
            return False

    def click_dropdown_item(self):
        """
        点击下拉框中的'网络查找手机/QQ号'项
        返回 True 表示成功点击
        """
        if not UIA_AVAILABLE:
            return False

        try:
            # 查找下拉列表控件
            # 微信下拉框通常是 ListItemControl 或 ListControl
            # 查找包含"网络查找"文本的项
            found = False

            # 尝试不同方式定位下拉项
            search_texts = ["网络查找", "查找手机", "查找QQ"]

            for text in search_texts:
                try:
                    item = auto.ListItemControl(Name=text)
                    if item.Exists(maxSearchSeconds=0.5):
                        item.Click()
                        found = True
                        logger.info(f"点击下拉项: {text}")
                        break
                except Exception:
                    continue

            # 如果精确文本没找到，尝试模糊匹配
            if not found:
                try:
                    # 遍历所有 ListItem
                    list_control = auto.ListControl()
                    if list_control.Exists(maxSearchSeconds=1):
                        items = list_control.GetChildren()
                        for item in items:
                            name = item.Name
                            if "网络" in name or "查找" in name or "手机" in name or "QQ" in name:
                                item.Click()
                                found = True
                                logger.info(f"点击下拉项(模糊匹配): {name}")
                                break
                except Exception:
                    pass

            if not found:
                # 最后尝试：直接点第一个下拉项
                try:
                    first_item = auto.ListItemControl()
                    if first_item.Exists(maxSearchSeconds=1):
                        first_item.Click()
                        found = True
                        logger.info("点击第一个下拉项(兜底)")
                except Exception:
                    pass

            if found:
                time.sleep(1.5)  # 等待弹窗出现
                return True
            else:
                logger.warning("未找到下拉项")
                return False

        except Exception as e:
            logger.error(f"点击下拉项失败: {e}")
            return False

    # ==================== 弹窗检测 ====================

    def check_popup_status(self):
        """
        检查弹出的"添加朋友"窗口
        检测有无头像控件 + 昵称控件 + "添加到通讯录"按钮

        返回:
            ("normal", nickname) — 正常
            ("abnormal", reason) — 异常
            ("not_found", "")     — 未找到弹窗
        """
        if not UIA_AVAILABLE:
            return ("not_found", "")

        try:
            # 等待弹窗出现（最多等 3 秒）
            time.sleep(0.5)

            # 查找"添加朋友"窗口
            popup = auto.WindowControl(Name="添加朋友")
            if not popup.Exists(maxSearchSeconds=2):
                # 尝试其他可能的窗口标题
                popup = auto.WindowControl(Name="详细信息")
                if not popup.Exists(maxSearchSeconds=1):
                    # 尝试找最近出现的新窗口
                    logger.warning("未找到添加朋友窗口")
                    return ("not_found", "")

            popup.SetActive()
            time.sleep(0.3)

            # 检测头像控件
            has_avatar = False
            has_nickname = False
            has_add_button = False
            nickname_text = ""

            # 1. 检测头像（ImageControl 或 ButtonControl 类型的头像）
            try:
                avatar = popup.ImageControl(searchDepth=5)
                has_avatar = avatar.Exists(maxSearchSeconds=0.5)
            except Exception:
                pass

            if not has_avatar:
                try:
                    # 有些版本头像可能是 Button
                    avatar_btn = popup.ButtonControl(searchDepth=5)
                    # 尝试找圆形头像按钮（通常第一个 Button 是头像）
                    if avatar_btn.Exists(maxSearchSeconds=0.5):
                        has_avatar = True
                except Exception:
                    pass

            # 2. 检测昵称（TextControl）
            try:
                # 找昵称文本：通常在特定区域
                nick = popup.TextControl(searchDepth=5)
                if nick.Exists(maxSearchSeconds=0.3):
                    nickname_text = nick.Name
                    has_nickname = bool(nickname_text and len(nickname_text) > 0)
            except Exception:
                pass

            # 如果直接找没找到，遍历子控件找文本
            if not has_nickname:
                try:
                    for ctrl in popup.GetChildren():
                        if hasattr(ctrl, 'Name') and ctrl.Name:
                            # 排除常见的非昵称文本
                            exclude = ["添加朋友", "微信号", "标签", "来源", "地区",
                                       "个性签名", "添加到通讯录", "发消息", "音视频通话"]
                            if ctrl.Name not in exclude and len(ctrl.Name) <= 20:
                                nickname_text = ctrl.Name
                                has_nickname = True
                                logger.debug(f"通过遍历找到昵称: {nickname_text}")
                                break
                except Exception:
                    pass

            # 3. 检测"添加到通讯录"按钮
            try:
                add_btn = popup.ButtonControl(Name="添加到通讯录")
                has_add_button = add_btn.Exists(maxSearchSeconds=0.3)
            except Exception:
                pass

            if not has_add_button:
                try:
                    add_btn = popup.ButtonControl(Name="添加")
                    has_add_button = add_btn.Exists(maxSearchSeconds=0.3)
                except Exception:
                    pass

            logger.debug(
                f"弹窗检测: 头像={has_avatar} 昵称={has_nickname}({nickname_text}) "
                f"添加按钮={has_add_button}"
            )

            # 判定
            if has_avatar and has_nickname:
                return ("normal", nickname_text)
            else:
                reason_parts = []
                if not has_avatar:
                    reason_parts.append("无头像")
                if not has_nickname:
                    reason_parts.append("无昵称")
                reason = " + ".join(reason_parts)
                return ("abnormal", reason)

        except Exception as e:
            logger.error(f"检测弹窗状态时出错: {e}")
            return ("not_found", "")

    def close_popup(self):
        """按 ESC 关闭弹窗"""
        if not UIA_AVAILABLE:
            return
        try:
            auto.SendKeys("{Esc}")
            time.sleep(0.5)
            logger.debug("已关闭弹窗")
        except Exception as e:
            logger.error(f"关闭弹窗失败: {e}")

    def clear_search(self):
        """清空搜索框"""
        if not UIA_AVAILABLE:
            return
        try:
            auto.SendKeys("{Ctrl}a")
            time.sleep(0.2)
            auto.SendKeys("{Delete}")
            time.sleep(0.3)
            logger.debug("搜索框已清空")
        except Exception as e:
            logger.error(f"清空搜索框失败: {e}")

    # ==================== 综合检查 ====================

    def check_single_account(self, wechat_id):
        """
        检查单个微信号的完整流程

        返回:
            ("success", nickname) — 正常
            ("abnormal", reason)  — 异常
            ("error", msg)        — 操作出错
        """
        logger.info(f"开始检查: {wechat_id}")

        # 1. 激活窗口
        if not self.activate_window():
            return ("error", "无法激活微信窗口")

        time.sleep(0.3)

        # 2. 聚焦搜索框
        if not self.focus_search_box():
            return ("error", "无法聚焦搜索框")

        time.sleep(0.5)

        # 3. 输入微信号
        if not self.input_wechat_id(wechat_id):
            return ("error", "无法输入微信号")

        time.sleep(0.5)

        # 4. 点击下拉项
        if not self.click_dropdown_item():
            # 如果没找到下拉项，可能是搜索无结果，也算异常
            self.close_popup()
            self.clear_search()
            return ("abnormal", "搜索无结果或无法点开详情")

        # 5. 检测弹窗状态
        status, detail = self.check_popup_status()

        # 6. 关闭弹窗并清空搜索框
        self.close_popup()
        self.clear_search()

        if status == "normal":
            logger.info(f"[正常] {wechat_id} -> 昵称: {detail}")
            return ("success", detail)
        elif status == "abnormal":
            logger.warning(f"[异常] {wechat_id} -> {detail}")
            return ("abnormal", detail)
        else:
            logger.warning(f"[未知] {wechat_id} -> 未检测到弹窗")
            return ("abnormal", "未检测到弹窗")


# ==================== 测试入口 ====================
if __name__ == "__main__":
    # 测试用
    ctrl = WeChatController(r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe")
    print(f"微信运行中: {ctrl.is_wechat_running()}")
