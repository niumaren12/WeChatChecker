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
  ├── checker_engine.py # 检查引擎（CheckerEngine）— 子线程循环/批次/进度
  │   └── wechat_controller.py  # 微信自动化（WeChatController）
  ├── config_manager.py # 配置读写（config.json）
  └── logger_setup.py   # 日志（按天滚动，保留7天）
```

**数据流**: GUI → `CheckerEngine.start()` 子线程循环 → 分批 → `WeChatController.check_single_account()` → 激活窗口 → Ctrl+F 搜索 → 输入微信号 → ↑ 选下拉项 → Enter 打开面板 → 三级弹窗搜索定位 → GDI 像素截取检测按钮（灰底+黑字）→ 回调 GUI。

## 关键：微信 PC 4.x 是 CEF/Chromium 应用

**uiautomation 看不到 CEF 内部的控件**（头像、昵称、文本标签等 Web 渲染元素不可见）。因此：

- **键盘操作全部用 Win32 API**，不依赖 uiautomation 的 SendKeys（子线程 COM 问题）：
  - `_send_hotkey(ctrl, key)` — keybd_event 组合键（Ctrl+F/A/V）
  - `_press_key(vk)` — keybd_event 单键（Delete/Esc/↑/↓/Enter）
  - `_type_text(text)` — SendInput + KEYEVENTF_UNICODE 逐字符输入
  - 输入回退：base64 编码 + PowerShell `[Windows.Clipboard]::SetText` + Ctrl+V
- **窗口查找仍可用**（找微信主窗口、弹窗），但内部控件不可用
- **弹窗按钮检测用 GDI 像素截取 + 颜色统计**：先用 uiautomation 定位弹窗位置，再用 `_capture_pixels` (BitBlt + GetDIBits) 截取弹窗底部区域，统计灰色像素 (170-240) 和深色像素 (0-60)，灰底>60% + 黑字>2% = 按钮有文字 = "添加到通讯录"

## 微信窗口操作流程

1. `activate_window`: Win32 `ShowWindow(SW_RESTORE)` + `BringWindowToTop` + `SetForegroundWindow`
2. `focus_search_box`: `_send_hotkey(ctrl, f)` → 等待 1.5s
3. `input_wechat_id`: 清空 `Ctrl+A/Delete` → `_type_text` SendInput → 失败回退剪贴板
4. `click_dropdown_item`: 等待 1.5s → **按 ↑ 往上一步**（微信默认选中"搜索网络结果"，目标"网络查找手机/QQ号"在它上方）→ Enter
5. `check_popup_status`: 三级弹窗搜索定位 → GDI 截取弹窗底部区域 → 灰底+黑字像素统计 → 判断按钮是否存在

## 像素检测按钮（GDI 截图）

`_capture_pixels` 用 Win32 GDI API 截取窗口像素（BitBlt + GetDIBits），64位兼容需显式声明 `argtypes`（见 commit 6632db3）。`_count_pixels` 统计矩形区域内匹配颜色范围的像素数。

`check_popup_status` 使用像素检测判断"添加到通讯录"按钮是否存在：
- 截取弹窗底部 10%-90% 宽度、80%-95% 高度区域
- 灰色范围 (170-240, 170-240, 170-240) = 按钮底色
- 深色范围 (0-60, 0-60, 0-60) = 按钮文字
- 灰底占比 > 60% **且** 黑字占比 > 2% → 有文字的按钮 → 正常账号
- 像素检测异常不返回 False，回退到"弹窗已打开=正常"（commit 3cfa768）

## 跨层日志回调

`WeChatController._gui_log` 由 `CheckerEngine.__init__` 注入为 `_emit_log`，使 wechat_controller 内部日志（如像素检测结果）能同时写 logger 和 GUI。避免了 wechat_controller 直接依赖 tkinter。

## 弹窗搜索（三级）

微信的"添加朋友"面板不是独立顶层窗口（CEF 渲染），需要三级搜索：
1. `WindowControl(Name="添加朋友", searchDepth=3)` — 深层独立窗口
2. `self.wechat_window.PaneControl(Name="添加朋友", searchDepth=10)` — 主窗口内面板
3. 兜底：直接用微信主窗口作为全局遍历范围

## 线程模型

检查循环在 daemon 子线程。GUI 回调通过 `root.after(0, ...)` 回主线程。异常回调 `_on_engine_abnormal` 用 `event.wait()` 阻塞等待用户手动关闭弹窗（不设超时）。

## 配置文件

- `config.json`: 微信路径、批次参数、间隔、max_rounds
- `wechat_ids.txt`: 每行一个微信号，`#` 注释，自动去重

## CI/CD

推送 main → GitHub Actions windows-latest 构建 exe → artifact 上传。
