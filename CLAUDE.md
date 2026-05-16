# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

微信账号状态检查工具 — 批量检查微信号是否正常。基于 tkinter 的 GUI 桌面应用，仅支持 Windows。

## 运行环境

- Windows 10+，微信 PC 客户端 (4.x)
- Python 3.12+

## 常用命令

```bash
pip install -r requirements.txt   # 安装依赖
python main.py                    # 源码运行
python generate_icon.py           # 生成图标
build_exe.bat                     # 打包为 exe
```

## 架构

```
main.py                 # tkinter GUI 主程序（WeChatCheckerApp）
  ├── checker_engine.py # 检查引擎（CheckerEngine）— 子线程循环/批次/进度，start_with_ids() 接收列表
  │   └── wechat_controller.py  # 微信自动化（WeChatController）
  ├── config_manager.py # 配置读写（config.json）
  └── logger_setup.py   # 日志（按天滚动，保留7天）

辅助工具:
  diagnose_wechat_window.py  # 桌面窗口诊断 — 列出所有顶层窗口类名/标题/位置
```

**数据流**: GUI 微信号列表 → `CheckerEngine.start_with_ids(ids)` 子线程循环 → 分批 → `WeChatController.check_single_account()` → 激活窗口 → Ctrl+F 搜索 → 输入微信号 → OCR 识别下拉菜单"网络查找"并鼠标点击 → 三级弹窗搜索定位 → OCR 识别弹窗内"添加到通讯录"文字 → 回调 GUI。

## 关键：微信 PC 4.x 是 CEF/Chromium 应用（进程名 Weixin.exe）

**uiautomation 看不到 CEF 内部的控件**（头像、昵称、文本标签等 Web 渲染元素不可见）。因此：

- **键盘操作全部用 Win32 API**，不依赖 uiautomation 的 SendKeys（子线程 COM 问题）：
  - `_send_hotkey(ctrl, key)` — keybd_event 组合键（Ctrl+F/A/V）
  - `_press_key(vk)` — keybd_event 单键（Delete/Esc/↑/↓/Enter）
  - `_type_text(text)` — SendInput + KEYEVENTF_UNICODE 逐字符输入
  - 输入回退：base64 编码 + PowerShell `[Windows.Clipboard]::SetText` + Ctrl+V
- **窗口查找仍可用**（找微信主窗口、弹窗），但内部控件不可用
- **下拉菜单和弹窗检测用 Tesseract OCR（内置打包）**：截图 → pytesseract 识别文字 → 获取坐标/判断文字存在
  - Tesseract 引擎 + 中文语言包通过 PyInstaller `--add-data` 打包进 exe，运行时自动解压
  - `_get_tesseract_path()` 优先用打包版，源码运行时从 PATH 找
  - 下拉：mss 截图搜索框下方 → pytesseract 找"网络查找" → `_mouse_click` 点击文字中心
  - 弹窗：保留 UIA 三级搜索定位弹窗 → mss 截图弹窗区域 → pytesseract 检查是否包含"添加到通讯录"

## 微信窗口操作流程

1. `activate_window`: Win32 `ShowWindow(SW_RESTORE)` + `BringWindowToTop` + `SetForegroundWindow`
2. `focus_search_box`: `_send_hotkey(ctrl, f)` → 等待 1.5s
3. `input_wechat_id`: 清空 `Ctrl+A/Delete` → `_type_text` SendInput → 失败回退剪贴板
4. `click_dropdown_item`: 等待 1.5s → mss 截图搜索框下方 → pytesseract 识别"网络查找" → `_mouse_click` 点击文字中心
5. `check_popup_status`: 三级弹窗搜索定位 → mss 截图弹窗区域 → pytesseract 检查是否包含"添加到通讯录"

## OCR 文字识别（Tesseract 内置打包）

使用 `pytesseract` + `mss` 截图实现屏幕文字识别。Tesseract 引擎和中文语言包随 exe 打包，无需系统权限。

- `_get_tesseract_path()` — 优先用 PyInstaller 打包的 tesseract.exe，源码运行从 PATH 找
- `_screenshot_region(left, top, right, bottom)` — mss 截取屏幕区域，返回 PIL Image
- `_preprocess_for_ocr(image)` — 图像预处理：2x放大(LANCZOS) → 灰度 → 锐化 → 二值化(threshold=140)，所有 OCR 调用前必走
- `_ocr_find_text(image, target, region_left, region_top)` — pytesseract 识别（PSM6），返回匹配项屏幕绝对坐标列表，坐标自动除以2还原
- `_ocr_contains_text(image, target)` — pytesseract 检查图片是否包含目标文字
- `_mouse_click(x, y)` — Win32 `SetCursorPos` + `mouse_event` 点击屏幕坐标

两个核心场景：
1. **下拉菜单**：截图搜索框下方区域 → OCR 找"网络查找" → 鼠标点击
2. **弹窗按钮**：保留 UIA 三级搜索定位弹窗 → 截图弹窗 → OCR 检查"添加到通讯录"

依赖：`pytesseract>=0.3.10`、`mss>=9.0.0`、`Pillow>=10.0.0`、Tesseract 引擎（CI 构建时通过 choco 安装后打入 exe）

## 跨层日志回调

`WeChatController._gui_log` 由 `CheckerEngine.__init__` 注入为 `_emit_log`，使 wechat_controller 内部日志（如 OCR 识别结果）能同时写 logger 和 GUI。避免了 wechat_controller 直接依赖 tkinter。

## 弹窗搜索（三级）

微信的"添加朋友"面板不是独立顶层窗口（CEF 渲染），需要三级搜索：
1. `WindowControl(Name="添加朋友", searchDepth=3)` — 深层独立窗口
2. `self.wechat_window.PaneControl(Name="添加朋友", searchDepth=10)` — 主窗口内面板
3. 兜底：直接用微信主窗口作为全局遍历范围

## 线程模型

检查循环在 daemon 子线程。GUI 回调通过 `root.after(0, ...)` 回主线程。异常回调 `_on_engine_abnormal` 用 `event.wait()` 阻塞等待用户手动关闭弹窗（不设超时）。子线程启动前通过 `config_snapshot` 字典快照所有配置参数，避免子线程读取 config 时被主线程并发修改。等待逻辑 `_wait_with_stop()` 每 0.5s 轮询 `_stop_event`，可被停止信号打断。

## 配置文件

- `config.json`: 微信路径、批次参数、间隔、max_rounds
- `wechat_ids.txt`: 微信号列表（GUI 内增删改，自动同步到此文件，不再手动编辑）
- 微信号管理已内置到 GUI（Listbox + 添加/删除/导入/清空），引擎通过 `start_with_ids()` 接收列表

## CI/CD

推送 main → GitHub Actions windows-latest 构建 exe → artifact 上传。
