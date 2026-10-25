"""Главное окно: связывает конфиг, аудио-движок и глобальные хоткеи с интерфейсом."""
from __future__ import annotations

import logging
import os
from functools import partial
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from core.audio_engine import (
    AudioConfig, AudioEngine, list_input_devices, list_output_devices,
)
from core.config_manager import STOP_ALL_OWNER, ConfigManager
from core.hotkey_manager import HotkeyManager, format_hotkey
from ui.widgets import (
    P, EmptyState, LabeledSlider, SettingsDialog, SoundRow, build_style, make_icon,
    repolish, tab_focus,
)

log = logging.getLogger(__name__)

SUPPORTED_EXTS = (".wav", ".mp3", ".ogg", ".opus", ".flac", ".aiff", ".aif")
FILE_FILTER = "Аудио (" + " ".join(f"*{e}" for e in SUPPORTED_EXTS) + ");;Все файлы (*)"


def _plural_sounds(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} звук"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} звука"
    return f"{n} звуков"


class MainWindow(QMainWindow):
    # Сигналы нужны, чтобы безопасно переходить из аудио-/хоткей-потоков в GUI-поток.
    sound_started = Signal(str)
    sound_stopped = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, config: ConfigManager, engine: AudioEngine, hotkeys: HotkeyManager) -> None:
        super().__init__()
        self.config = config
        self.engine = engine
        self.hotkeys = hotkeys
        self._rows: dict[str, SoundRow] = {}
        self._quitting = False
        self._tray: Optional[QSystemTrayIcon] = None

        self.setWindowTitle("Klaker")
        self.resize(940, 640)
        self.setMinimumSize(760, 480)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_style())
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))

        self._build_ui()
        self._build_tray()

        self.sound_started.connect(lambda sid: self._set_row_playing(sid, True))
        self.sound_stopped.connect(lambda sid: self._set_row_playing(sid, False))
        self.error_occurred.connect(self._status)
        engine.on_started = self.sound_started.emit
        engine.on_stopped = self.sound_stopped.emit
        engine.on_error = self.error_occurred.emit

        for sound in config.sounds_snapshot():
            self._add_row(sound)
            if os.path.isfile(sound.path):
                engine.preload(sound.path)
        self._update_empty_state()

        self._apply_audio_settings(show_dialog=False)
        self._rebuild_hotkeys()
        self.hotkeys.start()

    # ================================================================== UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 10)
        root.setSpacing(14)

        # --- верхняя панель
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("Klaker")
        title.setObjectName("appTitle")

        add_btn = QPushButton("Добавить звуки")
        add_btn.setObjectName("primary")
        add_btn.setIcon(make_icon("plus", "#0d1020", 16))
        add_btn.setIconSize(QSize(16, 16))
        add_btn.clicked.connect(self._on_add_clicked)

        stop_btn = QPushButton("Остановить все")
        stop_btn.setIcon(make_icon("stop", P["text"], 16))
        stop_btn.setIconSize(QSize(16, 16))
        stop_btn.clicked.connect(lambda: self.engine.stop_all())

        self.hotkeys_btn = QPushButton("Хоткеи: вкл")
        self.hotkeys_btn.setObjectName("hotkeysToggle")
        self.hotkeys_btn.setCheckable(True)
        self.hotkeys_btn.setChecked(True)
        self.hotkeys_btn.setToolTip("Глобальные хоткеи работают, даже когда окно свёрнуто")
        self.hotkeys_btn.toggled.connect(self._on_hotkeys_toggled)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(160)
        self.search.textChanged.connect(self._apply_filter)

        settings_btn = QPushButton()
        settings_btn.setObjectName("ghost")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setIcon(make_icon("gear", P["text"], 20))
        settings_btn.setIconSize(QSize(20, 20))
        settings_btn.setToolTip("Настройки")
        settings_btn.clicked.connect(self._open_settings)

        tab_focus(add_btn, stop_btn, self.hotkeys_btn, settings_btn)
        top.addWidget(title)
        for w in (add_btn, stop_btn, self.hotkeys_btn):
            top.addWidget(w)
        top.addWidget(self.search, 1)
        top.addWidget(settings_btn)
        root.addLayout(top)

        # --- список звуков
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        holder = QWidget()
        self._list_layout = QVBoxLayout(holder)
        self._list_layout.setContentsMargins(0, 0, 6, 0)
        self._list_layout.setSpacing(8)
        self._empty = EmptyState()
        self._list_layout.addWidget(self._empty)
        self._list_layout.addStretch(1)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        # --- микшер
        s = self.config.settings
        mixer = QFrame()
        mixer.setObjectName("mixer")
        bottom = QHBoxLayout(mixer)
        bottom.setContentsMargins(16, 10, 16, 10)
        bottom.setSpacing(28)
        for title_text, value, slot, tip in (
            ("Общая", s.master_volume, self._on_master_volume, "Общая громкость всех звуков"),
            ("Наушники", s.monitor_volume, self._on_monitor_volume, "Громкость в ваших наушниках (не влияет на микрофон)"),
            ("Микрофон", s.input_volume, self._on_input_volume, "Громкость вашего реального микрофона в миксе"),
        ):
            slider = LabeledSlider(title_text, value, tip)
            slider.valueChanged.connect(slot)
            bottom.addWidget(slider, 1)
        root.addWidget(mixer)

        self.setCentralWidget(central)
        self._count_label = QLabel()
        self._count_label.setObjectName("hint")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self._count_label)
        self.statusBar().showMessage("Готово")

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu(self)
        menu.addAction("Показать", self._show_from_tray)
        menu.addAction("Остановить все", lambda: self.engine.stop_all())
        menu.addSeparator()
        menu.addAction("Выход", self._quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._show_from_tray() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.show()
        self._tray, self._tray_menu = tray, menu

    # ============================================================ sound list
    def _add_row(self, sound) -> SoundRow:
        row = SoundRow(sound, self.hotkeys)
        row.play_toggled.connect(self._on_play_toggled)
        row.hotkey_changed.connect(self._on_hotkey_changed)
        row.volume_changed.connect(self._on_volume_changed)
        row.loop_changed.connect(self._on_loop_changed)
        row.mode_changed.connect(self._on_mode_changed)
        row.rename_requested.connect(self._on_rename)
        row.remove_requested.connect(self._on_remove)
        self._rows[sound.id] = row
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        return row

    def _update_empty_state(self) -> None:
        total = len(self._rows)
        visible = sum(1 for r in self._rows.values() if not r.isHidden())
        query = self.search.text().strip()
        if total == 0:
            self._empty.set_content(
                "Здесь пока нет звуков",
                "Перетащите сюда аудиофайлы или папку\nлибо нажмите «Добавить звуки»",
            )
        elif visible == 0:
            self._empty.set_content("Ничего не найдено", f"Нет звуков по запросу «{query}»", show_icon=False)
        self._empty.setVisible(visible == 0)
        if query and visible != total:
            self._count_label.setText(f"{visible} из {_plural_sounds(total)}")
        else:
            self._count_label.setText(_plural_sounds(total))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in self._rows.values():
            row.setVisible(needle in row.name.lower())
        self._update_empty_state()

    def _set_row_playing(self, sound_id: str, playing: bool) -> None:
        row = self._rows.get(sound_id)
        if row is not None:
            row.set_playing(playing)

    def _on_add_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить звуки", "", FILE_FILTER)
        self._add_files(paths)

    def _add_files(self, paths: list[str]) -> None:
        expanded: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                expanded += [os.path.join(p, f) for f in sorted(os.listdir(p))]
            else:
                expanded.append(p)
        added = skipped = 0
        for p in expanded:
            if not os.path.isfile(p) or not p.lower().endswith(SUPPORTED_EXTS):
                skipped += 1
                continue
            sound = self.config.add_sound(os.path.abspath(p))
            self._add_row(sound)
            self.engine.preload(sound.path)
            added += 1
        self._apply_filter(self.search.text())
        if added or skipped:
            self._status(f"Добавлено: {added}" + (f", пропущено (не аудио): {skipped}" if skipped else ""))

    # ====================================================== row event handlers
    def _trigger_sound(self, sound_id: str) -> None:
        """Вызывается и из GUI, и из потока хоткеев — всё потокобезопасно."""
        sound = self.config.get_sound(sound_id)
        if sound is not None:
            self.engine.play(sound.id, sound.path, sound.volume, sound.loop, sound.mode)

    def _on_play_toggled(self, sound_id: str) -> None:
        if self.engine.is_playing(sound_id):
            self.engine.stop(sound_id)
        else:
            self._trigger_sound(sound_id)

    def _on_hotkey_changed(self, sound_id: str, combo: str) -> None:
        if combo:
            owner = self.config.find_hotkey_owner(combo, exclude_sound_id=sound_id)
            note = ""
            if owner == STOP_ALL_OWNER:
                self.config.update_settings(stop_all_hotkey="")
                note = "«Остановить все»"
            elif owner:
                other = self.config.update_sound(owner, hotkey="")
                if owner in self._rows:
                    self._rows[owner].set_hotkey("")
                note = f"«{other.name}»" if other else ""
            if note:
                self._status(f"Хоткей {format_hotkey(combo)} снят с {note} и назначен сюда")
        self.config.update_sound(sound_id, hotkey=combo)
        self._rebuild_hotkeys()

    def _on_volume_changed(self, sound_id: str, volume: float) -> None:
        self.config.update_sound(sound_id, volume=volume)
        self.engine.set_sound_volume(sound_id, volume)

    def _on_loop_changed(self, sound_id: str, loop: bool) -> None:
        self.config.update_sound(sound_id, loop=loop)
        self.engine.set_sound_loop(sound_id, loop)

    def _on_mode_changed(self, sound_id: str, mode: str) -> None:
        self.config.update_sound(sound_id, mode=mode)

    def _on_rename(self, sound_id: str) -> None:
        sound = self.config.get_sound(sound_id)
        if sound is None:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Название:", text=sound.name)
        name = name.strip()
        if ok and name:
            self.config.update_sound(sound_id, name=name)
            row = self._rows[sound_id]
            row.set_name(name, sound.path)
            row.set_missing(not os.path.isfile(sound.path))
            self._apply_filter(self.search.text())

    def _on_remove(self, sound_id: str) -> None:
        self.engine.stop(sound_id)
        row = self._rows.pop(sound_id, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self.config.remove_sound(sound_id)
        self._rebuild_hotkeys()
        self._update_empty_state()

    # ================================================================ hotkeys
    def _rebuild_hotkeys(self) -> None:
        bindings = {}
        for s in self.config.sounds_snapshot():
            if s.hotkey:
                bindings[s.hotkey] = partial(self._trigger_sound, s.id)
        stop_combo = self.config.settings.stop_all_hotkey
        if stop_combo:
            bindings[stop_combo] = lambda: self.engine.stop_all()
        self.hotkeys.set_bindings(bindings)

    def _on_hotkeys_toggled(self, enabled: bool) -> None:
        self.hotkeys.enabled = enabled
        self.hotkeys_btn.setText("Хоткеи: вкл" if enabled else "Хоткеи: выкл")

    # ================================================================== audio
    def _on_master_volume(self, v: float) -> None:
        self.engine.set_master_volume(v)
        self.config.update_settings(master_volume=v)

    def _on_monitor_volume(self, v: float) -> None:
        self.engine.set_monitor_volume(v)
        self.config.update_settings(monitor_volume=v)

    def _on_input_volume(self, v: float) -> None:
        self.engine.set_input_volume(v)
        self.config.update_settings(input_volume=v)

    def _apply_audio_settings(self, show_dialog: bool) -> None:
        s = self.config.settings
        self.engine.set_master_volume(s.master_volume)
        self.engine.set_monitor_volume(s.monitor_volume)
        self.engine.set_input_volume(s.input_volume)
        self.engine.set_allow_overlap(s.allow_overlap)
        errors = self.engine.configure(AudioConfig(
            samplerate=s.samplerate,
            monitor_enabled=s.monitor_enabled, monitor_device=s.monitor_device,
            mic_enabled=s.mic_enabled, mic_device=s.mic_device,
            passthrough_enabled=s.passthrough_enabled, input_device=s.input_device,
        ))
        if errors:
            text = "\n".join(errors)
            self._status(errors[0])
            if show_dialog:
                QMessageBox.warning(self, "Аудио-устройства", text)
        else:
            self._status("Аудио-устройства готовы")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(
            self.config.settings, self.hotkeys, list_output_devices(), list_input_devices(), self
        )
        if dlg.exec() != SettingsDialog.DialogCode.Accepted:
            return
        values = dlg.values()
        combo = values["stop_all_hotkey"]
        if combo:
            owner = self.config.find_hotkey_owner(combo, ignore_stop_all=True)
            if owner and owner in self._rows:
                self.config.update_sound(owner, hotkey="")
                self._rows[owner].set_hotkey("")
        self.config.update_settings(**values)
        self._apply_audio_settings(show_dialog=True)
        self._rebuild_hotkeys()

    # ================================================================== misc
    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text, 6000)

    def _set_dragging(self, on: bool) -> None:
        """Подсвечиваем область списка, пока над окном перетаскивают файлы."""
        if self.scroll.property("dragging") != on:
            self.scroll.setProperty("dragging", on)
            repolish(self.scroll)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_dragging(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self._add_files(paths)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._tray is not None and self.config.settings.close_to_tray and not self._quitting:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Klaker", "Работаю в трее, хоткеи активны.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )
            return
        self.hotkeys.stop()
        self.engine.shutdown()
        self.config.flush()
        if self._tray is not None:
            self._tray.hide()
        event.accept()
        QApplication.quit()