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

**数据流**: GUI → `CheckerEngine.start()` 子线程循环 → 分批 → `WeChatController.check_single_account()` → 激活窗口 → Ctrl+F 搜索 → 输入微信号 → ↓/↑ 选下拉项 → Enter 打开面板 → 全局遍历检测按钮 → 回调 GUI。

## 关键：微信 PC 4.x 是 CEF/Chromium 应用

**uiautomation 看不到 CEF 内部的控件**（头像、昵称、文本标签等 Web 渲染元素不可见）。因此：

- **键盘操作全部用 Win32 API**，不依赖 uiautomation 的 SendKeys（子线程 COM 问题）：
  - `_send_hotkey(ctrl, key)` — keybd_event 组合键（Ctrl+F/A/V）
  - `_press_key(vk)` — keybd_event 单键（Delete/Esc/↑/↓/Enter）
  - `_type_text(text)` — SendInput + KEYEVENTF_UNICODE 逐字符输入
  - 输入回退：base64 编码 + PowerShell `[Windows.Clipboard]::SetText` + Ctrl+V
- **窗口查找仍可用**（找微信主窗口、弹窗），但内部控件不可用
- **弹窗检测用全局遍历 + 位置筛选**：从 `GetRootControl()` 遍历，用弹窗 `BoundingRectangle` 筛选范围内控件，检测"添加到通讯录"按钮

## 微信窗口操作流程

1. `activate_window`: Win32 `ShowWindow(SW_RESTORE)` + `BringWindowToTop` + `SetForegroundWindow`
2. `focus_search_box`: `_send_hotkey(ctrl, f)` → 等待 1.5s
3. `input_wechat_id`: 清空 `Ctrl+A/Delete` → `_type_text` SendInput → 失败回退剪贴板
4. `click_dropdown_item`: 等待 1.5s → **按 ↑ 往上一步**（微信默认选中"搜索网络结果"，目标"网络查找手机/QQ号"在它上方）→ Enter
5. `check_popup_status`: 三级弹窗搜索 → 全局遍历 + BoundingRectangle 筛选 → 找"添加到通讯录"按钮

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
