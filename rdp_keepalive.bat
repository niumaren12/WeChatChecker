@echo off
chcp 65001 >nul
title RDP 会话保活 - 切到 console

:: ============================================
:: RDP 断开时会话保活工具
:: ============================================
:: 背景：GUI 自动化（微信检查工具）依赖"交互桌面会话"。
:: 直接叉掉 RDP 窗口 → 会话变 disconnected → GUI 找不到桌面会挂。
:: 本脚本把当前 RDP 会话切到 console，然后断开，会话保持 active，
:: GUI 自动化继续在 console 会话里跑，不受影响。
::
:: 用法：远程维护完后，运行本脚本，看到"会话已切换"提示后
::       即可安全断开（脚本会自动断开 RDP 连接）。
:: 需要管理员权限。
:: ============================================

:: 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 需要管理员权限，请右键 → 以管理员身份运行
    pause
    exit /b 1
)

echo ===== RDP 会话保活 =====
echo.

:: 列出所有会话
echo 当前会话列表：
query session
echo.

:: 获取当前活动会话 ID（active 的那一个，就是你的 RDP 会话）
:: query session 输出格式：会话名 用户名 ID 状态 类型 设备
:: 用 findstr 找 active 行，再解析 ID
for /f "tokens=3" %%i in ('query session ^| findstr /i "active"') do (
    set "SESSION_ID=%%i"
    goto :found
)

echo [错误] 未找到 active 会话，可能你不在 RDP 会话中
pause
exit /b 1

:found
echo 找到活动会话 ID: %SESSION_ID%
echo.
echo 即将会话切换到 console 并断开 RDP...
echo 切换后此 RDP 窗口会自动断开，会话保持 active 继续运行。
echo.
pause

:: tscon 把会话切到 console，当前 RDP 连接随之断开
tscon %SESSION_ID% /dest:console

if %errorlevel% neq 0 (
    echo [错误] tscon 切换失败，errorlevel=%errorlevel%
    echo 可能原因：会话 ID 错误 / 权限不足
    pause
    exit /b 1
)

:: 正常情况下执行到这 RDP 已断开，下面看不到输出
echo 会话已切换到 console，RDP 将断开。
