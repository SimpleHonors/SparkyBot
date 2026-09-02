"""Small, plain-language UI for moving setup to and from other log tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
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
    COMPETITOR_IMPORT_TARGETS,
    CompetitorConfigError,
    CompetitorFinding,
    CompetitorImportPlan,
    ImportedWebhook,
    build_import_plan,
    discover_competitor_configs,
    expected_competitor_config_path,
    parse_competitor_config,
)
from core.interop_catalog import INTEROP_PLAIN_PROMISE, INTEROP_PROJECTS


_CONFIG_FILTER = (
    "Log-tool settings (*.json *.ini *.properties *.toml *.yaml *.yml "
    "*.xml *.config *.db *.sqlite *.sqlite3);;All files (*)"
)


class CompetitorImportDialog(QDialog):
    """One consent screen where every reusable item is independently selectable."""

    def __init__(
        self,
        finding: CompetitorFinding,
        parent: QWidget | None = None,
        *,
        existing_config=None,
    ):
        super().__init__(parent)
        self.finding = finding
        self.existing_config = existing_config
        self.default_plan = build_import_plan(finding)
        self.setWindowTitle(f"Set Up from {finding.app}?")
        self.setMinimumWidth(610)

        # The whole body scrolls; the action buttons live outside it so they
        # can never be clipped, no matter how many tools or files are merged
        # (768p / high-DPI safe).
        outer_layout = QVBoxLayout(self)
        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        intro = QLabel(
            f"SparkyBot found what it can reuse from <b>{finding.app}</b>. "
            "Useful items are selected. Uncheck anything you do not want."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if len(finding.source_files) == 1:
            source_text = f"Where it came from: {finding.source_file}"
        else:
            # A merged setup lists every file it drew from, so any stale or
            # unexpected find is visible before the user clicks Use This Setup.
            joined = "\n".join(f"  • {path}" for path in finding.source_files)
            source_text = f"Where it came from ({len(finding.source_files)} files):\n{joined}"
        source = QLabel(source_text)
        source.setTextFormat(Qt.TextFormat.PlainText)
        source.setWordWrap(True)
        theme.mark_hint(source)
        layout.addWidget(source)

        choice_bar = QHBoxLayout()
        choice_bar.addWidget(QLabel("<b>Choose what to copy</b>"))
        choice_bar.addStretch()
        self.select_all_button = QPushButton("All")
        self.select_none_button = QPushButton("None")
        choice_bar.addWidget(self.select_all_button)
        choice_bar.addWidget(self.select_none_button)
        layout.addLayout(choice_bar)

        self._import_checks: list[QCheckBox] = []
        self.setting_checks: list[tuple[object, QCheckBox]] = []
        self.webhook_checks: list[tuple[ImportedWebhook, QCheckBox]] = []

        form = QFormLayout()
        self.log_combo = QComboBox()
        self._fill_path_combo(
            self.log_combo,
            finding.log_folders,
            self.default_plan.log_folder,
            "Let SparkyBot find the fight logs",
        )
        self.log_check = self._make_item_check(
            "Fight log folder",
            self.log_combo,
            self.default_plan.log_folder is not None,
        )
        form.addRow(self.log_check, self.log_combo)

        self.parser_combo = QComboBox()
        self._fill_path_combo(
            self.parser_combo,
            finding.parser_executables,
            self.default_plan.parser_executable,
            "Let SparkyBot find the report helper",
        )
        self.parser_check = self._make_item_check(
            "Elite Insights parser",
            self.parser_combo,
            self.default_plan.parser_executable is not None,
        )
        form.addRow(self.parser_check, self.parser_combo)

        self.fight_combo = QComboBox()
        self._fill_webhook_combo(
            self.fight_combo,
            finding.webhooks,
            self.default_plan.fight_webhook,
            "Set up individual fight reports later",
        )
        self.fight_check = self._make_item_check(
            "Individual fight channel",
            self.fight_combo,
            self.default_plan.fight_webhook is not None,
        )
        form.addRow(self.fight_check, self.fight_combo)

        self.nightly_combo = QComboBox()
        self._fill_webhook_combo(
            self.nightly_combo,
            finding.webhooks,
            self.default_plan.nightly_webhook,
            "Set up nightly debrief later",
        )
        self.nightly_check = self._make_item_check(
            "Nightly debrief channel",
            self.nightly_combo,
            self.default_plan.nightly_webhook is not None,
        )
        form.addRow(self.nightly_check, self.nightly_combo)
        layout.addLayout(form)

        if self.default_plan.extra_webhooks:
            saved_group = QGroupBox("Other saved Discord channels")
            saved_layout = QVBoxLayout(saved_group)
            core_urls = {
                hook.url
                for hook in (
                    self.default_plan.fight_webhook,
                    self.default_plan.nightly_webhook,
                )
                if hook is not None
            }
            remaining_slots = max(0, 3 - len(core_urls))
            for index, hook in enumerate(self.default_plan.extra_webhooks):
                check = self._plain_item_check(
                    hook.display_name, index < remaining_slots
                )
                self.webhook_checks.append((hook, check))
                saved_layout.addWidget(check)
            layout.addWidget(saved_group)

        self.webhook_limit_label = QLabel(
            "SparkyBot can keep 3 Discord channels total, including any "
            "existing route you leave unchanged. Uncheck one before selecting "
            "a fourth channel."
        )
        self.webhook_limit_label.setWordWrap(True)
        theme.set_state(self.webhook_limit_label, "warn")
        self.webhook_limit_label.setVisible(len(finding.webhooks) > 3)
        layout.addWidget(self.webhook_limit_label)
        for combo in (
            self.log_combo,
            self.parser_combo,
            self.fight_combo,
            self.nightly_combo,
        ):
            combo.currentIndexChanged.connect(self._refresh_selection_state)

        if finding.settings:
            preferences_heading = QLabel("<b>Other settings found</b>")
            preferences_heading.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(preferences_heading)

            # Groups go straight into the scrolling body — no nested scroll to
            # overlap the privacy/warnings text below them.
            sections: dict[str, list] = {}
            for setting in finding.settings:
                sections.setdefault(setting.section, []).append(setting)
            for section, settings in sections.items():
                group = QGroupBox(section)
                group_form = QFormLayout(group)
                for setting in settings:
                    check = self._plain_item_check(setting.label)
                    self.setting_checks.append((setting, check))
                    value = QLabel(setting.display_value)
                    value.setTextFormat(Qt.TextFormat.PlainText)
                    value.setWordWrap(True)
                    group_form.addRow(check, value)
                layout.addWidget(group)

        privacy = QLabel(
            "Only the items shown above will be reused. SparkyBot does not "
            "change the other tool. Passwords, API keys, bot tokens, account "
            "data, and upload history are ignored. Optional features stay off."
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

        layout.addStretch()
        body_scroll.setWidget(body)
        outer_layout.addWidget(body_scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = QPushButton("Use This Setup")
        self.use_button.setMinimumHeight(36)
        theme.set_widget_class(self.use_button, "primary")
        buttons.addButton(self.use_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)  # fixed outside the scroll — never clipped
        self.select_all_button.clicked.connect(lambda: self._select_items(True))
        self.select_none_button.clicked.connect(lambda: self._select_items(False))
        self._refresh_selection_state()
        # Cap height so the body scrolls on 768p / high-DPI instead of the
        # dialog overflowing the screen and overlapping its own content.
        self.resize(620, 600)
        self.setMaximumHeight(720)

    def _plain_item_check(self, text: str, checked: bool = True) -> QCheckBox:
        check = QCheckBox(text)
        check.setProperty("importItem", True)
        check.setChecked(checked)
        check.toggled.connect(self._refresh_selection_state)
        self._import_checks.append(check)
        return check

    def _make_item_check(
        self, text: str, editor: QWidget, checked: bool
    ) -> QCheckBox:
        check = self._plain_item_check(text, checked)
        check.setEnabled(checked)
        editor.setEnabled(checked)
        check.toggled.connect(editor.setEnabled)
        return check

    def _select_items(self, selected: bool) -> None:
        for check in self._import_checks:
            if check.isEnabled():
                check.setChecked(selected)
        if selected:
            self._trim_webhook_selection()
        self._refresh_selection_state()

    def _selected_webhook_count(self) -> int:
        urls = set()
        for check, combo in (
            (getattr(self, "fight_check", None), getattr(self, "fight_combo", None)),
            (getattr(self, "nightly_check", None), getattr(self, "nightly_combo", None)),
        ):
            if check is not None and combo is not None and check.isChecked():
                hook = combo.currentData()
                if hook is not None:
                    urls.add(hook.url)
        urls.update(
            hook.url for hook, check in self.webhook_checks if check.isChecked()
        )
        preserved = self._preserved_webhook_slots()
        preserved_urls = {url for url in preserved.values() if url}
        return len(preserved) + len(urls - preserved_urls)

    def _preserved_webhook_slots(self) -> dict[int, str]:
        config = self.existing_config
        if config is None:
            return {}
        urls = (
            getattr(config, "discord_webhook", ""),
            getattr(config, "discord_webhook2", ""),
            getattr(config, "discord_webhook3", ""),
        )
        try:
            active = int(getattr(config, "active_discord_webhook", 1) or 1)
        except (TypeError, ValueError):
            active = 1
        if active not in (1, 2, 3):
            active = 1
        try:
            nightly = int(
                getattr(config, "raid_report_discord_webhook", 0) or 0
            )
        except (TypeError, ValueError):
            nightly = 0
        if nightly not in (1, 2, 3):
            nightly = active
        preserved = {}
        fight_selected = (
            self.fight_check.isChecked()
            and self.fight_combo.currentData() is not None
        )
        nightly_selected = (
            self.nightly_check.isChecked()
            and self.nightly_combo.currentData() is not None
        )
        if not fight_selected:
            preserved[active] = urls[active - 1]
        if not nightly_selected:
            preserved[nightly] = urls[nightly - 1]
        return preserved

    def _trim_webhook_selection(self) -> None:
        for _hook, check in reversed(self.webhook_checks):
            if self._selected_webhook_count() <= 3:
                break
            check.setChecked(False)

    def _refresh_selection_state(self) -> None:
        core_checks = {
            self.log_check,
            self.parser_check,
            self.fight_check,
            self.nightly_check,
        }
        selected = sum(
            check.isChecked()
            for check in self._import_checks
            if check not in core_checks
        )
        selected += sum(
            check.isChecked() and combo.currentData() is not None
            for check, combo in (
                (self.log_check, self.log_combo),
                (self.parser_check, self.parser_combo),
                (self.fight_check, self.fight_combo),
                (self.nightly_check, self.nightly_combo),
            )
        )
        webhook_overflow = self._selected_webhook_count() > 3
        if hasattr(self, "use_button"):
            self.use_button.setEnabled(selected > 0 and not webhook_overflow)
            self.use_button.setText("Use This Setup")
        if hasattr(self, "webhook_limit_label"):
            self.webhook_limit_label.setVisible(
                len(self.finding.webhooks) > 3 or webhook_overflow
            )

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
            log_folder=(
                self.log_combo.currentData() if self.log_check.isChecked() else None
            ),
            parser_executable=(
                self.parser_combo.currentData()
                if self.parser_check.isChecked()
                else None
            ),
            fight_webhook=(
                self.fight_combo.currentData()
                if self.fight_check.isChecked()
                else None
            ),
            nightly_webhook=(
                self.nightly_combo.currentData()
                if self.nightly_check.isChecked()
                else None
            ),
            settings=tuple(
                setting
                for setting, check in self.setting_checks
                if check.isChecked()
            ),
            extra_webhooks=tuple(
                hook for hook, check in self.webhook_checks if check.isChecked()
            ),
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
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        if len(preview.splitlines()) > 12:
            preview_scroll = QScrollArea()
            preview_scroll.setWidgetResizable(True)
            preview_scroll.setMinimumHeight(170)
            preview_scroll.setMaximumHeight(300)
            preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
            preview_scroll.setWidget(details)
            layout.addWidget(preview_scroll)
        else:
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


def _manual_import(
    parent: QWidget | None,
    initial_path: str | Path | None = None,
) -> CompetitorFinding | None:
    start = str(initial_path or "")
    if not start:
        start = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
    path, _selected_filter = QFileDialog.getOpenFileName(
        parent,
        "Choose Settings from Another Log Tool",
        start,
        _CONFIG_FILTER,
    )
    if not path:
        return None
    try:
        return parse_competitor_config(path)
    except CompetitorConfigError as exc:
        QMessageBox.warning(parent, "Settings File Not Used", str(exc))
        return None


def choose_manual_competitor_import(
    parent: QWidget | None,
    *,
    gw2_dirs: Iterable[str | Path] = (),
) -> CompetitorImportPlan | None:
    """Advanced path: ask which app, then seed its expected settings file."""
    names = [target.name for target in COMPETITOR_IMPORT_TARGETS]
    name, accepted = QInputDialog.getItem(
        parent,
        "Advanced Setup",
        "Which fight-report app do you already use?",
        names,
        0,
        False,
    )
    if not accepted:
        return None
    target = COMPETITOR_IMPORT_TARGETS[names.index(name)]
    try:
        expected = expected_competitor_config_path(
            target.key, gw2_dirs=gw2_dirs
        )
    except CompetitorConfigError as exc:
        QMessageBox.warning(parent, "Could Not Guess the File", str(exc))
        return None
    finding = _manual_import(parent, expected)
    if finding is None:
        return None
    return preview_competitor_finding(finding, parent)


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
            return choose_manual_competitor_import(parent, gw2_dirs=gw2_dirs)
        else:
            finding = findings[labels.index(label)]
    else:
        return choose_manual_competitor_import(parent, gw2_dirs=gw2_dirs)
    if finding is None:
        return None

    return preview_competitor_finding(finding, parent)


def preview_competitor_finding(
    finding: CompetitorFinding, parent: QWidget | None
) -> CompetitorImportPlan | None:
    """Preview one already-discovered setup and return the consented plan.

    Used by the wizard's proactive offer: discovery already ran, the user
    clicked "Use <tool>'s Settings", so no picker — straight to consent."""
    dialog = CompetitorImportDialog(
        finding,
        parent,
        existing_config=getattr(parent, "config", None),
    )
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
    """Plain-language links to neighboring tools and migration support."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Other WvW Log Tools")
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
                f'<a href="{project.url}"><b>{project.name}</b></a><br>'
                f'<b>What it does well:</b> {project.celebrates}<br>'
                f'<b>SparkyBot connection:</b> '
                f'{project.relationship}'
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
