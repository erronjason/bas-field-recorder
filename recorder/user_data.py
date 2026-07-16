import os
import sys
from pathlib import Path


def app_data_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    root = base / "DiarizedTranscriber"
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace() -> Path:
    p = app_data_root() / "workspace"
    p.mkdir(exist_ok=True)
    return p


def tmp_dir() -> Path:
    p = app_data_root() / "tmp"
    p.mkdir(exist_ok=True)
    return p


def backend_dir() -> Path:
    p = app_data_root() / "backend"
    p.mkdir(exist_ok=True)
    return p


def models_dir() -> Path:
    p = app_data_root() / "models"
    p.mkdir(exist_ok=True)
    return p


def settings_path() -> Path:
    return app_data_root() / "settings.json"


def deletions_log_path() -> Path:
    return app_data_root() / "deletions.log"
