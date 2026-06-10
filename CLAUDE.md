# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

微信账号状态检查工具 v1.2 — 批量检查微信号是否正常。基于 tkinter 的 GUI 桌面应用，仅支持 Windows。

## 运行环境

- Windows 10+，微信 PC 客户端 (4.x，进程名 Weixin.exe)
- Python 3.12+
- Mac 开发机通过 SSH (`ssh win`) 连接 Windows 测试机（网线直连 192.168.100.2）

## 开发流程

项目依赖 Windows 专用 API（`win32gui`、`uiautomation`、`winsound`），Mac 上无法直接运行或打包。标准流程：

1. **Mac 上改代码** → `git commit` + `git push`
2. **GitHub Actions 自动构建**（`build.yml`，windows-latest）
3. **下载产物** → `gh run download <id>`
4. **传到 Windows 测试** → `scp -r dist/WeChatChecker-Windows/* win:"Desktop/WeChatChecker/"`
5. **SSH 远程控制** → `ssh win` 执行命令、查看日志

### SSH 配置（~/.ssh/config）
```
Host win
    HostName 192.168.100.2
    User apple
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
```

- 密钥：`~/.ssh/id_ed25519`
- Windows 端公钥在 `C:\ProgramData\ssh\administrators_authorized_keys`（apple 是管理员）
- 新终端需执行 `ssh-add ~/.ssh/id_ed25519` 加载密钥到 agent

## 常用命令

```bash
pip install -r requirements.txt          # 安装依赖
python main.py                           # 源码运行
python diagnose_wechat_window.py         # 诊断 — 列出所有顶层窗口类名/标题/位置
build_exe.bat                            # 一键打包（推荐，等效 pyinstaller --clean WeChatChecker.spec）
python -m pytest tests/ -v               # 运行单元测试（仅 config_manager 和 ip_switcher，不含 Windows API）
```

## 架构

```
main.py                 # tkinter GUI 主程序（WeChatCheckerApp）
  ├── checker_engine.py # 检查引擎（CheckerEngine）— 子线程循环，start_with_ids() 接收列表
  │   ├── wechat_controller.py  # 微信自动化（WeChatController）— Win32 API + OCR
  │   ├── telegram_notifier.py  # Telegram Bot 通知（urllib，无外部依赖）
  │   └── ip_switcher.py        # IP自动切换（Clash API / 自定义命令，纯标准库）
  ├── config_manager.py # 配置读写（config.json）
  ├── abnormal_panel.py # 异常账号面板（AbnormalEntry dataclass + 警报）
  ├── ip_panel.py       # IP切换面板（当前IP显示 + 切换记录）
  └── logger_setup.py   # 日志（按天滚动，保留7天）

hooks/hook-wechat_controller.py  # PyInstaller hook
WeChatChecker.spec               # PyInstaller spec — 显式管理所有 hiddenimports 和 datas
tests/                           # 单元测试（仅 config_manager、ip_switcher，无 Windows API 依赖）
```

**依赖**：`uiautomation`(窗口查找) + `psutil`(进程检测) + `mss`+`Pillow`+`pytesseract`(OCR链路) + `pyinstaller`(仅打包)

## 关键架构约束

### 微信 PC 4.x 是 CEF/Chromium 应用

**uiautomation 看不到 CEF 内部的控件**（头像、昵称等 Web 渲染元素不可见）。因此：

- **键盘操作全部用 Win32 API**，不用 uiautomation 的 SendKeys（子线程 COM 问题）：
  - `_send_hotkey(ctrl, key)` — keybd_event 组合键
  - `_press_key(vk)` — keybd_event 单键
  - `_type_text(text)` — SendInput + KEYEVENTF_UNICODE 逐字符输入
  - 输入回退：base64 + PowerShell `[Windows.Clipboard]::SetText` + Ctrl+V
- **下拉菜单和弹窗检测用 Tesseract OCR**：mss 截图 → pytesseract 识别文字 → 获取坐标
- **鼠标操作用硬件级事件**：`SetCursorPos` + `mouse_event`（微信 CEF 不响应 `PostMessage`/`SendMessage` 窗口消息）

### 检查流程（单号）

```
激活窗口（验证前景）→ Ctrl+F 搜索 → 输入微信号
→ OCR 截图搜索框下方（一次OCR，多关键词共享结果）→ 鼠标点击下拉项
→ 两级UIA搜索定位弹窗（`_safe_uia_exists` 防COM死锁）→ OCR 截图弹窗 → 7关键字回退判断状态
→ 关闭弹窗（ESC×3 + Win32 FindWindowW验证 + 鼠标点击备用）→ 清空搜索框
```

### 弹窗两级搜索

微信"添加朋友"面板不是独立顶层窗口（CEF 渲染），需两级搜索。**`check_popup_status()` 和 `close_popup()` 必须使用相同搜索逻辑**：

1. `WindowControl(Name="添加朋友", searchDepth=3)` — 深层独立窗口
2. `self.wechat_window.PaneControl(Name="添加朋友", searchDepth=5)` — 主窗口内面板

**UIA COM 死锁防护**：所有 `Exists()` 调用通过 `_safe_uia_exists()` 包装——独立线程执行 + `join(timeout)` 硬时限，超时放弃线程避免永久阻塞。Win32 `FindWindowW` 优先于 UIA 搜索。

`close_popup()` 关闭策略：ESC×3（先 `SetForegroundWindow`）→ Win32 `FindWindowW` 快速验证 → 鼠标点击备用。不再用 UIA 验证关闭。

### OCR 关键参数

- 截图区域按窗口比例计算（`win_w*0.02~0.78`, `win_h*0.08~0.72`），**不要硬编码像素值**（不同 DPI 会偏移）
- 图像预处理：2x放大(LANCZOS) → 灰度 → 锐化 → 自适应二值化(三通道回退：无二值化 → 阈值100 → 阈值140)
- 文字匹配三级策略：逐条目精确匹配(conf>15) → 同行拼接回退(y容差10px) → 全文拼接兜底
- OCR 结果共享：每张截图只 OCR 一次，多个关键词对同一份 `(entries, rows, full_text)` 匹配
- 下拉关键字回退：`("网络查找", "QQ号", "络找", "查找", "络查")`
- 弹窗关键字回退：`("添加到通讯录", "添加到", "通讯录", "发消息", "音视频通话", "朋友", "加到")`
- 下拉截图重试间隔 1.5s，弹窗 OCR 重试间隔由 `check_popup_status` 管理
- 诊断截图存入 `%TEMP%/wechat_ocr_debug/popup_fail_{微信号}_{时间戳}.png`

### 线程模型

- 检查循环在 daemon 子线程，GUI 回调通过 `root.after(0, ...)` 回主线程
- 子线程启动前通过 `config_snapshot` 快照所有配置，避免与主线程并发读写
- 等待逻辑 `_wait_with_stop()` 每 0.5s 轮询 `_stop_event`，可被停止/暂停打断
- **`_sleep()` 同时检查 `_stop_event` 和 `_pause_event`**：长延迟(≥0.5s)每 0.2s 轮询，暂停时冻结；短延迟(<0.5s)直接 `time.sleep()` 避免精度损失
- `pytesseract.image_to_data()` 是阻塞 C 调用，期间 `_sleep` 也无法中断。OCR 前后必须检查停止/暂停信号
- uiautomation 底层用 Windows COM，`_run_check_loop` 开头必须调用 `CoInitializeEx(None, 2)`
- 单实例锁：文件锁 `.instance.lock`（PID） + 心跳文件 `.instance.heartbeat`（每轮/每批更新，30秒过期=判定卡死可接管）

### 跨层日志回调

`WeChatController._gui_log` 由 `CheckerEngine.__init__` 注入为 `_emit_log`，使底层日志（OCR 结果等）能同时写 logger 和 GUI，避免 wechat_controller 直接依赖 tkinter。

### 窗口句柄缓存

首次通过 Win32 `FindWindowW` 定位微信窗口 → `ControlFromHandle` 转 UIA 控件并缓存。后续用 Win32 `IsWindow` 验证 + `ShowWindow`/`SetForegroundWindow` 激活。`FindWindowW` 失败才回退到 UIA deskstop-level 搜索（`_safe_uia_exists` 包装）。

### 窗口激活验证

`SetForegroundWindow` 可能被 Windows 静默拒绝。`activate_window` 用 `GetForegroundWindow()` 验证结果并重试；`focus_search_box` 发 Ctrl+F 前确认窗口在前台。

## GUI 布局（左右分栏）

```
┌────────────────────────────────────────────┐
│  标题                                      │
├────────────────────────────────────────────┤
│  配置 (微信路径、参数、声音)               │
├──────────────┬─────────────────────────────┤
│  左栏        │  右栏                       │
│  Telegram通知│  IP自动切换 (当前IP/配置)    │
│  (上)        │  切换记录                   │
│  微信号列表  │  异常账号面板               │
│  (下)        │                             │
├──────────────┴─────────────────────────────┤
│  按钮行 + 进度条 + 状态                    │
├────────────────────────────────────────────┤
│  日志 (全宽，独占剩余垂直空间)             │
└────────────────────────────────────────────┘
```

- 窗口默认 820x780，Canvas+Scrollbar 全局滚动
- 左栏 `fill=BOTH, expand=True`，右栏 `fill=Y`
- 日志 `fill=BOTH, expand=True`

## IP 自动切换

纯 Python 标准库实现。支持两种方式：

- **Clash API**：REST API 获取节点 → `ThreadPoolExecutor` 并发测速（最多10并发）→ 按延迟升序逐个切换直到出口IP变化
- **自定义命令**：执行 shell 命令 → 等 5s → 轮询验证 IP 变化，带超时保护

核心配置项：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ip_switch_enabled` | bool | `false` | 总开关 |
| `ip_switch_method` | str | `"clash"` | clash / command |
| `ip_switch_clash_url` | str | `"http://127.0.0.1:9090"` | Clash API 地址 |
| `ip_switch_batch_count` | int | `3` | 每 N 批后切换 |
| `ip_switch_advance_seconds` | int | `300` | 提前测速(秒) |

切换记录保留最近 50 条。`api.ipify.org` 在国内需代理，不通时显示灰色"需代理"。

## Telegram 通知

`telegram_notifier.py` — 标准库 `urllib.request`，无外部依赖。Bot Token 硬编码在模块顶部。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `telegram_enabled` | bool | `false` | 总开关 |
| `telegram_chat_id` | str | — | 目标频道/群组 ID |
| `telegram_proxy` | str | `""` | 代理地址，不填走系统代理 |

限流 1s、`threading.Lock` 线程安全、静默失败不中断检查。

## 异常通知面板

异常记录用 `AbnormalEntry` dataclass，字典去重。`winsound.Beep` 循环警报直到用户停止。`_beep_stopped` 标志位防竞态。每条记录旁有"已修复"按钮。

## 微信号列表

GUI Listbox + 添加/删除/导入/清空/拖拽排序（5px阈值，`fleur` 光标），自动同步到 `wechat_ids.txt`。

## PyInstaller 打包

正式入口是 `WeChatChecker.spec`。spec 显式声明 `hiddenimports`，`datas` 将 `tesseract_bundle/`（exe+dll+tessdata/chi_sim.traineddata）打入 exe。`hooks/hook-wechat_controller.py` 强制收集 wechat_controller 模块。

Tesseract 打包：CI 中 `choco install tesseract-ocr` → 复制 exe+dll → 下载中文语言包 → spec 打入 bundle → 运行时 `_get_tesseract_path()` 自动解压。

## CI/CD

推送 main 或手动触发 → `windows-latest` 构建 → `actions/upload-artifact@v4` 上传 `dist/`。详见 `.github/workflows/build.yml`。

## 测试

单元测试在 `tests/` 目录，仅覆盖纯 Python 模块（config_manager、ip_switcher），不含 Windows API 依赖。可在 Mac 上运行 `python -m pytest tests/ -v`。wechat_controller 因依赖 win32gui/uiautomation/mss，无法在 Mac 上测试。

## 配置文件

- `config.json`：所有运行参数（微信路径、批次、间隔、Telegram、IP切换）
- `wechat_ids.txt`：微信号列表，GUI 内管理，不再手动编辑

## .claude 配置

`settings.local.json` 预授权了常用命令（git、ssh、scp、python编译、gh），启用了 `chrome-devtools` MCP 服务器和所有项目 MCP 服务器。

## 经验教训

> 详细踩坑记录见项目根目录 `LESSONS.md`。以下仅列架构层面的关键约束。

- **不要硬编码像素坐标** — 用窗口比例计算截图区域
- **不要手写固定阈值二值化** — Tesseract 自带自适应二值化，多通道回退是跨机器兼容关键
- **鼠标点击用硬件事件** — `SetCursorPos`+`mouse_event`，CEF 不响应窗口消息
- **等待必须可中断** — `_sleep()` 同时检查 stop/pause，短延迟直接 sleep 避免精度损失
- **OCR 结果共享** — 同一张截图只扫一次，多关键词匹配同一份 `entries/rows/full_text`
- **UIA 必须有硬超时** — `_safe_uia_exists()` 独立线程 + `join(timeout)`，COM 死锁时放弃线程
- **Win32 API 优先于 UIA** — `FindWindowW` 毫秒级无 COM 依赖，`close_popup` 用 Win32 验证代替 UIA 搜索
- **`_check_popup_status` 首个 title 超时就跳** — 不用试完 5 个 title
