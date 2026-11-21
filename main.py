"""Entry point; --scheduled runs headless under Windows Task Scheduler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    if "--scheduled" in sys.argv:
        from autobackup.scheduler import scheduled_main
        return scheduled_main()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from autobackup.ui import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("AutoBackup")
    try:
        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, "AutoBackup", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
