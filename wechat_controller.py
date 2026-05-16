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


def _screenshot_rect(left, top, right, bottom, filepath):
    """用 Win32 GDI 截取屏幕矩形区域，保存为 BMP 文件（零依赖）"""
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return False

    # 获取屏幕 DC
    hdc_screen = ctypes.windll.user32.GetDC(0)
    hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
    h_bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    ctypes.windll.gdi32.SelectObject(hdc_mem, h_bmp)
    ctypes.windll.gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, left, top, 0x00CC0020)  # SRCCOPY

    # 获取像素数据
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int), ("biHeight", ctypes.c_int),
            ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_uint), ("biSizeImage", ctypes.c_uint),
            ("biXPelsPerMeter", ctypes.c_int), ("biYPelsPerMeter", ctypes.c_int),
            ("biClrUsed", ctypes.c_uint), ("biClrImportant", ctypes.c_uint),
        ]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = height
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0  # BI_RGB

    # 分配缓冲区并获取像素
    buf_size = width * height * 4
    buf = (ctypes.c_ubyte * buf_size)()
    ctypes.windll.gdi32.GetDIBits(hdc_mem, h_bmp, 0, height, buf, ctypes.byref(bi), 0)

    # 写 BMP 文件
    bmp_header = b'BM'
    file_size = 54 + buf_size
    bmp_header += file_size.to_bytes(4, 'little')
    bmp_header += b'\x00\x00\x00\x00'  # reserved
    bmp_header += (54).to_bytes(4, 'little')  # data offset
    bmp_header += bytes(bi)
    # BMP 是倒序行，反转
    row_size = width * 4
    bmp_data = b''
    for y in range(height - 1, -1, -1):
        bmp_data += bytes(buf[y * row_size:(y + 1) * row_size])

    with open(filepath, 'wb') as f:
        f.write(bmp_header)
        f.write(bmp_data)

    # 清理
    ctypes.windll.gdi32.DeleteObject(h_bmp)
    ctypes.windll.gdi32.DeleteDC(hdc_mem)
    ctypes.windll.user32.ReleaseDC(0, hdc_screen)
    return True


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
        self._gui_log = None  # GUI 日志回调，由引擎注入

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
        方法A: SendInput 逐字符 → 方法B: 剪贴板 + Ctrl+V（base64 安全编码）
        """
        if not UIA_AVAILABLE:
            return False

        try:
            # 清空搜索框已有内容
            _send_hotkey(_VK["ctrl"], _VK["a"])
            time.sleep(0.2)
            _press_key(_VK["delete"])
            time.sleep(0.2)

            # 方法A: SendInput 逐字符输入
            _type_text(wechat_id)
            time.sleep(0.3)
            logger.debug(f"已通过 SendInput 输入微信号: {wechat_id}")
            return True

        except Exception as e:
            logger.debug(f"SendInput 失败: {e}，回退到剪贴板")

            # 方法B: base64 + PowerShell 剪贴板（安全，无注入风险）
            try:
                import base64
                encoded = base64.b64encode(wechat_id.encode('utf-16-le')).decode('ascii')
                ps_cmd = (
                    f'$s=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String("{encoded}"));'
                    f'[Windows.Clipboard]::SetText($s)'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, timeout=10
                )
                time.sleep(0.2)
                _send_hotkey(_VK["ctrl"], _VK["v"])
                time.sleep(0.3)
                logger.debug(f"已通过剪贴板粘贴微信号: {wechat_id}")
                return True
            except Exception as e2:
                logger.error(f"所有输入方式均失败: SendInput={e}, 剪贴板={e2}")
                return False

    def click_dropdown_item(self):
        """
        选择搜索下拉框中的'网络查找手机/QQ号'项
        截图下拉区域 → 键盘 ↑+Enter
        """
        if not UIA_AVAILABLE:
            return False

        try:
            # 等待搜索结果下拉列表出现
            time.sleep(1.5)

            # 截图下拉菜单区域（微信窗口内搜索框下方约 300x250 像素）
            try:
                if self.wechat_window:
                    hwnd = self.wechat_window.NativeWindowHandle
                    if hwnd:
                        r = ctypes.wintypes.RECT()
                        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                        # 下拉菜单在搜索框下方，搜索框大约在窗口顶部 30%~60% 区域
                        dropdown_left = r.left + 20
                        dropdown_top = r.top + 80
                        dropdown_right = r.right - 20
                        dropdown_bottom = r.top + 330
                        import os as _os
                        screenshot_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "screenshots")
                        _os.makedirs(screenshot_dir, exist_ok=True)
                        filepath = _os.path.join(screenshot_dir, "dropdown.bmp")
                        _screenshot_rect(dropdown_left, dropdown_top, dropdown_right, dropdown_bottom, filepath)
                        if self._gui_log:
                            self._gui_log(f"下拉截图已保存: {filepath} ({dropdown_right-dropdown_left}x{dropdown_bottom-dropdown_top})")
            except Exception as e:
                if self._gui_log:
                    self._gui_log(f"下拉截图失败: {e}")

            # 键盘操作：↑ 往上选"网络查找"，Enter 确认
            _press_key(0x26)  # VK_UP
            time.sleep(0.3)
            _press_key(0x0D)  # VK_RETURN
            time.sleep(2.0)

            logger.info("已通过键盘 ↑+Enter 选择下拉项")
            return True

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
            # 等待弹窗出现（CEF 面板加载较慢）
            time.sleep(2.0)

            # 查找弹窗 — 三级搜索
            popup = None

            def _glog(msg):
                """同时写 logger 和 GUI 回调"""
                logger.info(msg)
                if self._gui_log:
                    self._gui_log(msg)

            # 第1级：搜索较深层的独立窗口（searchDepth=3）
            popup_titles = ["添加朋友", "详细信息", "联系人", "朋友验证", "新的朋友"]
            for title in popup_titles:
                try:
                    w = auto.WindowControl(Name=title, searchDepth=3)
                    if w.Exists(maxSearchSeconds=0.8):
                        popup = w
                        _glog(f"找到弹窗(深层窗口): {title}")
                        break
                except Exception:
                    continue

            # 第2级：在微信主窗口内搜索面板（PaneControl）
            if popup is None and self.wechat_window:
                for title in popup_titles:
                    try:
                        pane = self.wechat_window.PaneControl(Name=title, searchDepth=10)
                        if pane.Exists(maxSearchSeconds=0.8):
                            popup = pane
                            _glog(f"找到弹窗(主窗口内面板): {title}")
                            break
                    except Exception:
                        continue

            # 第3级：兜底用微信主窗口本身作为搜索范围
            if popup is None:
                _glog("未找到独立弹窗，使用微信主窗口作为搜索范围")
                popup = self.wechat_window if self.wechat_window else auto.GetRootControl()

            if popup is None:
                # 诊断：列出桌面所有顶层窗口
                try:
                    all_top = auto.GetRootControl().GetChildren()
                    _glog(f"未找到弹窗，桌面共 {len(all_top)} 个顶层窗口:")
                    for w in all_top[:15]:
                        try:
                            _glog(f"  [{w.ClassName}] Name='{w.Name}' "
                                  f"Rect={w.BoundingRectangle}")
                        except Exception:
                            pass
                except Exception:
                    pass
                _glog("未找到任何弹窗或微信窗口")
                return ("not_found", "")

            # 不激活弹窗，避免抢前台
            time.sleep(0.5)

            # 获取弹窗屏幕位置范围（优先 Win32 API，更可靠）
            popup_rect = None
            try:
                hwnd = popup.NativeWindowHandle
                if hwnd:
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    popup_rect = (rect.left, rect.top, rect.right, rect.bottom)
                    _glog(f"弹窗位置(Win32): left={popup_rect[0]} top={popup_rect[1]} "
                          f"right={popup_rect[2]} bottom={popup_rect[3]}")
            except Exception:
                pass

            if popup_rect is None:
                try:
                    popup_rect = popup.BoundingRectangle
                    _glog(f"弹窗位置(UIA): {popup_rect}")
                except Exception as e:
                    _glog(f"无法获取弹窗位置: {e}")

            # 截图弹窗区域
            if popup_rect:
                try:
                    import os as _os
                    screenshot_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "screenshots")
                    _os.makedirs(screenshot_dir, exist_ok=True)
                    filepath = _os.path.join(screenshot_dir, "popup.bmp")
                    _screenshot_rect(popup_rect[0], popup_rect[1], popup_rect[2], popup_rect[3], filepath)
                    _glog(f"弹窗截图已保存: {filepath} ({popup_rect[2]-popup_rect[0]}x{popup_rect[3]-popup_rect[1]})")
                except Exception as e:
                    _glog(f"弹窗截图失败: {e}")

            # 从桌面根控件全局遍历，筛选弹窗范围内的控件
            found_ctrls = []  # (ctrl_type, name, rect)
            has_add_button = False
            nickname_text = ""

            if popup_rect:
                def collect_in_rect(ctrl, depth=0):
                    nonlocal has_add_button, nickname_text
                    if depth > 20:
                        return
                    try:
                        ctrl_rect = ctrl.BoundingRectangle
                        # 检查控件位置是否在弹窗范围内（有交集即可）
                        if (ctrl_rect[2] > popup_rect[0] and ctrl_rect[0] < popup_rect[2] and
                            ctrl_rect[3] > popup_rect[1] and ctrl_rect[1] < popup_rect[3]):
                            ctrl_type = type(ctrl).__name__
                            name = ctrl.Name
                            found_ctrls.append((ctrl_type, name, ctrl_rect))

                            # 检测"添加到通讯录"按钮
                            if "Button" in ctrl_type and name in ["添加到通讯录", "添加", "发消息"]:
                                has_add_button = True
                                logger.info(f"全局遍历找到按钮: {name}")

                            # 检测候选昵称
                            if not nickname_text and ("Text" in ctrl_type or "Edit" in ctrl_type):
                                if name and name not in self.NICKNAME_EXCLUDE and 1 <= len(name) <= 20:
                                    nickname_text = name
                                    logger.info(f"全局遍历找到候选昵称: {name}")

                        # 继续遍历子控件
                        for child in ctrl.GetChildren():
                            collect_in_rect(child, depth + 1)
                    except Exception:
                        pass

                try:
                    collect_in_rect(auto.GetRootControl())
                except Exception as e:
                    logger.warning(f"全局遍历异常: {e}")

            # 输出找到的控件列表
            _glog(f"=== 弹窗范围内控件 (共{len(found_ctrls)}个) ===")
            for ctrl_type, name, rect in found_ctrls[:40]:
                _glog(f"  {ctrl_type}: [{name}] @ ({rect[0]},{rect[1]})-({rect[2]},{rect[3]})")
            if len(found_ctrls) > 40:
                _glog(f"  ... 省略 {len(found_ctrls)-40} 个")
            _glog("=== 控件列表结束 ===")

            # 判断逻辑 — CEF 弹窗内部无 UIA 控件，以弹窗是否打开为主要判据
            diag = f"弹窗: 按钮={has_add_button} 昵称={nickname_text} 控件数={len(found_ctrls)}"
            logger.info(diag)
            self._last_popup_diag = diag

            if has_add_button:
                return ("normal", nickname_text or "(未识别昵称)")

            # CEF 弹窗：无内部控件但弹窗已打开 → 判定为正常
            # 异常号（用户不存在）不会弹出"添加朋友"窗口，会走到 not_found 分支
            _glog("CEF弹窗: 内部控件不可见，弹窗已打开→判定正常")
            return ("normal", "(CEF弹窗已打开)" if len(found_ctrls) == 0 else f"(控件{len(found_ctrls)}个)")

        except Exception as e:
            logger.error(f"检测弹窗状态时出错: {e}")
            return ("not_found", "")

    def close_popup(self):
        """关闭弹窗 - 找到弹窗 → 激活 → ESC"""
        if not UIA_AVAILABLE:
            return
        try:
            # 找到弹窗窗口
            popup = None
            for title in ["添加朋友", "详细信息", "联系人", "朋友验证", "新的朋友"]:
                try:
                    w = auto.WindowControl(Name=title, searchDepth=3)
                    if w.Exists(maxSearchSeconds=0.5):
                        popup = w
                        break
                except Exception:
                    continue

            # 激活弹窗使其能接收 ESC 按键
            if popup:
                try:
                    hwnd = popup.NativeWindowHandle
                    if hwnd:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        time.sleep(0.2)
                except Exception:
                    pass

            # 发送 ESC 关闭弹窗
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
