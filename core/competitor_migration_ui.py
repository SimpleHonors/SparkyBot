"""Small, plain-language UI for moving setup to and from other log tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import theme
from core.competitor_export import (
    COMPETITOR_EXPORT_TARGETS,
    CompetitorExportResult,
    export_competitor_config,
    export_preview,
)
from core.competitor_import import (
    CompetitorConfigError,
    CompetitorFinding,
    CompetitorImportPlan,
    ImportedWebhook,
    build_import_plan,
    discover_competitor_configs,
    parse_competitor_config,
)
from core.interop_catalog import INTEROP_PLAIN_PROMISE, INTEROP_PROJECTS


_CONFIG_FILTER = (
    "Log-tool settings (*.json *.ini *.properties *.toml *.yaml *.yml "
    "*.xml *.config *.db *.sqlite *.sqlite3);;All files (*)"
)


class CompetitorImportDialog(QDialog):
    """One preview with smart defaults and four optional corrections."""

    def __init__(self, finding: CompetitorFinding, parent: QWidget | None = None):
        super().__init__(parent)
        self.finding = finding
        self.default_plan = build_import_plan(finding)
        self.setWindowTitle(f"Use {finding.app} Setup?")
        self.setMinimumWidth(610)

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"SparkyBot found settings from <b>{finding.app}</b>. "
            "The obvious choices are already selected."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        source = QLabel(f"Settings file: {finding.source_file}")
        source.setTextFormat(Qt.TextFormat.PlainText)
        source.setWordWrap(True)
        theme.mark_hint(source)
        layout.addWidget(source)

        form = QFormLayout()
        self.log_combo = QComboBox()
        self._fill_path_combo(
            self.log_combo,
            finding.log_folders,
            self.default_plan.log_folder,
            "Let SparkyBot find the fight logs",
        )
        form.addRow("ArcDPS fight logs:", self.log_combo)

        self.parser_combo = QComboBox()
        self._fill_path_combo(
            self.parser_combo,
            finding.parser_executables,
            self.default_plan.parser_executable,
            "Let SparkyBot install or find the parser",
        )
        form.addRow("Fight-log parser:", self.parser_combo)

        self.fight_combo = QComboBox()
        self._fill_webhook_combo(
            self.fight_combo,
            finding.webhooks,
            self.default_plan.fight_webhook,
            "Set up individual fight reports later",
        )
        form.addRow("Individual fight reports:", self.fight_combo)

        self.nightly_combo = QComboBox()
        self._fill_webhook_combo(
            self.nightly_combo,
            finding.webhooks,
            self.default_plan.nightly_webhook,
            "Set up nightly debrief later",
        )
        form.addRow("Nightly debrief and logs:", self.nightly_combo)
        layout.addLayout(form)

        privacy = QLabel(
            "SparkyBot reads only these selected paths and Discord destinations. "
            "It does not change the other tool. Passwords, API keys, bot tokens, "
            "Twitch, account data, and upload history are ignored."
        )
        privacy.setWordWrap(True)
        theme.mark_hint(privacy)
        layout.addWidget(privacy)

        if finding.warnings:
            warnings = QLabel("\n".join(f"• {item}" for item in finding.warnings))
            warnings.setWordWrap(True)
            warnings.setTextFormat(Qt.TextFormat.PlainText)
            theme.set_state(warnings, "warn")
            layout.addWidget(warnings)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = QPushButton("Use This Setup")
        self.use_button.setMinimumHeight(36)
        theme.set_widget_class(self.use_button, "primary")
        buttons.addButton(self.use_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _fill_path_combo(
        combo: QComboBox,
        values: Iterable[Path],
        selected: Path | None,
        empty_text: str,
    ) -> None:
        combo.addItem(empty_text, None)
        selected_index = 0
        paths = list(values)
        # The import plan may safely narrow an ArcDPS root to its WvW child.
        # Keep that smart selection visible even though the source config only
        # stored the parent folder.
        if selected is not None and selected not in paths:
            paths.insert(0, selected)
        for path in paths:
            combo.addItem(str(path), path)
            if selected is not None and path == selected:
                selected_index = combo.count() - 1
        combo.setCurrentIndex(selected_index)
        combo.setToolTip(combo.currentText())
        combo.currentTextChanged.connect(combo.setToolTip)

    @staticmethod
    def _fill_webhook_combo(
        combo: QComboBox,
        values: Iterable[ImportedWebhook],
        selected: ImportedWebhook | None,
        empty_text: str,
    ) -> None:
        combo.addItem(empty_text, None)
        selected_index = 0
        for hook in values:
            label = hook.display_name
            if hook.role == "fight":
                label += " — fight reports"
            elif hook.role == "nightly":
                label += " — nightly reports"
            combo.addItem(label, hook)
            if selected is not None and hook.url == selected.url:
                selected_index = combo.count() - 1
        combo.setCurrentIndex(selected_index)
        combo.setToolTip(combo.currentText())
        combo.currentTextChanged.connect(combo.setToolTip)

    def selected_plan(self) -> CompetitorImportPlan:
        return CompetitorImportPlan(
            finding=self.finding,
            log_folder=self.log_combo.currentData(),
            parser_executable=self.parser_combo.currentData(),
            fight_webhook=self.fight_combo.currentData(),
            nightly_webhook=self.nightly_combo.currentData(),
        )


class CompetitorExportConfirmDialog(QDialog):
    """Readable, stable confirmation for a reversible one-time export."""

    def __init__(
        self,
        target_name: str,
        preview: str,
        output: Path,
        *,
        patches_existing: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Create {target_name} Setup?")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>Ready to create a setup for {target_name}.</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        theme.set_variant(heading, "heading")
        layout.addWidget(heading)

        details = QLabel(preview)
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setWordWrap(True)
        layout.addWidget(details)

        destination = QLabel(f"Destination:\n{output}")
        destination.setTextFormat(Qt.TextFormat.PlainText)
        destination.setWordWrap(True)
        destination.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(destination)

        safety = QLabel(
            "Existing settings will be backed up before they are patched."
            if patches_existing
            else "A separate migration folder will be created."
        )
        safety.setWordWrap(True)
        theme.set_state(safety, "ok")
        layout.addWidget(safety)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = QPushButton("Create Setup")
        self.create_button.setMinimumHeight(36)
        theme.set_widget_class(self.create_button, "primary")
        buttons.addButton(
            self.create_button, QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CompetitorExportDoneDialog(QDialog):
    """Plain success result with readable, selectable output paths."""

    def __init__(
        self,
        result: CompetitorExportResult,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Setup Created")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)

        heading = QLabel(f"<b>{result.target.name} setup is ready.</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        theme.set_variant(heading, "heading")
        layout.addWidget(heading)

        _heading, _separator, detail_text = result.summary().partition("\n\n")
        details = QLabel(detail_text or result.summary())
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setWordWrap(True)
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(details)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.done_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _manual_import(parent: QWidget | None) -> CompetitorFinding | None:
    downloads = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    path, _selected_filter = QFileDialog.getOpenFileName(
        parent,
        "Choose Settings from Another Log Tool",
        downloads,
        _CONFIG_FILTER,
    )
    if not path:
        return None
    try:
        return parse_competitor_config(path)
    except CompetitorConfigError as exc:
        QMessageBox.warning(parent, "Settings File Not Used", str(exc))
        return None


def choose_competitor_import(
    parent: QWidget | None,
    *,
    gw2_dirs: Iterable[str | Path] = (),
) -> CompetitorImportPlan | None:
    """Discover, select, preview, and return a consented import plan."""
    findings = discover_competitor_configs(gw2_dirs=gw2_dirs)
    finding: CompetitorFinding | None = None
    if len(findings) == 1:
        finding = findings[0]
    elif findings:
        labels = [
            f"{item.app} — {item.source_file.parent}" for item in findings
        ]
        labels.append("Choose a different settings file...")
        label, accepted = QInputDialog.getItem(
            parent,
            "Import from Another Log Tool",
            "SparkyBot found these setups:",
            labels,
            0,
            False,
        )
        if not accepted:
            return None
        if label == labels[-1]:
            finding = _manual_import(parent)
        else:
            finding = findings[labels.index(label)]
    else:
        finding = _manual_import(parent)
    if finding is None:
        return None

    return preview_competitor_finding(finding, parent)


def preview_competitor_finding(
    finding: CompetitorFinding, parent: QWidget | None
) -> CompetitorImportPlan | None:
    """Preview one already-discovered setup and return the consented plan.

    Used by the wizard's proactive offer: discovery already ran, the user
    clicked "Use <tool>'s Settings", so no picker — straight to consent."""
    dialog = CompetitorImportDialog(finding, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_plan()


def _safe_folder_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", name).strip() or "Log Tool"


def choose_competitor_export(
    parent: QWidget | None,
    config,
) -> CompetitorExportResult | None:
    """Choose one target and either patch its directory or create a clean pack."""
    names = [target.name for target in COMPETITOR_EXPORT_TARGETS]
    name, accepted = QInputDialog.getItem(
        parent,
        "Create Setup for Another Log Tool",
        "Which tool should receive this setup?",
        names,
        0,
        False,
    )
    if not accepted:
        return None
    target = COMPETITOR_EXPORT_TARGETS[names.index(name)]

    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    base = QFileDialog.getExistingDirectory(
        parent,
        f"Choose {target.name}'s Settings Folder or an Export Location",
        documents or str(Path.home()),
    )
    if not base:
        return None
    selected = Path(base)
    has_existing = any((selected / filename).is_file() for filename in target.filenames)
    output = (
        selected
        if has_existing
        else selected / f"SparkyBot Export - {_safe_folder_name(target.name)}"
    )

    try:
        preview = export_preview(config, target.key)
    except CompetitorConfigError as exc:
        QMessageBox.warning(parent, "Setup Could Not Be Created", str(exc))
        return None
    confirmation = CompetitorExportConfirmDialog(
        target.name,
        preview,
        output,
        patches_existing=has_existing,
        parent=parent,
    )
    if confirmation.exec() != QDialog.DialogCode.Accepted:
        return None

    try:
        result = export_competitor_config(config, target.key, output)
    except CompetitorConfigError as exc:
        QMessageBox.warning(parent, "Setup Could Not Be Created", str(exc))
        return None
    CompetitorExportDoneDialog(result, parent).exec()
    return result


class InteropCatalogDialog(QDialog):
    """Linked credits that celebrate why another tool may be the better fit."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Other WvW Log Tools & Credits")
        self.setMinimumSize(720, 620)
        outer = QVBoxLayout(self)

        heading = QLabel("<b>Good tools make the whole WvW community stronger.</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        theme.set_variant(heading, "heading")
        outer.addWidget(heading)
        promise = QLabel(INTEROP_PLAIN_PROMISE)
        promise.setWordWrap(True)
        promise.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(promise)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for project in INTEROP_PROJECTS:
            entry = QLabel(
                f'<a href="{project.url}"><b>{project.name}</b></a> '
                f'— {project.license}<br>'
                f'<b>Where it may be the better fit:</b> {project.celebrates}<br>'
                f'<span style="color:#9aa4b2;">{project.relationship}</span>'
            )
            entry.setTextFormat(Qt.TextFormat.RichText)
            entry.setOpenExternalLinks(True)
            entry.setWordWrap(True)
            entry.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
            )
            body_layout.addWidget(entry)
            body_layout.addSpacing(10)
        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        note = QLabel(
            "Interoperability adapters are original SparkyBot code based on "
            "documented settings formats. No competitor code, artwork, or "
            "binaries are copied by this feature."
        )
        note.setWordWrap(True)
        theme.mark_hint(note)
        outer.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)


def show_interop_catalog(parent: QWidget | None = None) -> None:
    InteropCatalogDialog(parent).exec()
