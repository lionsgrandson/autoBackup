"""Bilingual Qt desktop UI. The user only needs a username and password."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QTime, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QTimeEdit,
    QVBoxLayout, QWidget,
)

from . import auth, core, scheduler
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
            result = self.action(self.progress.emit)
            self.success.emit(result)
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = core.load_config()
        self.lang = self.config.get("language", "he")
        if self.lang not in TEXT:
            self.lang = "he"
        self.sources = list(self.config.get("sources") or [str(Path.home())])
        self.snapshots: list[dict] = []
        self.busy = False
        self.thread = None
        self.job = None
        self.after_job = None
        self.widgets: dict[str, object] = {}
        self.build()
        self.apply_language()
        self.apply_signed_in()

    def t(self, key):
        return TEXT[self.lang][key]

    def label(self, key):
        item = QLabel(self.t(key))
        item.setWordWrap(True)
        self.widgets[key] = item
        return item

    def button(self, key, handler, primary=False):
        item = QPushButton(self.t(key))
        item.setProperty("primary", primary)
        item.clicked.connect(handler)
        self.widgets[key] = item
        return item

    def build(self):
        self.setWindowTitle("AutoBackup")
        self.resize(890, 800)
        self.setMinimumSize(690, 640)
        self.setStyleSheet("""
            QMainWindow, QScrollArea { background: #f4f7fc; }
            QLabel { color: #263650; font-size: 13px; }
            QGroupBox { font-size: 16px; font-weight: bold; background: white;
                border: 1px solid #dbe3ee; border-radius: 12px;
                margin-top: 16px; padding: 18px 16px 15px 16px; }
            QGroupBox::title { subcontrol-origin: margin; left: 15px; top: 0px;
                padding: 0 8px; color: #143662; }
            QLineEdit, QComboBox, QListWidget, QTimeEdit {
                border: 1px solid #ccd6e4; border-radius: 7px; background: #fff;
                color: #1b2a40; padding: 8px; min-height: 22px; font-size: 13px; }
            QPushButton { background: #e8eef6; color: #203553; border: none;
                border-radius: 8px; padding: 10px 15px; font-size: 13px;
                font-weight: 600; }
            QPushButton:hover { background: #d7e5f5; }
            QPushButton:disabled { background: #eaedf2; color: #9aa7b8; }
            QPushButton[primary="true"] { background: #1761c6; color: white; }
            QPushButton[primary="true"]:hover { background: #0d4da6; }
            QProgressBar { border: none; height: 9px; background: #e0e7f3; }
            QProgressBar::chunk { background: #1761c6; }
        """)
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        area.setWidget(holder)
        self.setCentralWidget(area)
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(24, 22, 24, 30)
        outer.setSpacing(14)

        header = QHBoxLayout()
        headers = QVBoxLayout()
        title = self.label("app")
        title.setStyleSheet("font-size: 30px; font-weight: bold; color: #163663;")
        headers.addWidget(title)
        subtitle = self.label("subtitle")
        subtitle.setStyleSheet("font-size: 15px; color: #627794;")
        headers.addWidget(subtitle)
        header.addLayout(headers, 1)
        self.language = QComboBox()
        self.language.addItem("עברית", "he")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(0 if self.lang == "he" else 1)
        self.language.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language)
        outer.addLayout(header)
        scope = self.label("scope")
        scope.setStyleSheet("background: #fff4e3; color: #65491c; padding: 12px; border-radius: 8px;")
        outer.addWidget(scope)

        self.login_box = QGroupBox()
        auth_layout = QVBoxLayout(self.login_box)
        form = QFormLayout()
        self.username = QLineEdit(self.config.get("username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.username.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.password.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.password.returnPressed.connect(self.signin)
        form.addRow(self.label("username"), self.username)
        form.addRow(self.label("password"), self.password)
        auth_layout.addLayout(form)
        auth_layout.addWidget(self.label("signin_hint"))
        buttons = QHBoxLayout()
        buttons.addWidget(self.button("signin", self.signin, True))
        buttons.addWidget(self.button("signout", self.signout))
        auth_layout.addLayout(buttons)
        self.account_label = QLabel("")
        auth_layout.addWidget(self.account_label)
        outer.addWidget(self.login_box)

        self.source_box = QGroupBox()
        source_layout = QVBoxLayout(self.source_box)
        source_layout.addWidget(self.label("sources_hint"))
        self.source_list = QListWidget()
        self.source_list.setMinimumHeight(115)
        self.source_list.addItems(self.sources)
        source_layout.addWidget(self.source_list)
        buttons = QHBoxLayout()
        buttons.addWidget(self.button("add", self.add_source))
        buttons.addWidget(self.button("remove", self.remove_source))
        buttons.addStretch()
        source_layout.addLayout(buttons)
        outer.addWidget(self.source_box)

        self.schedule_box = QGroupBox()
        sched = QVBoxLayout(self.schedule_box)
        row = QHBoxLayout()
        row.addWidget(self.label("time"))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        parsed = QTime.fromString(self.config.get("time", "02:00"), "HH:mm")
        self.time.setTime(parsed if parsed.isValid() else QTime(2, 0))
        row.addWidget(self.time)
        row.addStretch()
        sched.addLayout(row)
        sched.addWidget(self.label("time_hint"))
        self.vss = QCheckBox()
        self.vss.setChecked(bool(self.config.get("vss")))
        self.widgets["vss"] = self.vss
        sched.addWidget(self.vss)
        buttons = QHBoxLayout()
        buttons.addWidget(self.button("save", self.save_settings))
        buttons.addWidget(self.button("backup", self.backup_now, True))
        sched.addLayout(buttons)
        outer.addWidget(self.schedule_box)

        self.restore_box = QGroupBox()
        rest = QVBoxLayout(self.restore_box)
        row = QHBoxLayout()
        row.addWidget(self.label("device"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.currentIndexChanged.connect(self.device_changed)
        row.addWidget(self.device_combo, 1)
        rest.addLayout(row)
        rest.addWidget(self.button("devices", self.refresh_devices))
        rest.addWidget(self.button("refresh", self.refresh_snapshots))
        self.snapshot_list = QListWidget()
        self.snapshot_list.setMinimumHeight(100)
        rest.addWidget(self.snapshot_list)
        rest.addWidget(self.button("restore_btn", self.restore_selected))
        outer.addWidget(self.restore_box)

        self.status_box = QGroupBox()
        status = QVBoxLayout(self.status_box)
        self.status_label = QLabel(self.t("ready"))
        self.status_label.setWordWrap(True)
        status.addWidget(self.status_label)
        self.last_label = QLabel()
        status.addWidget(self.last_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        status.addWidget(self.progress)
        outer.addWidget(self.status_box)
        self.update_last()

    def apply_language(self):
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if self.lang == "he"
                                else Qt.LayoutDirection.LeftToRight)
        for target, key in [(self.login_box, "connection"),
                            (self.source_box, "sources"),
                            (self.schedule_box, "schedule"),
                            (self.restore_box, "restore"),
                            (self.status_box, "status")]:
            target.setTitle(self.t(key))
        for key, obj in self.widgets.items():
            if hasattr(obj, "setText"):
                obj.setText(self.t(key))
        self.update_last()
        self.apply_signed_in()

    def change_language(self):
        self.lang = self.language.currentData()
        config = core.load_config()
        config["language"] = self.lang
        core.save_config(config)
        self.apply_language()

    def apply_signed_in(self):
        logged = bool(self.config.get("user_id"))
        self.username.setReadOnly(logged)
        self.password.setEnabled(not logged)
        self.widgets["signin"].setEnabled(not logged)
        self.widgets["signout"].setEnabled(logged)
        for box in (self.source_box, self.schedule_box, self.restore_box):
            box.setEnabled(logged)
        self.account_label.setText(
            f"{self.t('signed')}: {self.config.get('username', '')}" if logged else "")
        if logged and self.device_combo.count() == 0:
            self.device_combo.addItem(self.config.get("device_id", ""), self.config.get("device_id", ""))

    def signin(self):
        if self.busy:
            return
        username = self.username.text()
        password = self.password.text()

        def action(progress):
            config = core.load_config()
            config = auth.sign_in(config, username, password)
            core.save_config(config)
            schedule_error = None
            try:
                scheduler.install_schedule(config["time"])
            except Exception as exc:
                schedule_error = str(exc)
            return {"config": config, "schedule_error": schedule_error}

        def done(result):
            self.config = result["config"]
            self.password.clear()
            self.apply_signed_in()
            self.status_label.setText(self.t("connected"))
            if result["schedule_error"]:
                QMessageBox.warning(self, self.t("notice"),
                                    self.t("schedule_error") + "\n" + result["schedule_error"])
            self.after_job = self.refresh_devices

        self.start_job(action, done)

    def signout(self):
        if self.busy:
            return
        try:
            self.config = auth.sign_out(core.load_config())
            core.save_config(self.config)
            scheduler.remove_schedule()
        except Exception as exc:
            self.job_error(str(exc))
            return
        self.snapshots = []
        self.snapshot_list.clear()
        self.device_combo.clear()
        self.apply_signed_in()
        self.status_label.setText(self.t("signed_out"))

    def add_source(self):
        path = QFileDialog.getExistingDirectory(self, self.t("add"), str(Path.home()))
        if path and path not in self.sources:
            self.sources.append(path)
            self.source_list.addItem(path)

    def remove_source(self):
        index = self.source_list.currentRow()
        if index >= 0 and len(self.sources) > 1:
            self.sources.pop(index)
            self.source_list.takeItem(index)

    def form_config(self):
        config = core.load_config()
        config.update({
            "language": self.lang, "sources": list(self.sources),
            "time": self.time.time().toString("HH:mm"), "interval_days": 30,
            "vss": self.vss.isChecked(),
        })
        core.validate(config)
        return config

    def start_job(self, action, on_success):
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
        self.job.success.connect(on_success)
        self.job.failure.connect(self.job_error)
        self.job.finished.connect(self.thread.quit)
        self.job.finished.connect(self.job.deleteLater)
        self.thread.finished.connect(self.job_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def job_finished(self):
        self.busy = False
        self.job = None
        self.thread = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.update_last()
        if self.after_job:
            callback = self.after_job
            self.after_job = None
            QTimer.singleShot(0, callback)

    def job_error(self, message):
        self.status_label.setText(message[:400])
        QMessageBox.critical(self, self.t("error"), message[:1700])

    def save_settings(self):
        try:
            config = self.form_config()
        except Exception as exc:
            self.job_error(str(exc))
            return
        def action(progress):
            core.save_config(config)
            scheduler.install_schedule(config["time"])
            return config
        def done(result):
            self.config = result
            self.status_label.setText(self.t("saved"))
        self.start_job(action, done)

    def backup_now(self):
        try:
            config = self.form_config()
        except Exception as exc:
            self.job_error(str(exc))
            return
        def action(progress):
            core.save_config(config)
            return core.run_backup(config, callback=progress)
        def done(_result):
            self.status_label.setText(self.t("success"))
            self.after_job = self.refresh_snapshots
        self.start_job(action, done)

    def refresh_devices(self):
        if not self.config.get("user_id"):
            return
        config = core.load_config()
        def done(devices):
            own = config["device_id"]
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for item in devices:
                name = item.get("device_name", "Computer")
                self.device_combo.addItem(name, item["id"])
            index = self.device_combo.findData(own)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
            self.device_combo.blockSignals(False)
            self.status_label.setText(f"{len(devices)} computer(s)")
        self.start_job(lambda progress: auth.list_devices(config), done)

    def device_changed(self):
        self.snapshots = []
        self.snapshot_list.clear()

    def selected_device(self):
        return self.device_combo.currentData() or self.config.get("device_id")

    def refresh_snapshots(self):
        try:
            config = self.form_config()
            device_id = self.selected_device()
        except Exception as exc:
            self.job_error(str(exc))
            return
        def done(snapshots):
            self.snapshots = snapshots
            self.snapshot_list.clear()
            for item in snapshots:
                self.snapshot_list.addItem(
                    f"{item.get('time', '')[:19]} — {item.get('short_id', item.get('id', ''))}")
            self.status_label.setText(f"{len(snapshots)} recovery point(s)")
        self.start_job(lambda progress: core.list_snapshots(config, device_id), done)

    def restore_selected(self):
        row = self.snapshot_list.currentRow()
        if row < 0 or row >= len(self.snapshots):
            QMessageBox.information(self, self.t("info"), self.t("empty"))
            return
        target = QFileDialog.getExistingDirectory(self, self.t("choose"), str(Path.home()))
        if not target:
            return
        try:
            config = self.form_config()
        except Exception as exc:
            self.job_error(str(exc))
            return
        snapshot_id = self.snapshots[row]["id"]
        device_id = self.selected_device()
        self.start_job(lambda progress: core.restore(config, snapshot_id, target,
                                                     callback=progress, device_id=device_id),
                       lambda result: self.status_label.setText(self.t("restored")))

    def update_last(self):
        last = core.load_config().get("last_success")
        self.last_label.setText(
            self.t("last") + " " +
            (last[:19].replace("T", " ") if last else self.t("never")))

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.warning(self, self.t("notice"), self.t("busy_close"))
            event.ignore()
            return
        super().closeEvent(event)
