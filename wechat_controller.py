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


def _capture_pixels(hwnd, left, top, right, bottom):
    """截取窗口区域，返回 (width, height, pixels_bgra_topdown)。
    64位兼容：显式设置 argtypes 避免句柄溢出。"""
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return (0, 0, b"")

    # 显式声明 argtypes 防止 64 位句柄溢出
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [ctypes.c_void_p]
    user32.GetWindowDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    h_bmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
    gdi32.SelectObject(hdc_mem, h_bmp)
    # BitBlt 有9个参数，需要完整声明
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_win, left, top, 0x00CC0020)

    class BI(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int), ("biHeight", ctypes.c_int),
            ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_uint), ("biSizeImage", ctypes.c_uint),
            ("biXPelsPerMeter", ctypes.c_int), ("biYPelsPerMeter", ctypes.c_int),
            ("biClrUsed", ctypes.c_uint), ("biClrImportant", ctypes.c_uint),
        ]
    bi = BI()
    bi.biSize = ctypes.sizeof(BI)
    bi.biWidth = width
    bi.biHeight = height
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf_size = width * height * 4
    buf = (ctypes.c_ubyte * buf_size)()
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    gdi32.GetDIBits(hdc_mem, h_bmp, 0, height, buf, ctypes.byref(bi), 0)

    # 翻转行顺序：BMP 底部行在前 → 顶行在前
    row_size = width * 4
    pixels_topdown = bytearray(buf_size)
    for y in range(height):
        src_start = (height - 1 - y) * row_size
        dst_start = y * row_size
        pixels_topdown[dst_start:dst_start + row_size] = buf[src_start:src_start + row_size]

    gdi32.DeleteObject(h_bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)
    return (width, height, bytes(pixels_topdown))


def _count_pixels(pixels, width, height, x1, y1, x2, y2, rgb_range):
    """统计矩形区域内匹配颜色范围的像素数。
    rgb_range: ((r_min,r_max), (g_min,g_max), (b_min,b_max))"""
    count = 0
    (r_min, r_max), (g_min, g_max), (b_min, b_max) = rgb_range
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(width, x2)
    y2 = min(height, y2)
    for py in range(y1, y2):
        row_start = py * width * 4
        for px in range(x1, x2):
            offset = row_start + px * 4
            b = pixels[offset]
            g = pixels[offset + 1]
            r = pixels[offset + 2]
            if r_min <= r <= r_max and g_min <= g <= g_max and b_min <= b <= b_max:
                count += 1
    return count


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
        键盘 ↑+Enter（微信默认选中"搜索网络结果"，↑回到"网络查找"）
        """
        if not UIA_AVAILABLE:
            return False

        time.sleep(1.5)
        _press_key(0x26)  # VK_UP → "网络查找手机/QQ号"
        time.sleep(0.3)
        _press_key(0x0D)  # VK_RETURN
        time.sleep(2.0)
        return True

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
            popup_hwnd = None
            try:
                popup_hwnd = popup.NativeWindowHandle
                if popup_hwnd:
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(popup_hwnd, ctypes.byref(rect))
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

            # 像素检测"添加到通讯录"按钮：灰底 + 黑字 = 有文字的按钮
            has_add_button = False
            if popup_rect and popup_hwnd:
                try:
                    pw = popup_rect[2] - popup_rect[0]
                    ph = popup_rect[3] - popup_rect[1]
                    w, h, pixels = _capture_pixels(popup_hwnd, 0, 0, pw, ph)
                    if w > 0 and h > 0:
                        btn_x1 = int(w * 0.10)
                        btn_y1 = int(h * 0.80)
                        btn_x2 = int(w * 0.90)
                        btn_y2 = int(h * 0.95)
                        # 灰色背景 (按钮底色)
                        gray_range = ((170, 240), (170, 240), (170, 240))
                        gray_count = _count_pixels(pixels, w, h, btn_x1, btn_y1, btn_x2, btn_y2, gray_range)
                        # 黑色/深色文字 (按钮上的字)
                        dark_range = ((0, 60), (0, 60), (0, 60))
                        dark_count = _count_pixels(pixels, w, h, btn_x1, btn_y1, btn_x2, btn_y2, dark_range)
                        total_in_rect = (btn_x2 - btn_x1) * (btn_y2 - btn_y1)
                        gray_pct = gray_count / total_in_rect * 100 if total_in_rect > 0 else 0
                        dark_pct = dark_count / total_in_rect * 100 if total_in_rect > 0 else 0
                        # 灰底(>60%) + 黑字(>2%) = 有文字的按钮 = "添加到通讯录"
                        has_add_button = gray_pct > 60 and dark_pct > 2
                        _glog(f"像素检测: 灰底={gray_pct:.0f}% 黑字={dark_pct:.1f}% → "
                              f"按钮={'有文字' if has_add_button else '无文字/不存在'}")
                except Exception as e:
                    _glog(f"像素检测异常: {e}，回退到弹窗打开=正常")

            # 判断逻辑
            if has_add_button:
                _glog("弹窗: 按钮有文字(添加到通讯录) → 正常")
                return ("normal", "(已识别按钮)")
            elif popup_rect:
                _glog("弹窗: 已打开但按钮无文字 → 可能异常")
                return ("abnormal", "按钮无文字")
            else:
                return ("abnormal", "弹窗未打开")

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
