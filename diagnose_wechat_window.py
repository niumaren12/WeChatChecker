"""
微信窗口诊断脚本 — 列出所有顶层窗口的类名和标题
在 Windows 上运行: python diagnose_wechat_window.py
"""
import uiautomation as auto

print("=== 所有顶层窗口 ===\n")
root = auto.GetRootControl()
children = root.GetChildren()
for w in children:
    try:
        name = w.Name or "(无标题)"
        cls = w.ClassName or "(无类名)"
        rect = w.BoundingRectangle
        print(f"类名: {cls}")
        print(f"标题: {name}")
        print(f"位置: {rect}")
        print("---")
    except Exception:
        pass

print(f"\n共 {len(children)} 个顶层窗口")

# 特别标记可能含"微信"的窗口
print("\n=== 可能相关的窗口 ===")
for w in children:
    try:
        name = (w.Name or "").lower()
        cls = (w.ClassName or "").lower()
        if "微信" in name or "wechat" in name or "weixin" in name or "wechat" in cls or "weixin" in cls:
            print(f"类名: {w.ClassName}  标题: {w.Name}")
    except Exception:
        pass
