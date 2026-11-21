"""Native Windows desktop interface using PySide6. No browser or cloud backend."""
from __future__ import annotations
import json
import secrets
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QTime, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QTimeEdit, QVBoxLayout, QWidget,
)

from . import core, scheduler
from .i18n import TEXT


class Job(QObject):
    progress = Signal(str)
    success = Signal(object)
    failure = Signal(str)
    finished = Signal()

    def __init__(self, action):
        super().__init__()
        self.action = action

    @Slot()
    def run(self):
        try:
            value = self.action(self.progress.emit)
            self.success.emit(value)
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = core.load_config()
        self.lang = self.config.get("language", "he") if self.config.get("language") in TEXT else "he"
        self.sources = list(self.config.get("sources") or [str(Path.home())])
        self.busy = False
        self.thread = None
        self.job = None
        self.snapshots = []
        self._refresh_after_job = False
        self.widgets = {}
        self.build()
        self.apply_language()

    def t(self, key):
        return TEXT[self.lang][key]

    def label(self, text_key):
        label = QLabel(self.t(text_key))
        label.setWordWrap(True)
        self.widgets[text_key] = label
        return label

    def button(self, key, handler):
        btn = QPushButton(self.t(key))
        btn.clicked.connect(handler)
        self.widgets[key] = btn
        return btn

    def field(self, value="", secret=False, hint=""):
        line = QLineEdit(value)
        if secret:
            line.setEchoMode(QLineEdit.EchoMode.Password)
        if hint:
            line.setPlaceholderText(hint)
        return line

    def build(self):
        self.setWindowTitle("AutoBackup — Cloudflare R2")
        self.setMinimumSize(760, 680)
        self.resize(940, 850)
        self.setStyleSheet("""
            QMainWindow, QScrollArea { background: #f5f7fb; }
            QLabel { color: #23324b; font-size: 13px; }
            QGroupBox { font-size: 16px; font-weight: 700; background: white;
                border: 1px solid #dbe3ee; border-radius: 12px;
                margin-top: 16px; padding: 18px 15px 15px 15px; }
            QGroupBox::title { subcontrol-origin: margin; left: 16px; top: 0px;
                padding: 0 8px; color: #1d3762; }
            QLineEdit, QListWidget, QTimeEdit, QComboBox {
                border: 1px solid #cad5e4; border-radius: 7px;
                background: #fff; padding: 8px; min-height: 20px;
                color: #1b2a40; font-size: 13px;
            }
            QPushButton { background: #e9eef6; color: #203553; border: none;
                border-radius: 7px; padding: 10px 15px; font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #dbe6f6; }
            QPushButton:disabled { background: #e9ebee; color: #99a4b4; }
            QPushButton[primary="true"] { background: #1761c6; color: white; }
            QPushButton[primary="true"]:hover { background: #0e4da5; }
            QProgressBar { border: none; height: 8px; background: #e0e8f3;
                border-radius: 4px; }
            QProgressBar::chunk { background: #1761c6; border-radius: 4px; }
        """)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        central = QWidget()
        scroll.setWidget(central)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(26, 20, 26, 26)
        outer.setSpacing(15)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = self.label("app")
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #153763;")
        title_box.addWidget(title)
        sub = self.label("subtitle")
        sub.setStyleSheet("font-size: 15px; color: #627794;")
        title_box.addWidget(sub)
        header.addLayout(title_box, 1)
        self.language = QComboBox()
        self.language.addItem("עברית", "he")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(0 if self.lang == "he" else 1)
        self.language.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language)
        outer.addLayout(header)
        scope = self.label("scope")
        scope.setStyleSheet("background: #fff4e3; color: #6a4c19; border-radius: 8px; padding: 12px;")
        outer.addWidget(scope)

        self.connect_box = QGroupBox(self.t("connection"))
        conn = QVBoxLayout(self.connect_box)
        form = QFormLayout()
        form.setSpacing(10)
        self.account = self.field(self.config.get("account_id", ""), hint="32 hexadecimal characters")
        self.endpoint = self.field(self.config.get("endpoint", ""), hint="https://ACCOUNT.eu.r2.cloudflarestorage.com")
        self.bucket = self.field(self.config.get("bucket", ""))
        self.profile = self.field(self.config.get("profile", ""), hint="my-laptop")
        self.access = self.field()
        self.secret = self.field(secret=True)
        self.password = self.field(secret=True)
        for key, widget in [
            ("account_id", self.account), ("endpoint", self.endpoint),
            ("bucket", self.bucket), ("profile", self.profile),
            ("access_id", self.access), ("access_secret", self.secret),
            ("password", self.password),
        ]:
            form.addRow(self.label(key), widget)
        conn.addLayout(form)
        for field in (self.account, self.endpoint, self.bucket, self.profile, self.access, self.secret, self.password):
            field.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        hint = self.label("keys_hint")
        hint.setStyleSheet("color: #60758d;")
        conn.addWidget(hint)
        hint2 = self.label("password_hint")
        hint2.setStyleSheet("color: #a44c25; font-weight: 600;")
        conn.addWidget(hint2)
        tools = QHBoxLayout()
        self.show_box = QCheckBox(self.t("show"))
        self.widgets["show"] = self.show_box
        self.show_box.toggled.connect(self.show_secrets)
        tools.addWidget(self.show_box)
        tools.addWidget(self.button("generate", self.generate_password))
        tools.addWidget(self.button("copy", self.copy_password))
        tools.addStretch(1)
        conn.addLayout(tools)
        self.connect_btn = self.button("connect_btn", self.connect_account)
        self.connect_btn.setProperty("primary", True)
        conn.addWidget(self.connect_btn)
        outer.addWidget(self.connect_box)

        self.source_box = QGroupBox(self.t("sources"))
        source_layout = QVBoxLayout(self.source_box)
        source_layout.addWidget(self.label("sources_hint"))
        self.source_list = QListWidget()
        self.source_list.setMinimumHeight(105)
        self.source_list.addItems(self.sources)
        source_layout.addWidget(self.source_list)
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self.button("add", self.add_source))
        source_buttons.addWidget(self.button("remove", self.remove_source))
        source_buttons.addStretch(1)
        source_layout.addLayout(source_buttons)
        outer.addWidget(self.source_box)

        self.schedule_box = QGroupBox(self.t("schedule"))
        sched = QVBoxLayout(self.schedule_box)
        time_row = QHBoxLayout()
        time_row.addWidget(self.label("time"))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setTime(QTime.fromString(self.config.get("time", "02:00"), "HH:mm"))
        time_row.addWidget(self.time)
        time_row.addStretch(1)
        sched.addLayout(time_row)
        sched.addWidget(self.label("time_hint"))
        self.vss = QCheckBox(self.t("vss"))
        self.widgets["vss"] = self.vss
        self.vss.setChecked(bool(self.config.get("vss", False)))
        sched.addWidget(self.vss)
        bottom = QHBoxLayout()
        bottom.addWidget(self.button("save", self.save_settings))
        self.backup_btn = self.button("backup", self.backup_now)
        self.backup_btn.setProperty("primary", True)
        bottom.addWidget(self.backup_btn)
        sched.addLayout(bottom)
        outer.addWidget(self.schedule_box)

        self.restore_box = QGroupBox(self.t("restore"))
        rest = QVBoxLayout(self.restore_box)
        rest.addWidget(self.button("refresh", self.refresh_snapshots))
        self.snapshot_list = QListWidget()
        self.snapshot_list.setMinimumHeight(105)
        rest.addWidget(self.snapshot_list)
        rest.addWidget(self.button("restore_btn", self.restore_selected))
        rest.addWidget(self.button("export", self.export_connection))
        outer.addWidget(self.restore_box)

        self.status_box = QGroupBox(self.t("status"))
        status_layout = QVBoxLayout(self.status_box)
        self.status_label = QLabel(self.t("ready"))
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        self.last_label = QLabel()
        status_layout.addWidget(self.last_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        status_layout.addWidget(self.progress)
        outer.addWidget(self.status_box)
        self.update_last()

    def apply_language(self):
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if self.lang == "he"
                                else Qt.LayoutDirection.LeftToRight)
        self.connect_box.setTitle(self.t("connection"))
        self.source_box.setTitle(self.t("sources"))
        self.schedule_box.setTitle(self.t("schedule"))
        self.restore_box.setTitle(self.t("restore"))
        self.status_box.setTitle(self.t("status"))
        for name, widget in self.widgets.items():
            if isinstance(widget, QLabel):
                widget.setText(self.t(name))
            elif isinstance(widget, QPushButton):
                widget.setText(self.t(name))
            elif isinstance(widget, QCheckBox):
                widget.setText(self.t(name))
        self.update_last()

    def change_language(self):
        self.lang = self.language.currentData()
        self.apply_language()
        config = core.load_config()
        config["language"] = self.lang
        core.save_config(config)

    def show_secrets(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.secret.setEchoMode(mode)
        self.password.setEchoMode(mode)

    def generate_password(self):
        self.password.setText(secrets.token_urlsafe(36))
        QMessageBox.warning(self, self.t("notice"), self.t("recover"))

    def copy_password(self):
        if not self.password.text():
            QMessageBox.information(self, self.t("info"), self.t("no_pass"))
            return
        QApplication.clipboard().setText(self.password.text())
        QMessageBox.information(self, self.t("notice"), self.t("copied"))

    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, self.t("add"), str(Path.home()))
        if path and path not in self.sources:
            self.sources.append(path)
            self.source_list.addItem(path)

    def remove_source(self):
        row = self.source_list.currentRow()
        if row >= 0 and len(self.sources) > 1:
            self.sources.pop(row)
            self.source_list.takeItem(row)

    def from_form(self):
        config = core.load_config()
        config.update({
            "language": self.lang,
            "account_id": self.account.text().strip(),
            "endpoint": self.endpoint.text().strip(),
            "bucket": self.bucket.text().strip(),
            "profile": self.profile.text().strip(),
            "sources": list(self.sources),
            "time": self.time.time().toString("HH:mm"),
            "interval_days": 30,
            "vss": self.vss.isChecked(),
        })
        core.validate(config)
        return config

    def job_start(self, action, success):
        if self.busy:
            return
        self.busy = True
        self.progress.setRange(0, 0)
        self.status_label.setText(self.t("busy"))
        self.thread = QThread(self)
        self.job = Job(action)
        self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run)
        self.job.progress.connect(self.status_label.setText)
        self.job.success.connect(success)
        self.job.failure.connect(self.job_error)
        self.job.finished.connect(self.thread.quit)
        self.job.finished.connect(self.job.deleteLater)
        self.thread.finished.connect(self.job_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def job_finished(self):
        self.busy = False
        self.thread = None
        self.job = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.update_last()
        if self._refresh_after_job:
            self._refresh_after_job = False
            QTimer.singleShot(0, self.refresh_snapshots)

    def job_error(self, message):
        self.status_label.setText(message[:450])
        QMessageBox.critical(self, self.t("error"), message[:2200])

    def connect_account(self):
        try:
            config = self.from_form()
        except Exception as exc:
            self.job_error(str(exc))
            return
        access = self.access.text()
        secret = self.secret.text()
        password = self.password.text()

        def action(progress):
            core.connect(config, access, secret, password)
            try:
                scheduler.install_schedule(config["time"])
            except Exception as exc:
                return ("schedule_failed", str(exc))
            return ("connected", None)

        def done(result):
            self.config = core.load_config()
            if result[0] == "connected":
                self.status_label.setText(self.t("connected"))
                QMessageBox.information(self, self.t("notice"), self.t("recover"))
            else:
                self.job_error(self.t("schedule_error") + "\n" + result[1])

        self.job_start(action, done)

    def save_settings(self):
        try:
            config = self.from_form()
            core.get_secrets(config)
        except Exception as exc:
            self.job_error(str(exc))
            return

        def action(progress):
            core.save_config(config)
            scheduler.install_schedule(config["time"])
            return True

        def done(result):
            self.config = core.load_config()
            self.status_label.setText(self.t("saved"))

        self.job_start(action, done)

    def backup_now(self):
        try:
            config = self.from_form()
            core.get_secrets(config)
        except Exception as exc:
            self.job_error(str(exc))
            return
        self.job_start(lambda progress: self._save_then_backup(config, progress),
                       self.backup_done)

    def _save_then_backup(self, config, progress):
        core.save_config(config)
        return core.run_backup(config, callback=progress)

    def backup_done(self, result):
        self.status_label.setText(self.t("success"))
        self._refresh_after_job = True

    def refresh_snapshots(self):
        try:
            config = self.from_form()
        except Exception as exc:
            self.job_error(str(exc))
            return
        def done(result):
            self.snapshots = result
            self.snapshot_list.clear()
            for entry in result:
                label = f"{entry.get('time', '')[:19]}  —  {entry.get('short_id', entry.get('id', ''))}"
                self.snapshot_list.addItem(label)
            self.status_label.setText(f"{len(result)} snapshots")
        self.job_start(lambda progress: core.list_snapshots(config), done)

    def restore_selected(self):
        row = self.snapshot_list.currentRow()
        if row < 0 or row >= len(self.snapshots):
            QMessageBox.information(self, self.t("info"), self.t("empty"))
            return
        target = QFileDialog.getExistingDirectory(self, self.t("choose"), str(Path.home()))
        if not target:
            return
        try:
            config = self.from_form()
        except Exception as exc:
            self.job_error(str(exc))
            return
        snapshot_id = self.snapshots[row]["id"]
        self.job_start(lambda progress: core.restore(config, snapshot_id, target, progress),
                       lambda result: self.status_label.setText(self.t("restored")))

    def export_connection(self):
        try:
            config = self.from_form()
        except Exception as exc:
            self.job_error(str(exc))
            return
        path, unused = QFileDialog.getSaveFileName(
            self, self.t("export"), "AutoBackup-connection.json", "JSON (*.json)")
        if path:
            safe = {key: config[key] for key in
                    ("account_id", "endpoint", "bucket", "profile")}
            Path(path).write_text(json.dumps(safe, indent=2), encoding="utf-8")
            QMessageBox.information(self, self.t("info"), self.t("export_done"))

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.warning(self, self.t("notice"), self.t("busy_close"))
            event.ignore()
            return
        super().closeEvent(event)

    def update_last(self):
        try:
            stamp = core.load_config().get("last_success")
        except Exception:
            stamp = None
        self.last_label.setText(self.t("last") + " " + (stamp[:19].replace("T", " ") if stamp else self.t("never")))
