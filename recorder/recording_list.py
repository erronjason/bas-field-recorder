"""RecordingList — live-updating list of records in the records directory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import user_data


# ---------------------------------------------------------------------------
# Row data
# ---------------------------------------------------------------------------

class _RowData:
    __slots__ = ("record_id", "display_name", "created_at", "duration_seconds",
                 "status", "filename", "error")

    def __init__(
        self,
        record_id: str,
        display_name: str,
        created_at: str,
        duration_seconds: Optional[float],
        status: str,          # "transcribed" | "pending" | "error"
        filename: str,
        error: str = "",
    ) -> None:
        self.record_id = record_id
        self.display_name = display_name
        self.created_at = created_at
        self.duration_seconds = duration_seconds
        self.status = status
        self.filename = filename
        self.error = error


def _parse_record(json_path: Path) -> _RowData:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _RowData(
            record_id="",
            display_name=json_path.name,
            created_at="",
            duration_seconds=None,
            status="error",
            filename=json_path.name,
            error=str(exc),
        )
    has_segments = bool(data.get("segments"))
    status = "transcribed" if has_segments else "pending"
    return _RowData(
        record_id=data.get("record_id", ""),
        display_name=data.get("display_name") or json_path.stem,
        created_at=data.get("created_at", ""),
        duration_seconds=data.get("duration_seconds"),
        status=status,
        filename=json_path.name,
    )


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d  %H:%M")
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Elided label
# ---------------------------------------------------------------------------

class _ElidedLabel(QLabel):
    """QLabel that elides overflowing text instead of clipping it."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        w = self.width()
        if w < 1:
            return
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w)
        super().setText(elided)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


# ---------------------------------------------------------------------------
# Row widget
# ---------------------------------------------------------------------------

_ROW_HEIGHT = 54   # fixed item height; avoids pre-QSS sizeHint underestimate


class _RecordRow(QWidget):
    def __init__(self, row: _RowData, parent=None) -> None:
        super().__init__(parent)
        self._row = row
        self.setProperty("role", "record-row")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Status indicator: 12px wide rule
        self._status_dot = QFrame()
        self._status_dot.setFixedWidth(3)
        self._status_dot.setMinimumHeight(20)
        self._status_dot.setProperty("role", "status-rule")
        self._status_dot.setProperty("state", row.status)
        layout.addWidget(self._status_dot)

        # Name + date column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = _ElidedLabel(row.display_name)
        self._name_lbl.setProperty("role", "record-name")
        text_col.addWidget(self._name_lbl)

        date_str = _fmt_date(row.created_at)
        if row.error:
            date_str = f"Malformed — {row.error[:60]}"
        self._date_lbl = QLabel(date_str)
        self._date_lbl.setProperty("role", "record-meta")
        text_col.addWidget(self._date_lbl)

        layout.addLayout(text_col, stretch=1)

        # Duration
        dur_str = _fmt_duration(row.duration_seconds)
        if dur_str:
            dur_lbl = QLabel(dur_str)
            dur_lbl.setProperty("role", "record-duration")
            dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(dur_lbl)


# ---------------------------------------------------------------------------
# RecordingList
# ---------------------------------------------------------------------------

class RecordingList(QWidget):
    """Live list of records. Emits record_selected(record_id) on selection change."""

    record_selected = Signal(str)   # record_id, or "" when selection cleared

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._selected_id: str = ""
        self._all_rows: list[_RowData] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter records")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        # Rule
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setProperty("role", "rule")
        layout.addWidget(rule)

        # List
        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSpacing(0)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list)

        # Empty state label (shown when list is empty after filter)
        self._empty_lbl = QLabel("No records. Start a capture from the tray.")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setProperty("role", "empty-state")
        self._empty_lbl.hide()
        layout.addWidget(self._empty_lbl)

        # File system watcher
        self._watcher = QFileSystemWatcher()
        records = str(user_data.records_dir())
        self._watcher.addPath(records)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

        # Debounce timer
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._rebuild)

        self._rebuild()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def clear_search(self) -> bool:
        """Clear the search field. Returns True if it was non-empty."""
        if self._search.text():
            self._search.clear()
            return True
        return False

    def move_selection(self, delta: int) -> None:
        row = self._list.currentRow()
        count = self._list.count()
        if count == 0:
            return
        new_row = max(0, min(count - 1, row + delta))
        self._list.setCurrentRow(new_row)

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    @Slot()
    def _on_dir_changed(self, _path: str = "") -> None:
        self._debounce.start()

    def _rebuild(self) -> None:
        records_dir = user_data.records_dir()
        # Re-watch if directory was removed and recreated
        if str(records_dir) not in self._watcher.directories():
            self._watcher.addPath(str(records_dir))

        parsed = [_parse_record(p) for p in records_dir.glob("*.json")]
        rows = sorted(parsed, key=lambda r: r.created_at or "", reverse=True)

        self._all_rows = rows
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.lower()
        visible = [r for r in self._all_rows if needle in r.display_name.lower()]
        self._populate(visible)

    def _populate(self, rows: list[_RowData]) -> None:
        prev_id = self._selected_id
        scroll_val = self._list.verticalScrollBar().value() if self._list.count() else 0

        self._list.blockSignals(True)
        self._list.clear()

        for row in rows:
            item = QListWidgetItem()
            widget = _RecordRow(row)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            item.setData(Qt.ItemDataRole.UserRole, row.record_id)
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)

        self._list.blockSignals(False)

        # Restore selection
        restored = False
        if prev_id:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == prev_id:
                    self._list.setCurrentRow(i)
                    restored = True
                    break

        if not restored and prev_id:
            self._selected_id = ""
            self.record_selected.emit("")

        # Restore scroll
        self._list.verticalScrollBar().setValue(scroll_val)

        # Empty state
        has_records = bool(self._all_rows)
        has_visible = bool(rows)
        self._list.setVisible(has_visible)
        if not has_records:
            self._empty_lbl.setText("No records. Start a capture from the tray.")
            self._empty_lbl.show()
        elif not has_visible:
            self._empty_lbl.setText("No records match the filter.")
            self._empty_lbl.show()
        else:
            self._empty_lbl.hide()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    @Slot()
    def _on_selection_changed(self) -> None:
        item = self._list.currentItem()
        if item is None:
            self._selected_id = ""
            self.record_selected.emit("")
            return
        rid = item.data(Qt.ItemDataRole.UserRole) or ""
        if rid != self._selected_id:
            self._selected_id = rid
            self.record_selected.emit(rid)

    # ------------------------------------------------------------------
    # WindowActivate reconcile
    # ------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowActivate:
            self._rebuild()
