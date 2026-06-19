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
            finally:
                processed.close()

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


def _mouse_click(x, y, hold=False):
    """在屏幕绝对坐标执行真实鼠标点击。

    使用 SetCursorPos + mouse_event 产生硬件级鼠标事件。
    微信 CEF 下拉菜单不响应 PostMessage/SendMessage 窗口消息。

    CEF 需要时间处理 WM_MOUSEMOVE 才能确定光标在哪个 DOM 元素上。
    SetCursorPos 之后必须等足够多帧（150ms≈9帧），否则 mousedown 可能命中空白区。
    hold=True 时延迟0.5s后恢复光标（让CEF处理弹窗触发），不即刻恢复也不永远停留。
    """
    orig = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(orig))

    try:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.15)  # 等 CEF 处理光标移动（9帧），确定光标下的 DOM 元素
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.08)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        if hold:
            time.sleep(0.5)  # 停留让 CEF 处理弹窗触发
    finally:
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
        self._pause_event = None # 暂停信号，由引擎注入
        self._last_window_rect = None  # 上一次窗口位置尺寸，用于检测变化
        self._uia_timeout_count = 0     # UIA 超时计数器

    def set_stop_event(self, event):
        """注入停止信号，用于中断长时间等待"""
        self._stop_event = event

    def set_pause_event(self, event):
        """注入暂停信号，用于立即暂停当前检查"""
        self._pause_event = event

    def _sleep(self, seconds):
        """可中断的等待：每0.1s检查停止/暂停信号，即使短延迟也能立即响应"""
        interval = 0.1
        elapsed = 0.0
        while elapsed < seconds:
            if self._stop_event and self._stop_event.is_set():
                return
            if self._pause_event and self._pause_event.is_set():
                time.sleep(0.1)  # Event已set，wait()不阻塞，用sleep防空转
                continue
            remaining = seconds - elapsed
            time.sleep(min(interval, remaining))
            elapsed += min(interval, remaining)

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

    def _safe_uia_exists(self, control, hard_timeout=3.0, label=""):
        """在独立线程中运行 control.Exists()，加硬超时。

        UIA Exists() 的 maxSearchSeconds 依赖 COM RPC 消息分发，若微信 CEF
        界面线程不响应，COM RPC 会永久阻塞。本方法用独立线程执行 Exists()
        并用 join(timeout) 做硬时限——超时未返回即判为 COM 死锁，放弃线程。

        Returns True if control was found within hard_timeout, False otherwise.
        """
        import threading as _thr
        result = [False]
        done = [False]

        def _uia_thread():
            try:
                result[0] = control.Exists(maxSearchSeconds=hard_timeout)
            except Exception:
                pass
            finally:
                done[0] = True

        t = _thr.Thread(target=_uia_thread, daemon=True)
        t.start()
        t.join(timeout=hard_timeout + 1.0)

        if done[0]:
            return result[0]

        self._uia_timeout_count += 1
        logger.warning(
            f"_safe_uia_exists 超时 ({hard_timeout}s): {label or control}，"
            f"UIA/COM 可能死锁，放弃本次搜索 (累计{self._uia_timeout_count}次)"
        )
        if self._uia_timeout_count >= 20:
            logger.error(
                f"_safe_uia_exists 累计超时 {self._uia_timeout_count} 次，"
                "建议重启程序以释放泄漏的COM资源"
            )
        return False

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
                        if self._safe_uia_exists(window, hard_timeout=1.0, label=f"is_wechat_running:{class_name}"):
                            logger.debug(f"通过 uiautomation 类名 {class_name} 检测到微信窗口")
                            return True
                    except Exception:
                        continue
                try:
                    window = auto.WindowControl(searchDepth=1, Name="微信")
                    if self._safe_uia_exists(window, hard_timeout=1.0, label="is_wechat_running:微信"):
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
        Win32 FindWindowW 定位 → ControlFromHandle 转 UIA 控件（快速可靠）。
        首次成功后缓存窗口句柄，后续调用直接用 Win32 API 激活。
        """
        if not UIA_AVAILABLE:
            self._emit_log("uiautomation 不可用，无法操作微信窗口", "error")
            return False

        SW_RESTORE = 9   # ShowWindow 恢复最小化窗口
        SW_SHOW = 5      # ShowWindow 显示隐藏窗口（托盘）

        # 已有缓存窗口
        if self.wechat_window is not None:
            try:
                hwnd = self.wechat_window.NativeWindowHandle
                if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                    # 已经在最前，直接返回
                    if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                        return True
                    # 窗口最小化或隐藏时必须 ShowWindow，否则 SetForegroundWindow 无效
                    if ctypes.windll.user32.IsIconic(hwnd):
                        self._emit_log("微信窗口已最小化，正在恢复...", "warn")
                        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                        self._sleep(0.3)
                    elif not ctypes.windll.user32.IsWindowVisible(hwnd):
                        self._emit_log("微信窗口已隐藏(托盘)，正在显示...", "warn")
                        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
                        self._sleep(0.3)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    self._sleep(0.1)
                    return True
                # 句柄失效，清缓存重新搜索
                self.wechat_window = None
            except Exception:
                self.wechat_window = None

        try:
            window = None
            self._emit_log("正在定位微信窗口...")

            # 优先用 Win32 FindWindowW（快，无 COM 依赖，不会卡死）
            hwnd = None
            for cls in ["WeChatMainWndForPC", "ChatWnd", "MainWindow"]:
                hwnd = ctypes.windll.user32.FindWindowW(cls, None)
                if hwnd:
                    logger.debug(f"FindWindowW 通过类名找到: {cls}")
                    break
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW(None, "微信")
                if hwnd:
                    logger.debug("FindWindowW 通过标题找到微信窗口")

            if hwnd:
                try:
                    window = auto.ControlFromHandle(hwnd)
                except Exception:
                    pass

            # Win32 未找到则回退到 UIA 搜索
            if window is None:
                for class_name in ["WeChatMainWndForPC", "ChatWnd", "MainWindow"]:
                    try:
                        w = auto.WindowControl(searchDepth=1, ClassName=class_name)
                        if self._safe_uia_exists(w, hard_timeout=1.0, label=f"activate_window:{class_name}"):
                            window = w
                            logger.debug(f"UIA 通过类名找到: {class_name}")
                            break
                    except Exception:
                        continue

                if window is None:
                    try:
                        w = auto.WindowControl(searchDepth=1, Name="微信")
                        if self._safe_uia_exists(w, hard_timeout=1.0, label="activate_window:微信"):
                            window = w
                            logger.debug("UIA 通过标题找到微信窗口")
                    except Exception:
                        pass

            if window is None:
                self._emit_log("找不到微信主窗口（微信是否已登录且未最小化到托盘？）", "error")
                return False

            # Win32 API 激活（禁止无条件 ShowWindow/BringWindowToTop，仅最小化/隐藏时恢复）
            hwnd = window.NativeWindowHandle
            if hwnd:
                if ctypes.windll.user32.IsIconic(hwnd):
                    self._emit_log("微信窗口已最小化，正在恢复...", "warn")
                    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                    self._sleep(0.3)
                elif not ctypes.windll.user32.IsWindowVisible(hwnd):
                    self._emit_log("微信窗口已隐藏(托盘)，正在显示...", "warn")
                    ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
                    self._sleep(0.3)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                self._sleep(0.1)
                self.wechat_window = window
                return True
            else:
                window.SetActive()
                self._sleep(0.3)

            self.wechat_window = window
            return True

        except Exception as e:
            self._emit_log(f"激活微信窗口失败: {e}", "error")
            return False

    def _ensure_foreground(self):
        """确保微信窗口在前台，不在则尝试激活"""
        if not self.wechat_window:
            return False
        try:
            hwnd = self.wechat_window.NativeWindowHandle
            if hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
        self._emit_log("微信窗口不在前台，尝试重新激活...", "warn")
        return self.activate_window()

    # ==================== 搜索操作 ====================

    def focus_search_box(self):
        """
        按 Ctrl+F 激活搜索框。先确认窗口在前台，再发快捷键。
        用 Win32 keybd_event，不依赖 uiautomation COM 线程
        """
        if not UIA_AVAILABLE:
            self._emit_log("uiautomation 不可用", "error")
            return False

        try:
            # 确保微信窗口在前台，否则 Ctrl+F 发到错误窗口
            if not self._ensure_foreground():
                self._emit_log("无法将微信窗口置顶，放弃聚焦搜索框", "error")
                return False

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

        # 确保微信窗口在前台
        if not self._ensure_foreground():
            self._emit_log("无法将微信窗口置顶，放弃输入", "error")
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
        OCR 识别搜索下拉框中的"网络查找手机/QQ号"并点击。
        每次截图只做一次 OCR，多个关键词共用结果（之前每个关键词都重新 OCR）。
        同区域两次截图重试：第1次等2s，失败再等1.5s（应对下拉加载慢）。
        """
        self._sleep(2.0)  # 等待下拉菜单加载

        hwnd = None
        rect = None
        if self.wechat_window:
            try:
                hwnd = self.wechat_window.NativeWindowHandle
                if hwnd:
                    r = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                    rect = (r.left, r.top, r.right, r.bottom)
                    win_w = r.right - r.left
                    win_h = r.bottom - r.top
                    self._emit_log(f"微信窗口位置: left={r.left} top={r.top} "
                                   f"right={r.right} bottom={r.bottom} "
                                   f"({win_w}x{win_h})")
                    if self._last_window_rect:
                        old_w = self._last_window_rect[2] - self._last_window_rect[0]
                        old_h = self._last_window_rect[3] - self._last_window_rect[1]
                        if old_w > 0 and old_h > 0:
                            w_change = abs(win_w - old_w) / old_w
                            h_change = abs(win_h - old_h) / old_h
                            if w_change > 0.2 or h_change > 0.2:
                                self._emit_log(
                                    f"⚠ 窗口尺寸变化: {old_w}x{old_h} → {win_w}x{win_h} "
                                    f"(宽{w_change:.0%} 高{h_change:.0%})，可能影响OCR", "warn")
                    self._last_window_rect = rect
            except Exception as e:
                self._emit_log(f"获取窗口位置异常: {e}", "error")

        if rect is None:
            self._emit_log("无法获取微信窗口位置，无法截图下拉菜单", "error")
            return False

        win_left, win_top, win_right, win_bottom = rect
        win_w = win_right - win_left
        win_h = win_bottom - win_top
        region = (win_left + int(win_w * 0.02), win_top + int(win_h * 0.08),
                  win_left + int(win_w * 0.78), win_top + int(win_h * 0.72))

        for attempt in range(2):
            if attempt > 0:
                self._emit_log("下拉菜单未出现，等待1.5秒后重试...")
                self._sleep(1.5)

            self._emit_log(f"截取下拉区域{attempt+1}: {region}")
            img = _screenshot_region(*region)
            if img is None:
                self._emit_log("截图下拉菜单失败", "error")
                continue
            self._emit_log(f"截图成功 {img.width}x{img.height}，开始OCR识别...")
            t_ocr_start = time.time()
            try:
                # 一次 OCR，多个关键词共用结果（之前每个关键词都重新OCR，浪费）
                entries, rows, full_text = _ocr_get_text_entries(img)
                if not entries:
                    continue

                for target in ("网络查找", "QQ号", "络找", "查找", "络查"):
                    first, last = _find_text_in_entries(entries, rows, target)
                    if first and last:
                        cx = region[0] + (first["x"] + last["x"] + last["w"]) // 2
                        cy = region[1] + (first["y"] + first["h"] // 2)
                        # 同行拼接日志
                        for row in rows:
                            merged = "".join(e["text"] for e in row)
                            if target in merged:
                                self._emit_log(f"同行拼接匹配: '{target}' 在 '{merged[:30]}' 中")
                                break
                        self._emit_log(f"OCR 找到下拉项('{target}'): '{target}' → 后台点击 ({cx}, {cy}) "
                                       f"(OCR耗时{time.time()-t_ocr_start:.1f}秒)")
                        if hwnd:
                            try:
                                ctypes.windll.user32.SetForegroundWindow(hwnd)
                                time.sleep(0.15)
                            except Exception:
                                pass
                        _mouse_click(cx, cy, hold=True)
                        self._sleep(2.0)
                        return True

                # 所有关键词未匹配，记录诊断
                self._emit_log(f"OCR未匹配任何下拉关键词 ({len(entries)}条目) "
                               f"全文前30字: {full_text[:30]}", "warn")
            finally:
                img.close()

        # 两次均失败：诊断输出
        try:
            diag_img = _screenshot_region(*region)
            if diag_img is not None:
                try:
                    entries, rows, full_text = _ocr_get_text_entries(diag_img)
                    if entries:
                        all_text = " | ".join(e["text"] for e in entries[:15])
                        self._emit_log(f"OCR识别到的全部文字({len(entries)}条): {all_text}", "warn")
                finally:
                    diag_img.close()
        except Exception as e2:
            self._emit_log(f"OCR调试输出异常: {e2}", "error")

        self._emit_log("OCR 未找到下拉菜单中的'网络查找'项，可能窗口失焦", "error")
        return "retry"  # 返回特殊状态，触发重新激活+重新输入

    # ==================== 弹窗检测 ====================

    def check_popup_status(self, wechat_id=""):
        """
        检查弹出的"添加朋友"窗口
        OCR 识别弹窗内是否有"添加到通讯录"按钮来判断账号状态。
        两级搜索弹窗：独立窗口 → 主窗口内面板，均未找到则直接报 not_found。

        Args:
            wechat_id: 当前检查的微信号（用于诊断截图文件名）
        """
        if not UIA_AVAILABLE:
            return ("not_found", "")

        try:
            # 轮询等待弹窗（FindWindowW 快且稳定，UIA 对CEF弹窗100%超时不再用）
            popup = None
            for poll in range(3):
                if poll > 0:
                    self._sleep(0.5)

                popup_hwnd = ctypes.windll.user32.FindWindowW(None, "添加朋友")
                if popup_hwnd:
                    try:
                        popup = auto.ControlFromHandle(popup_hwnd)
                        self._emit_log(f"FindWindowW 找到弹窗: 添加朋友 (轮询{poll+1}/3)")
                        break
                    except Exception:
                        pass

                if poll < 2:
                    self._emit_log(f"弹窗未出现，第{poll+1}次重试...")

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

            # OCR 识别弹窗区域，检查状态特征文字
            # 两类关键字：正常账号特征 + 搜索限制提示
            # 多关键词回退 + 2次重试（弹窗可能有滑入动画，一次截图可能捕获过渡帧）
            has_add_button = False
            has_rate_limit = False
            hit_target = ""
            popup_targets = ("添加到通讯录", "添加到", "通讯录", "讯录", "加到", "到通")
            # 频繁限制关键字：完整短语优先，截断片段兜底（移除高风险的"稍后"单字）
            rate_limit_targets = (
                "操作频繁", "搜索频繁", "过于频繁", "请稍后再试",  # 完整短语
                "频繁请", "稍后再试", "频繁",                    # 截断片段兜底
            )
            if popup_rect:
                t_popup_ocr_start = time.time()
                for ocr_attempt in range(2):
                    if ocr_attempt > 0:
                        self._sleep(1.5)
                        self._emit_log(f"弹窗OCR第{ocr_attempt+1}次重试...")
                    try:
                        img = _screenshot_region(*popup_rect)
                        if img is not None:
                            try:
                                # 优先检测正常账号特征（添加到通讯录按钮）
                                for target in popup_targets:
                                    if _ocr_contains_text(img, target, glog=self._emit_log):
                                        has_add_button = True
                                        hit_target = target
                                        break
                                if has_add_button:
                                    break

                                # 没有按钮，再检测"频繁"限制提示
                                for target in rate_limit_targets:
                                    if _ocr_contains_text(img, target, glog=self._emit_log):
                                        has_rate_limit = True
                                        hit_target = target
                                        self._emit_log(f"检测到搜索限制提示: '{target}'")
                                        break
                                if has_rate_limit:
                                    break

                                # 最终失败时保存诊断截图，方便跨轮对比排查
                                if ocr_attempt == 1:
                                    try:
                                        import tempfile
                                        debug_dir = os.path.join(tempfile.gettempdir(), "wechat_ocr_debug")
                                        os.makedirs(debug_dir, exist_ok=True)
                                        ts = int(time.time())
                                        safe_id = wechat_id.replace("/", "_").replace("\\", "_") if wechat_id else "unknown"
                                        filename = f"popup_fail_{safe_id}_{ts}.png"
                                        img.save(os.path.join(debug_dir, filename))
                                        self._emit_log(f"诊断截图已保存: {debug_dir}\\{filename}")
                                    except Exception:
                                        pass
                            finally:
                                img.close()
                    except Exception as e:
                        self._emit_log(f"OCR弹窗检测异常(第{ocr_attempt+1}次): {e}")
                self._emit_log(f"OCR弹窗检测: 截图{popup_rect[2]-popup_rect[0]}x{popup_rect[3]-popup_rect[1]} "
                               f"→ {'正常' if has_add_button else ('频繁限制' if has_rate_limit else '异常')}"
                               f" (OCR耗时{time.time()-t_popup_ocr_start:.1f}秒)")

            # 判断逻辑：正常 > 频繁 > 异常
            if has_add_button:
                self._emit_log("弹窗: 有'添加到通讯录' → 正常")
                return ("normal", "(已识别按钮)")
            elif has_rate_limit:
                self._emit_log("弹窗: 检测到'频繁'限制 → 搜索受限")
                return ("rate_limit", f"检测到'{hit_target}'")
            elif popup_rect:
                self._emit_log("弹窗: 已打开但无'添加到通讯录' → 可能异常")
                return ("abnormal", "按钮无文字")
            else:
                return ("abnormal", "弹窗未打开")

        except Exception as e:
            logger.error(f"检测弹窗状态时出错: {e}")
            return ("not_found", "")

    def close_popup(self):
        """关闭弹窗：ESC×3 → 鼠标点击备用。

        不再使用 UIA 搜索验证（几秒到几十秒延迟），信任 ESC 关闭。
        如果弹窗残留，下个号的 check_popup_status 会检测到并正确报告。
        """
        for attempt in range(3):
            hwnd = ctypes.windll.user32.FindWindowW(None, "添加朋友")
            if not hwnd:
                logger.debug("弹窗已关闭")
                return  # 弹窗没了，立即退出，避免ESC打中微信主窗口
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            self._sleep(0.1)
            _press_key(_VK["escape"])
            self._sleep(0.3)

        # 3次 ESC 都没关掉
        self._emit_log("ESC未关闭弹窗，尝试鼠标点击...", "warn")
        try:
            if self.wechat_window:
                hwnd_wx = self.wechat_window.NativeWindowHandle
                if hwnd_wx:
                    ctypes.windll.user32.SetForegroundWindow(hwnd_wx)
                    self._sleep(0.2)
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd_wx, ctypes.byref(rect))
                    click_x = rect.left + int((rect.right - rect.left) * 0.15)
                    click_y = rect.top + int((rect.bottom - rect.top) * 0.5)
                    _mouse_click(click_x, click_y)
                    self._sleep(0.3)
        except Exception as e:
            logger.warning(f"鼠标点击关闭弹窗失败: {e}")

        logger.debug("弹窗已关闭(鼠标点击)")

    def clear_search(self):
        """清空搜索框"""
        if not UIA_AVAILABLE:
            return

        # 确保微信窗口在前台
        if not self._ensure_foreground():
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

        try:
            # 1. 激活窗口
            if not self.activate_window():
                return ("error", "无法激活微信窗口")

            self._sleep(0.3)

            # 2-4. 聚焦搜索框 → 输入微信号 → 点击下拉项（支持重试）
            max_dropdown_attempts = 3  # 无下拉菜单不是账号异常，多试几次
            for attempt in range(max_dropdown_attempts):
                # 2. 聚焦搜索框
                if not self.focus_search_box():
                    return ("error", "无法聚焦搜索框")

                self._sleep(0.5)

                # 3. 输入微信号
                if not self.input_wechat_id(wechat_id):
                    return ("error", "无法输入微信号")

                self._sleep(0.5)

                # 4. 点击下拉项
                result = self.click_dropdown_item()
                if result is True:
                    break  # 成功找到下拉项，继续后续流程
                elif result == "retry":
                    # 还有剩余尝试次数才执行清理
                    if attempt < max_dropdown_attempts - 1:
                        self._emit_log(f"下拉菜单未检测到，重新激活微信窗口并输入 (尝试 {attempt+2}/{max_dropdown_attempts})")
                        if not self.activate_window():
                            return ("error", "无法激活微信窗口")
                        self._sleep(0.3)
                        try:
                            self.clear_search()
                        except Exception as e:
                            logger.warning(f"清空搜索框失败: {e}")
                        self._sleep(0.3)
                else:
                    return ("abnormal", "无法搜索到该账号")
            else:
                # 重试耗尽：3次都搜不到下拉项 → 账号被限制搜索/对方设置了权限
                return ("abnormal", "无法搜索到该账号")

            # OCR/点击期间用户可能点了停止或暂停，立即退出
            if self._stop_event and self._stop_event.is_set():
                return ("error", "用户停止")
            if self._pause_event and self._pause_event.is_set():
                return ("paused", "")

            # 5. 检测弹窗状态（已内置5次轮询，每次1s，共5s）
            status, detail = self.check_popup_status(wechat_id)

            # OCR/UIA 搜索期间用户可能点了停止或暂停
            if self._stop_event and self._stop_event.is_set():
                return ("error", "用户停止")
            if self._pause_event and self._pause_event.is_set():
                return ("paused", "")

            # 弹窗未找到：重试一次完整点击（可能是CEF渲染延迟/光标移开导致点击未生效）
            if status == "not_found":
                self._emit_log("弹窗未找到，重试点击一次...")
                # 先清搜索再重激活，确保下拉菜单状态干净
                try:
                    self.clear_search()
                except Exception:
                    pass
                if not self.activate_window():
                    return ("error", "无法激活微信窗口")
                self._sleep(0.3)
                if not self.focus_search_box():
                    return ("error", "无法聚焦搜索框")
                self._sleep(0.3)
                if not self.input_wechat_id(wechat_id):
                    return ("error", "无法输入微信号")
                self._sleep(0.5)
                result2 = self.click_dropdown_item()
                if result2 is not True:
                    self._emit_log("重试时仍无法找到下拉项，判定异常")
                    return ("abnormal", "未检测到弹窗(重试)")
                status, detail = self.check_popup_status(wechat_id)

            if status == "normal":
                logger.info(f"[正常] {wechat_id} -> 昵称: {detail}")
                return ("success", detail)
            elif status == "abnormal":
                logger.warning(f"[异常] {wechat_id} -> {detail}")
                return ("abnormal", detail)
            else:
                logger.warning(f"[未知] {wechat_id} -> 未检测到弹窗")
                return ("abnormal", "未检测到弹窗")

        finally:
            # 无论成功/失败/异常，确保清理弹窗和搜索框，避免影响下一个号
            try:
                self.close_popup()
            except Exception:
                pass
            try:
                self.clear_search()
            except Exception:
                pass


# ==================== 测试入口 ====================
if __name__ == "__main__":
    # 测试用
    ctrl = WeChatController(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
    print(f"微信运行中: {ctrl.is_wechat_running()}")
