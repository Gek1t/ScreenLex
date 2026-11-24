# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

block_cipher = None

# Collect winrt, rapidocr, and minimal onnxruntime capi/runtime (avoiding transformers/quantization -> llvmlite/numba bloat)
winrt_datas, winrt_binaries, winrt_hiddenimports = collect_all('winrt')
rapid_datas, rapid_binaries, rapid_hiddenimports = collect_all('rapidocr_onnxruntime')
onnx_binaries = collect_dynamic_libs('onnxruntime')
onnx_hiddenimports = collect_submodules('onnxruntime.capi') + ['onnxruntime', 'onnxruntime.capi']

# Project root
proj_dir = os.path.abspath(SPECPATH)

# 仅打包运行时必需的数据文件（common_words.txt、offline_dict.db 与图标），严禁整目录拷贝 desktop/ 避免携带开发期 screenlex.db / .pyc / 死脚本
datas = [
    (os.path.join(proj_dir, 'desktop', 'common_words.txt'), 'desktop'),
    (os.path.join(proj_dir, 'desktop', 'offline_dict.db'), 'desktop'),
    (os.path.join(proj_dir, 'screenlex.ico'), '.'),
] + winrt_datas + rapid_datas

binaries = [] + winrt_binaries + rapid_binaries + onnx_binaries

hiddenimports = [
    'desktop',
    'desktop.app',
    'desktop.database',
    'desktop.dictionary_service',
    'desktop.dictionary_window',
    'desktop.floating_dock',
    'desktop.freeze_overlay',
    'desktop.gesture_overlay',
    'desktop.grammar_checker',
    'desktop.input_hook',
    'desktop.logger',
    'desktop.ocr_helpers',
    'desktop.offline_dict',
    'desktop.settings_window',
    'desktop.subtitle_engine',
    'desktop.subtitle_window',
    'desktop.ui_icons',
    'desktop.vault_window',
    'desktop.win_ocr',
    'PIL',
    'PIL.Image',
    'PIL.ImageGrab',
    'PIL.ImageEnhance',
    'qrcode',
    'qrcode.image.pil',
    'psutil',
    'sqlite3',
    'cv2',
    'onnxruntime',
    'rapidocr_onnxruntime',
] + winrt_hiddenimports + rapid_hiddenimports + onnx_hiddenimports

# Exclude unused data-science / deep-learning / compiler packages (保留 cv2 以支持打包版 RapidOCR 手写与描边字幕兜底)
excludes = [
    'torch', 'torchvision', 'torchaudio',
    'scipy', 'matplotlib', 'pandas', 'polars',
    'IPython', 'ipykernel', 'jupyter', 'notebook',
    'playwright', 'altair', 'sympy', 'xgboost',
    'fastmcp', 'mcp',
    'botocore', 'boto3', 'google', 'tensorboard',
    'llvmlite', 'numba', 'onnx', 'cryptography', 'ml_dtypes', 'tqdm', 'requests',
    'onnxruntime.transformers', 'onnxruntime.quantization', 'onnxruntime.backend', 'onnxruntime.tools',
    'win32ui', 'pywin', 'Pythonwin',
]

a = Analysis(
    ['run_desktop.py'],
    pathex=[proj_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 剔除未使用的大型 Qt6 插件与软渲染 DLL（仅依赖 QtCore/QtGui/QtWidgets）
_UNUSED_QT_DLLS = (
    'opengl32sw.dll',
    'qpdf.dll',
    'qt6pdf.dll',
    'qtvirtualkeyboardplugin.dll',
    'qt6quick.dll',
    'qt6qml.dll',
    'qt6qmlmodels.dll',
    'qt6qmlmeta.dll',
    'qt6qmlworkerscript.dll',
    'qt6virtualkeyboard.dll',
)
a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in _UNUSED_QT_DLLS]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenLex',
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
    icon=os.path.join(proj_dir, 'screenlex.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScreenLex',
)
