"""
微信自动化操作模块
基于 uiautomation 操控微信 PC 客户端
"""
import ctypes
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


def _set_clipboard_text(text):
    """通过 Win32 API 设置剪贴板文本，带重试，失败时抛明确异常"""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    last_err = None
    for attempt in range(3):
        try:
            h_wnd = ctypes.windll.user32.GetForegroundWindow()
            if not ctypes.windll.user32.OpenClipboard(h_wnd):
                last_err = f"OpenClipboard 失败 (尝试 {attempt+1}/3)"
                time.sleep(0.1)
                continue

            try:
                if not ctypes.windll.user32.EmptyClipboard():
                    last_err = f"EmptyClipboard 失败 (尝试 {attempt+1}/3)"
                    continue

                wchar_size = ctypes.sizeof(ctypes.c_wchar)
                buf_size = (len(text) + 1) * wchar_size
                h_mem = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, buf_size)
                if not h_mem:
                    last_err = f"GlobalAlloc 失败 (尝试 {attempt+1}/3)"
                    continue

                p_mem = ctypes.windll.kernel32.GlobalLock(h_mem)
                if not p_mem:
                    ctypes.windll.kernel32.GlobalFree(h_mem)
                    last_err = f"GlobalLock 失败 (尝试 {attempt+1}/3)"
                    continue

                buf = ctypes.create_unicode_buffer(text)
                ctypes.memmove(p_mem, buf, buf_size)
                ctypes.windll.kernel32.GlobalUnlock(h_mem)

                if not ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                    ctypes.windll.kernel32.GlobalFree(h_mem)
                    last_err = f"SetClipboardData 失败 (尝试 {attempt+1}/3)"
                    continue

                return  # 成功
            finally:
                ctypes.windll.user32.CloseClipboard()
        except Exception as e:
            last_err = f"剪贴板异常 (尝试 {attempt+1}/3): {e}"
            time.sleep(0.1)

    raise OSError(last_err or "设置剪贴板失败")


# ---------- Win32 键盘模拟（不依赖 uiautomation COM 线程）----------

# 虚拟键码表（只定义用到的）
_VK = {
    "ctrl":   0x11,
    "f":      0x46,
    "a":      0x41,
    "v":      0x56,
    "delete": 0x2E,
    "escape": 0x1B,
}
_KEYEVENTF_KEYUP = 0x0002


def _keydown(vk):
    """按下键"""
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)


def _keyup(vk):
    """释放键"""
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def _send_hotkey(mod_vk, key_vk):
    """发送 Ctrl+某键 组合"""
    _keydown(mod_vk)
    time.sleep(0.03)
    _keydown(key_vk)
    time.sleep(0.03)
    _keyup(key_vk)
    time.sleep(0.03)
    _keyup(mod_vk)
    time.sleep(0.05)


def _press_key(vk):
    """按下并释放单键（Delete、Esc 等）"""
    _keydown(vk)
    time.sleep(0.03)
    _keyup(vk)
    time.sleep(0.05)


# SendInput 结构体定义（正确使用 Union 匹配 Windows SDK 布局）
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_uint),
        ("u", _INPUT_UNION),
    ]


def _type_text(text):
    """用 SendInput 逐字符输入 Unicode 文本，不依赖剪贴板"""
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1

    inputs = []
    for ch in text:
        code = ord(ch)
        # 按键按下
        inp_d = _INPUT()
        inp_d.type = INPUT_KEYBOARD
        inp_d.ki.wVk = 0
        inp_d.ki.wScan = code
        inp_d.ki.dwFlags = KEYEVENTF_UNICODE
        inp_d.ki.time = 0
        inp_d.ki.dwExtraInfo = 0
        inputs.append(inp_d)
        # 按键释放
        inp_u = _INPUT()
        inp_u.type = INPUT_KEYBOARD
        inp_u.ki.wVk = 0
        inp_u.ki.wScan = code
        inp_u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        inp_u.ki.time = 0
        inp_u.ki.dwExtraInfo = 0
        inputs.append(inp_u)

    count = len(inputs)
    arr = (_INPUT * count)(*inputs)
    sent = ctypes.windll.user32.SendInput(count, arr, ctypes.sizeof(_INPUT))
    if sent < count:
        logger.warning(f"SendInput 发送 {count} 个事件，仅成功 {sent} 个")


class WeChatController:
    """微信控制器，封装所有自动化操作"""

    # 微信进程名
    WECHAT_PROCESS = "WeChat.exe"
    # 微信主窗口标题可能包含的文字
    WECHAT_WINDOW_TITLE = "微信"

    # 弹窗中除昵称外可能出现的固定标签文字（用于昵称兜底检测时排除）
    NICKNAME_EXCLUDE = frozenset([
        "添加朋友", "微信号", "标签", "来源", "地区",
        "个性签名", "添加到通讯录", "发消息", "音视频通话",
        "微信", "WeChat", "昵称",
    ])

    def __init__(self, wechat_path):
        self.wechat_path = wechat_path
        self.wechat_window = None
        self.main_control = None

    # ==================== 窗口管理 ====================

    def is_wechat_running(self):
        """检查微信是否在运行 - 优先用进程名检查"""
        # 方法1: 通过 psutil 检查进程（最可靠）
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] == self.WECHAT_PROCESS:
                    logger.debug("通过 psutil 检测到微信进程运行中")
                    return True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"psutil 检查进程异常: {e}")

        # 方法2: 通过 tasklist 检查进程
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.WECHAT_PROCESS}"],
                capture_output=True, text=True, timeout=5
            )
            if self.WECHAT_PROCESS in result.stdout:
                logger.debug("通过 tasklist 检测到微信进程运行中")
                return True
        except Exception as e:
            logger.debug(f"tasklist 检查异常: {e}")

        # 方法3: 通过 uiautomation 查找窗口（兜底）
        if UIA_AVAILABLE:
            try:
                for class_name in ["WeChatMainWndForPC", "ChatWnd", "MainWindow"]:
                    try:
                        window = auto.WindowControl(searchDepth=1, ClassName=class_name)
                        if window.Exists(maxSearchSeconds=0.3):
                            logger.debug(f"通过 uiautomation 类名 {class_name} 检测到微信窗口")
                            return True
                    except Exception:
                        continue
                try:
                    window = auto.WindowControl(searchDepth=1, Name="微信")
                    if window.Exists(maxSearchSeconds=0.3):
                        logger.debug("通过 uiautomation 标题检测到微信窗口")
                        return True
                except Exception:
                    pass
            except Exception:
                pass

        logger.warning("未检测到微信进程/窗口")
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
        SendKeys 必须发到前台窗口才能生效，所以必须 SetActive
        """
        if not UIA_AVAILABLE:
            logger.error("uiautomation 不可用，无法操作微信窗口")
            return False

        try:
            window = None

            for class_name in ["WeChatMainWndForPC", "ChatWnd", "MainWindow"]:
                try:
                    w = auto.WindowControl(searchDepth=1, ClassName=class_name)
                    if w.Exists(maxSearchSeconds=1):
                        window = w
                        logger.debug(f"通过类名找到微信窗口: {class_name}")
                        break
                except Exception:
                    continue

            if window is None:
                try:
                    w = auto.WindowControl(searchDepth=1, Name="微信")
                    if w.Exists(maxSearchSeconds=1):
                        window = w
                        logger.debug("通过标题找到微信窗口")
                except Exception:
                    pass

            if window is None:
                logger.error("找不到微信主窗口")
                return False

            # 用 Win32 API 强制激活微信窗口为前台窗口
            hwnd = window.NativeWindowHandle
            if hwnd:
                # 先恢复窗口（如果最小化了）
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                # BringWindowToTop + SetForegroundWindow 组合确保前台
                ctypes.windll.user32.BringWindowToTop(hwnd)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            else:
                # 回退到 uiautomation 的 SetActive
                window.SetActive()
                time.sleep(0.3)

            self.wechat_window = window
            logger.debug("微信窗口已激活为前台窗口")
            return True

        except Exception as e:
            logger.error(f"激活微信窗口失败: {e}")
            return False

    # ==================== 搜索操作 ====================

    def focus_search_box(self):
        """
        按 Ctrl+F 激活搜索框
        用 Win32 keybd_event，不依赖 uiautomation COM 线程
        """
        if not UIA_AVAILABLE:
            logger.error("uiautomation 不可用")
            return False

        try:
            _send_hotkey(_VK["ctrl"], _VK["f"])
            time.sleep(1.5)
            logger.debug("已按下 Ctrl+F 激活搜索框")
            return True
        except Exception as e:
            logger.error(f"激活搜索框失败: {e}")
            return False

    def input_wechat_id(self, wechat_id):
        """
        在搜索框中输入微信号
        优先尝试 uiautomation 直接设置 EditControl 的值，失败则用 keybd_event + SendInput
        """
        if not UIA_AVAILABLE:
            return False

        try:
            # 方法A: 尝试找到搜索框 EditControl 直接设置文本值
            try:
                search_root = self.wechat_window if self.wechat_window else auto
                edit = search_root.EditControl(searchDepth=10)
                if edit.Exists(maxSearchSeconds=0.5):
                    # 清空已有内容
                    edit.GetValuePattern().SetValue("")
                    time.sleep(0.1)
                    # 直接设置微信号
                    edit.GetValuePattern().SetValue(wechat_id)
                    time.sleep(0.3)
                    logger.debug(f"已通过 SetValue 输入微信号: {wechat_id}")
                    return True
            except Exception as e:
                logger.debug(f"直接 SetValue 失败: {e}，回退到键盘模拟")

            # 方法B: 键盘模拟 — keybd_event 清空 + SendInput 逐字符输入
            _send_hotkey(_VK["ctrl"], _VK["a"])
            time.sleep(0.2)
            _press_key(_VK["delete"])
            time.sleep(0.3)
            _type_text(wechat_id)
            time.sleep(0.3)

            logger.debug(f"已通过键盘模拟输入微信号: {wechat_id}")
            return True
        except Exception as e:
            logger.error(f"输入微信号失败: {e}")
            return False

    def click_dropdown_item(self):
        """
        点击下拉框中的'网络查找手机/QQ号'项
        只用 InvokePattern，搜索范围限定在微信窗口内
        """
        if not UIA_AVAILABLE:
            return False

        try:
            found = False
            clicked_name = ""

            # 等待下拉列表出现
            time.sleep(1.0)

            def invoke_item(item):
                """只用 InvokePattern，不移动鼠标"""
                try:
                    pattern = item.GetInvokePattern()
                    pattern.Invoke()
                    return True
                except Exception:
                    logger.warning(f"InvokePattern 失败: {item.Name}")
                    return False

            # 确定搜索起点：优先从微信窗口内搜索
            search_root = self.wechat_window if self.wechat_window else auto

            # 方法1: 遍历 ListControl 的子项
            try:
                list_ctrl = search_root.ListControl(searchDepth=8)
                if list_ctrl.Exists(maxSearchSeconds=1):
                    items = list_ctrl.GetChildren()
                    logger.info(f"下拉列表中有 {len(items)} 个子项")
                    for item in items:
                        name = item.Name
                        logger.info(f"  下拉项: [{name}]")
                        if "网络" in name or "查找" in name or "手机" in name or "QQ" in name:
                            if invoke_item(item):
                                found = True
                                clicked_name = name
                                logger.info(f"点击下拉项: {name}")
                                break
            except Exception as e:
                logger.debug(f"遍历 ListControl 失败: {e}")

            # 方法2: 直接按名称在微信窗口内查找
            if not found:
                for text in ["网络查找", "查找手机", "查找QQ"]:
                    try:
                        item = search_root.ListItemControl(Name=text, searchDepth=8)
                        if item.Exists(maxSearchSeconds=0.5):
                            if invoke_item(item):
                                found = True
                                clicked_name = text
                                logger.info(f"点击下拉项(精确匹配): {text}")
                                break
                    except Exception:
                        continue

            if found:
                logger.info(f"成功点击下拉项: {clicked_name}")
                time.sleep(2.0)
                return True
            else:
                logger.warning("未找到'网络查找'下拉项（搜索范围已限定在微信窗口内）")
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

            # 查找弹窗 - 尝试多种可能的窗口标题
            popup = None
            popup_titles = ["添加朋友", "详细信息", "联系人", "搜索",
                           "朋友验证", "新的朋友", "添加",
                           "WeChat", "微信"]  # 兜底

            for title in popup_titles:
                try:
                    w = auto.WindowControl(Name=title, searchDepth=1)
                    if w.Exists(maxSearchSeconds=0.5):
                        # 排除主窗口本身
                        if title == "微信" or title == "WeChat":
                            # 检查是不是新弹窗（不是主窗口）
                            try:
                                if w.ClassName == "WeChatMainWndForPC":
                                    continue
                            except Exception:
                                pass
                        popup = w
                        logger.info(f"找到弹窗: {title}")
                        break
                except Exception:
                    continue

            if popup is None:
                # 最后尝试：找所有顶层窗口，排除已知的主窗口
                try:
                    all_windows = auto.GetRootControl().GetChildren()
                    for w in all_windows:
                        if isinstance(w, auto.WindowControl):
                            try:
                                cls = w.ClassName
                                if cls and cls not in ["WeChatMainWndForPC", "ChatWnd"]:
                                    name = w.Name
                                    if name and len(name) > 0 and name != "微信":
                                        popup = w
                                        logger.info(f"通过遍历找到弹窗: {name} (class={cls})")
                                        break
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"遍历窗口失败: {e}")

            if popup is None:
                logger.warning("未找到任何弹窗")
                return ("not_found", "")

            # 不激活弹窗，避免抢前台
            time.sleep(0.5)

            # 记录弹窗内所有控件信息（调试用）
            try:
                all_ctrls = []
                def collect_ctrls(ctrl, depth=0):
                    try:
                        name = ctrl.Name
                        ctrl_type = type(ctrl).__name__
                        all_ctrls.append(f"  {'  '*depth}{ctrl_type}: [{name}]")
                        for child in ctrl.GetChildren():
                            collect_ctrls(child, depth+1)
                    except Exception:
                        pass
                collect_ctrls(popup)
                for line in all_ctrls:
                    logger.debug(line)
            except Exception:
                pass

            # 检测头像控件
            has_avatar = False
            has_nickname = False
            has_add_button = False
            nickname_text = ""

            # 1. 检测头像
            try:
                avatar = popup.ImageControl(searchDepth=5)
                has_avatar = avatar.Exists(maxSearchSeconds=0.5)
            except Exception:
                pass

            if not has_avatar:
                try:
                    avatar_btn = popup.ButtonControl(searchDepth=5)
                    if avatar_btn.Exists(maxSearchSeconds=0.5):
                        has_avatar = True
                except Exception:
                    pass

            # 2. 检测昵称
            try:
                nick = popup.TextControl(searchDepth=5)
                if nick.Exists(maxSearchSeconds=0.3):
                    nickname_text = nick.Name
                    has_nickname = bool(nickname_text and len(nickname_text) > 0)
            except Exception:
                pass

            if not has_nickname:
                try:
                    for ctrl in popup.GetChildren():
                        if hasattr(ctrl, 'Name') and ctrl.Name:
                            name = ctrl.Name
                            if name not in self.NICKNAME_EXCLUDE and len(name) <= 20:
                                nickname_text = name
                                has_nickname = True
                                logger.debug(f"通过遍历找到昵称: {nickname_text}")
                                break
                            else:
                                logger.debug(f"排除非昵称文本: [{name}]")
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
        """关闭弹窗 - 优先找关闭按钮，兜底 ESC"""
        if not UIA_AVAILABLE:
            return
        try:
            # 优先找关闭按钮
            try:
                close_btn = auto.ButtonControl(Name="关闭", searchDepth=5)
                if close_btn.Exists(maxSearchSeconds=0.3):
                    try:
                        pattern = close_btn.GetInvokePattern()
                        pattern.Invoke()
                        time.sleep(0.5)
                        logger.debug("通过关闭按钮关闭弹窗")
                        return
                    except Exception:
                        pass
            except Exception:
                pass

            # 兜底：ESC
            _press_key(_VK["escape"])
            time.sleep(0.5)
            logger.debug("已关闭弹窗(ESC)")
        except Exception as e:
            logger.error(f"关闭弹窗失败: {e}")

    def clear_search(self):
        """清空搜索框"""
        if not UIA_AVAILABLE:
            return
        try:
            _send_hotkey(_VK["ctrl"], _VK["a"])
            time.sleep(0.2)
            _press_key(_VK["delete"])
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
