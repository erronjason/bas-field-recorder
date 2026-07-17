# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Diarized Transcriber GUI (Windows, --onedir).
# Build with:  build.ps1  (from the project root)
#
# Qt modules that are NOT needed are excluded below to keep the output lean.
# Add back any module here if a future feature needs it.

block_cipher = None

a = Analysis(
    ["recorder_gui.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle the server script so server_manager can locate it via
        # _app_resource_path() whether frozen or running from source.
        ("transcription_server.py", "."),
        ("transcribe.py", "."),
        # BAS wordmark SVGs rendered in the setup wizard header.
        ("recorder/resources/brand/BAS-landscape.svg", "recorder/resources/brand"),
        ("recorder/resources/brand/BAS-stacked.svg",   "recorder/resources/brand"),
        # Global visual system stylesheet.
        ("field_recorder.qss", "."),
    ],
    hiddenimports=[
        # PyAudioWPatch ships native DLLs; ensure its package is collected.
        "pyaudiowpatch",
        # scipy.signal is imported lazily (inside do_mixdown).
        "scipy.signal",
        "scipy.signal._upfirdn",
        "scipy.signal._upfirdn_apply",
        # soundfile uses cffi; ensure the extension is collected.
        "soundfile",
        "_soundfile_data",
        # SVG rendering for setup wizard header.
        "PySide6.QtSvg",
        # Audio playback in the Records Viewer.
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy Qt modules we don't use.
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtLocation",
        "PySide6.QtNfc",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSerialBus",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtSql",
        "PySide6.QtStateMachine",
        "PySide6.QtTest",
        "PySide6.QtVirtualKeyboard",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        # Exclude transcription-only deps — they are provided by the backend server.
        "whisper",
        "whisperx",
        "pyannote",
        "assemblyai",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
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
    name="FieldRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window — tray-only app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FieldRecorder",
)
