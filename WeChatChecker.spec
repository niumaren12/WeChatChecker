# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec 文件 — 显式管理所有模块，不依赖 import 分析"""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('wechat_ids.txt', '.'),
    ],
    hiddenimports=[
        'wechat_controller',
        'checker_engine',
        'config_manager',
        'logger_setup',
        'ip_panel',
        'abnormal_panel',
        'PIL',
        'PIL.Image',
        'PIL.ImageOps',
        'PIL.ImageFilter',
        'mss',
        'pytesseract',
        'psutil',
        'uiautomation',
        'telegram_notifier',
        'ip_switcher',
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# 将 tesseract_bundle 目录打包（含 exe/dll/tessdata）
a.datas += [('tesseract/tesseract.exe', 'tesseract_bundle/tesseract.exe', 'DATA')]

import glob
import os
for root, dirs, files in os.walk('tesseract_bundle'):
    for f in files:
        src = os.path.join(root, f)
        dst = os.path.join('tesseract', os.path.relpath(src, 'tesseract_bundle'))
        if not a.datas or not any(d[0] == dst for d in a.datas):
            a.datas.append((dst, src, 'DATA'))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WeChatChecker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
