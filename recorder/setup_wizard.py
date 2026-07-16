"""First-run setup wizard — installs the backend transcription stack.

Shown when backend/venv/ does not exist (or is broken). All heavy work runs
in a background QThread so the UI stays responsive. Can be re-run from
Settings → Server → Reinstall Backend.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal, Slot
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


def backend_ready() -> bool:
    """Return True if backend/venv/ looks usable."""
    if sys.platform == "win32":
        python = user_data.backend_dir() / "venv" / "Scripts" / "python.exe"
    else:
        python = user_data.backend_dir() / "venv" / "bin" / "python"
    return python.exists()


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _InstallWorker(QThread):
    step_started = Signal(int, str)        # step index, description
    step_progress = Signal(int, int)       # step index, percent (0-100)
    step_done = Signal(int)               # step index
    step_failed = Signal(int, str)        # step index, error message
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
        except Exception as exc:  # noqa: BLE001
            pass  # individual steps already emit step_failed

    def _run_steps(self) -> None:
        backend = user_data.backend_dir()

        # ── Step 0: Find system Python ───────────────────────────────────
        self.step_started.emit(0, "Finding Python runtime…")
        python = self._find_system_python()
        if python is None:
            self.step_failed.emit(0, (
                "Python 3.9 or later not found on PATH.\n"
                "Install Python from https://www.python.org/downloads/ and try again."
            ))
            return
        self.step_done.emit(0)

        # ── Step 1: Create venv ──────────────────────────────────────────
        self.step_started.emit(1, "Creating virtual environment…")
        venv_path = backend / "venv"
        if venv_path.exists():
            shutil.rmtree(venv_path, ignore_errors=True)
        ok, err = self._run([str(python), "-m", "venv", str(venv_path)], 1, indeterminate=True)
        if not ok:
            self.step_failed.emit(1, err)
            return
        self.step_done.emit(1)

        pip = self._pip_exe(venv_path)

        # ── Step 2: Upgrade pip ──────────────────────────────────────────
        self.step_started.emit(2, "Upgrading pip…")
        ok, err = self._run([str(pip), "install", "--upgrade", "pip"], 2, indeterminate=True)
        if not ok:
            self.step_failed.emit(2, err)
            return
        self.step_done.emit(2)

        # ── Step 3: Install PyTorch ──────────────────────────────────────
        self.step_started.emit(3, "Installing PyTorch (this may take several minutes)…")
        torch_pkgs = _TORCH_CUDA if self._use_cuda else _TORCH_CPU
        ok, err = self._run([str(pip), "install"] + torch_pkgs, 3)
        if not ok:
            self.step_failed.emit(3, err)
            return
        self.step_done.emit(3)

        # ── Step 4: Install transcription stack ─────────────────────────
        self.step_started.emit(4, "Installing transcription stack…")
        ok, err = self._run([str(pip), "install"] + _STACK, 4)
        if not ok:
            self.step_failed.emit(4, err)
            return
        self.step_done.emit(4)

        # ── Step 5: Pre-download Whisper model ───────────────────────────
        self.step_started.emit(5, "Downloading Whisper model (medium, ~1.5 GB)…")
        venv_python = self._python_exe(venv_path)
        script = (
            "import whisperx, torch; "
            "device = 'cuda' if torch.cuda.is_available() else 'cpu'; "
            "whisperx.load_model('medium', device, compute_type='int8')"
        )
        ok, err = self._run([str(venv_python), "-c", script], 5, indeterminate=True)
        if not ok:
            self.step_failed.emit(5, err)
            return
        self.step_done.emit(5)

        self.all_done.emit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_system_python(self) -> Optional[Path]:
        candidates = ["python3", "python", "python3.11", "python3.12", "python3.10", "python3.9"]
        for name in candidates:
            exe = shutil.which(name)
            if exe is None:
                continue
            try:
                result = subprocess.run(
                    [exe, "-c", "import sys; print(sys.version_info[:2])"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    ver = eval(result.stdout.strip())  # noqa: S307
                    if ver >= (3, 9):
                        return Path(exe)
            except Exception:
                continue
        return None

    def _run(
        self,
        cmd: list[str],
        step: int,
        indeterminate: bool = False,
    ) -> tuple[bool, str]:
        """Run a subprocess and stream its stdout to progress signals."""
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
                lines.append(line)
                if indeterminate:
                    percent = (percent + 3) % 95
                    self.step_progress.emit(step, percent)
                else:
                    # pip outputs "Downloading ... X%" or "Installing ..."
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
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    @staticmethod
    def _pip_exe(venv: Path) -> Path:
        if sys.platform == "win32":
            return venv / "Scripts" / "pip.exe"
        return venv / "bin" / "pip"

    @staticmethod
    def _python_exe(venv: Path) -> Path:
        if sys.platform == "win32":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"


# ---------------------------------------------------------------------------
# Step row widget
# ---------------------------------------------------------------------------

class _StepRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(14)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

    def set_active(self, description: str) -> None:
        self._label.setText(f"⏳  {description}")
        self._label.setStyleSheet("font-size: 12px; color: #0d6efd;")

    def set_progress(self, pct: int) -> None:
        self._bar.setValue(pct)

    def set_done(self) -> None:
        self._label.setStyleSheet("font-size: 12px; color: #198754;")
        self._label.setText(self._label.text().replace("⏳", "✓"))
        self._bar.setValue(100)

    def set_failed(self) -> None:
        self._label.setStyleSheet("font-size: 12px; color: #dc3545;")
        self._label.setText(self._label.text().replace("⏳", "✗"))


# ---------------------------------------------------------------------------
# SetupWizard dialog
# ---------------------------------------------------------------------------

_STEP_LABELS = [
    "Find Python runtime",
    "Create virtual environment",
    "Upgrade pip",
    "Install PyTorch",
    "Install transcription stack",
    "Download Whisper model",
]


class SetupWizard(QDialog):
    """Modal dialog shown on first launch when backend/venv/ is absent.

    Blocks the caller until setup succeeds or the user cancels. Check the
    return value of exec():
      - QDialog.DialogCode.Accepted  → ready to start ServerManager
      - QDialog.DialogCode.Rejected  → user cancelled; run in recording-only mode
    """

    def __init__(self, use_cuda: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._use_cuda = use_cuda
        self._worker: Optional[_InstallWorker] = None

        self.setWindowTitle("Diarized Transcriber — First-time Setup")
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        intro = QLabel(
            "<b>Welcome to Diarized Transcriber.</b><br><br>"
            "First-time setup installs the local transcription engine "
            "(approx. 3–5 GB including PyTorch and model weights).<br>"
            "You only need to do this once."
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
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(self._status_label)

        btns = QDialogButtonBox()
        self._cancel_btn = btns.addButton("Cancel setup", QDialogButtonBox.ButtonRole.RejectRole)
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
        self._status_label.setText(f"Setup failed at step {idx + 1}:\n{error}")
        self._status_label.setStyleSheet("font-size: 11px; color: #dc3545;")
        self._cancel_btn.setText("Close")

    @Slot()
    def _on_all_done(self) -> None:
        self._status_label.setText("Setup complete.")
        self._status_label.setStyleSheet("font-size: 11px; color: #198754;")
        self._cancel_btn.setText("Close")
        self.accept()

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
            # Clean up partial venv
            venv = user_data.backend_dir() / "venv"
            if venv.exists():
                shutil.rmtree(venv, ignore_errors=True)
        self.reject()

    def closeEvent(self, event) -> None:
        self._on_cancel()
        super().closeEvent(event)
