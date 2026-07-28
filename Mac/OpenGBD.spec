# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=[('bin/ffmpeg_arm64', 'bin')],
    datas=[],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='OpenGBD',
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OpenGBD',
)
app = BUNDLE(
    coll,
    name='OpenGBD.app',
    icon='icon.icns',
    bundle_identifier='com.lanccj.openGBD',
    info_plist={
        'NSCameraUsageDescription': '需要访问相机用于 GB28181 视频采集与推流',
        'NSCameraUseContinuityCameraDeviceType': True,
    }
)
