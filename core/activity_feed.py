"""Home activity feed model — the scrollback for what the bot just did.

Model-backed rows (time, kind, text) fed from the per-fight pipeline
signals: processing / posted / skipped-with-named-filter-reason / error, plus AI
commentary/voice moments (which only occur while AI is enabled) and
run/report events. Newest row first, capped so a week-long session can
never balloon memory; sparkybot.log stays the durable record.
"""

from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

MAX_ROWS = 200

KIND_ROLE = Qt.ItemDataRole.UserRole
TEXT_ROLE = Qt.ItemDataRole.UserRole + 1
TIME_ROLE = Qt.ItemDataRole.UserRole + 2

# Semantic kind -> feed label. Kinds are stable API (tests and the status
# bar key off them); labels are display copy.
KIND_LABELS = {
    "processing": "Processing",
    "posted": "Posted",
    "skipped": "Skipped",
    "error": "Error",
    "commentary": "Commentary",
    "voice": "Voice",
    "run": "Run",
    "report": "Report",
    "info": "Info",
}


def format_clock(when: datetime) -> str:
    """Classic 12-hour clock ('8:14 PM') — matches the fight table's style.
    Built portably: dash-modified strftime flags crash on Windows."""
    hour12 = when.hour % 12 or 12
    ampm = "AM" if when.hour < 12 else "PM"
    return f"{hour12}:{when.minute:02d} {ampm}"


class ActivityFeedModel(QAbstractListModel):
    """List model over (time, kind, text) rows, newest first."""

    def __init__(self, parent=None, max_rows: int = MAX_ROWS):
        super().__init__(parent)
        self._rows = []          # list[(datetime, kind, text)], newest first
        self._max_rows = max_rows

    # ------------------------------------------------------------------
    # QAbstractListModel
    # ------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        when, kind, text = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._format(when, kind, text)
        if role == KIND_ROLE:
            return kind
        if role == TEXT_ROLE:
            return text
        if role == TIME_ROLE:
            return when
        return None

    # ------------------------------------------------------------------
    # feed API
    # ------------------------------------------------------------------

    @staticmethod
    def _format(when: datetime, kind: str, text: str) -> str:
        label = KIND_LABELS.get(kind, kind.title())
        return f"{format_clock(when)}  {label}  {text}"

    def add(self, kind: str, text: str, when: datetime | None = None):
        """Prepend a row (newest on top); trim past the cap."""
        if when is None:
            when = datetime.now()
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, (when, kind, text))
        self.endInsertRows()
        if len(self._rows) > self._max_rows:
            first = self._max_rows
            last = len(self._rows) - 1
            self.beginRemoveRows(QModelIndex(), first, last)
            del self._rows[first:]
            self.endRemoveRows()

    def line(self, row: int) -> str:
        """Formatted display line for a row (right-click Copy line)."""
        if not (0 <= row < len(self._rows)):
            return ""
        return self._format(*self._rows[row])
