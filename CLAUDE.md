# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

微信账号状态检查工具 v1.2 — 批量检查微信号是否正常。基于 tkinter 的 GUI 桌面应用，仅支持 Windows。

## 运行环境

- Windows 10+，微信 PC 客户端 (4.x，进程名 Weixin.exe)
- Python 3.12+
- Mac 开发机通过 SSH (`ssh win`) 连接 Windows 测试机（网线直连 192.168.100.2）

## 开发流程

由于项目依赖 Windows 专用 API（`win32gui`、`uiautomation`、`winsound`），Mac 上无法直接运行或打包。标准流程：

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
python generate_icon.py                  # 从 base64 数据生成 app.ico
python diagnose_wechat_window.py         # 诊断 — 列出所有顶层窗口类名/标题/位置
build_exe.bat                            # 一键打包（推荐）
pyinstaller --clean WeChatChecker.spec   # 直接用 PyInstaller 打包（build_exe.bat 的核心步骤）
```

## PyInstaller 打包注意事项

**正式打包入口是 `WeChatChecker.spec`**，不是 `main.py`。spec 文件显式声明了所有 `hiddenimports`（`wechat_controller`、`checker_engine`、`PIL`、`mss`、`pytesseract` 等），并用 `datas` 将 `tesseract_bundle/` 目录（exe + dll + tessdata/chi_sim.traineddata）打入 exe。

**PyInstaller hook** (`hooks/hook-wechat_controller.py`) 强制 PyInstaller 收集 `wechat_controller` 模块。该模块在 `main.py` 中被显式 import（虽然只用于确保打包），不依赖隐式分析。

Tesseract OCR 引擎的打包流程：
1. CI 中通过 `choco install tesseract-ocr` 安装
2. 复制 `tesseract.exe` + 所有 `.dll` 到 `tesseract_bundle/`
3. 下载 `chi_sim.traineddata` 到 `tesseract_bundle/tessdata/`
4. spec 文件将整个 `tesseract_bundle/` 作为 DATA 打入 exe
5. 运行时 `_get_tesseract_path()` 自动解压到临时目录使用

## 架构

```
main.py                 # tkinter GUI 主程序（WeChatCheckerApp）
  ├── checker_engine.py # 检查引擎（CheckerEngine）— 子线程循环/批次/进度/IP切换，start_with_ids() 接收列表
  │   ├── wechat_controller.py  # 微信自动化（WeChatController）
  │   ├── telegram_notifier.py  # Telegram Bot 通知（urllib，无外部依赖）
  │   └── ip_switcher.py        # IP自动切换（Clash API测速选最快节点 / 自定义命令，纯标准库）
  ├── config_manager.py # 配置读写（config.json）
  └── logger_setup.py   # 日志（按天滚动，保留7天）

构建辅助:
  hooks/hook-wechat_controller.py  # PyInstaller hook — 强制收集 wechat_controller 模块
  WeChatChecker.spec               # PyInstaller spec — 显式管理所有 hiddenimports 和 datas

辅助工具:
  diagnose_wechat_window.py  # 桌面窗口诊断 — 列出所有顶层窗口类名/标题/位置
```

**依赖说明**：
- `uiautomation` — 窗口查找和控件定位（仅 Windows，CEF 内部控件不可见）
- `psutil` — 检测微信进程是否在运行
- `pyinstaller` — 打包为 exe（仅打包时需）
- `mss` + `Pillow` + `pytesseract` — OCR 文字识别链路
- Tesseract 引擎 — 系统级依赖，CI 中通过 choco 安装后打入 exe，运行时自动解压

**数据流**: GUI 微信号列表（支持拖拽排序）→ `CheckerEngine.start_with_ids(ids)` 子线程循环 → 分批 → 每批后可触发IP切换 → `WeChatController.check_single_account()` → 激活窗口（验证前景）→ Ctrl+F 搜索（检查窗口置顶）→ 输入微信号 → OCR 识别下拉菜单（多关键字回退 + 2次重试）并鼠标点击 → 两级弹窗搜索定位 → OCR 识别弹窗内文字（7关键字回退 + 2次重试）→ 关闭弹窗（两级搜索 + ESC + 鼠标点击备用）→ 清空搜索框 → 回调 GUI（含 Telegram 通知状态）。

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
- 左栏 `fill=BOTH, expand=True`，右栏 `fill=Y`（固定宽度）
- 日志 `fill=BOTH, expand=True`，独占底部所有剩余空间
- 按钮、进度条、状态标签合并到同一行

## IP 自动切换（ip_switcher.py）

支持两种切换方式，纯 Python 标准库实现，无外部依赖。

### Clash API 方式
- 通过 Clash REST API (`http://127.0.0.1:9090`) 获取代理组节点列表
- `ThreadPoolExecutor` 并发测速所有候选节点（最多10并发）
- 按延迟升序排列，逐个尝试切换直到出口IP真正变化
- 排除 DIRECT、REJECT 等特殊类型节点
- 切换后等待 2s 建立连接，验证新 IP 后再继续

### 自定义命令方式
- 执行任意 shell 命令切换 IP（如拨号重连脚本）
- 命令执行后等待 5s，轮询验证 IP 变化
- 带超时保护 (`timeout` 参数)

### 核心方法
| 方法 | 说明 |
|------|------|
| `get_current_ip()` | 访问 `verify_url`（默认 api.ipify.org）获取公网IP，3次重试 |
| `get_clash_info()` | 获取代理组当前节点和所有候选节点 |
| `test_node_delay(node)` | 单节点延迟测试（通过 Clash /proxies/{name}/delay） |
| `switch_ip(stop_event)` | 主入口，返回 `(ok, msg, old_ip, new_ip, node_name, delay)` |

### IP 切换触发时机
- 引擎每 N 批完成后自动触发（`ip_switch_batch_count` 配置）
- 提前 `ip_switch_advance_seconds` 秒测速，确保切换不耽误下一批
- GUI 提供"立即切换(测试)"按钮手动触发
- 切换记录保留最近 50 条，显示在右栏 IP 面板下方

### IP 切换配置项
| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ip_switch_enabled` | bool | `false` | 总开关 |
| `ip_switch_method` | str | `"clash"` | 切换方式：clash / command |
| `ip_switch_clash_url` | str | `"http://127.0.0.1:9090"` | Clash API 地址 |
| `ip_switch_clash_group` | str | `"Proxy"` | 代理组名称 |
| `ip_switch_command` | str | `""` | 自定义切换命令 |
| `ip_switch_batch_count` | int | `3` | 每 N 批后切换 |
| `ip_switch_timeout` | int | `30` | 命令超时(秒) |
| `ip_switch_advance_seconds` | int | `300` | 提前测速(秒) |
| `ip_switch_verify_url` | str | `"https://api.ipify.org"` | IP验证地址 |

### IP 获取失败的 UI 处理
- `api.ipify.org` 在国内需代理，网络不通时显示灰色"需代理"而非红色报错
- 主窗口启动 1s 后自动获取当前 IP 和节点名，刷新按钮可手动重试

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
  - 下拉：mss 截图搜索框下方 → pytesseract 找"网络查找" → `_mouse_click`（SetCursorPos+mouse_event 真实鼠标事件）点击文字中心
  - 弹窗：UIA 两级搜索定位弹窗 → mss 截图弹窗区域 → pytesseract 检查是否包含"添加到通讯录"

## 微信窗口操作流程

1. `activate_window`: Win32 `ShowWindow(SW_RESTORE)` + `BringWindowToTop` + `SetForegroundWindow` → `GetForegroundWindow()` 验证是否真的置顶，失败重试一次
2. `focus_search_box`: 先检查 `GetForegroundWindow() == 微信hwnd` → 确认窗口在前台再发 `Ctrl+F` → 等待
3. `input_wechat_id`: 清空 `Ctrl+A/Delete` → `_type_text` SendInput → 失败回退剪贴板（base64 + PowerShell）
4. `click_dropdown_item`: 等待 2s → mss 截图搜索框下方（按窗口比例计算区域）→ pytesseract 多关键字回退匹配 `("网络查找", "QQ号", "络找", "查找", "络查")` → `_mouse_click` 点击 → 失败则等 1.5s 同区域重试
5. `check_popup_status(wechat_id)`: 两级弹窗搜索定位 → mss 截图弹窗区域 → pytesseract 7关键字回退 `("添加到通讯录", "添加到", "通讯录", "发消息", "音视频通话", "朋友", "加到")` → 失败则等 1.5s 重试一次（应对弹窗滑入动画）→ 诊断截图文件名含微信号
6. `close_popup`: 两级搜索弹窗（与 check_popup_status 对齐）→ 激活 → ESC（最多3次）→ 仍失败则鼠标点击窗口左侧关闭 → 两级搜索验证弹窗真的消失

## OCR 文字识别（Tesseract 内置打包）

使用 `pytesseract` + `mss` 截图实现屏幕文字识别。Tesseract 引擎和中文语言包随 exe 打包，无需系统权限。

- `_get_tesseract_path()` — 优先用 PyInstaller 打包的 tesseract.exe，源码运行从 PATH 找
- `_screenshot_region(left, top, right, bottom)` — mss 截取屏幕区域，返回 PIL Image
- `_preprocess_for_ocr(image)` — 图像预处理：2x放大(LANCZOS) → 灰度 → 锐化 → 二值化(threshold=140)，所有 OCR 调用前必走
- `_ocr_find_text(image, target, region_left, region_top)` — pytesseract 识别（PSM6），返回匹配项屏幕绝对坐标列表，坐标自动除以2还原
- `_ocr_contains_text(image, target)` — pytesseract 检查图片是否包含目标文字
- `_mouse_click(x, y)` — Win32 `SetCursorPos` + `mouse_event` 点击屏幕坐标

两个核心场景：
1. **下拉菜单**：按窗口尺寸比例计算截图区域（左2%顶8% → 左78%顶72%，适配不同DPI/分辨率）→ OCR 多关键字回退 `("网络查找", "QQ号", "络找", "查找", "络查")` → 鼠标点击。第一次失败后等 1.5s 同区域重试（应对下拉加载慢）
2. **弹窗按钮**：UIA 两级搜索定位弹窗 → 截图弹窗 → OCR 7关键字回退 `("添加到通讯录", "添加到", "通讯录", "发消息", "音视频通话", "朋友", "加到")` → 第一次失败后等 1.5s 同区域重试（应对弹窗滑入动画导致过渡帧 OCR 失败）。诊断截图存入 `%TEMP%/wechat_ocr_debug/popup_fail_{微信号}_{时间戳}.png`

OCR 文字匹配采用 `_find_text_in_entries()` 三级策略：
1. 逐条目精确匹配 (conf > 15)
2. 同行拼接回退（CEF 字符间距导致单字被拆分，按 y 坐标容差 10px 分组合并）
3. 全文拼接兜底

依赖：`pytesseract>=0.3.10`、`mss>=9.0.0`、`Pillow>=10.0.0`、Tesseract 引擎（CI 构建时通过 choco 安装后打入 exe）

## 跨层日志回调

`WeChatController._gui_log` 由 `CheckerEngine.__init__` 注入为 `_emit_log`，使 wechat_controller 内部日志（如 OCR 识别结果）能同时写 logger 和 GUI。避免了 wechat_controller 直接依赖 tkinter。

## 弹窗搜索与关闭（两级）

微信的"添加朋友"面板不是独立顶层窗口（CEF 渲染），需要两级搜索。**`check_popup_status()` 和 `close_popup()` 使用相同的两级搜索逻辑**，确保关闭时能找到 CEF 内部面板：

1. `WindowControl(Name="添加朋友", searchDepth=3)` — 深层独立窗口
2. `self.wechat_window.PaneControl(Name="添加朋友", searchDepth=10)` — 主窗口内面板
3. 均未找到则直接返回 `not_found`（check_popup_status）或认为已关闭（close_popup）

### `close_popup()` 关闭策略（三级递进）

1. **ESC × 3次**：找到弹窗 → 激活 → 发送 ESC → 等 0.5s → 重新搜索验证
2. **鼠标点击**：3次 ESC 均失败 → 激活微信窗口 → 鼠标点击窗口左侧 15% 位置（聊天列表区域，可关闭右侧面板）
3. **最终验证**：两级搜索确认弹窗真的消失，否则记录 error 日志

### 历史 Bug：`close_popup()` 只搜 WindowControl 导致连锁失败

早期版本 `close_popup()` 只搜索 WindowControl（Level 1），漏掉了 PaneControl（Level 2）。当弹窗是 CEF 内部面板时：
- `check_popup_status()` 通过 Level 2 找到了弹窗 → OCR 假阳性 → 返回 abnormal
- `close_popup()` 只搜 Level 1 → 找不到 → 误以为"弹窗已关闭" → 实际未关闭
- 下一个号：旧弹窗残留 → UIA 再次搜到同一个旧弹窗 → OCR 同样结果 → **连锁全错**

修复：`close_popup()` 补全 Level 2 PaneControl 搜索 + 鼠标点击备用关闭 + 关闭后验证。

## 线程模型

检查循环在 daemon 子线程。GUI 回调通过 `root.after(0, ...)` 回主线程。异常回调 `_on_engine_abnormal` 用 `event.wait()` 阻塞等待用户手动关闭弹窗（不设超时）。子线程启动前通过 `config_snapshot` 字典快照所有配置参数，避免子线程读取 config 时被主线程并发修改。等待逻辑 `_wait_with_stop()` 每 0.5s 轮询 `_stop_event`，可被停止信号打断。

## 配置文件

- `config.json`: 微信路径、批次参数、间隔、max_rounds、sound_enabled、Telegram 通知配置
- `wechat_ids.txt`: 微信号列表（GUI 内增删改，自动同步到此文件，不再手动编辑）
- 微信号管理已内置到 GUI（Listbox + 添加/删除/导入/清空/拖拽排序），引擎通过 `start_with_ids()` 接收列表

## Telegram 通知

通过 Telegram Bot API 将异常账号信息推送到指定频道/群组。

- **模块**: `telegram_notifier.py`，使用标准库 `urllib.request`，无外部依赖
- **Token**: 硬编码在 `telegram_notifier.py` 顶部 `TELEGRAM_BOT_TOKEN` 常量
- **Chat ID**: 在 GUI 左栏顶部 Telegram 面板中配置，持久化到 `config.json` 的 `telegram_chat_id`
- **代理**: 可选，在 GUI 中填写 `http://127.0.0.1:7890` 格式的代理地址。不填则走系统代理（`urllib` 默认行为）
- **限流**: 两次发送最小间隔 1 秒，防止 Telegram API 429
- **线程安全**: `threading.Lock` 保护发送状态
- **静默失败**: 网络错误只记日志，不中断检查循环
- GUI 异常面板每条记录显示通知状态：`✅已通知` / `❌发送失败` / `—`(未启用)

### Telegram 相关配置项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `telegram_enabled` | bool | `false` | 总开关 |
| `telegram_chat_id` | str | `"-1003974347005"` | 目标频道/群组 ID |
| `telegram_proxy` | str | `""` | 代理地址（选填） |
| `telegram_message_template` | str | 含占位符的模板 | `{wechat_id}`, `{reason}`, `{timestamp}` |

## 倒计时

等待期间状态栏实时显示剩余时间，非静态文本。

- **引擎侧**: `CheckerEngine` 新增 `on_countdown` 回调，`_wait_with_stop()` 每 0.5s 调用一次
- **GUI 侧**: `_on_engine_countdown(remaining, label)` 格式化后更新状态栏
- 账号间等待：`等待中 - 4秒后检查下一个`
- 批次间等待：`等待中 - 第2轮 下一批 (3分25秒后)`
- 轮次间等待：`等待中 - 下一轮 (12分08秒后)`
- 只在秒数变化时更新 GUI（减少 UI 负担）

## 微信号列表拖拽排序

- Listbox 绑定 `<Button-1>`, `<B1-Motion>`, `<ButtonRelease-1>`
- 拖拽超过 5px 阈值进入拖拽模式，光标变为 `fleur`
- 松手完成排序，自动保存到 `wechat_ids.txt`

## 窗口激活验证

防止窗口未置顶导致键盘操作发到错误窗口：

- `activate_window`: `SetForegroundWindow` 后用 `GetForegroundWindow()` 验证，失败自动重试
- `focus_search_box`: 发 Ctrl+F 前检查 `GetForegroundWindow() == 微信 hwnd`

## 异常通知面板与警告音

发现异常账号时，GUI 右栏底部出现红色异常通知面板（可滚动），不阻塞检查循环。同时触发声音警报。

- **异常记录**: `AbnormalEntry` dataclass（`wechat_id`, `reason`, `timestamp`, `telegram_sent`），字典去重（同号多次异常只保留最新）
- **声音警报**: `winsound.Beep(1000, 200)` 每 1.5s 循环，直到用户点击"停止声音"或面板清空
- **竞态防护**: `_beep_stopped` 标志位防止 `_stop_beep()` 和 `_beep_loop()` 之间的 race condition（停止后 `after` 回调可能仍在队列中）
- **静音**: 用户点"停止声音"设 `_sound_muted = True`，面板清空自动恢复
- **已修复**: 每条异常记录旁有"已修复"按钮，点击后从字典移除并刷新面板

## 运行时环境自检

`_check_runtime_env()` 在 GUI 启动 500ms 后执行（`root.after(500, ...)`）：调用 `tesseract --version` 验证 OCR 引擎可用。不可用时在日志区显示红色警告。Tesseract 子进程调用使用 `_no_window_popen()` 辅助函数（`subprocess.Popen` + `CREATE_NO_WINDOW` 标志），避免检查期间弹出命令行窗口。

## CI/CD

推送 main 或手动触发 → GitHub Actions `windows-latest` 构建 exe → artifact 上传。

CI 流程 (`build.yml`)：
1. 安装 Python 3.12 + pip 依赖
2. `choco install tesseract-ocr` 安装 Tesseract 引擎
3. 复制 `tesseract.exe` + dll 到 `tesseract_bundle/`
4. 下载 `chi_sim.traineddata` 中文语言包
5. `python generate_icon.py` 生成图标
6. `pyinstaller --clean WeChatChecker.spec` 打包
7. 复制 `config.json`、`wechat_ids.txt` 到 `dist/`
8. `actions/upload-artifact@v4` 上传整个 `dist/` 目录

## 常见问题排查（踩坑记录）

### 换台电脑卡死/没反应

**症状**: 日志停在"第 1/1 批"之后，长时间无输出。

**根因**: `activate_window()` 对每号都执行 4 次 UIA 窗口搜索（3 类名 + 1 标题），每次 `maxSearchSeconds` 等待。类名不匹配时 9 个号 × 4 秒 = 36 秒。

**修复**: 窗口句柄缓存 — 首次搜索成功后存 `self.wechat_window`，后续直接用 Win32 `IsWindow` 验证 + `ShowWindow`/`SetForegroundWindow` 激活（秒级）。类名搜索超时从 1s 降到 0.5s。

**另一根因**: uiautomation 底层用 Windows COM，子线程不会自动初始化。`_run_check_loop` 开头调用 `CoInitializeEx(None, 2)`。

### OCR 识别不到"网络查找"

**症状**: OCR 输出碎片如"网|络|柳|:"，找不到"网络查找"。

**根因**: 固定阈值二值化（`.point(lambda p: 255 if p > 140 else 0)`）在不同屏幕/ClearType 下效果完全不同。细笔画字（"查"、"找"）被截断消失。Tesseract 4.x 内部有自适应二值化，手写固定阈值是多余且有害的。

**修复**: 三通道预处理管道 — ①不做二值化（灰度直送 Tesseract）→ ②阈值 100 → ③阈值 140（兼容）。任一通道有结果即返回。同时匹配目标从"网络查找"扩展到"络找"（处理"查"完全不可读的情况）。

**教训**: 不要手写图像二值化阈值，Tesseract 自己能做更好。多通道回退是跨机器兼容的关键。

### 下拉菜单点击无响应

**症状**: OCR 找到文字但点击后弹窗不出现。

**根因**: `PostMessageW`/`SendMessageW` 发送窗口消息点击，微信 CEF 渲染的下拉菜单不响应窗口消息。

**修复**: 改用 `SetCursorPos` + `mouse_event` 硬件级鼠标事件。点击前后保存/恢复用户鼠标位置。

### 停止按钮延迟

**症状**: 点停止后状态栏显示"正在停止..."持续数秒。

**根因**: `pytesseract.image_to_data()` 是阻塞 C 调用，期间线程无法检查 `_stop_event`。所有 `time.sleep()` 应改用 `_sleep()`（内部用 `Event.wait(timeout)` 实现可中断等待）。

**修复**: 
- WeChatController 所有等待改为 `self._sleep()` 
- `check_single_account` 在 OCR/点击完成后立即检查 `_stop_event`
- 异常发现后不停止（改为非阻塞通知面板），消除模态弹窗阻塞

### exe 换机器运行提示 Tesseract 不可用

**症状**: 启动后日志显示"Tesseract OCR 不可用"。

**根因**: PyInstaller 打包的 tesseract.exe 依赖 VC++ 运行时。极少数精简版 Windows 缺少。自检代码 `_check_runtime_env` 会在启动 500ms 后检测并警告。

**解决**: CI 构建的 exe 已通过 choco 安装 tesseract 并打入 bundle。如仍不可用，安装 [VC++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

### OCR 同台电脑部分账号失败（下拉菜单识别不到）

**症状**: 同一台电脑，有的账号正常，有的OCR识别不到"网络查找"。日志显示碎片如 `网|络|查|手|机`，"找"字丢失。

**根因**: ClearType 子像素渲染导致 Tesseract 对笔画密集的字（"找"、"到"、"录"）时灵时不灵。不同 ID 号码长度不同，文字像素位置微移，渲染结果不同。

**修复**: 
- 下拉匹配目标改为多级回退：`("网络查找", "QQ号", "络找", "查找", "络查")`
- 弹窗匹配改为7关键字回退 + 2次重试：`("添加到通讯录", "添加到", "通讯录", "发消息", "音视频通话", "朋友", "加到")`
- "QQ号"是英文数字组合，Tesseract 识别稳定
- 下拉区域改为同一区域两次重试（等 2s → 失败等 1.5s 再截一次）
- 弹窗 OCR 同样增加两次重试（间隔 1.5s），应对弹窗滑入动画

### 弹窗假阳性连锁失败（已修复）

**症状**: OCR 将正常号误判为异常（假阳性），且一旦出错，后续所有号都被判为同一种异常。

**根因**: `close_popup()` 只搜索 WindowControl（独立窗口），漏掉了 PaneControl（CEF 内部面板）。当弹窗是内部面板时：`check_popup_status()` 通过 PaneControl 搜索找到了弹窗 → OCR 假阳性 → `close_popup()` 只搜 WindowControl → 找不到 → 误以为已关闭 → 弹窗残留 → 下一个号 UIA 再搜到同一个旧弹窗 → OCR 同样结果 → **连锁全错**。

**修复**:
- `close_popup()` 补全 Level 2 PaneControl 搜索，与 `check_popup_status()` 对齐
- ESC 3次关闭失败后，改用鼠标点击微信窗口左侧（聊天列表区域）关闭面板
- 关闭后两级搜索最终验证弹窗消失
- `check_popup_status()` 弹窗 OCR 增加第2次重试（间隔 1.5s），关键词从3个扩到7个
- 诊断截图文件名加入微信号，方便跨轮对比排查

### OCR 换电脑截图区域偏移

**症状**: 换电脑后下拉菜单 OCR 识别率大幅下降。

**根因**: 截图区域用硬编码像素偏移（`win_top + 40`、宽度 `400`、高度 `340`），不同 DPI/分辨率下对不上实际 UI。

**修复**: 改为按窗口尺寸比例计算：`int(win_w * 0.02)` ~ `int(win_w * 0.78)`、`int(win_h * 0.08)` ~ `int(win_h * 0.72)`。

### Telegram 通知换电脑失效

**症状**: 旧电脑 Telegram 通知正常，新电脑完全收不到。

**根因**: `urllib.request.urlopen()` 无显式代理配置，直连 `api.telegram.org`（国内被封锁）。旧电脑有系统代理，新电脑没有。

**修复**: 在 TelegramNotifier 中支持可选代理参数，GUI 面板加代理输入框。不填走系统代理，填了走指定代理。

### 窗口未置顶导致键盘操作发错窗口

**症状**: 偶尔一整批账号都异常，日志无明显错误。

**根因**: `SetForegroundWindow` 可能被 Windows 静默拒绝（其他进程拦截），后续 Ctrl+F/输入都发到了错误窗口。

**修复**: `activate_window` 用 `GetForegroundWindow()` 验证置顶结果，失败重试；`focus_search_box` 发快捷键前检查窗口是否在前台。
