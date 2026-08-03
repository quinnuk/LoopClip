# -*- mode: python ; coding: utf-8 -*-
#
# cv2 (opencv-python-headless) and scikit-image both ship compiled
# extensions and submodules that PyInstaller's default static analysis
# doesn't reliably find on its own - without collect_all() here, the
# built .exe launches fine but crashes specifically when loop detection
# runs (import errors for cv2 or skimage.* deep inside loop_detector.py).
# scipy is included too since skimage depends on it and it has the same
# kind of hidden-import gaps.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ("cv2", "skimage", "scipy"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LoopClip',
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
    icon=['icon.ico'],
)
