# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

微信账号状态检查工具 — 通过 Windows UI 自动化（uiautomation）操控微信 PC 客户端，批量检查微信号是否正常（有无头像+昵称）。基于 tkinter 的 GUI 桌面应用，仅支持 Windows。

## 运行环境

- Windows 10+，微信 PC 客户端 (4.1.9.4)
- Python 3.12+

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 源码运行
python main.py

# 生成图标（打包前）
python generate_icon.py

# 打包为 exe（开发机）
build_exe.bat

# PyInstaller 直接打包
pyinstaller --onefile --windowed --icon=app.ico --name WeChatChecker --add-data "config.json;." --add-data "wechat_ids.txt;." main.py
```

## 架构

```
main.py                 # tkinter GUI 主程序（WeChatCheckerApp）
  ├── checker_engine.py # 检查引擎（CheckerEngine）— 管理循环/批次/进度
  │   └── wechat_controller.py  # 微信自动化（WeChatController）— uiautomation 操作
  ├── config_manager.py # 配置读写（config.json），含验证和微信号文件解析
  └── logger_setup.py   # 日志初始化（按天滚动，保留7天）
```

**数据流**: GUI 点"开始检查" → `CheckerEngine.start()` 在子线程中循环 → 每轮分批次 → 每批逐个调用 `WeChatController.check_single_account()` → 通过 uiautomation 的 Ctrl+F 搜索微信号 → 检测弹窗中的头像/昵称/添加按钮 → 回调 GUI 更新日志/进度 → 异常时弹窗并停止。

## 关键实现细节

- **微信窗口检测** (`is_wechat_running`): 三层 fallback — psutil 进程名 → tasklist 命令 → uiautomation 窗口类名（WeChatMainWndForPC/ChatWnd/MainWindow）
- **窗口激活** (`activate_window`): 用 SetFocus/SetActive 代替 SetTopmost，避免频繁抢前台。下拉项点击用 InvokePattern 代替 Click()，避免鼠标移动
- **异常处理**: 发现无头像/无昵称时触发 `on_abnormal` 回调 → GUI 弹窗 + 警告音并停止检查
- **间隔策略**: 账号间 3-5 秒随机，批次间 30-50 分钟随机，可配置
- **多轮循环**: 所有号检查完后自动开始下一轮，直到用户手动停止
- **线程模型**: 检查循环在 daemon 子线程，GUI 回调通过 `root.after(0, ...)` 回主线程

## 配置文件

- `config.json`: 微信路径、微信号文件路径、批次参数和间隔参数
- `wechat_ids.txt`: 每行一个微信号，`#` 开头为注释

## CI/CD

GitHub Actions (`build.yml`): 推送 main 分支时自动在 windows-latest 上构建 exe，产出物上传为 artifact。
