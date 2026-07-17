"""First-run setup wizard — installs the backend transcription stack.

Shown when the embedded Python is absent (or broken). All heavy work runs in a
background QThread so the UI stays responsive. Can be re-run from
Settings → Service → Reinstall transcription service.

Architecture: instead of requiring a system Python, we download the official
Python embeddable package (~10 MB) into the bureau data directory and install
all packages directly into it. No system changes; no UAC; no PATH modifications.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import user_data
from .icons import bas_icon, bas_svg_path

# ---------------------------------------------------------------------------
# Embedded Python config
# ---------------------------------------------------------------------------

_PYTHON_VERSION = "3.11.9"
_PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{_PYTHON_VERSION}/"
    f"python-{_PYTHON_VERSION}-embed-amd64.zip"
)
_GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# ---------------------------------------------------------------------------
# Package lists
# ---------------------------------------------------------------------------

_TORCH_CPU = [
    "torch",
    "torchaudio",
    "--index-url",
    "https://download.pytorch.org/whl/cpu",
]

_TORCH_CUDA = [
    "torch",
    "torchaudio",
    "--index-url",
    "https://download.pytorch.org/whl/cu124",
]

_STACK = [
    "whisperx",
    "pyannote.audio",
    "fastapi",
    "uvicorn[standard]",
    "assemblyai",
    "soundfile",
    "scipy",
    "numpy",
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _python_dir() -> Path:
    return user_data.backend_dir() / "python"


def _python_exe() -> Path:
    if sys.platform == "win32":
        return _python_dir() / "python.exe"
    return _python_dir() / "python"


def backend_ready() -> bool:
    """Return True if the embedded Python looks usable."""
    return _python_exe().exists()


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _InstallWorker(QThread):
    step_started = Signal(int, str)     # step index, description
    step_progress = Signal(int, int)    # step index, percent (0-100)
    step_done = Signal(int)             # step index
    step_failed = Signal(int, str)      # step index, error message
    all_done = Signal()

    def __init__(self, use_cuda: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._use_cuda = use_cuda
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True
        self.terminate()

    def run(self) -> None:
        try:
            self._run_steps()
        except Exception:
            pass  # individual steps already emit step_failed

    def _run_steps(self) -> None:
        python_dir = _python_dir()
        python_exe = _python_exe()

        # ── Step 0: Prepare Python runtime ──────────────────────────────
        if python_exe.exists():
            self.step_started.emit(0, "Python runtime found.")
            self.step_progress.emit(0, 100)
            self.step_done.emit(0)
        else:
            self.step_started.emit(0, f"Downloading Python {_PYTHON_VERSION}…")
            ok, err = self._download_python(python_dir, 0)
            if not ok:
                self.step_failed.emit(0, err)
                return
            self.step_done.emit(0)

        # ── Step 1: Bootstrap pip ────────────────────────────────────────
        self.step_started.emit(1, "Checking package manager…")
        ok, err = self._bootstrap_pip(python_dir, python_exe, 1)
        if not ok:
            self.step_failed.emit(1, err)
            return
        self.step_done.emit(1)

        # ── Step 2: Upgrade pip ──────────────────────────────────────────
        self.step_started.emit(2, "Upgrading pip…")
        ok, err = self._run(
            [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"],
            2, indeterminate=True,
        )
        if not ok:
            self.step_failed.emit(2, err)
            return
        self.step_done.emit(2)

        # ── Step 3: Install PyTorch ──────────────────────────────────────
        self.step_started.emit(3, "Installing PyTorch (this may take several minutes)…")
        torch_pkgs = _TORCH_CUDA if self._use_cuda else _TORCH_CPU
        ok, err = self._run(
            [str(python_exe), "-m", "pip", "install"] + torch_pkgs, 3,
        )
        if not ok:
            self.step_failed.emit(3, err)
            return
        self.step_done.emit(3)

        # ── Step 4: Install transcription stack ─────────────────────────
        self.step_started.emit(4, "Installing transcription stack…")
        ok, err = self._run(
            [str(python_exe), "-m", "pip", "install"] + _STACK, 4,
        )
        if not ok:
            self.step_failed.emit(4, err)
            return
        self.step_done.emit(4)

        # ── Step 5: Pre-download Whisper model ───────────────────────────
        self.step_started.emit(5, "Downloading Whisper model (medium, ~1.5 GB)…")
        script = (
            "import whisperx, torch; "
            "device = 'cuda' if torch.cuda.is_available() else 'cpu'; "
            "whisperx.load_model('medium', device, compute_type='int8')"
        )
        ok, err = self._run([str(python_exe), "-c", script], 5, indeterminate=True)
        if not ok:
            self.step_failed.emit(5, err)
            return
        self.step_done.emit(5)

        self.all_done.emit()

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    def _download_python(self, python_dir: Path, step: int) -> tuple[bool, str]:
        """Download and extract the Python embeddable package."""
        try:
            python_dir.mkdir(parents=True, exist_ok=True)
            zip_path = python_dir.parent / "_python.zip"

            # Stream download with progress
            with urllib.request.urlopen(_PYTHON_EMBED_URL, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunks: list[bytes] = []
                while True:
                    if self._cancel:
                        return False, "Cancelled."
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if total:
                        self.step_progress.emit(step, min(int(downloaded * 50 / total), 50))

            zip_path.write_bytes(b"".join(chunks))
            self.step_progress.emit(step, 55)

            # Extract
            self.step_started.emit(step, "Extracting…")
            with zipfile.ZipFile(str(zip_path), "r") as z:
                z.extractall(str(python_dir))
            zip_path.unlink(missing_ok=True)
            self.step_progress.emit(step, 80)

            # Enable site-packages: patch the ._pth file generated by the
            # embeddable package (e.g. python311._pth). The file disables site
            # by default; uncommenting `import site` activates it.
            for pth in python_dir.glob("python*._pth"):
                text = pth.read_text(encoding="utf-8")
                patched = text.replace("#import site", "import site")
                if patched == text:
                    # Line may lack the leading #; add if import site is missing
                    if "import site" not in text:
                        patched = text.rstrip() + "\nimport site\n"
                pth.write_text(patched, encoding="utf-8")

            self.step_progress.emit(step, 100)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _bootstrap_pip(
        self, python_dir: Path, python_exe: Path, step: int
    ) -> tuple[bool, str]:
        """Ensure pip is available in the embedded Python."""
        # Check if pip already works (re-run case)
        try:
            r = subprocess.run(
                [str(python_exe), "-m", "pip", "--version"],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0:
                self.step_progress.emit(step, 100)
                return True, ""
        except Exception:
            pass

        try:
            # Download get-pip.py
            self.step_started.emit(step, "Downloading pip bootstrap…")
            get_pip_path = python_dir / "_get-pip.py"
            with urllib.request.urlopen(_GET_PIP_URL, timeout=30) as resp:
                get_pip_path.write_bytes(resp.read())
            self.step_progress.emit(step, 40)

            # Run it
            self.step_started.emit(step, "Installing pip…")
            ok, err = self._run(
                [str(python_exe), str(get_pip_path)], step, indeterminate=True,
            )
            get_pip_path.unlink(missing_ok=True)
            return ok, err
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # Subprocess runner
    # ------------------------------------------------------------------

    def _run(
        self,
        cmd: list[str],
        step: int,
        indeterminate: bool = False,
    ) -> tuple[bool, str]:
        """Run a subprocess, stream stdout, emit progress signals."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            lines: list[str] = []
            percent = 0
            for line in proc.stdout:  # type: ignore[union-attr]
                if self._cancel:
                    proc.terminate()
                    return False, "Cancelled."
                lines.append(line)
                if indeterminate:
                    percent = (percent + 3) % 95
                    self.step_progress.emit(step, percent)
                else:
                    if "%" in line:
                        try:
                            pct = int(line.split("%")[0].strip().split()[-1])
                            self.step_progress.emit(step, min(pct, 99))
                        except (ValueError, IndexError):
                            pass

            proc.wait()
            if proc.returncode != 0:
                return False, "".join(lines[-40:])
            return True, ""
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _python_exe_static(venv: Path) -> Path:
        if sys.platform == "win32":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"


# ---------------------------------------------------------------------------
# Step row widget
# ---------------------------------------------------------------------------

class _StepRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label_text = label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setProperty("role", "step-label")
        self._label.setProperty("state", "pending")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(4)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

    def _set_state(self, state: str) -> None:
        self._label.setProperty("state", state)
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)

    def set_active(self, description: str) -> None:
        self._label.setText(description)
        self._set_state("active")

    def set_progress(self, pct: int) -> None:
        self._bar.setValue(pct)

    def set_done(self) -> None:
        self._label.setText(self._label_text)
        self._set_state("done")
        self._bar.setValue(100)

    def set_failed(self) -> None:
        self._set_state("error")


# ---------------------------------------------------------------------------
# SetupWizard dialog
# ---------------------------------------------------------------------------

_STEP_LABELS = [
    "Prepare Python runtime",
    "Bootstrap package manager",
    "Upgrade pip",
    "Install PyTorch",
    "Install transcription stack",
    "Download Whisper model",
]


class SetupWizard(QDialog):
    """Modal dialog shown on first launch when the embedded Python is absent.

    Blocks the caller until installation succeeds or the user cancels. Check
    the return value of exec():
      - QDialog.DialogCode.Accepted  → ready to start ServerManager
      - QDialog.DialogCode.Rejected  → user cancelled; run in capture-only mode
    """

    def __init__(self, use_cuda: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._use_cuda = use_cuda
        self._worker: Optional[_InstallWorker] = None

        self.setWindowTitle("Field Recorder — Installation")
        self.setWindowIcon(bas_icon(32))
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        svg_path = bas_svg_path("BAS-landscape")
        if svg_path.exists():
            renderer = QSvgRenderer(str(svg_path))
            native = renderer.defaultSize()            # 683 × 137
            target_w = 460
            target_h = int(target_w * native.height() / native.width())
            px = QPixmap(target_w, target_h)
            px.fill(QColor("transparent"))
            painter = QPainter(px)
            renderer.render(painter)
            painter.end()
            header_lbl = QLabel()
            header_lbl.setPixmap(px)
            layout.addWidget(header_lbl)
        else:
            fallback = QLabel("<b>Bureau of Applied Science<br>Field Recorder — Model 1</b>")
            fallback.setWordWrap(True)
            layout.addWidget(fallback)

        intro = QLabel(
            "On-device transcription engine — installation procedure.<br>"
            "Approximately 3–5 GB including PyTorch and model weights.<br>"
            "Python runtime downloaded automatically (~10 MB)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._rows: list[_StepRow] = []
        for label in _STEP_LABELS:
            row = _StepRow(label)
            layout.addWidget(row)
            self._rows.append(row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setProperty("role", "metadata")
        layout.addWidget(self._status_label)

        btns = QDialogButtonBox()
        self._cancel_btn = btns.addButton(
            "Cancel installation", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(btns)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._start_install()

    def _start_install(self) -> None:
        self._worker = _InstallWorker(use_cuda=self._use_cuda, parent=self)
        self._worker.step_started.connect(self._on_step_started)
        self._worker.step_progress.connect(self._on_step_progress)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.step_failed.connect(self._on_step_failed)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------

    @Slot(int, str)
    def _on_step_started(self, idx: int, desc: str) -> None:
        self._rows[idx].set_active(desc)

    @Slot(int, int)
    def _on_step_progress(self, idx: int, pct: int) -> None:
        self._rows[idx].set_progress(pct)

    @Slot(int)
    def _on_step_done(self, idx: int) -> None:
        self._rows[idx].set_done()

    @Slot(int, str)
    def _on_step_failed(self, idx: int, error: str) -> None:
        self._rows[idx].set_failed()
        self._status_label.setText(f"Installation failed at step {idx + 1}:\n{error}")
        self._status_label.setProperty("role", "error")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._cancel_btn.setText("Close")

    @Slot()
    def _on_all_done(self) -> None:
        self._status_label.setText("Installation complete.")
        self._cancel_btn.setText("Close")
        self.accept()

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
            # Remove partial installation so the wizard restarts clean next time
            shutil.rmtree(_python_dir(), ignore_errors=True)
        self.reject()

    def closeEvent(self, event) -> None:
        self._on_cancel()
        super().closeEvent(event)
