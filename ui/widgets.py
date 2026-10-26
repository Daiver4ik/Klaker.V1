"""
The "Klaler" project, an analogue of the SoundPad application!
Author: Daiver4ik
Description: Copyright protected!
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRectF, QSize, Qt, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QActionGroup, QColor, QDesktopServices, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from core.audio_engine import DeviceInfo
from core.config_manager import Category, Settings, SoundItem
from core.hotkey_manager import HotkeyManager, format_hotkey
from ui.theme import (
    ACCENT, BG_ROW, BG_ROW_HOVER, BG_SELECTED, DANGER, PALETTE, TEXT, TEXT_MUTED,
    auto_color, dot_icon, icon, mix, paint_icon, with_alpha,
)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def fmt_time(seconds: float, precise: bool = False) -> str:
    if precise:
        return f"{seconds:.1f}"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _text_option(align: Qt.AlignmentFlag) -> QTextOption:
    opt = QTextOption(align)
    opt.setWrapMode(QTextOption.WrapMode.NoWrap)
    return opt


def make_icon_button(name: str, tooltip: str, color: str = TEXT_MUTED, size: int = 30, icon_size: int = 18) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(icon(name, color, icon_size))
    btn.setIconSize(QSize(icon_size, icon_size))
    btn.setFixedSize(size, size)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class Fade(QObject):
    """Плавно меняющееся значение 0..1. Читайте .value в paintEvent, слушайте .changed."""

    changed = Signal()

    def __init__(self, parent: QObject, duration: int = 150, start: float = 0.0) -> None:
        super().__init__(parent)
        self.value = start
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)

    def to(self, target: float, animate: bool = True) -> None:
        self._anim.stop()
        if not animate:
            self.value = float(target)
            self.changed.emit()
            return
        self._anim.setStartValue(float(self.value))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_value(self, v) -> None:
        self.value = float(v)
        self.changed.emit()


class ElidedLabel(QLabel):
    """Метка, которая обрезает длинный текст многоточием вместо растягивания строки."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(90)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))


class PlayButton(QAbstractButton):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = QColor(ACCENT)
        self._is_playing = False
        self._hover = Fade(self, 120)
        self._level = Fade(self, 220)
        self._hover.changed.connect(self.update)
        self._level.changed.connect(self.update)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._is_playing = playing
        self._level.to(1.0 if playing else 0.0)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h, t = self._hover.value, self._level.value
        fill = mix(with_alpha(self._color, 0.16 + 0.12 * h), self._color, t)
        if t > 0:
            fill = mix(fill, QColor("white"), 0.14 * h * t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        icon_color = mix(self._color, QColor("white"), t)
        paint_icon(p, "stop" if self._is_playing else "play", QRectF(self.rect()).adjusted(8, 8, -8, -8), icon_color)


class WaveformWidget(QWidget):
    BAR, GAP = 3, 2

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._peaks: Optional[np.ndarray] = None
        self._color = QColor(ACCENT)
        self._progress: Optional[float] = None
        self._cache_n = -1
        self._cache: list[float] = []

    def set_peaks(self, peaks: Optional[np.ndarray]) -> None:
        self._peaks = peaks
        self._cache_n = -1
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_progress(self, fraction: Optional[float]) -> None:
        if fraction is not None:
            fraction = max(0.0, min(1.0, fraction))
        if fraction == self._progress:
            return
        self._progress = fraction
        self.update()

    def _bars(self, n: int) -> Optional[list[float]]:
        if self._peaks is None or len(self._peaks) == 0:
            return None
        if n != self._cache_n:
            idx = np.linspace(0, len(self._peaks), n + 1).astype(int)
            self._cache = [float(self._peaks[a:max(b, a + 1)].max()) for a, b in zip(idx[:-1], idx[1:])]
            self._cache_n = n
        return self._cache

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        n = max(1, (w + self.GAP) // (self.BAR + self.GAP))
        bars = self._bars(n)
        playing = self._progress is not None
        played = QColor(self._color)
        rest = with_alpha(self._color, 0.28 if playing else (0.5 if bars else 0.18))
        edge = (self._progress or 0.0) * w
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            x = i * (self.BAR + self.GAP)
            amp = bars[i] if bars else 0.12
            bh = max(3.0, amp * (h - 4))
            p.setBrush(played if playing and x + self.BAR / 2 <= edge else rest)
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, self.BAR, bh), 1.5, 1.5)


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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
            self.setText(format_hotkey(self._hotkey) or "Хоткей")
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


class SoundRow(QWidget):
    play_toggled = Signal(str)
    hotkey_changed = Signal(str, str)
    volume_changed = Signal(str, float)
    loop_changed = Signal(str, bool)
    mode_changed = Signal(str, str)
    color_changed = Signal(str, str)       # "" = сбросить на цвет категории/авто
    category_changed = Signal(str, str)    # "" = без категории
    rename_requested = Signal(str)
    remove_requested = Signal(str)

    MODES = (
        ("restart", "Рестарт — играть с начала"),
        ("overlap", "Наложение — ещё одна копия поверх"),
        ("toggle", "Старт / стоп"),
    )

    def __init__(self, sound: SoundItem, color: str, categories: list[str], manager: HotkeyManager,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(68)
        self.sound_id = sound.id
        self.name = sound.name
        self._path = sound.path
        self._mode = sound.mode
        self._category = sound.category
        self._categories = list(categories)
        self._color = color
        self._duration = 0.0
        self._missing = not os.path.isfile(sound.path)
        self._hover = Fade(self, 150)
        self._play = Fade(self, 280)
        self._hover.changed.connect(self.update)
        self._play.changed.connect(self.update)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 10, 10, 10)
        lay.setSpacing(12)

        self.play_btn = PlayButton()
        self.play_btn.clicked.connect(lambda: self.play_toggled.emit(self.sound_id))
        lay.addWidget(self.play_btn)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.name_label = ElidedLabel()
        self.name_label.setObjectName("soundName")
        self.sub_label = ElidedLabel()
        self.sub_label.setObjectName("muted")
        text_col.addStretch(1)
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.sub_label)
        text_col.addStretch(1)
        lay.addLayout(text_col, 3)

        self.wave = WaveformWidget()
        lay.addWidget(self.wave, 4)

        self.time_label = QLabel("")
        self.time_label.setObjectName("muted")
        self.time_label.setFixedWidth(88)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.time_label)

        self.loop_btn = make_icon_button("loop", "Зациклить звук")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setChecked(sound.loop)
        self.loop_btn.toggled.connect(self._on_loop)
        lay.addWidget(self.loop_btn)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setValue(int(round(sound.volume * 100)))
        self.volume_slider.setFixedWidth(84)
        self.volume_slider.valueChanged.connect(self._on_volume)
        lay.addWidget(self.volume_slider)

        self.hotkey_btn = HotkeyButton(manager, sound.hotkey)
        self.hotkey_btn.hotkey_changed.connect(lambda c: self.hotkey_changed.emit(self.sound_id, c))
        lay.addWidget(self.hotkey_btn)

        self.more_btn = make_icon_button("dots", "Ещё")
        self.more_btn.clicked.connect(lambda: self._show_menu(self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height()))))
        lay.addWidget(self.more_btn)

        self.name_label.set_full_text(sound.name)
        self.name_label.setToolTip(f"{sound.path}\n(двойной клик — переименовать)")
        self._on_volume(self.volume_slider.value(), emit=False)
        self._apply_color()
        self._refresh_sub()
        self.set_progress(None)

    # ------------------------------------------------------------- состояние
    def set_name(self, name: str) -> None:
        self.name = name
        self.name_label.set_full_text(name)

    def set_hotkey(self, combo: str) -> None:
        self.hotkey_btn.set_hotkey(combo)

    def set_color(self, color: str) -> None:
        self._color = color
        self._apply_color()

    def set_categories(self, names: list[str]) -> None:
        self._categories = list(names)

    def set_category(self, name: str) -> None:
        self._category = name
        self._refresh_sub()

    def set_missing(self, missing: bool) -> None:
        self._missing = missing
        self._refresh_sub()

    def set_peaks(self, peaks: Optional[np.ndarray], duration: float) -> None:
        self._duration = duration
        self.wave.set_peaks(peaks)
        if self.wave._progress is None:
            self.time_label.setText(fmt_time(duration, duration < 10) + (" с" if duration < 10 else "") if duration else "")

    def set_playing(self, playing: bool) -> None:
        self.play_btn.set_playing(playing)
        self._play.to(1.0 if playing else 0.0)
        if not playing:
            self.set_progress(None)

    def set_progress(self, progress: Optional[tuple[float, float]]) -> None:
        """progress = (позиция_сек, длительность_сек) или None, когда звук не играет."""
        if progress is None or progress[1] <= 0:
            self.wave.set_progress(None)
            d = self._duration
            self.time_label.setText((fmt_time(d, d < 10) + (" с" if d < 10 else "")) if d else "")
            return
        pos, total = progress
        self.wave.set_progress(pos / total)
        precise = total < 10
        self.time_label.setText(f"{fmt_time(pos, precise)} / {fmt_time(total, precise)}" + (" с" if precise else ""))

    def _apply_color(self) -> None:
        self.play_btn.set_color(self._color)
        self.wave.set_color(self._color)
        self._refresh_loop_icon()
        self.update()

    def _refresh_loop_icon(self) -> None:
        color = self._color if self.loop_btn.isChecked() else TEXT_MUTED
        self.loop_btn.setIcon(icon("loop", color, 18))

    def _refresh_sub(self) -> None:
        if self._missing:
            self.sub_label.set_full_text("Файл не найден")
            self.sub_label.setStyleSheet(f"color: {DANGER};")
            return
        self.sub_label.setStyleSheet("")
        parts = []
        if self._category:
            parts.append(self._category)
        ext = os.path.splitext(self._path)[1].lstrip(".").upper()
        if ext:
            parts.append(ext)
        self.sub_label.set_full_text(" · ".join(parts))

    # ---------------------------------------------------------------- события
    def _on_volume(self, value: int, emit: bool = True) -> None:
        self.volume_slider.setToolTip(f"Громкость: {value}%")
        if emit:
            self.volume_changed.emit(self.sound_id, value / 100.0)

    def _on_loop(self, checked: bool) -> None:
        self._refresh_loop_icon()
        self.loop_changed.emit(self.sound_id, checked)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.mode_changed.emit(self.sound_id, mode)

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Цвет звука")
        if chosen.isValid():
            self.color_changed.emit(self.sound_id, chosen.name())

    def _reveal(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._path)))

    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)

        mode_menu = menu.addMenu("Повторное нажатие")
        group = QActionGroup(mode_menu)
        group.setExclusive(True)
        for key, label in self.MODES:
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == self._mode)
            group.addAction(act)
            act.triggered.connect(lambda _=False, k=key: self._set_mode(k))

        color_menu = menu.addMenu("Цвет")
        for name, hex_value in PALETTE:
            act = color_menu.addAction(dot_icon(hex_value), name)
            act.triggered.connect(lambda _=False, h=hex_value: self.color_changed.emit(self.sound_id, h))
        color_menu.addSeparator()
        color_menu.addAction("Свой цвет…").triggered.connect(lambda _=False: self._pick_custom_color())
        color_menu.addAction("По умолчанию (как у категории)").triggered.connect(
            lambda _=False: self.color_changed.emit(self.sound_id, "")
        )

        cat_menu = menu.addMenu("Категория")
        none_act = cat_menu.addAction("Без категории")
        none_act.setCheckable(True)
        none_act.setChecked(not self._category)
        none_act.triggered.connect(lambda _=False: self.category_changed.emit(self.sound_id, ""))
        for name in self._categories:
            act = cat_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self._category)
            act.triggered.connect(lambda _=False, n=name: self.category_changed.emit(self.sound_id, n))

        menu.addSeparator()
        menu.addAction("Переименовать…").triggered.connect(lambda _=False: self.rename_requested.emit(self.sound_id))
        menu.addAction("Показать файл в папке").triggered.connect(lambda _=False: self._reveal())
        menu.addSeparator()
        menu.addAction("Удалить из списка").triggered.connect(lambda _=False: self.remove_requested.emit(self.sound_id))
        menu.exec(pos)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._show_menu(event.globalPos())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.rename_requested.emit(self.sound_id)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        color = QColor(self._color)
        h, t = self._hover.value, self._play.value

        bg = mix(QColor(BG_ROW), QColor(BG_ROW_HOVER), h)
        bg = mix(bg, color, 0.11 * t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

        if t > 0.01:  # мягкая рамка вокруг играющего звука
            p.setPen(QPen(with_alpha(color, 0.55 * t), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 10, 10)

        p.setPen(Qt.PenStyle.NoPen)  # цветная полоска слева
        p.setBrush(with_alpha(color, 0.35 + 0.65 * max(t, h * 0.4)))
        p.drawRoundedRect(QRectF(r.left() + 7, r.top() + 14, 3.5, r.height() - 28), 1.75, 1.75)


class CategoryButton(QAbstractButton):
    def __init__(self, key: str, text: str, color: Optional[str] = None, count: int = 0,
                 grid: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        self._text = text
        self._color = color
        self._count = count
        self._grid = grid
        self.setCheckable(True)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = Fade(self, 120)
        self._sel = Fade(self, 180)
        self._hover.changed.connect(self.update)
        self._sel.changed.connect(self.update)

    def set_count(self, n: int) -> None:
        self._count = n
        self.update()

    def set_selected(self, selected: bool, animate: bool = True) -> None:
        self.setChecked(selected)
        self._sel.to(1.0 if selected else 0.0, animate)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h, s = self._hover.value, self._sel.value
        w, ht = self.width(), self.height()
        bg = mix(with_alpha(QColor(BG_ROW_HOVER), h * (1 - s)), QColor(BG_SELECTED), s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)

        fg = mix(QColor(TEXT_MUTED), QColor("white"), max(s, h * 0.7))
        if self._grid:
            paint_icon(p, "grid", QRectF(9, ht / 2 - 9, 18, 18), fg)
        else:
            p.setBrush(QColor(self._color or TEXT_MUTED))
            p.drawEllipse(QPointF(18, ht / 2), 5, 5)

        p.setPen(fg)
        elided = self.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, w - 36 - 44)
        p.drawText(QRectF(36, 0, w - 36 - 44, ht), elided, _text_option(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft))
        p.setPen(with_alpha(fg, 0.7))
        p.drawText(QRectF(w - 46, 0, 36, ht), str(self._count), _text_option(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight))


class Sidebar(QWidget):
    ALL = "__all__"

    category_selected = Signal(str)
    add_category_requested = Signal()
    category_menu_requested = Signal(str, QPoint)
    settings_requested = Signal()
    master_changed = Signal(float)
    monitor_changed = Signal(float)
    input_changed = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.selected = self.ALL
        self._buttons: dict[str, CategoryButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 12)
        root.setSpacing(4)

        title = QLabel("KlakerPad")
        title.setObjectName("appTitle")
        title.setContentsMargins(6, 0, 0, 0)
        root.addWidget(title)
        root.addSpacing(14)

        self._all_btn = CategoryButton(self.ALL, "Все звуки", grid=True)
        self._all_btn.clicked.connect(lambda: self.select(self.ALL))
        root.addWidget(self._all_btn)
        root.addSpacing(10)

        head = QHBoxLayout()
        label = QLabel("КАТЕГОРИИ")
        label.setObjectName("sectionLabel")
        label.setContentsMargins(6, 0, 0, 0)
        add = make_icon_button("plus", "Новая категория", size=26)
        add.clicked.connect(lambda: self.add_category_requested.emit())
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(add)
        root.addLayout(head)

        self._cat_layout = QVBoxLayout()
        self._cat_layout.setSpacing(2)
        root.addLayout(self._cat_layout)
        root.addStretch(1)

        vol_label = QLabel("ГРОМКОСТЬ")
        vol_label.setObjectName("sectionLabel")
        vol_label.setContentsMargins(6, 0, 0, 0)
        root.addWidget(vol_label)
        self._sliders: list[QSlider] = []
        for text, sig, tip in (
            ("Общая", self.master_changed, "Общая громкость всех звуков"),
            ("Наушники", self.monitor_changed, "Громкость в ваших наушниках (не влияет на микрофон)"),
            ("Микрофон", self.input_changed, "Громкость вашего реального микрофона в миксе"),
        ):
            row = QHBoxLayout()
            lab = QLabel(text)
            lab.setObjectName("muted")
            lab.setFixedWidth(72)
            lab.setContentsMargins(6, 0, 0, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setToolTip(tip)
            slider.valueChanged.connect(lambda v, s=sig: s.emit(v / 100.0))
            row.addWidget(lab)
            row.addWidget(slider, 1)
            root.addLayout(row)
            self._sliders.append(slider)
        root.addSpacing(6)

        settings_btn = QPushButton("  Настройки")
        settings_btn.setProperty("kind", "ghost")
        settings_btn.setIcon(icon("sliders", TEXT_MUTED, 18))
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.clicked.connect(lambda: self.settings_requested.emit())
        root.addWidget(settings_btn)

        self._all_btn.set_selected(True, animate=False)

    def set_volumes(self, master: float, monitor: float, mic: float) -> None:
        for slider, value in zip(self._sliders, (master, monitor, mic)):
            slider.blockSignals(True)
            slider.setValue(int(round(value * 100)))
            slider.blockSignals(False)

    def set_categories(self, categories: list[Category], counts: dict[str, int], total: int,
                       select: Optional[str] = None) -> None:
        if select is not None:
            self.selected = select
        if self.selected != self.ALL and self.selected not in {c.name for c in categories}:
            self.selected = self.ALL

        for btn in self._buttons.values():
            self._cat_layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()

        self._all_btn.set_count(total)
        for cat in categories:
            btn = CategoryButton(cat.name, cat.name, cat.color or auto_color(cat.name), counts.get(cat.name, 0))
            btn.clicked.connect(lambda _=False, k=cat.name: self.select(k))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn: self.category_menu_requested.emit(b.key, b.mapToGlobal(pos))
            )
            self._cat_layout.addWidget(btn)
            self._buttons[cat.name] = btn

        self.select(self.selected, animate=False)

    def select(self, key: str, animate: bool = True) -> None:
        self.selected = key
        self._all_btn.set_selected(key == self.ALL, animate)
        for name, btn in self._buttons.items():
            btn.set_selected(name == key, animate)
        self.category_selected.emit(key)


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
        self.setMinimumWidth(580)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        # Монитор
        self.monitor_check = QCheckBox("Слышать звуки сам (наушники/колонки)")
        self.monitor_check.setChecked(settings.monitor_enabled)
        self.monitor_combo = QComboBox()
        self._fill(self.monitor_combo, outputs, settings.monitor_device)
        self.monitor_check.toggled.connect(self.monitor_combo.setEnabled)
        self.monitor_combo.setEnabled(settings.monitor_enabled)
        form.addRow(self.monitor_check)
        form.addRow("Устройство:", self.monitor_combo)

        # Виртуальный микрофон
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
        form.addRow(self.mic_check)
        form.addRow("Устройство вывода:", self.mic_combo)
        form.addRow(self.pass_check)
        form.addRow("Реальный микрофон:", self.input_combo)
        self._sync_enabled()

        hint = QLabel(
            "Чтобы звуки слышали в Discord/играх: установите виртуальный аудиокабель (например VB-Cable), "
            "выберите «CABLE Input» как устройство вывода выше, а в Discord/игре укажите "
            "«CABLE Output» как микрофон. Включите «подмешивать реальный микрофон», чтобы вас тоже слышали."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)

        # Поведение
        self.overlap_check = QCheckBox("Разрешить одновременное воспроизведение разных звуков")
        self.overlap_check.setChecked(settings.allow_overlap)
        self.tray_check = QCheckBox("При закрытии окна сворачивать в трей")
        self.tray_check.setChecked(settings.close_to_tray)
        self.stop_hotkey = HotkeyButton(manager, settings.stop_all_hotkey)
        form.addRow(self.overlap_check)
        form.addRow(self.tray_check)
        form.addRow("Хоткей «Остановить все»:", self.stop_hotkey)
        root.addLayout(form)
        root.addSpacing(8)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("kind", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
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
