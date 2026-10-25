"""Кастомные элементы интерфейса: кнопка записи хоткея, строка звука, диалог настроек."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from string import Template
from typing import Optional

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from core.audio_engine import DeviceInfo
from core.config_manager import Settings, SoundItem
from core.hotkey_manager import HotkeyManager, format_hotkey

# ------------------------------------------------------------------ палитра
# Графит с холодным оттенком; акцент — «сигнальный» васильково-фиолетовый,
# зелёный зарезервирован только за состоянием «звук играет».
P = {
    "base": "#111318",
    "panel": "#1a1d24",
    "raised": "#232730",
    "border": "#2a2e38",
    "border_hi": "#3a3f4b",
    "text": "#e7e9ee",
    "muted": "#8a91a0",
    "dim": "#5a6070",
    "accent": "#7b8cff",
    "accent_hi": "#93a1ff",
    "accent_soft": "#212745",
    "live": "#3dd68c",
    "live_soft": "#15261f",
    "danger": "#f06a65",
    "warn": "#f2b24c",
}

_QSS = Template("""
QWidget { color: $text; font-family: "Segoe UI Variable", "Segoe UI", "Inter", "SF Pro Text", "Noto Sans", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: $base; }
QLabel { background: transparent; }
QScrollArea { background: transparent; border: 1.5px solid transparent; border-radius: 12px; }
QScrollArea[dragging="true"] { border: 1.5px dashed $accent; background: $accent_soft; }
QScrollArea > QWidget > QWidget { background: transparent; }

QLabel#appTitle { font-size: 20px; font-weight: 700; padding-right: 8px; }
QLabel#hint, QLabel#mixerLabel { color: $muted; }
QLabel#valueLabel { color: $muted; }
QLabel#valueLabel[boost="true"] { color: $warn; }
QLabel#metaLabel { color: $dim; font-size: 12px; }
QLabel#metaLabel[missing="true"] { color: $warn; }
QLabel#soundName { font-size: 14px; font-weight: 600; }
QLabel#hintBox { background: $panel; border: 1px solid $border; border-left: 3px solid $accent; border-radius: 8px; padding: 10px 12px; color: $muted; }

QFrame#emptyState { border: 1.5px dashed $border_hi; border-radius: 14px; background: transparent; }
QFrame#emptyState QLabel#emptyTitle { font-size: 16px; font-weight: 600; }
QFrame#emptyState QLabel#emptyHint { color: $muted; }
QFrame#mixer { background: $panel; border: 1px solid $border; border-radius: 10px; }

QFrame#soundRow { background: $panel; border: 1px solid $border; border-left: 3px solid $panel; border-radius: 10px; }
QFrame#soundRow:hover { border-color: $border_hi; border-left-color: $panel; }
QFrame#soundRow[playing="true"] { background: $live_soft; border: 1px solid #235a3e; border-left: 3px solid $live; }
QFrame#soundRow[missing="true"] { border-left: 3px solid $warn; }

QPushButton { background: $raised; border: 1px solid $border_hi; border-radius: 8px; padding: 7px 14px; font-weight: 500; }
QPushButton:hover { background: #2b303b; border-color: #4a5060; }
QPushButton:pressed { background: #1f232b; }
QPushButton:focus { border-color: $accent; }
QPushButton:disabled { color: $dim; background: $panel; border-color: $border; }
QPushButton:checked { background: $accent_soft; border-color: $accent; color: $accent_hi; }
QPushButton#primary { background: $accent; border: 1px solid $accent; color: #0d1020; font-weight: 600; }
QPushButton#primary:hover { background: $accent_hi; border-color: $accent_hi; }
QPushButton#primary:pressed { background: #6676ee; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; padding: 0; }
QPushButton#ghost:hover { background: $raised; border-color: $border_hi; }
QPushButton#hotkeysToggle:checked { background: $live_soft; border-color: #235a3e; color: $live; }
QPushButton#playButton { background: $raised; border: 1px solid $border_hi; border-radius: 19px; padding: 0; }
QPushButton#playButton:hover { background: $accent_soft; border-color: $accent; }
QPushButton#playButton[playing="true"] { background: $live; border-color: $live; }
QPushButton#removeButton { background: transparent; border: none; border-radius: 6px; padding: 0; }
QPushButton#removeButton:hover { background: #3a2327; }
QPushButton#hotkeyButton { font-family: "Cascadia Mono", "Consolas", "SF Mono", monospace; font-size: 12px; background: $base; border: 1px solid $border_hi; border-bottom: 2px solid #4a5060; border-radius: 6px; padding: 5px 10px; }
QPushButton#hotkeyButton[empty="true"] { font-family: "Segoe UI Variable", "Segoe UI", "Inter", sans-serif; font-size: 13px; color: $muted; border: 1px dashed $border_hi; }
QPushButton#hotkeyButton[recording="true"] { background: $warn; color: #1a1304; border: 1px solid $warn; border-bottom: 2px solid #b8802a; }

QLineEdit, QComboBox { background: $panel; border: 1px solid $border_hi; border-radius: 8px; padding: 6px 10px; selection-background-color: $accent; selection-color: #fff; }
QLineEdit:focus, QComboBox:focus, QComboBox:on { border-color: $accent; }
QComboBox:disabled { color: $dim; border-color: $border; }
QComboBox::drop-down { border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox::down-arrow { image: url("$arrow"); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $border_hi; padding: 4px; outline: 0; selection-background-color: $accent; selection-color: #fff; }

QCheckBox { spacing: 8px; }
QCheckBox:disabled { color: $dim; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid $border_hi; background: $panel; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url("$check"); }
QCheckBox::indicator:disabled { background: $base; border-color: $border; }

QSlider::groove:horizontal { height: 4px; background: $border_hi; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $text; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background: #ffffff; border: 2px solid $accent; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_hi; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $dim; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QGroupBox { border: 1px solid $border; border-radius: 10px; margin-top: 14px; padding: 16px 12px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: $muted; font-weight: 600; background: $base; }

QMenu { background: $panel; border: 1px solid $border_hi; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 6px 24px; border-radius: 5px; }
QMenu::item:selected { background: $accent_soft; }
QMenu::separator { height: 1px; background: $border; margin: 6px 4px; }

QStatusBar { color: $muted; border-top: 1px solid $border; }
QStatusBar::item { border: none; }
QToolTip { background: $raised; color: $text; border: 1px solid $border_hi; padding: 4px 6px; }
""")


# ------------------------------------------------------------------ иконки
def make_pixmap(name: str, color: str = P["text"], size: int = 20) -> QPixmap:
    """Рисует векторную иконку (чётко на любом DPI), чтобы не зависеть от шрифтовых глифов."""
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    s = float(size)
    pen = QPen(c, max(1.6, s * 0.1))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    if name == "play":
        p.setPen(pen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(.34 * s, .22 * s), QPointF(.34 * s, .78 * s), QPointF(.78 * s, .5 * s)]))
    elif name == "stop":
        p.setPen(pen)
        p.setBrush(c)
        p.drawRoundedRect(.27 * s, .27 * s, .46 * s, .46 * s, 2, 2)
    elif name == "plus":
        p.setPen(pen)
        p.drawLine(QPointF(.5 * s, .22 * s), QPointF(.5 * s, .78 * s))
        p.drawLine(QPointF(.22 * s, .5 * s), QPointF(.78 * s, .5 * s))
    elif name == "close":
        p.setPen(pen)
        p.drawLine(QPointF(.3 * s, .3 * s), QPointF(.7 * s, .7 * s))
        p.drawLine(QPointF(.7 * s, .3 * s), QPointF(.3 * s, .7 * s))
    elif name == "gear":
        p.setPen(pen)
        p.drawEllipse(QPointF(s / 2, s / 2), .2 * s, .2 * s)
        p.translate(s / 2, s / 2)
        for _ in range(8):
            p.drawLine(QPointF(0, -.31 * s), QPointF(0, -.43 * s))
            p.rotate(45)
    p.end()
    return pm


def make_icon(name: str, color: str = P["text"], size: int = 20) -> QIcon:
    return QIcon(make_pixmap(name, color, size))


def _asset(name: str, draw) -> str:
    """Маленькие PNG для QSS (галочка, стрелка списка). Кладём во временную папку."""
    folder = Path(tempfile.gettempdir()) / "Klaker_assets"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.png"
    pm = QPixmap(14, 14)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw(p)
    p.end()
    pm.save(str(path), "PNG")
    return path.as_posix()


def build_style() -> str:
    """Собирает общий QSS. Вызывать ПОСЛЕ создания QApplication (нужен QPixmap)."""
    def check(p: QPainter) -> None:
        pen = QPen(QColor("#0d1020"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(3, 7.5), QPointF(6, 10.5), QPointF(11, 4)]))

    def arrow(p: QPainter) -> None:
        pen = QPen(QColor(P["muted"]), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(3, 5), QPointF(7, 9), QPointF(11, 5)]))

    return _QSS.substitute(P, check=_asset("check", check), arrow=_asset("arrow", arrow))


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def tab_focus(*widgets: QWidget) -> None:
    """Кнопки получают фокус только с клавиатуры — иначе после клика мышью остаётся рамка."""
    for w in widgets:
        w.setFocusPolicy(Qt.FocusPolicy.TabFocus)


# ------------------------------------------------------------------ виджеты
class EmptyState(QFrame):
    """Пустой список / «ничего не найдено»."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setMinimumHeight(260)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setPixmap(make_pixmap("plus", P["dim"], 40))
        self._title = QLabel()
        self._title.setObjectName("emptyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel()
        self._hint.setObjectName("emptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self._icon, self._title, self._hint):
            lay.addWidget(w)

    def set_content(self, title: str, hint: str, show_icon: bool = True) -> None:
        self._title.setText(title)
        self._hint.setText(hint)
        self._icon.setVisible(show_icon)


class LabeledSlider(QWidget):
    """Подпись + ползунок + значение в процентах. Сигнал отдаёт долю (0.0–1.0+)."""

    valueChanged = Signal(float)

    def __init__(self, title: str, value: float, tip: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("mixerLabel")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(value * 100)))
        self.value_label = QLabel()
        self.value_label.setObjectName("valueLabel")
        self.value_label.setFixedWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setText(f"{self.slider.value()}%")
        self.slider.valueChanged.connect(self._on_change)
        self.setToolTip(tip)
        lay.addWidget(label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)

    def _on_change(self, v: int) -> None:
        self.value_label.setText(f"{v}%")
        self.valueChanged.emit(v / 100.0)


class HotkeyButton(QPushButton):
    """Клик -> «Нажмите клавиши…» -> записывает следующую комбинацию.

    Esc — отмена, Backspace — очистить. Сигнал hotkey_changed("") означает очистку.
    Проверка конфликтов — на стороне получателя сигнала (он может вернуть set_hotkey()).
    """

    hotkey_changed = Signal(str)
    _captured = Signal(object)  # приходит из потока перехвата клавиш

    def __init__(self, manager: HotkeyManager, hotkey: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._hotkey = hotkey
        self._recording = False
        self.setObjectName("hotkeyButton")
        self.setMinimumWidth(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tab_focus(self)
        self.clicked.connect(self._on_clicked)
        self._captured.connect(self._on_captured)
        self._refresh()

    def hotkey(self) -> str:
        return self._hotkey

    def set_hotkey(self, combo: str) -> None:
        self._hotkey = combo
        self._refresh()

    def _refresh(self) -> None:
        if self._recording:
            self.setText("Нажмите клавиши…")
            self.setToolTip("Esc — отмена, Backspace — убрать хоткей")
        else:
            self.setText(format_hotkey(self._hotkey) or "Назначить")
            self.setToolTip("Клик — записать новую комбинацию")
        self.setProperty("empty", not self._hotkey and not self._recording)
        self.setProperty("recording", self._recording)
        repolish(self)

    def _on_clicked(self) -> None:
        if self._recording:
            self._manager.cancel_recording()
            self._recording = False
            self._refresh()
            return
        self._recording = True
        self._refresh()
        self._manager.start_recording(self._captured.emit)

    def _on_captured(self, result) -> None:
        self._recording = False
        if result is None:
            self._refresh()
            return
        self._hotkey = result
        self._refresh()
        self.hotkey_changed.emit(result)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._recording:
            self._manager.cancel_recording()
            self._recording = False
            self._refresh()
        super().hideEvent(event)


class SoundRow(QFrame):
    play_toggled = Signal(str)
    hotkey_changed = Signal(str, str)
    volume_changed = Signal(str, float)
    loop_changed = Signal(str, bool)
    mode_changed = Signal(str, str)
    rename_requested = Signal(str)
    remove_requested = Signal(str)

    MODES = (
        ("restart", "Рестарт", "Повторное нажатие запускает звук с начала"),
        ("overlap", "Наложение", "Повторное нажатие запускает ещё одну копию поверх"),
        ("toggle", "Старт/стоп", "Повторное нажатие останавливает звук"),
    )

    def __init__(self, sound: SoundItem, manager: HotkeyManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("soundRow")
        self.sound_id = sound.id
        self.name = sound.name
        self._path = sound.path
        self._missing = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(12)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("playButton")
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.setIconSize(QSize(18, 18))
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip("Воспроизвести / остановить")
        self.play_btn.clicked.connect(lambda: self.play_toggled.emit(self.sound_id))
        lay.addWidget(self.play_btn)

        # Название + имя файла вторым рядом
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.name_label = QLabel()
        self.name_label.setObjectName("soundName")
        self.meta_label = QLabel()
        self.meta_label.setObjectName("metaLabel")
        for lbl in (self.name_label, self.meta_label):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text_col.addWidget(lbl)
        lay.addLayout(text_col, 1)

        self.mode_combo = QComboBox()
        self.mode_combo.setFixedWidth(124)
        for key, label, tip in self.MODES:
            self.mode_combo.addItem(label, key)
            self.mode_combo.setItemData(self.mode_combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(sound.mode)))
        self.mode_combo.setToolTip("Что делать при повторном нажатии")
        self.mode_combo.currentIndexChanged.connect(
            lambda: self.mode_changed.emit(self.sound_id, self.mode_combo.currentData())
        )
        lay.addWidget(self.mode_combo)

        self.loop_check = QCheckBox("Повтор")
        self.loop_check.setChecked(sound.loop)
        self.loop_check.toggled.connect(lambda v: self.loop_changed.emit(self.sound_id, v))
        lay.addWidget(self.loop_check)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setValue(int(round(sound.volume * 100)))
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setToolTip("Громкость этого звука (до 200%)")
        self.volume_label = QLabel()
        self.volume_label.setObjectName("valueLabel")
        self.volume_label.setFixedWidth(42)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_slider.valueChanged.connect(self._on_volume)
        lay.addWidget(self.volume_slider)
        lay.addWidget(self.volume_label)

        self.hotkey_btn = HotkeyButton(manager, sound.hotkey)
        self.hotkey_btn.hotkey_changed.connect(lambda c: self.hotkey_changed.emit(self.sound_id, c))
        lay.addWidget(self.hotkey_btn)

        remove = QPushButton()
        remove.setObjectName("removeButton")
        remove.setFixedSize(28, 28)
        remove.setIcon(make_icon("close", P["muted"], 14))
        remove.setIconSize(QSize(14, 14))
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setToolTip("Удалить из списка (файл не удаляется)")
        remove.clicked.connect(lambda: self.remove_requested.emit(self.sound_id))
        lay.addWidget(remove)

        tab_focus(self.play_btn, remove)
        self.set_name(sound.name, sound.path)
        self._on_volume(self.volume_slider.value(), emit=False)
        self.set_missing(not os.path.isfile(sound.path))
        self.set_playing(False)

    # -- состояние
    def set_name(self, name: str, path: str = "") -> None:
        self.name = name
        self._path = path or self._path
        self.name_label.setText(name)
        self.name_label.setToolTip((self._path or "") + "\n(двойной клик по строке — переименовать)")
        if not self._missing:
            self.meta_label.setText(os.path.basename(self._path))

    def set_hotkey(self, combo: str) -> None:
        self.hotkey_btn.set_hotkey(combo)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setIcon(make_icon("stop" if playing else "play", "#0b1f15" if playing else P["text"], 18))
        for w in (self, self.play_btn):
            w.setProperty("playing", playing)
            repolish(w)

    def set_missing(self, missing: bool) -> None:
        self._missing = missing
        self.setProperty("missing", missing)
        self.meta_label.setProperty("missing", missing)
        repolish(self)
        repolish(self.meta_label)
        if missing:
            self.meta_label.setText("Файл не найден — звук не будет воспроизводиться")
        else:
            self.meta_label.setText(os.path.basename(self._path))

    # -- события
    def _on_volume(self, value: int, emit: bool = True) -> None:
        self.volume_label.setText(f"{value}%")
        self.volume_label.setProperty("boost", value > 100)
        repolish(self.volume_label)
        if emit:
            self.volume_changed.emit(self.sound_id, value / 100.0)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.rename_requested.emit(self.sound_id)


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        manager: HotkeyManager,
        outputs: list[DeviceInfo],
        inputs: list[DeviceInfo],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(6)

        def make_form(title: str) -> QFormLayout:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            root.addWidget(box)
            return form

        # Монитор
        monitor_form = make_form("Прослушивание")
        self.monitor_check = QCheckBox("Слышать звуки самому (наушники/колонки)")
        self.monitor_check.setChecked(settings.monitor_enabled)
        self.monitor_combo = QComboBox()
        self._fill(self.monitor_combo, outputs, settings.monitor_device)
        self.monitor_check.toggled.connect(self.monitor_combo.setEnabled)
        self.monitor_combo.setEnabled(settings.monitor_enabled)
        monitor_form.addRow(self.monitor_check)
        monitor_form.addRow("Устройство:", self.monitor_combo)

        # Виртуальный микрофон
        mic_form = make_form("Виртуальный микрофон")
        self.mic_check = QCheckBox("Отправлять звуки в «микрофон» (виртуальный кабель)")
        self.mic_check.setChecked(settings.mic_enabled)
        self.mic_combo = QComboBox()
        self._fill(self.mic_combo, outputs, settings.mic_device)
        self.pass_check = QCheckBox("Подмешивать мой реальный микрофон")
        self.pass_check.setChecked(settings.passthrough_enabled)
        self.input_combo = QComboBox()
        self._fill(self.input_combo, inputs, settings.input_device)
        self.mic_check.toggled.connect(self._sync_enabled)
        self.pass_check.toggled.connect(self._sync_enabled)
        mic_form.addRow(self.mic_check)
        mic_form.addRow("Устройство вывода:", self.mic_combo)
        mic_form.addRow(self.pass_check)
        mic_form.addRow("Реальный микрофон:", self.input_combo)
        self._sync_enabled()

        hint = QLabel(
            "Чтобы звуки слышали в Discord или играх, установите виртуальный аудиокабель (например, VB-Cable). "
            "Выберите «CABLE Input» как устройство вывода выше, а в Discord или игре укажите «CABLE Output» "
            "как микрофон. Включите подмешивание реального микрофона, чтобы вас тоже слышали."
        )
        hint.setObjectName("hintBox")
        hint.setWordWrap(True)
        mic_form.addRow(hint)

        # Поведение
        behavior_form = make_form("Поведение")
        self.overlap_check = QCheckBox("Разрешить одновременное воспроизведение разных звуков")
        self.overlap_check.setChecked(settings.allow_overlap)
        self.tray_check = QCheckBox("При закрытии окна сворачивать в трей")
        self.tray_check.setChecked(settings.close_to_tray)
        self.stop_hotkey = HotkeyButton(manager, settings.stop_all_hotkey)
        behavior_form.addRow(self.overlap_check)
        behavior_form.addRow(self.tray_check)
        behavior_form.addRow("Хоткей «Остановить все»:", self.stop_hotkey)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Сохранить")
        ok_btn.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addSpacing(6)
        root.addWidget(buttons)

    @staticmethod
    def _fill(combo: QComboBox, devices: list[DeviceInfo], current: Optional[str]) -> None:
        combo.addItem("Системное по умолчанию", None)
        seen = set()
        for d in devices:
            if d.name not in seen:
                seen.add(d.name)
                combo.addItem(d.name, d.name)
        if current and current not in seen:
            combo.addItem(f"{current} (не найдено)", current)
        if current:
            combo.setCurrentIndex(max(0, combo.findData(current)))

    def _sync_enabled(self) -> None:
        mic = self.mic_check.isChecked()
        self.mic_combo.setEnabled(mic)
        self.pass_check.setEnabled(mic)
        self.input_combo.setEnabled(mic and self.pass_check.isChecked())

    def values(self) -> dict:
        return {
            "monitor_enabled": self.monitor_check.isChecked(),
            "monitor_device": self.monitor_combo.currentData(),
            "mic_enabled": self.mic_check.isChecked(),
            "mic_device": self.mic_combo.currentData(),
            "passthrough_enabled": self.pass_check.isChecked(),
            "input_device": self.input_combo.currentData(),
            "allow_overlap": self.overlap_check.isChecked(),
            "close_to_tray": self.tray_check.isChecked(),
            "stop_all_hotkey": self.stop_hotkey.hotkey(),
        }