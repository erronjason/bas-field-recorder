import os
import sys
from pathlib import Path


def bureau_root() -> Path:
    """Root data directory for all Bureau of Applied Science instruments."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        root = base / "BureauOfAppliedScience"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Bureau of Applied Science"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        root = base / "bureau-of-applied-science"
    root.mkdir(parents=True, exist_ok=True)
    return root


def app_data_root() -> Path:
    return bureau_root()


def instrument_dir() -> Path:
    """Field Recorder's own data directory (backend, models, settings)."""
    p = bureau_root() / "instruments" / "field-recorder"
    p.mkdir(parents=True, exist_ok=True)
    return p


def records_dir() -> Path:
    """The records store — shared across all Bureau instruments."""
    p = bureau_root() / "records"
    p.mkdir(exist_ok=True)
    return p


def workspace() -> Path:
    """Alias for records_dir()."""
    return records_dir()


def tmp_dir() -> Path:
    p = bureau_root() / "tmp"
    p.mkdir(exist_ok=True)
    return p


def backend_dir() -> Path:
    p = instrument_dir() / "backend"
    p.mkdir(exist_ok=True)
    return p


def models_dir() -> Path:
    p = instrument_dir() / "models"
    p.mkdir(exist_ok=True)
    return p


def settings_path() -> Path:
    return instrument_dir() / "settings.json"


def onboarding_marker_path() -> Path:
    """Marker written once the first-run help has been shown."""
    return instrument_dir() / ".onboarding_shown"


def bureau_settings_path() -> Path:
    return bureau_root() / "settings.json"


def deletions_log_path() -> Path:
    return bureau_root() / "deletions.log"
