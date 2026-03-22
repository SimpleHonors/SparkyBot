"""System tray manager for SparkyBot"""

from pathlib import Path
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QObject, pyqtSignal


class TrayManager(QObject):
    """Manages system tray icon and menu"""

    activated = pyqtSignal(str)  # Signal when tray action is triggered
    quit_requested = pyqtSignal()

    def __init__(self, app_name: str = "SparkyBot"):
        super().__init__()
        self.app_name = app_name
        self._tray: QSystemTrayIcon = None
        self._menu: QMenu = None
        self._status_action: QAction = None
        self._watcher_action: QAction = None
        self._status = "Idle"

    def setup(self, icon_path: str = None):
        """Setup the system tray icon"""
        # Guard against re-initialization
        if self._tray is not None:
            return

        self._tray = QSystemTrayIcon()

        # Try to load custom icon, fallback to default
        if icon_path and Path(icon_path).exists():
            self._tray.setIcon(QIcon(icon_path))
        else:
            # Use a simple built-in icon representation
            self._tray.setIcon(self._create_default_icon())

        self._tray.setToolTip(f"{self.app_name} - {self._status}")

        # Create menu
        self._menu = QMenu()

        # Status action (disabled, just for display)
        self._status_action = QAction("Status: Idle")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()

        # Show/Hide window action
        self._show_action = QAction("Show Settings")
        self._show_action.triggered.connect(lambda: self.activated.emit("show"))
        self._menu.addAction(self._show_action)

        # Start/Stop watcher
        self._watcher_action = QAction("Start Watcher")
        self._watcher_action.triggered.connect(lambda: self.activated.emit("toggle_watcher"))
        self._menu.addAction(self._watcher_action)

        self._menu.addSeparator()

        # Quit action
        self._quit_action = QAction("Quit")
        self._quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self._quit_action)

        self._tray.setContextMenu(self._menu)

        # Connect activated signal
        self._tray.activated.connect(self._on_tray_activated)

    def _create_default_icon(self) -> QIcon:
        """Create a simple default icon"""
        # Create a 16x16 red/yellow icon using pixmap
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen

        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(50, 50, 50))

        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(100, 200, 100), 2))
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()

        return QIcon(pixmap)

    def _on_tray_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.activated.emit("show")

    def set_status(self, status: str):
        """Update the status text"""
        self._status = status
        if self._tray:
            self._tray.setToolTip(f"{self.app_name} - {status}")
        if self._status_action:
            self._status_action.setText(f"Status: {status}")

    def set_watcher_running(self, running: bool):
        """Update watcher state in menu"""
        if self._watcher_action:
            self._watcher_action.setText("Stop Watcher" if running else "Start Watcher")

    def show(self):
        """Show the tray icon"""
        if self._tray:
            self._tray.show()

    def hide(self):
        """Hide the tray icon"""
        if self._tray:
            self._tray.hide()

    def show_message(self, title: str, message: str, icon=None, timeout: int = 3000):
        """Show a notification from the tray"""
        if self._tray:
            # Default to Information if no icon specified
            msg_icon = icon if icon is not None else QSystemTrayIcon.MessageIcon.Information
            self._tray.showMessage(title, message, msg_icon, timeout)

    # Class-level alias so callers can use TrayManager.MessageIcon.Warning
    # (accessible both as a class attribute and via instance)
    MessageIcon = QSystemTrayIcon.MessageIcon
