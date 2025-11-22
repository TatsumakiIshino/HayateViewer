# -*- mode: python ; coding: utf-8 -*-
# HayateViewer macOS PyInstaller Spec File

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# アプリケーション名
app_name = 'HayateViewer'

# データファイルの収集
datas = [
    ('app/shaders', 'app/shaders'),
]

# 隠しインポートの収集
hiddenimports = [
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtOpenGL',
    'OpenGL',
    'OpenGL.GL',
    'OpenGL.GLU',
    'PIL',
    'PIL.Image',
    'numpy',
    'cv2',
]

# OpenGLアクセラレーションの追加インポート
hiddenimports += collect_submodules('OpenGL')
hiddenimports += collect_submodules('OpenGL.GL')

# Pillow画像フォーマットプラグイン
hiddenimports += collect_submodules('PIL')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows専用モジュールを除外
        'comtypes',
        'pywin32',
        'win32com',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)

# macOS .app バンドルの作成
app = BUNDLE(
    coll,
    name=f'{app_name}.app',
    icon=None,  # アイコンファイルがあれば指定: 'icon.icns'
    bundle_identifier='com.hayateviewer.app',
    version='1.0.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'CFBundleName': app_name,
        'CFBundleDisplayName': app_name,
        'CFBundleExecutable': app_name,
        'CFBundleIdentifier': 'com.hayateviewer.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSMinimumSystemVersion': '10.13.0',  # macOS High Sierra以降
        # ファイルタイプの関連付け（オプション）
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Archive Files',
                'CFBundleTypeRole': 'Viewer',
                'LSHandlerRank': 'Default',
                'LSItemContentTypes': ['public.zip-archive', 'com.rarlab.rar-archive'],
            }
        ],
    },
)
