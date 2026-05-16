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


# ---------- OCR 语言包自动安装 ----------

_ocr_lang_installed = False  # 是否已成功安装OCR语言包


def _ensure_chinese_ocr(glog=None):
    """检查并自动安装 Windows OCR 语言包（en-US + zh-CN）
    glog: 可选回调(msg) 用于推送GUI消息
    先静默安装，失败则通过glog提示用户后弹UAC提权安装"""
    global _ocr_lang_installed
    if _ocr_lang_installed:
        return True

    import subprocess

    needed = [
        ("Language.OCR~~~en-US~0.0.1.0", "英文OCR"),
        ("Language.OCR~~~zh-CN~0.0.1.0", "中文OCR"),
    ]

    def _check_installed(pkg_name):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-WindowsCapability -Online -Name '{pkg_name}').State"],
                capture_output=True, text=True, timeout=30
            )
            return r.returncode == 0 and "Installed" in r.stdout
        except Exception:
            return False

    def _install_silent(pkg_name):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Add-WindowsCapability -Online -Name {pkg_name}"],
                capture_output=True, text=True, timeout=120
            )
            return r.returncode == 0
        except Exception:
            return False

    def _install_elevated(pkg_name, desc):
        ps = (
            f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
            f"-ArgumentList '-NoProfile -Command "
            f"\\\"Add-WindowsCapability -Online -Name {pkg_name}; exit\\\"'"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=180
            )
            return r.returncode == 0
        except Exception:
            return False

    # 逐个检查并安装
    missing = [(n, d) for n, d in needed if not _check_installed(n)]
    if not missing:
        logger.info("OCR语言包已全部安装")
        _ocr_lang_installed = True
        return True

    # 先静默尝试
    all_ok = True
    for pkg_name, desc in missing:
        logger.info(f"{desc}语言包未安装，静默安装...")
        if _install_silent(pkg_name):
            logger.info(f"{desc}语言包安装成功")
        else:
            all_ok = False
            break

    if all_ok:
        _ocr_lang_installed = True
        return True

    # 静默失败 → GUI提示 + UAC弹窗
    if glog:
        glog("需要安装Windows OCR语言包，请在弹出的权限确认窗口点击'是'")

    for pkg_name, desc in missing:
        if _check_installed(pkg_name):
            continue
        logger.info(f"{desc}语言包提权安装（将弹出UAC窗口）...")
        if _install_elevated(pkg_name, desc):
            logger.info(f"{desc}语言包提权安装成功")
        else:
            logger.error(f"{desc}语言包安装失败")
            if glog:
                glog(f"OCR语言包安装失败，请以管理员运行PowerShell执行: "
                     f"Add-WindowsCapability -Online -Name {pkg_name}")
            return False

    _ocr_lang_installed = True
    return True


# ---------- OCR 辅助函数 ----------

def _screenshot_region(left, top, right, bottom):
    """截取屏幕指定区域，返回 PIL.Image（mss 截图，DPI 感知）"""
    import mss
    from PIL import Image

    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None

    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": width, "height": height}
        sct_img = sct.grab(monitor)
        return Image.frombytes("RGB", (width, height), sct_img.bgra, "raw", "BGRX")


def _ocr_find_text(image, target_text, region_left=0, region_top=0, glog=None):
    """OCR 识别图片，查找目标文字，返回屏幕绝对坐标列表 [(cx, cy, text), ...]"""
    from winocr import recognize_pil_sync

    _ensure_chinese_ocr(glog=glog)

    try:
        result = recognize_pil_sync(image)
    except Exception as e:
        logger.warning(f"winocr 识别失败: {e}")
        return []

    # recognize_pil_sync 返回 dict: {'text': ..., 'lines': [{'text':..., 'words':[...]}, ...]}
    lines = result.get("lines", []) if isinstance(result, dict) else getattr(result, "lines", [])
    matches = []
    for line in lines:
        # line 可能是 dict 或对象
        line_text = line.get("text", "") if isinstance(line, dict) else getattr(line, "text", "")
        if target_text in line_text:
            # 用第一个 word 的 bounding_rect 定位
            words = line.get("words", []) if isinstance(line, dict) else getattr(line, "words", [])
            if words:
                w = words[0]
                if isinstance(w, dict):
                    br = w.get("bounding_rect", {})
                    wx, wy, ww, wh = br.get("x", 0), br.get("y", 0), br.get("width", 0), br.get("height", 0)
                else:
                    br = getattr(w, "bounding_rect", None)
                    if br:
                        wx, wy, ww, wh = getattr(br, "x", 0), getattr(br, "y", 0), getattr(br, "width", 0), getattr(br, "height", 0)
                    else:
                        wx = wy = ww = wh = 0
                cx = region_left + wx + ww // 2
                cy = region_top + wy + wh // 2
            else:
                cx = cy = 0
            matches.append((cx, cy, line_text))
    return matches


def _ocr_contains_text(image, target_text, glog=None):
    """OCR 识别图片，检查是否包含目标文字"""
    from winocr import recognize_pil_sync

    _ensure_chinese_ocr(glog=glog)

    try:
        result = recognize_pil_sync(image)
    except Exception as e:
        logger.warning(f"winocr 识别失败: {e}")
        return False

    full_text = result.get("text", "") if isinstance(result, dict) else getattr(result, "text", "")
    return target_text in full_text


def _mouse_click(x, y):
    """移动鼠标到屏幕绝对坐标并左键点击"""
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.03)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    time.sleep(0.05)


class WeChatController:
    """微信控制器，封装所有自动化操作"""

    # 微信进程名
    WECHAT_PROCESS = "Weixin.exe"
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
        OCR 识别搜索下拉框中的"网络查找手机/QQ号"并点击
        截图搜索框下方区域 → winocr 识别 → 鼠标点击文字中心
        """
        def _glog(msg, level="info"):
            if level == "info":
                logger.info(msg)
            elif level == "warn":
                logger.warning(msg)
            elif level == "error":
                logger.error(msg)
            if self._gui_log:
                self._gui_log(msg)

        time.sleep(1.5)

        rect = None
        if self.wechat_window:
            try:
                hwnd = self.wechat_window.NativeWindowHandle
                if hwnd:
                    r = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                    rect = (r.left, r.top, r.right, r.bottom)
                    _glog(f"微信窗口位置: left={r.left} top={r.top} "
                          f"right={r.right} bottom={r.bottom} "
                          f"({r.right-r.left}x{r.bottom-r.top})")
            except Exception as e:
                _glog(f"获取窗口位置异常: {e}", "error")

        if rect is None:
            _glog("无法获取微信窗口位置，无法截图下拉菜单", "error")
            return False

        win_left, win_top, win_right, win_bottom = rect
        region = (win_left, win_top + 40, win_left + 400, win_top + 380)
        _glog(f"截取下拉区域1: {region}")

        img = _screenshot_region(*region)
        if img is None:
            _glog("截图下拉菜单失败", "error")
            return False

        _glog(f"截图成功 {img.width}x{img.height}，开始OCR识别...")

        matches = _ocr_find_text(img, "网络查找", region[0], region[1], glog=_glog)
        if matches:
            cx, cy, text = matches[0]
            _glog(f"OCR 找到下拉项: '{text}' → 点击 ({cx}, {cy})")
            if self.wechat_window:
                try:
                    wh = self.wechat_window.NativeWindowHandle
                    if wh:
                        ctypes.windll.user32.SetForegroundWindow(wh)
                        time.sleep(0.15)
                except Exception:
                    pass
            _mouse_click(cx, cy)
            time.sleep(2.0)
            return True

        _glog(f"第一区域未找到'网络查找'，扩大范围再试...")

        region2 = (win_left, win_top + 30, win_left + 500, win_top + 450)
        _glog(f"扩大截取区域2: {region2}")
        img2 = _screenshot_region(*region2)
        if img2 is not None:
            matches2 = _ocr_find_text(img2, "网络查找", region2[0], region2[1], glog=_glog)
            if matches2:
                cx, cy, text = matches2[0]
                _glog(f"OCR(扩区) 找到下拉项: '{text}' → 点击 ({cx}, {cy})")
                if self.wechat_window:
                    try:
                        wh = self.wechat_window.NativeWindowHandle
                        if wh:
                            ctypes.windll.user32.SetForegroundWindow(wh)
                            time.sleep(0.15)
                    except Exception:
                        pass
                _mouse_click(cx, cy)
                time.sleep(2.0)
                return True

        try:
            from winocr import recognize_pil_sync
            result = recognize_pil_sync(img)
            lines = result.get("lines", []) if isinstance(result, dict) else getattr(result, "lines", [])
            all_text = " | ".join([
                (ln.get("text", "") if isinstance(ln, dict) else getattr(ln, "text", ""))
                for ln in lines[:15]
            ])
            _glog(f"OCR识别到的全部文字({len(lines)}行): {all_text}", "warn")
        except Exception as e2:
            _glog(f"OCR调试输出异常: {e2}", "error")

        _glog("OCR 未找到下拉菜单中的'网络查找'项", "error")
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

            # OCR 识别弹窗区域，检查是否包含"添加到通讯录"
            has_add_button = False
            if popup_rect:
                try:
                    img = _screenshot_region(*popup_rect)
                    if img is not None:
                        has_add_button = _ocr_contains_text(img, "添加到通讯录", glog=_glog)
                        _glog(f"OCR弹窗检测: 截图{popup_rect[2]-popup_rect[0]}x{popup_rect[3]-popup_rect[1]} "
                              f"→ {'找到' if has_add_button else '未找到'}'添加到通讯录'")
                except Exception as e:
                    _glog(f"OCR弹窗检测异常: {e}")

            # 判断逻辑
            if has_add_button:
                _glog("弹窗: 有'添加到通讯录' → 正常")
                return ("normal", "(已识别按钮)")
            elif popup_rect:
                _glog("弹窗: 已打开但无'添加到通讯录' → 可能异常")
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
    ctrl = WeChatController(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
    print(f"微信运行中: {ctrl.is_wechat_running()}")
