@echo off
chcp 65001 >nul
title 微信账号检查工具 - 打包程序

:: ============================================
:: 微信账号状态检查工具 - 一键打包脚本
:: 在 Windows 上右键 → 以管理员身份运行
:: ============================================

echo ===== 微信账号状态检查工具 - 打包脚本 =====
echo.

:: 让用户能实时看到输出
echo === [1] 检查 Python 环境 ===

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python 未安装或未添加到 PATH
    echo.
    echo 请访问 https://www.python.org/downloads/ 下载 Python 3.12+
    echo 安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

python --version
echo Python 环境正常

:: 检查 uiautomation 是否已装（快速路径：已装则跳过安装）
pip show uiautomation >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo === [2/4] 依赖已安装，跳过安装步骤 ===
    goto skip_install
)

echo.
echo === [2/4] 安装依赖（首次需要联网下载）===
echo 正在安装 uiautomation psutil pyinstaller...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败，请检查网络连接
    echo 可尝试手动运行: pip install -r requirements.txt
    pause
    exit /b 1
)
echo 依赖安装完成

:skip_install

echo.
echo === [3/4] 生成程序图标 ===
python generate_icon.py
if %errorlevel% neq 0 (
    echo [警告] 图标生成失败，将使用默认图标
)

echo.
echo === [4/4] 打包为 exe（需等待1-3分钟）===
echo 正在打包...

pyinstaller --onefile --windowed --icon=app.ico --name WeChatChecker --add-data "config.json;." --add-data "wechat_ids.txt;." main.py
if %errorlevel% neq 0 (
    echo [错误] 打包失败
    echo 常见原因：
    echo   - 磁盘空间不足
    echo   - 杀毒软件拦截
    echo   - Python 版本不兼容（推荐 3.12）
    pause
    exit /b 1
)

echo.
echo === 复制配置文件 ===
copy config.json dist\ >nul 2>&1
copy wechat_ids.txt dist\ >nul 2>&1

echo.
echo ====================================
echo     打包完成！
echo ====================================
echo.
echo 可执行文件: %cd%\dist\WeChatChecker.exe
echo 配置文件:   %cd%\dist\config.json
echo 微信号列表: %cd%\dist\wechat_ids.txt
echo.
echo 使用方法：
echo 1. 打开微信 PC 客户端并登录
echo 2. 运行 WeChatChecker.exe
echo 3. 在界面中配置参数
echo 4. 点击"开始检查"
echo.
pause
