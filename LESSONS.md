# LESSONS.md

项目级经验教训记录。同步维护到 `/Users/mac/Documents/知识库/Claude/LESSONS/自动检查v.md`。

---

## 换台电脑卡死/没反应

**问题**：日志停在"第 1/1 批"之后，长时间无输出。

**原因**：`activate_window()` 对每号都执行 4 次 UIA 窗口搜索（3 类名 + 1 标题），每次 `maxSearchSeconds` 等待。类名不匹配时 9 个号 × 4 秒 = 36 秒。另外 uiautomation 底层用 Windows COM，子线程不会自动初始化。

**解法**：窗口句柄缓存 — 首次搜索成功后存 `self.wechat_window`，后续直接用 Win32 `IsWindow` 验证。类名搜索超时从 1s 降到 0.5s。`_run_check_loop` 开头调用 `CoInitializeEx(None, 2)`。

**避坑**：不要每次操作都重新搜索窗口句柄，缓存 + Win32 验证即可。

---

## OCR 识别不到"网络查找"

**问题**：OCR 输出碎片如"网|络|柳|:"，找不到"网络查找"。

**原因**：固定阈值二值化（`.point(lambda p: 255 if p > 140 else 0)`）在不同屏幕/ClearType 下效果完全不同。细笔画字（"查"、"找"）被截断消失。Tesseract 4.x 内部有自适应二值化，手写固定阈值是多余且有害的。

**解法**：三通道预处理管道 — ①灰度直送 Tesseract → ②阈值 100 → ③阈值 140（兼容）。任一通道有结果即返回。匹配目标从"网络查找"扩展到"络找"。

**避坑**：不要手写图像二值化阈值，Tesseract 自己能做更好。多通道回退是跨机器兼容的关键。

---

## 下拉菜单点击无响应

**问题**：OCR 找到文字但点击后弹窗不出现。

**原因**：`PostMessageW`/`SendMessageW` 发送窗口消息点击，微信 CEF 渲染的下拉菜单不响应窗口消息。

**解法**：改用 `SetCursorPos` + `mouse_event` 硬件级鼠标事件。点击前后保存/恢复用户鼠标位置。

**避坑**：CEF 应用只响应硬件级输入，不要用窗口消息模拟点击。

---

## 停止按钮延迟

**问题**：点停止后状态栏显示"正在停止..."持续数秒。

**原因**：`pytesseract.image_to_data()` 是阻塞 C 调用，期间线程无法检查 `_stop_event`。所有 `time.sleep()` 应改用 `_sleep()`。

**解法**：WeChatController 所有等待改为 `self._sleep()`（Event.wait 实现）。`check_single_account` 在 OCR/点击完成后立即检查 `_stop_event`。异常发现后改为非阻塞通知面板。

**避坑**：任何等待逻辑必须可中断，`time.sleep()` 在子线程中不可用。

---

## exe 换机器运行提示 Tesseract 不可用

**问题**：启动后日志显示"Tesseract OCR 不可用"。

**原因**：PyInstaller 打包的 tesseract.exe 依赖 VC++ 运行时。极少数精简版 Windows 缺少此运行时。

**解法**：安装 [VC++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

---

## OCR 同台电脑部分账号失败

**问题**：同一台电脑，有的账号正常，有的 OCR 识别不到。日志显示碎片如 `网|络|查|手|机`。

**原因**：ClearType 子像素渲染导致 Tesseract 对笔画密集的字（"找"、"到"、"录"）时灵时不灵。不同 ID 号码长度不同，文字像素位置微移，渲染结果不同。

**解法**：下拉匹配多级回退 + 弹窗 7 关键字回退 + 每次 OCR 前重试一次（间隔 1.5s）。"QQ号"是英文数字组合，Tesseract 识别稳定。

---

## 弹窗假阳性连锁失败（已修复）

**问题**：OCR 将正常号误判为异常，且一旦出错，后续所有号都被判为同一种异常。

**原因**：`close_popup()` 只搜索 WindowControl（独立窗口），漏掉了 PaneControl（CEF 内部面板）。当弹窗是内部面板时：`check_popup_status()` 通过 PaneControl 找到了弹窗 → OCR 假阳性 → `close_popup()` 只搜 WindowControl → 找不到 → 误以为已关闭 → 弹窗残留 → 下一个号 UIA 再搜到同一个旧弹窗 → 连锁全错。

**解法**：
- `close_popup()` 补全 Level 2 PaneControl 搜索，与 `check_popup_status()` 对齐
- ESC 3次关闭失败后，改用鼠标点击微信窗口左侧关闭面板
- 关闭后两级搜索最终验证
- 弹窗 OCR 增加重试 + 关键词从3个扩到7个

**避坑**：任何配对的查找/关闭操作，搜索逻辑必须完全一致。

---

## OCR 换电脑截图区域偏移

**问题**：换电脑后下拉菜单 OCR 识别率大幅下降。

**原因**：截图区域用硬编码像素偏移（`win_top + 40`、宽度 `400`、高度 `340`），不同 DPI/分辨率下对不上实际 UI。

**解法**：改为按窗口尺寸比例计算：`int(win_w * 0.02)` ~ `int(win_w * 0.78)`、`int(win_h * 0.08)` ~ `int(win_h * 0.72)`。

**避坑**：永远不要硬编码像素坐标，用窗口比例。

---

## 日志停在5月24日，之后再无新日志

**问题**：程序在 Windows 上双击没反应，没有新日志。Mac 上拉下来的日志快照也停在 5 月 24 日。

**原因**：3 层叠加 — ① Windows 上有 4 个僵尸进程（Session 0 和 Session 6 都有），占着剪贴板/COM 导致新进程卡死 ② 启动后第一条日志要等 500ms 才写（`_check_runtime_env`），启动阶段崩溃无迹可查 ③ 日志 `flush()` filter 没返回 `True` 导致所有日志被 drop ④ `ctypes` 没导入导致 `NameError` 崩溃。

**解法**：运行时入口加最早期的 `logger.info`（GUI 创建前）→ 每条日志立即 `flush()` → 全局 `try/except` 捕获启动崩溃写日志 + 弹窗 → 单实例锁防多开。

**避坑**：Logger setup 中 filter 必须返回 `True`，返回 `None` 等价于丢弃日志。GUI 应用启动阶段就需要日志，不能等到 GUI 初始化后再写。

---

## UIA Exists() COM 死锁 — 检查线程永久卡死

**问题**：程序启动正常、GUI 显示正常，但点开始检查后卡在"正在定位微信窗口..."或弹窗检测，不写日志、不报错。

**原因**：`uiautomation` 的 `Exists(maxSearchSeconds=0.8)` 依赖 COM RPC 消息分发。`maxSearchSeconds` 是**咨询性**的超时参数，不是硬时限 — 它通过 COM 消息循环计数计时，如果微信 CEF 界面线程不响应，COM RPC 调用就永久阻塞。

**解法**：`_safe_uia_exists()` — 在独立 daemon 线程中执行 `control.Exists()`，主线程用 `thread.join(timeout+1)` 做硬时限。超时未返回 = COM 死锁，放弃线程返回 `False`。超时线程会泄漏 COM 资源，但 daemon 模式下进程退出时回收。≥20 次超时记录 error 建议重启。

**避坑**：永远不要信任 uiautomation 的超时参数。任何 UI Automation 调用都要包装成可中断的形式。Win32 `FindWindowW` 零依赖无超时风险，优先使用。用 `ctypes.windll.kernel32.OpenProcess` 检查 PID 存活（替代 `GetLastError`）。

---

## close_popup() UIA 验证耗时 22 秒

**问题**：每个号正常检测仅 ~15 秒，但关闭弹窗花了 22 秒（占总耗时 60%），全是 UIA 搜索验证的时间。

**原因**：`close_popup()` 在弹窗已被 ESC 关闭后，仍用 UIA 搜索 5 个 WindowControl + 5 个 PaneControl 来"验证"弹窗关闭。每个 `Exists()` 硬超时 3 秒，全超时 = 30 秒。

**解法**：`close_popup` 改掉全部 UIA 调用 — ESC×3 后仅一次 `FindWindowW("添加朋友")` 毫秒级验证。弹窗残留由下个号的 `check_popup_status` 兜底。弹窗存在时 ESC 前先 `SetForegroundWindow` 确保焦点正确。

**避坑**：已验证存在的 UI 元素，不需要用同种方式再验证"消失"。Win32 API 毫秒级可靠且无 COM 依赖。

---

## OCR 下拉检测重复扫描 — 同一张截图 OCR 6 次

**问题**：日志显示 153 个垃圾 OCR 条目被重复识别了 6 次，每次 3 秒 = 18 秒浪费，最终全失败。

**原因**：`click_dropdown_item` 对每个关键词（5 个）都调用 `_ocr_find_text`，而 `_ocr_find_text` 内部每次都调 `_ocr_get_text_entries` 重新 OCR。同一张截图被扫了 5 次，如果重试则 10 次。

**解法**：每张截图只 OCR 一次 → 得到 `entries, rows, full_text` → 5 个关键词对同一份结果用 `_find_text_in_entries` 匹配。从最坏 5×3 管道×2 截图 = 30 次 Tesseract，降到 2 次。

**避坑**：OCR 结果对同一张图是不变的，多个关键词应该共享同一次 OCR 输出，而不是每个关键词重新扫描。

---

## _sleep() 不检查暂停信号 — 点击暂停后 20 秒才生效

**问题**：点暂停后状态栏立即显示"已暂停"，但实际检查线程要继续跑完当前整个长操作（最多 20 秒）才真正暂停。

**原因**：`_sleep()` 只检查 `_stop_event`，完全忽略 `_pause_event`。`click_dropdown_item`（2s sleep + 2 次 OCR ~6s）和 `check_popup_status`（2s sleep + UIA 搜索 + OCR ~8s）之间无暂停检查点。

**解法**：`_sleep()` 改为每 0.2 秒轮询 `_stop_event` 和 `_pause_event`，暂停时 `_pause_event.wait()` 冻结。短延迟（< 0.5s）直接 `time.sleep()` 跳过轮询精度损失。暂停恢复后自动 `activate_window()` 再重查。

**避坑**：暂停/停止信号必须是最高优先级的检查，任何 `sleep` 或等待循环都必须同时检查两个信号。

---

## 单实例 Mutex 在 PyInstaller 下失效

**问题**：`Global\WeChatChecker_SingleInstance` Named Mutex + `GetLastError()` 检测不到多开，Windows 上同时 2-4 个进程。

**原因**：`ctypes.windll.kernel32.GetLastError()` 返回值可能被 Python 内部调用覆盖。`Global\` 前缀在非管理员下创建失败但 `CreateMutexW` 仍返回 handle。

**解法**：改用**文件锁**（`os.open(O_CREAT|O_EXCL)`）+ PID 写入 + `OpenProcess` 检查存活 + 心跳文件（30 秒过期 = 判定卡死可接管）。

**避坑**：Win32 Mutex 在 PyInstaller 单文件模式下不可靠。文件锁更简单可靠，加上心跳可以区分"正常运行"和"卡死"。

---

## Telegram 通知换电脑失效

**问题**：旧电脑通知正常，新电脑完全收不到。

**原因**：`urllib.request.urlopen()` 无显式代理配置，直连 `api.telegram.org`（国内被封锁）。旧电脑有系统代理，新电脑没有。

**解法**：TelegramNotifier 支持可选代理参数，GUI 面板加代理输入框。

---

## 窗口未置顶导致键盘操作发错窗口

**问题**：偶尔一整批账号都异常，日志无明显错误。

**原因**：`SetForegroundWindow` 可能被 Windows 静默拒绝（其他进程拦截），后续 Ctrl+F/输入都发到了错误窗口。

**解法**：`activate_window` 用 `GetForegroundWindow()` 验证置顶结果，失败重试；`focus_search_box` 发快捷键前检查窗口是否在前台。

---

## 微信窗口最小化后 OCR 截图全空白

**问题**：输入微信号后每次都报"搜索无结果或无法点开详情"，日志显示窗口位置在 `left=-32000 top=-32000` 尺寸仅 `160x28`，截图区域全在负坐标。

**原因**：Windows 将最小化窗口移至屏幕外负坐标（-32000）。`SetForegroundWindow` 无法恢复最小化窗口。之前 commit `acc7c67` 移除了所有 `ShowWindow(SW_RESTORE)` 以防破坏 CEF 渲染，但没考虑窗口已最小化的场景。

**解法**：`activate_window()` 中加 `IsIconic(hwnd)` 检测 — 仅窗口最小化时调用 `ShowWindow(hwnd, SW_RESTORE)` 恢复，已可见窗口保持纯 `SetForegroundWindow`。

**避坑**：`ShowWindow(SW_RESTORE)` 对已可见的 CEF 窗口会破坏渲染，但对最小化窗口是唯一恢复方法。用 `IsIconic()` 条件判断区分两种场景。

---

## 单实例文件锁 TOCTOU 竞态导致双开 + 日志停写

**问题**：Windows 上同时存在两个 WeChatChecker 进程，`checker.log` 停在数小时前不再更新，心跳文件也停更。用户看到"日志不实时更新"。

**原因**：`_acquire_lock` 的「卡死接管」逻辑有 TOCTOU 竞态。获锁成功后**先写 PID 到锁文件，稍后才在检查循环里写心跳**。新实例 B 探活时若旧实例 A 刚启动、心跳文件还不存在，`_check_heartbeat_stale()` 走 `except` 分支返回 `True`（"无心跳文件→认为已死"），误判 A 卡死 → 删锁重建 → **双开**。两进程争抢同一份 `checker.log`，一方 `os.rename` 滚动后另一方 stream 指向旧 inode，主日志文件停滞。

**解法**：调整写入顺序——新增 `_write_heartbeat()` helper，**三处写 PID 之前先写一次心跳**。建立因果链：B 读到锁里 PID ⟹ A 已 `write(PID)` ⟹ A 已 `write(heartbeat)`（写心跳在写 PID 之前）⟹ B 探活时心跳必然新鲜 ⟹ B 退出。B 读不到 PID（锁文件空）⟹ `int('')` 抛 `ValueError` ⟹ 走 except ⟹ B 退出。两种情况 B 都不误接管。真卡死场景（A 拿锁 30s 不更新心跳）仍能正确接管。commit 9f594ad。

**避坑**：单实例锁若用「PID + 心跳探活」模式，心跳必须在写 PID 之前落盘，否则旧实例启动期的「心跳空窗」会被新实例误判为卡死而强制接管。这是经典的 check-then-act 竞态，靠调整写入顺序建立 happens-before 因果链即可根治，无需 sleep 二次确认。另外：多进程写同一日志文件会因 `os.rename` 滚动产生 inode 错位，日志隔离不如从根上防双开。


---
tags: [Python, 踩坑, WeChatChecker]
date: 2026-06-20
project: WeChatChecker
---

## scp -r 覆盖 wechat_ids.txt

**问题**：每次构建产物含默认 `wechat_ids.txt`，`scp -r` 会覆盖 Windows 上的真实微信号列表
**原因**：spec 将 `wechat_ids.txt` 打入 exe 目录，传输时连同覆盖
**解法**：scp 只传 `WeChatChecker.exe`，不传其他文件
**避坑**：以后传产物到 Windows 时，手动指定单文件 `scp win:"..."`，不用 `scp -r`


---
tags: [Python, Windows, GUI自动化, 守护进程, WeChatChecker]
date: 2026-06-20
project: WeChatChecker
---

## 24h 无人值守守护架构 — GUI 自动化不能用 NSSM/服务

**问题**：微信检查工具要在 Windows 上 24 小时无人值守跑，需要保证主程序崩溃/卡死能自动恢复，且系统层不干扰运行。

**原因**：三重坑叠加 ——
1. **主程序是 tkinter + uiautomation 的 GUI 自动化，必须跑在交互桌面会话（Session 1+）**。Windows 服务（含 NSSM 包裹）跑在 Session 0，没有桌面，uiautomation 找不到微信窗口直接废。所以"装个守护服务"这条路对 GUI 自动化是错的。
2. **RDP 直接叉掉断开 → 会话变 disconnected → GUI 找不到桌面会挂**。必须用 `tscon <会话ID> /dest:console` 切到 console 保活。
3. **主程序心跳只在"每轮开始+每批结束"更新，批次间等待 20-30 分钟期间不更新**。如果 watchdog 卡死阈值照搬单实例锁的 30s，会在批次等待时每批误杀一次。

**解法**：复用主程序已有的心跳文件 `.instance.heartbeat` 做三态判断的 watchdog（`watchdog.py`）：
- 进程不在 → 拉起 `python main.py`（用 pythonw.exe + DETACHED_PROCESS 分离，无黑窗、与 watchdog 解耦）
- 进程在 + 心跳新鲜 → 正常不动
- 进程在 + 心跳过期(>60min) → COM 死锁/卡死 → 杀进程树 + 拉起

系统层用 `setup_keepalive.ps1` 一键配：防睡眠(powercfg /change ... 0)、防锁屏(注册表 ScreenSaveActive=0)、防更新重启(组策略 AUOptions=2 + NoAutoRebootWithLoggedOnUsers=1 + 活动时间 08-23)、任务计划程序"登录时启动 watchdog"（**关键：选 Interactive 登录而非 SYSTEM，否则落到 Session 0**）。

**避坑**：
- GUI 自动化的守护**绝不能用 Session 0 服务**，必须"开机登录 + 会话内 watchdog 拉起"。这是和命令行/headless 自动化守护最大的区别。
- watchdog 卡死阈值必须 > `batch_interval_max` + 余量（这里 30min → 阈值设 60min），否则批次等待时不更新心跳会被误杀。先搞清主程序心跳更新的真实频率再定阈值。
- 加崩溃风暴保护（1 小时重启超 N 次停止自动拉起），否则恶性崩溃会把日志刷爆、把 Telegram 通知刷屏，且掩盖真正的根因。
- watchdog 拉起用 `pythonw.exe`（无控制台窗）+ `DETACHED_PROCESS`，否则 watchdog 退出会连带杀掉子进程。
- 复用主程序已有基础设施（心跳文件、单实例锁）比另起一套健康检查更可靠——单实例锁的心跳探活接管逻辑正好和 watchdog 重启逻辑互补不冲突。
- 「异常只通知不停止」其实主程序代码早就是这样了（`checker_engine.py` abnormal 分支只等 5s 继续），README 写的"并停止"是过时描述。24h 无人值守只需把 `config.json` 的 `sound_enabled` 设 false（避免蜂鸣到天亮），通知走 Telegram/Bark。
