"""
The "Klaler" project, an analogue of the SoundPad application!
Author: Daiver4ik
Description: Copyright protected!
"""
from __future__ import annotations

import logging
import os
from functools import partial
from typing import Optional

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from core.audio_engine import AudioConfig, AudioEngine, list_input_devices, list_output_devices
from core.config_manager import STOP_ALL_OWNER, ConfigManager, SoundItem
from core.hotkey_manager import HotkeyManager, format_hotkey
from ui.theme import PALETTE, build_stylesheet, dot_icon, effective_color, icon
from ui.widgets import SettingsDialog, Sidebar, SoundRow

log = logging.getLogger(__name__)

SUPPORTED_EXTS = (".wav", ".mp3", ".ogg", ".opus", ".flac", ".aiff", ".aif")
FILE_FILTER = "Аудио (" + " ".join(f"*{e}" for e in SUPPORTED_EXTS) + ");;Все файлы (*)"
WAVE_BUCKETS = 160


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "звук"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "звука"
    return "звуков"


class MainWindow(QMainWindow):
    # Сигналы нужны, чтобы безопасно переходить из аудио-/хоткей-потоков в GUI-поток.
    sound_started = Signal(str)
    sound_stopped = Signal(str)
    error_occurred = Signal(str)
    peaks_ready = Signal(str, object, float)

    def __init__(self, config: ConfigManager, engine: AudioEngine, hotkeys: HotkeyManager) -> None:
        super().__init__()
        self.config = config
        self.engine = engine
        self.hotkeys = hotkeys
        self._rows: dict[str, SoundRow] = {}
        self._current = Sidebar.ALL
        self._playing: set[str] = set()
        self._quitting = False
        self._tray: Optional[QSystemTrayIcon] = None

        self.setWindowTitle("KlakerPad")
        self.resize(1080, 700)
        self.setMinimumSize(960, 560)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_stylesheet())
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(33)
        self._progress_timer.timeout.connect(self._tick_progress)

        self._build_ui()
        self._build_tray()

        self.sound_started.connect(lambda sid: self._on_engine_started(sid))
        self.sound_stopped.connect(lambda sid: self._on_engine_stopped(sid))
        self.error_occurred.connect(self._status)
        self.peaks_ready.connect(self._on_peaks)
        engine.on_started = self.sound_started.emit
        engine.on_stopped = self.sound_stopped.emit
        engine.on_error = self.error_occurred.emit

        for sound in config.sounds_snapshot():
            self._add_row(sound)
        self._refresh_sidebar()

        self._apply_audio_settings(show_dialog=False)
        self._rebuild_hotkeys()
        self.hotkeys.start()

    # ================================================================== UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        s = self.config.settings
        self.sidebar = Sidebar()
        self.sidebar.setFixedWidth(236)
        self.sidebar.set_volumes(s.master_volume, s.monitor_volume, s.input_volume)
        self.sidebar.category_selected.connect(self._on_category_selected)
        self.sidebar.add_category_requested.connect(self._on_add_category)
        self.sidebar.category_menu_requested.connect(self._on_category_menu)
        self.sidebar.settings_requested.connect(self._open_settings)
        self.sidebar.master_changed.connect(self._on_master_volume)
        self.sidebar.monitor_changed.connect(self._on_monitor_volume)
        self.sidebar.input_changed.connect(self._on_input_volume)
        root.addWidget(self.sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(24, 20, 16, 6)
        content.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title_label = QLabel("Все звуки")
        self.title_label.setObjectName("pageTitle")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("muted")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(190)
        self.search.textChanged.connect(lambda _: self._refresh_visibility())
        header.addWidget(self.search)

        self.hotkeys_btn = QPushButton("Хоткеи: вкл")
        self.hotkeys_btn.setObjectName("hotkeysToggle")
        self.hotkeys_btn.setCheckable(True)
        self.hotkeys_btn.setChecked(True)
        self.hotkeys_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hotkeys_btn.setToolTip("Глобально включить/выключить все горячие клавиши")
        self.hotkeys_btn.toggled.connect(self._on_hotkeys_toggled)
        header.addWidget(self.hotkeys_btn)

        stop_btn = QPushButton("  Остановить все")
        stop_btn.setProperty("kind", "danger")
        stop_btn.setIcon(icon("stop", "#ff7b7e", 16))
        stop_btn.setIconSize(QSize(16, 16))
        stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stop_btn.clicked.connect(lambda: self.engine.stop_all())
        header.addWidget(stop_btn)

        add_btn = QPushButton("  Добавить")
        add_btn.setProperty("kind", "primary")
        add_btn.setIcon(icon("plus", "#ffffff", 16))
        add_btn.setIconSize(QSize(16, 16))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._on_add_clicked)
        header.addWidget(add_btn)
        content.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        holder = QWidget()
        self._list_layout = QVBoxLayout(holder)
        self._list_layout.setContentsMargins(0, 0, 6, 8)
        self._list_layout.setSpacing(8)
        self._empty_label = QLabel("")
        self._empty_label.setObjectName("emptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setMinimumHeight(260)
        self._list_layout.addWidget(self._empty_label)
        self._list_layout.addStretch(1)
        self.scroll.setWidget(holder)
        content.addWidget(self.scroll, 1)

        root.addLayout(content, 1)
        self.setCentralWidget(central)
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
    def _color_for(self, sound: SoundItem) -> str:
        cat = self.config.get_category(sound.category) if sound.category else None
        return effective_color(sound.color, cat.color if cat else "", sound.id)

    def _category_names(self) -> list[str]:
        return [c.name for c in self.config.categories_snapshot()]

    def _add_row(self, sound: SoundItem) -> SoundRow:
        row = SoundRow(sound, self._color_for(sound), self._category_names(), self.hotkeys)
        row.play_toggled.connect(self._on_play_toggled)
        row.hotkey_changed.connect(self._on_hotkey_changed)
        row.volume_changed.connect(self._on_volume_changed)
        row.loop_changed.connect(self._on_loop_changed)
        row.mode_changed.connect(self._on_mode_changed)
        row.color_changed.connect(self._on_color_changed)
        row.category_changed.connect(self._on_category_changed)
        row.rename_requested.connect(self._on_rename)
        row.remove_requested.connect(self._on_remove)
        self._rows[sound.id] = row
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        # декодирование + огибающая волны считаются в фоне (заодно прогревается кэш)
        self.engine.request_peaks(
            sound.path, WAVE_BUCKETS,
            lambda peaks, dur, sid=sound.id: self.peaks_ready.emit(sid, peaks, dur),
        )
        return row

    def _on_peaks(self, sound_id: str, peaks, duration: float) -> None:
        row = self._rows.get(sound_id)
        if row is not None:
            row.set_peaks(peaks, duration)

    def _refresh_visibility(self) -> None:
        needle = self.search.text().strip().lower()
        shown = 0
        for sid, row in self._rows.items():
            sound = self.config.get_sound(sid)
            visible = (
                sound is not None
                and (self._current == Sidebar.ALL or sound.category == self._current)
                and needle in sound.name.lower()
            )
            row.setVisible(visible)
            shown += visible

        if not self._rows:
            text = "Здесь пока пусто\n\nПеретащите аудиофайлы в окно или нажмите «Добавить»"
        elif shown == 0 and needle:
            text = "Ничего не найдено"
        elif shown == 0:
            text = "В этой категории пока нет звуков\n\nДобавьте звук или перенесите существующий через меню «⋯» → Категория"
        else:
            text = ""
        self._empty_label.setText(text)
        self._empty_label.setVisible(shown == 0)
        self.subtitle_label.setText(f"{shown} {_plural(shown)}")

    def _refresh_sidebar(self, select: Optional[str] = None) -> None:
        sounds = self.config.sounds_snapshot()
        counts: dict[str, int] = {}
        for s in sounds:
            if s.category:
                counts[s.category] = counts.get(s.category, 0) + 1
        self.sidebar.set_categories(self.config.categories_snapshot(), counts, len(sounds), select)

    def _on_category_selected(self, key: str) -> None:
        self._current = key
        self.title_label.setText("Все звуки" if key == Sidebar.ALL else key)
        self._refresh_visibility()

    def _sync_rows(self) -> None:
        """Раздать строкам актуальные категории и цвета (после правок категорий)."""
        names = self._category_names()
        for sid, row in self._rows.items():
            sound = self.config.get_sound(sid)
            if sound is None:
                continue
            row.set_categories(names)
            row.set_category(sound.category)
            row.set_color(self._color_for(sound))

    def _after_categories_changed(self, select: Optional[str] = None) -> None:
        self._refresh_sidebar(select)
        self._sync_rows()

    # ----------------------------------------------------------- добавление
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
        category = "" if self._current == Sidebar.ALL else self._current
        added = skipped = 0
        for p in expanded:
            if not os.path.isfile(p) or not p.lower().endswith(SUPPORTED_EXTS):
                skipped += 1
                continue
            sound = self.config.add_sound(os.path.abspath(p), category=category)
            self._add_row(sound)
            added += 1
        if added:
            self._refresh_sidebar()
        self._refresh_visibility()
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

    def _on_color_changed(self, sound_id: str, color: str) -> None:
        sound = self.config.update_sound(sound_id, color=color)
        if sound is not None:
            self._rows[sound_id].set_color(self._color_for(sound))

    def _on_category_changed(self, sound_id: str, category: str) -> None:
        sound = self.config.update_sound(sound_id, category=category)
        if sound is None:
            return
        row = self._rows[sound_id]
        row.set_category(sound.category)
        row.set_color(self._color_for(sound))
        self._refresh_sidebar()
        self._refresh_visibility()

    def _on_rename(self, sound_id: str) -> None:
        sound = self.config.get_sound(sound_id)
        if sound is None:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Название:", text=sound.name)
        name = name.strip()
        if ok and name:
            self.config.update_sound(sound_id, name=name)
            self._rows[sound_id].set_name(name)

    def _on_remove(self, sound_id: str) -> None:
        self.engine.stop(sound_id)
        self._playing.discard(sound_id)
        row = self._rows.pop(sound_id, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self.config.remove_sound(sound_id)
        self._rebuild_hotkeys()
        self._refresh_sidebar()
        self._refresh_visibility()

    # ============================================================ categories
    def _on_add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Название:")
        name = name.strip()
        if not ok or not name:
            return
        color = PALETTE[len(self.config.categories_snapshot()) % len(PALETTE)][1]
        if self.config.add_category(name, color) is None:
            self._status("Категория с таким названием уже есть")
            return
        self._after_categories_changed(select=name)

    def _on_category_menu(self, name: str, pos: QPoint) -> None:
        if self.config.get_category(name) is None:
            return
        menu = QMenu(self)
        menu.addAction("Переименовать…").triggered.connect(lambda _=False: self._rename_category(name))
        color_menu = menu.addMenu("Цвет")
        for pname, hex_value in PALETTE:
            act = color_menu.addAction(dot_icon(hex_value), pname)
            act.triggered.connect(lambda _=False, h=hex_value: self._set_category_color(name, h))
        color_menu.addSeparator()
        color_menu.addAction("Свой цвет…").triggered.connect(lambda _=False: self._pick_category_color(name))
        menu.addSeparator()
        menu.addAction("Удалить категорию").triggered.connect(lambda _=False: self._delete_category(name))
        menu.exec(pos)

    def _rename_category(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Переименовать категорию", "Название:", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        if not self.config.rename_category(name, new):
            self._status("Не удалось переименовать: имя пустое или уже занято")
            return
        select = new if self.sidebar.selected == name else None
        self._after_categories_changed(select)

    def _set_category_color(self, name: str, color: str) -> None:
        self.config.set_category_color(name, color)
        self._after_categories_changed()

    def _pick_category_color(self, name: str) -> None:
        cat = self.config.get_category(name)
        chosen = QColorDialog.getColor(QColor(cat.color if cat and cat.color else "#5865f2"), self, "Цвет категории")
        if chosen.isValid():
            self._set_category_color(name, chosen.name())

    def _delete_category(self, name: str) -> None:
        self.config.remove_category(name)
        self._after_categories_changed()
        self._status(f"Категория «{name}» удалена, её звуки остались в «Все звуки»")

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
    def _on_engine_started(self, sound_id: str) -> None:
        self._playing.add(sound_id)
        row = self._rows.get(sound_id)
        if row is not None:
            row.set_playing(True)
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def _on_engine_stopped(self, sound_id: str) -> None:
        self._playing.discard(sound_id)
        row = self._rows.get(sound_id)
        if row is not None:
            row.set_playing(False)
        if not self._playing:
            self._progress_timer.stop()

    def _tick_progress(self) -> None:
        for sid in list(self._playing):
            row = self._rows.get(sid)
            if row is not None:
                row.set_progress(self.engine.get_progress(sid))

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
            self._status(errors[0])
            if show_dialog:
                QMessageBox.warning(self, "Аудио-устройства", "\n".join(errors))
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

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
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
                "KlakerPad", "Работаю в трее, хоткеи активны.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )
            return
        self._progress_timer.stop()
        self.hotkeys.stop()
        self.engine.shutdown()
        self.config.flush()
        if self._tray is not None:
            self._tray.hide()
        event.accept()
        QApplication.quit()
