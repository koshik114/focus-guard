from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from focus_guard.config import AppConfig
from focus_guard.storage import EventStore
from focus_guard.ui.main_window import MainWindow
from focus_guard.ui.theme import apply_light_theme


def main() -> int:
    config = AppConfig.from_env()
    app = QApplication(sys.argv)
    app.setApplicationName("Focus Guard")
    app.setOrganizationName("Focus Guard")
    apply_light_theme(app)

    store = EventStore(config.data_dir / "focus_guard.sqlite3")
    window = MainWindow(config=config, store=store)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
