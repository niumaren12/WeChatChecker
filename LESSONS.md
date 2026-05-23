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

## Telegram 通知换电脑失效

**问题**：旧电脑通知正常，新电脑完全收不到。

**原因**：`urllib.request.urlopen()` 无显式代理配置，直连 `api.telegram.org`（国内被封锁）。旧电脑有系统代理，新电脑没有。

**解法**：TelegramNotifier 支持可选代理参数，GUI 面板加代理输入框。

---

## 窗口未置顶导致键盘操作发错窗口

**问题**：偶尔一整批账号都异常，日志无明显错误。

**原因**：`SetForegroundWindow` 可能被 Windows 静默拒绝（其他进程拦截），后续 Ctrl+F/输入都发到了错误窗口。

**解法**：`activate_window` 用 `GetForegroundWindow()` 验证置顶结果，失败重试；`focus_search_box` 发快捷键前检查窗口是否在前台。
