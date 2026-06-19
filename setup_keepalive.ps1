# 微信检查工具 - Windows 24h 保活一键配置
# ============================================
# 在 Windows 上以管理员身份运行：右键 → 用 PowerShell 运行（管理员）
# 完成后机器具备：防睡眠/防锁屏/防更新重启/登录自启 watchdog
#
# 本脚本只配置 Windows 系统层，不修改主程序代码。
# 开机自动登录(AutoLogon)需手动配，见脚本末尾说明。

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$WatchdogPath = Join-Path $ScriptDir "watchdog.py"

Write-Host "`n===== 微信检查工具 24h 保活配置 =====" -ForegroundColor Cyan

# ---------- 1. 防睡眠/休眠/关硬盘 ----------
Write-Host "`n[1/4] 配置电源策略（永不睡眠/休眠/关硬盘）..." -ForegroundColor Yellow
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
# 显示器可关闭（省电，不影响 GUI 自动化）
powercfg /change monitor-timeout-ac 10
Write-Host "  已设置：接电源时永不睡眠/休眠/关硬盘，显示器 10 分钟后关闭" -ForegroundColor Green

# ---------- 2. 关屏保/防锁屏 ----------
Write-Host "`n[2/4] 关闭屏保、防止自动锁屏..." -ForegroundColor Yellow
# 当前用户屏保关闭
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name ScreenSaveActive -Value "0" -Type String
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name ScreenSaverIsSecure -Value "0" -Type String
# 系统空闲超时不锁屏
if (-not (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System")) {
    New-Item -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Force | Out-Null
}
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name InactivityTimeoutSecs -Value 0 -Type DWord
Write-Host "  已关闭屏保、系统空闲不自动锁屏" -ForegroundColor Green
Write-Host "  注意：若设了 Win+L 锁屏习惯请勿用，锁屏会让部分 GUI 自动化失效" -ForegroundColor DarkYellow

# ---------- 3. 防更新强制重启（专业版组策略） ----------
Write-Host "`n[3/4] 配置 Windows 更新策略（防凌晨强制重启）..." -ForegroundColor Yellow
$wuPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
if (-not (Test-Path $wuPath)) {
    New-Item -Path $wuPath -Force | Out-Null
}
# AUOptions=2 = 通知下载并通知安装，不会自动重启
Set-ItemProperty -Path $wuPath -Name AUOptions -Value 2 -Type DWord
# NoAutoRebootWithLoggedOnUsers=1 = 有用户登录时不自动重启
Set-ItemProperty -Path $wuPath -Name NoAutoRebootWithLoggedOnUsers -Value 1 -Type DWord
# 活动时间设为全天运行时段 08:00-23:00，避开重启窗口
$activePath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
if (-not (Test-Path $activePath)) {
    New-Item -Path $activePath -Force | Out-Null
}
Set-ItemProperty -Path $activePath -Name ActiveHoursStart -Value 8 -Type DWord
Set-ItemProperty -Path $activePath -Name ActiveHoursEnd -Value 23 -Type DWord
Set-ItemProperty -Path $activePath -Name SetActiveHours -Value 1 -Type DWord
Write-Host "  已设置：更新=通知安装不自动重启，有登录用户不重启，活动时间 08-23" -ForegroundColor Green

# ---------- 4. 任务计划程序：登录时启动 watchdog ----------
Write-Host "`n[4/4] 创建任务计划程序：登录时启动 watchdog..." -ForegroundColor Yellow

if (-not (Test-Path $WatchdogPath)) {
    Write-Host "  [错误] 未找到 watchdog.py: $WatchdogPath" -ForegroundColor Red
    Write-Host "  请确保 watchdog.py 与本脚本在同一目录" -ForegroundColor Red
    exit 1
}

# 找 pythonw.exe（无控制台窗口，watchdog 常驻不弹黑窗）
$PyExe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $PyExe) {
    $Py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($Py) {
        $PyExe = Join-Path (Split-Path -Parent $Py) "pythonw.exe"
        if (-not (Test-Path $PyExe)) { $PyExe = $Py }
    }
}
if (-not $PyExe) {
    Write-Host "  [错误] 未找到 Python，请先安装 Python 3.12+ 并加入 PATH" -ForegroundColor Red
    exit 1
}
Write-Host "  使用解释器: $PyExe" -ForegroundColor DarkGray

$TaskName = "WeChatCheckerWatchdog"
$Action = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$WatchdogPath`"" -WorkingDirectory $ScriptDir
# 触发器：用户登录时（当前用户）
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# 设置：失败每 5 分钟重启，运行不限时
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
# 主体：仅用户登录时运行（不选 SYSTEM，否则落到 Session 0 拉不起 GUI）+ 最高权限
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

# 先删旧任务（幂等）
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "微信检查工具守护进程，登录时启动，崩溃/卡死自动拉起" | Out-Null
Write-Host "  已创建任务: $TaskName （登录时自动启动，最高权限）" -ForegroundColor Green

# ---------- 摘要 ----------
Write-Host "`n===== 配置完成 =====" -ForegroundColor Cyan
Write-Host "下一步操作：" -ForegroundColor White
Write-Host "  1. 立即启动 watchdog（不必等重启登录）：" -ForegroundColor White
Write-Host "     Start-ScheduledTask -TaskName WeChatCheckerWatchdog" -ForegroundColor DarkGray
Write-Host "  2. 查看 watchdog 日志：" -ForegroundColor White
Write-Host "     Get-Content $ScriptDir\logs\watchdog.log -Tail 20 -Wait" -ForegroundColor DarkGray
Write-Host "  3. 确认微信已登录，主程序会被 watchdog 自动拉起" -ForegroundColor White
Write-Host ""
Write-Host "可选 - 开机自动登录到桌面（断电恢复后全自动）：" -ForegroundColor Magenta
Write-Host "  本脚本不自动配置（涉及密码）。需手动执行：" -ForegroundColor DarkYellow
Write-Host "  netplwiz → 取消勾选'要使用本计算机，用户必须输入用户名和密码' → 输入密码" -ForegroundColor DarkGray
Write-Host "  或用 Sysinternals AutoLogon 工具（密码加密存储，更安全）" -ForegroundColor DarkGray
Write-Host ""
Write-Host "可选 - RDP 断开会话保活（远程维护完跑这个再断开）：" -ForegroundColor Magenta
Write-Host "  见同目录 rdp_keepalive.bat" -ForegroundColor DarkGray
Write-Host ""
