"""Manages the lifecycle of diarized_transcriber_server.py as a child QProcess."""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import user_data


def _app_resource_path(filename: str) -> Path:
    """Return path to a bundled resource (frozen) or project-root file (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / filename  # type: ignore[attr-defined]
    return Path(__file__).parent.parent / filename


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex(("127.0.0.1", port)) != 0


CANDIDATE_PORTS = [7777, 7778, 7779, 7780]


class ServerManager(QObject):
    """Starts, monitors, and stops the FastAPI transcription server.

    Signals
    -------
    status_changed(str) — emitted with "starting", "ready", "error", "stopped"
    """

    status_changed = Signal(str)

    _POLL_MS = 2_000
    _STARTUP_TIMEOUT_S = 45

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._port: Optional[int] = None
        self._ready = False
        self._elapsed_s = 0.0

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_MS)
        self._poll_timer.timeout.connect(self._poll_health)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def port(self) -> Optional[int]:
        return self._port

    @property
    def base_url(self) -> Optional[str]:
        return f"http://127.0.0.1:{self._port}" if self._port else None

    def is_ready(self) -> bool:
        return self._ready

    def start(self) -> None:
        """Launch the server using the backend venv Python."""
        # If already running (maybe from a previous session) just adopt it
        existing = self._find_running_server()
        if existing:
            self._port = existing
            self._ready = True
            self.status_changed.emit("ready")
            return

        python = self._backend_python()
        if python is None:
            self.status_changed.emit("error")
            return

        port = self._pick_port()
        if port is None:
            self.status_changed.emit("error")
            return

        server_script = _app_resource_path("diarized_transcriber_server.py")

        self._port = port
        self._ready = False
        self._elapsed_s = 0.0

        self._proc = QProcess(self)
        self._proc.setProgram(str(python))
        self._proc.setArguments([str(server_script), "--port", str(port)])
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.start()

        self.status_changed.emit("starting")
        self._poll_timer.start()

    def stop(self) -> None:
        """Gracefully shut down the server."""
        self._poll_timer.stop()
        self._ready = False

        if self._port:
            self._http("POST", "/shutdown")

        if self._proc and self._proc.state() != QProcess.ProcessState.NotRunning:
            if not self._proc.waitForFinished(5_000):
                self._proc.kill()

        self.status_changed.emit("stopped")

    # ------------------------------------------------------------------
    # HTTP helpers (stdlib only — httpx lives in the backend venv)
    # ------------------------------------------------------------------

    def get(self, path: str, timeout: float = 3.0) -> Optional[dict]:
        if not self._port:
            return None
        try:
            url = f"http://127.0.0.1:{self._port}{path}"
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def post(self, path: str, body: Optional[dict] = None, timeout: float = 5.0) -> Optional[dict]:
        return self._http("POST", path, body, timeout)

    def delete(self, path: str, timeout: float = 5.0) -> Optional[dict]:
        return self._http("DELETE", path, None, timeout)

    def _http(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> Optional[dict]:
        if not self._port:
            return None
        try:
            url = f"http://127.0.0.1:{self._port}{path}"
            data = json.dumps(body).encode() if body else b""
            headers = {"Content-Type": "application/json"} if data else {}
            req = urllib.request.Request(url, data=data or None, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_health(self) -> None:
        self._elapsed_s += self._POLL_MS / 1000

        if self._elapsed_s > self._STARTUP_TIMEOUT_S:
            self._poll_timer.stop()
            self.status_changed.emit("error")
            return

        result = self.get("/health", timeout=1.0)
        if result and result.get("status") == "ready":
            self._poll_timer.stop()
            self._ready = True
            self._save_port()
            self.status_changed.emit("ready")

    def _on_proc_finished(self, exit_code: int, _exit_status) -> None:
        if self._ready:
            return  # expected shutdown after stop()
        self._poll_timer.stop()
        self.status_changed.emit("error")

    def _backend_python(self) -> Optional[Path]:
        if sys.platform == "win32":
            p = user_data.backend_dir() / "python" / "python.exe"
        else:
            p = user_data.backend_dir() / "python" / "python"
        return p if p.exists() else None

    def _pick_port(self) -> Optional[int]:
        saved = self._load_saved_port()
        candidates = ([saved] if saved else []) + [p for p in CANDIDATE_PORTS if p != saved]
        for port in candidates:
            if _port_free(port):
                return port
        return None

    def _find_running_server(self) -> Optional[int]:
        """Return port if a healthy server is already listening."""
        for port in CANDIDATE_PORTS:
            try:
                url = f"http://127.0.0.1:{port}/health"
                with urllib.request.urlopen(url, timeout=0.5) as r:
                    data = json.loads(r.read().decode())
                    if data.get("status") == "ready":
                        return port
            except Exception:
                continue
        return None

    def _save_port(self) -> None:
        path = user_data.settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            data["server_port"] = self._port
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_saved_port(self) -> Optional[int]:
        try:
            data = json.loads(user_data.settings_path().read_text(encoding="utf-8"))
            v = data.get("server_port")
            return int(v) if v else None
        except Exception:
            return None
