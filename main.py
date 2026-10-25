"""Точка входа SoundPad.

Запуск:  python main.py   (из папки soundpad_app)
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from core.audio_engine import AudioEngine
from core.config_manager import ConfigManager
from core.hotkey_manager import HotkeyManager
from ui.main_window import MainWindow


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("soundpad")

    app = QApplication(sys.argv)
    app.setApplicationName("SoundPad")
    # Приложение должно жить в трее, когда окно скрыто; выход — через window.closeEvent.
    app.setQuitOnLastWindowClosed(False)

    def excepthook(exc_type, exc, tb) -> None:
        log.critical("Необработанное исключение", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, "SoundPad", f"Непредвиденная ошибка:\n{exc}")

    sys.excepthook = excepthook

    config = ConfigManager()
    config.load()
    engine = AudioEngine()
    hotkeys = HotkeyManager()

    window = MainWindow(config, engine, hotkeys)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
