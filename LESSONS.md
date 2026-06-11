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
