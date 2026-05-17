"""
微信自动化操作模块
基于 uiautomation 操控微信 PC 客户端
"""
import ctypes
import time
import random
import subprocess
import os
import threading
from contextlib import contextmanager

from logger_setup import logger


# ---------- 尝试导入 uiautomation ----------
UIA_AVAILABLE = False
try:
    import uiautomation as auto
    UIA_AVAILABLE = True
except Exception:
    logger.warning("uiautomation 导入失败，将使用模拟方案", exc_info=True)


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
    """用 SendInput 逐字符输入 Unicode 文本，不依赖剪贴板。部分失败时抛 OSError"""
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
        raise OSError(f"SendInput 发送 {count} 个事件，仅成功 {sent} 个")


# ---------- Tesseract 路径 ----------

_tesseract_path = None


def _get_tesseract_path():
    """获取 Tesseract 可执行文件路径（优先用 PyInstaller 打包的版本）"""
    global _tesseract_path
    if _tesseract_path:
        return _tesseract_path

    import sys

    # PyInstaller 打包后，tesseract 在临时解压目录
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        candidate = os.path.join(base, "tesseract", "tesseract.exe")
        if os.path.exists(candidate):
            _tesseract_path = candidate
            return _tesseract_path

    # 源码运行时从 PATH 找
    _tesseract_path = "tesseract"
    return _tesseract_path


# ---------- OCR 辅助函数 ----------

# 保护 subprocess.Popen 补丁的锁，防止并发 OCR 调用时嵌套覆盖
_tesseract_lock = threading.Lock()


@contextmanager
def _no_console_popen():
    """上下文管理器：临时禁用 subprocess.Popen 的控制台窗口，防止抢焦点"""
    import sys
    if sys.platform != "win32":
        yield
        return

    with _tesseract_lock:
        original = subprocess.Popen
        def _no_window_popen(*args, **kwargs):
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= 0x08000000  # CREATE_NO_WINDOW
            return original(*args, **kwargs)
        subprocess.Popen = _no_window_popen
        try:
            yield
        finally:
            subprocess.Popen = original


def _preprocess_for_ocr(image, threshold=None):
    """图像预处理：放大2倍 + 灰度 + 锐化 + 可选二值化。

    threshold=None 时不二值化，直接送灰度图给 Tesseract（推荐，保留最多信息）。
    threshold 为整数时生效：白色背景+深色文字 → 黑白分明。
    """
    from PIL import Image, ImageOps, ImageFilter

    w, h = image.size
    image = image.resize((w * 2, h * 2), Image.LANCZOS)
    image = ImageOps.grayscale(image)
    image = image.filter(ImageFilter.SHARPEN)
    if threshold is not None:
        image = image.point(lambda p: 255 if p > threshold else 0)
    return image


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


def _ocr_get_text_entries(image):
    """OCR识别图片，返回 (条目列表, 分行列表, 全文拼接)。

    三通道预处理（逐级回退）：
    1. 不做二值化（Tesseract内部自适应，保留最多笔画信息）
    2. 阈值=100（处理高对比度屏幕）
    3. 阈值=140（兼容原始行为）
    """
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = _get_tesseract_path()
    scale = 0.5  # 坐标缩放（图片放大了2倍）

    def _parse_ocr_data(data):
        """从 Tesseract 结果解析条目列表"""
        entries = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            conf = data["conf"][i]
            if text and conf > 10:
                x = int(data["left"][i] * scale)
                y = int(data["top"][i] * scale)
                w = int(data["width"][i] * scale)
                h = int(data["height"][i] * scale)
                entries.append({"text": text, "x": x, "y": y, "w": w, "h": h, "conf": conf})
        return entries

    # 三级管道，任一成功即返回
    pipelines = [
        ("灰度直送(无二值化)", None, "--psm 6"),
        ("阈值100", 100, "--psm 6"),
        ("阈值140(兼容)", 140, "--psm 6"),
    ]

    for pipe_name, threshold, tesseract_config in pipelines:
        with _no_console_popen():
            processed = _preprocess_for_ocr(image, threshold=threshold)
            try:
                data = pytesseract.image_to_data(
                    processed, lang="chi_sim", output_type=pytesseract.Output.DICT,
                    config=tesseract_config,
                )
            except Exception as e:
                logger.warning(f"Tesseract OCR 失败({pipe_name}): {e}")
                continue

        entries = _parse_ocr_data(data)
        if entries:
            logger.debug(f"OCR 通道成功: {pipe_name}, {len(entries)} 个条目")

            # 按 Y 坐标分组（容差 10px 内视为同一行）
            entries.sort(key=lambda e: e["y"])
            rows = []
            current_row = [entries[0]]
            for e in entries[1:]:
                if abs(e["y"] - current_row[-1]["y"]) <= 10:
                    current_row.append(e)
                else:
                    rows.append(current_row)
                    current_row = [e]
            rows.append(current_row)

            # 全文拼接
            all_entries = []
            for row in rows:
                all_entries.extend(row)
            all_entries.sort(key=lambda e: (e["y"], e["x"]))
            full_text = "".join(e["text"] for e in all_entries)

            return entries, rows, full_text

    return [], [], ""


def _find_text_in_entries(entries, rows, target_text):
    """在OCR条目中定位目标文字，返回匹配条目的起止信息。

    返回 (first_entry, last_entry) 或 (None, None)。

    三级匹配策略：
    1. 逐条目精确匹配 (conf > 15)
    2. 同行拼接回退（CEF字符间距导致单字被拆分）
    3. 全文拼接兜底
    """
    # ---- 第1步：逐条目精确匹配 ----
    for e in entries:
        if target_text in e["text"] and e["conf"] > 15:
            return e, e

    # ---- 第2步：同行拼接回退 ----
    for row in rows:
        row.sort(key=lambda e: e["x"])
        merged = "".join(e["text"] for e in row)
        idx = merged.find(target_text)
        if idx == -1:
            continue

        pos = 0
        first_entry = None
        last_entry = None
        for e in row:
            e_start = pos
            e_end = pos + len(e["text"])
            if e_start < idx + len(target_text) and e_end > idx:
                if first_entry is None:
                    first_entry = e
                last_entry = e
            pos = e_end

        if first_entry and last_entry:
            return first_entry, last_entry

    # ---- 第3步：全文拼接兜底 ----
    all_entries = []
    for row in rows:
        all_entries.extend(row)
    all_entries.sort(key=lambda e: (e["y"], e["x"]))
    full_text = "".join(e["text"] for e in all_entries)
    idx = full_text.find(target_text)
    if idx != -1:
        pos = 0
        first_entry = None
        last_entry = None
        for e in all_entries:
            e_start = pos
            e_end = pos + len(e["text"])
            if e_start < idx + len(target_text) and e_end > idx:
                if first_entry is None:
                    first_entry = e
                last_entry = e
            pos = e_end
        if first_entry and last_entry:
            return first_entry, last_entry

    return None, None


def _ocr_find_text(image, target_text, region_left=0, region_top=0, glog=None):
    """OCR 识别图片，查找目标文字，返回屏幕绝对坐标列表 [(cx, cy, text), ...]"""
    entries, rows, _full_text = _ocr_get_text_entries(image)

    if not entries:
        return []

    first, last = _find_text_in_entries(entries, rows, target_text)
    if first and last:
        cx = region_left + (first["x"] + last["x"] + last["w"]) // 2
        cy = region_top + (first["y"] + first["h"] // 2)
        if glog:
            for row in rows:
                merged = "".join(e["text"] for e in row)
                if target_text in merged:
                    glog(f"同行拼接匹配: '{target_text}' 在 '{merged[:30]}' 中")
                    break
            else:
                glog(f"全文拼接匹配: '{target_text}'")
        return [(cx, cy, target_text)]

    return []


def _ocr_contains_text(image, target_text, glog=None):
    """OCR 识别图片，检查是否包含目标文字（支持CEF字符间距拆分）"""
    entries, rows, _full_text = _ocr_get_text_entries(image)

    if not entries:
        return False

    first, last = _find_text_in_entries(entries, rows, target_text)
    if first and last:
        return True

    # 未找到时输出诊断信息
    if glog:
        all_entries = []
        for row in rows:
            all_entries.extend(row)
        glog(f"OCR未找到'{target_text}'，识别到的文字({len(all_entries)}条): "
             f"{' | '.join(e['text'] for e in all_entries[:20])}")

    return False


def _mouse_click(x, y):
    """在屏幕绝对坐标执行真实鼠标点击。

    使用 SetCursorPos + mouse_event 产生硬件级鼠标事件。
    微信 CEF 下拉菜单不响应 PostMessage/SendMessage 窗口消息，
    必须用真实鼠标事件才能触发 CEF 内部的点击处理。
    """
    # 保存当前鼠标位置，点击后恢复
    orig = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(orig))

    try:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.03)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.05)
    finally:
        # 恢复鼠标位置
        ctypes.windll.user32.SetCursorPos(orig.x, orig.y)


class WeChatController:
    """微信控制器，封装所有自动化操作"""

    # 微信进程名
    WECHAT_PROCESS = "Weixin.exe"
    # 微信主窗口标题可能包含的文字
    WECHAT_WINDOW_TITLE = "微信"

    def __init__(self, wechat_path):
        self.wechat_path = wechat_path
        self.wechat_window = None
        self.main_control = None
        self._gui_log = None     # GUI 日志回调，由引擎注入
        self._stop_event = None  # 停止信号，由引擎注入

    def set_stop_event(self, event):
        """注入停止信号，用于中断长时间等待"""
        self._stop_event = event

    def _sleep(self, seconds):
        """可中断的等待：检查停止信号，被停止时提前返回"""
        if self._stop_event is None:
            time.sleep(seconds)
            return
        self._stop_event.wait(seconds)

    def _emit_log(self, msg, level="info"):
        """同时写 logger 和 GUI 回调（取代各处重复的 _glog 闭包）"""
        if level == "info":
            logger.info(msg)
        elif level == "warn":
            logger.warning(msg)
        elif level == "error":
            logger.error(msg)
        if self._gui_log:
            self._gui_log(msg)

    # ==================== 窗口管理 ====================

    def is_wechat_running(self):
        """检查微信是否在运行 — tasklist 优先（快），psutil 兜底（慢但可靠）"""
        # 方法1: tasklist 精确匹配（最快，通常 <1s）
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.WECHAT_PROCESS}"],
                capture_output=True, text=True, timeout=3
            )
            if self.WECHAT_PROCESS in result.stdout:
                logger.debug("通过 tasklist 检测到微信进程运行中")
                return True
        except Exception as e:
            logger.debug(f"tasklist 检查异常: {e}")

        # 方法2: psutil 进程遍历（较慢但更可靠）
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

        # 方法3: uiautomation 查找窗口（兜底）
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
        将微信窗口置前激活。
        首次搜索成功后缓存窗口句柄，后续调用直接用 Win32 API 激活（秒级）。
        """
        if not UIA_AVAILABLE:
            self._emit_log("uiautomation 不可用，无法操作微信窗口", "error")
            return False

        # 已有缓存窗口，直接用 Win32 API 激活
        if self.wechat_window is not None:
            try:
                hwnd = self.wechat_window.NativeWindowHandle
                if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.BringWindowToTop(hwnd)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    self._sleep(0.3)
                    return True
            except Exception:
                pass
            # 窗口已失效，清空缓存重新搜索
            self.wechat_window = None

        try:
            window = None
            self._emit_log("正在定位微信窗口...")

            for class_name in ["WeChatMainWndForPC", "ChatWnd", "MainWindow"]:
                try:
                    w = auto.WindowControl(searchDepth=1, ClassName=class_name)
                    if w.Exists(maxSearchSeconds=0.5):
                        window = w
                        logger.debug(f"通过类名找到微信窗口: {class_name}")
                        break
                except Exception:
                    continue

            if window is None:
                try:
                    w = auto.WindowControl(searchDepth=1, Name="微信")
                    if w.Exists(maxSearchSeconds=0.5):
                        window = w
                        logger.debug("通过标题找到微信窗口")
                except Exception:
                    pass

            if window is None:
                self._emit_log("找不到微信主窗口（类名可能已变化）", "error")
                return False

            # 用 Win32 API 强制激活微信窗口为前台窗口
            hwnd = window.NativeWindowHandle
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.BringWindowToTop(hwnd)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                self._sleep(0.3)
            else:
                window.SetActive()
                self._sleep(0.3)

            self.wechat_window = window
            logger.debug("微信窗口已激活为前台窗口")
            return True

        except Exception as e:
            self._emit_log(f"激活微信窗口失败: {e}", "error")
            return False

    # ==================== 搜索操作 ====================

    def focus_search_box(self):
        """
        按 Ctrl+F 激活搜索框
        用 Win32 keybd_event，不依赖 uiautomation COM 线程
        """
        if not UIA_AVAILABLE:
            self._emit_log("uiautomation 不可用", "error")
            return False

        try:
            _send_hotkey(_VK["ctrl"], _VK["f"])
            self._sleep(1.5)
            logger.debug("已按下 Ctrl+F 激活搜索框")
            return True
        except Exception as e:
            self._emit_log(f"激活搜索框失败: {e}", "error")
            return False

    def input_wechat_id(self, wechat_id):
        """
        在搜索框中输入微信号
        方法A: SendInput 逐字符 → 方法B: 剪贴板 + Ctrl+V（base64 安全编码）
        """
        if not UIA_AVAILABLE:
            return False

        # 清空搜索框已有内容
        _send_hotkey(_VK["ctrl"], _VK["a"])
        self._sleep(0.2)
        _press_key(_VK["delete"])
        self._sleep(0.2)

        # 方法A: SendInput 逐字符输入
        try:
            _type_text(wechat_id)
            self._sleep(0.3)
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
            self._sleep(0.2)
            _send_hotkey(_VK["ctrl"], _VK["v"])
            self._sleep(0.3)
            logger.debug(f"已通过剪贴板粘贴微信号: {wechat_id}")
            return True
        except Exception as e2:
            self._emit_log(f"所有输入方式均失败: SendInput={e}, 剪贴板={e2}", "error")
            return False

    def click_dropdown_item(self):
        """
        OCR 识别搜索下拉框中的"网络查找手机/QQ号"并点击
        截图搜索框下方区域 → Tesseract 识别 → 鼠标点击文字中心
        """
        self._sleep(1.5)

        hwnd = None
        rect = None
        if self.wechat_window:
            try:
                hwnd = self.wechat_window.NativeWindowHandle
                if hwnd:
                    r = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                    rect = (r.left, r.top, r.right, r.bottom)
                    self._emit_log(f"微信窗口位置: left={r.left} top={r.top} "
                                   f"right={r.right} bottom={r.bottom} "
                                   f"({r.right-r.left}x{r.bottom-r.top})")
            except Exception as e:
                self._emit_log(f"获取窗口位置异常: {e}", "error")

        if rect is None:
            self._emit_log("无法获取微信窗口位置，无法截图下拉菜单", "error")
            return False

        win_left, win_top, win_right, win_bottom = rect

        def _try_click_dropdown(region, label):
            """尝试截取指定区域并 OCR 识别点击，支持多级匹配回退"""
            self._emit_log(f"截取下拉区域{label}: {region}")
            img = _screenshot_region(*region)
            if img is None:
                self._emit_log("截图下拉菜单失败", "error")
                return False
            self._emit_log(f"截图成功 {img.width}x{img.height}，开始OCR识别...")

            # 多级匹配：从最精确到最宽松
            for target in ("网络查找", "络找"):
                matches = _ocr_find_text(img, target, region[0], region[1], glog=self._emit_log)
                if matches:
                    cx, cy, text = matches[0]
                    self._emit_log(f"OCR 找到下拉项('{target}'): '{text}' → 后台点击 ({cx}, {cy})")
                    if hwnd:
                        try:
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                            time.sleep(0.15)
                        except Exception:
                            pass
                    _mouse_click(cx, cy)
                    self._sleep(2.0)
                    return True
            return False

        # 按窗口比例计算截图区域（适配不同DPI/分辨率/窗口大小）
        win_w = win_right - win_left
        win_h = win_bottom - win_top

        # 第1次尝试：搜索框正下方（约占窗口宽78%、高64%）
        region1 = (win_left + int(win_w * 0.02), win_top + int(win_h * 0.08),
                   win_left + int(win_w * 0.78), win_top + int(win_h * 0.72))
        if _try_click_dropdown(region1, "1"):
            return True

        # 第2次尝试：扩大范围（约占窗口宽85%、高72%）
        region2 = (win_left, win_top + int(win_h * 0.06),
                   win_left + int(win_w * 0.85), win_top + int(win_h * 0.78))
        if _try_click_dropdown(region2, "2(扩区)"):
            return True

        # 均失败：诊断输出
        try:
            diag_img = _screenshot_region(*region1)
            if diag_img is None:
                diag_img = _screenshot_region(*region2)
            if diag_img is not None:
                entries, rows, full_text = _ocr_get_text_entries(diag_img)
                if entries:
                    all_text = " | ".join(e["text"] for e in entries[:15])
                    self._emit_log(f"OCR识别到的全部文字({len(entries)}条): {all_text}", "warn")
        except Exception as e2:
            self._emit_log(f"OCR调试输出异常: {e2}", "error")

        self._emit_log("OCR 未找到下拉菜单中的'网络查找'项", "error")
        return False

    # ==================== 弹窗检测 ====================

    def check_popup_status(self):
        """
        检查弹出的"添加朋友"窗口
        OCR 识别弹窗内是否有"添加到通讯录"按钮来判断账号状态。
        两级搜索弹窗：独立窗口 → 主窗口内面板，均未找到则直接报 not_found。

        返回:
            ("normal", nickname) — 正常
            ("abnormal", reason) — 异常
            ("not_found", "")     — 未找到弹窗（点击可能未生效）
        """
        if not UIA_AVAILABLE:
            return ("not_found", "")

        try:
            # 等待弹窗出现（CEF 面板加载较慢）
            self._sleep(2.0)

            # 查找弹窗 — 两级搜索：独立窗口 + 主窗口内面板
            popup = None

            # 第1级：搜索较深层的独立窗口（searchDepth=3）
            popup_titles = ["添加朋友", "详细信息", "联系人", "朋友验证", "新的朋友"]
            for title in popup_titles:
                try:
                    w = auto.WindowControl(Name=title, searchDepth=3)
                    if w.Exists(maxSearchSeconds=0.8):
                        popup = w
                        self._emit_log(f"找到弹窗(深层窗口): {title}")
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
                            self._emit_log(f"找到弹窗(主窗口内面板): {title}")
                            break
                    except Exception:
                        continue

            # 第1-2级均未找到弹窗，说明点击未生效，直接返回
            if popup is None:
                self._emit_log("未找到弹窗，点击可能未生效")
                return ("not_found", "")

            # 不激活弹窗，避免抢前台
            self._sleep(0.5)

            # 获取弹窗屏幕位置范围（优先 Win32 API，更可靠）
            popup_rect = None
            popup_hwnd = None
            try:
                popup_hwnd = popup.NativeWindowHandle
                if popup_hwnd:
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(popup_hwnd, ctypes.byref(rect))
                    popup_rect = (rect.left, rect.top, rect.right, rect.bottom)
                    self._emit_log(f"弹窗位置(Win32): left={popup_rect[0]} top={popup_rect[1]} "
                                   f"right={popup_rect[2]} bottom={popup_rect[3]}")
            except Exception:
                pass

            if popup_rect is None:
                try:
                    popup_rect = popup.BoundingRectangle
                    self._emit_log(f"弹窗位置(UIA): {popup_rect}")
                except Exception as e:
                    self._emit_log(f"无法获取弹窗位置: {e}")

            # OCR 识别弹窗区域，检查是否包含"添加到通讯录"
            has_add_button = False
            if popup_rect:
                try:
                    img = _screenshot_region(*popup_rect)
                    if img is not None:
                        has_add_button = _ocr_contains_text(img, "添加到通讯录", glog=self._emit_log)
                        self._emit_log(f"OCR弹窗检测: 截图{popup_rect[2]-popup_rect[0]}x{popup_rect[3]-popup_rect[1]} "
                                       f"→ {'找到' if has_add_button else '未找到'}'添加到通讯录'")
                except Exception as e:
                    self._emit_log(f"OCR弹窗检测异常: {e}")

            # 判断逻辑
            if has_add_button:
                self._emit_log("弹窗: 有'添加到通讯录' → 正常")
                return ("normal", "(已识别按钮)")
            elif popup_rect:
                self._emit_log("弹窗: 已打开但无'添加到通讯录' → 可能异常")
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
                        self._sleep(0.2)
                except Exception:
                    pass

            # 发送 ESC 关闭弹窗
            _press_key(_VK["escape"])
            self._sleep(0.5)
            logger.debug("已关闭弹窗(ESC)")
        except Exception as e:
            logger.error(f"关闭弹窗失败: {e}")

    def clear_search(self):
        """清空搜索框"""
        if not UIA_AVAILABLE:
            return
        try:
            _send_hotkey(_VK["ctrl"], _VK["a"])
            self._sleep(0.2)
            _press_key(_VK["delete"])
            self._sleep(0.3)
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

        self._sleep(0.3)

        # 2. 聚焦搜索框
        if not self.focus_search_box():
            return ("error", "无法聚焦搜索框")

        self._sleep(0.5)

        # 3. 输入微信号
        if not self.input_wechat_id(wechat_id):
            return ("error", "无法输入微信号")

        self._sleep(0.5)

        # 4. 点击下拉项
        if not self.click_dropdown_item():
            self.close_popup()
            self.clear_search()
            return ("abnormal", "搜索无结果或无法点开详情")

        # OCR/点击期间用户可能点了停止，立即退出
        if self._stop_event and self._stop_event.is_set():
            return ("error", "用户停止")

        # 5. 检测弹窗状态
        status, detail = self.check_popup_status()

        # OCR/UIA 搜索期间用户可能点了停止
        if self._stop_event and self._stop_event.is_set():
            self.close_popup()
            self.clear_search()
            return ("error", "用户停止")

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
