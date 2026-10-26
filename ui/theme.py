"""Тема оформления: цвета, палитра звуков, иконки (рисуются кодом) и общий QSS.

Стиль — тёмный минимализм в духе Discord/Spotify. Все цвета собраны здесь,
чтобы менять оформление в одном месте.
"""
from __future__ import annotations

import hashlib
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap, QPolygonF

BG_SIDEBAR = "#1e1f22"
BG_MAIN = "#313338"
BG_ROW = "#2b2d31"
BG_ROW_HOVER = "#35373c"
BG_SELECTED = "#404249"
BORDER = "#3f4147"
TEXT = "#dbdee1"
TEXT_MUTED = "#949ba4"
ACCENT = "#5865f2"
ACCENT_HOVER = "#4752c4"
DANGER = "#f23f43"
SUCCESS = "#23a559"

# Палитра для звуков и категорий: (название, цвет)
PALETTE = (
    ("Индиго", "#5865f2"),
    ("Небо", "#3ba7e0"),
    ("Бирюза", "#2dc4b2"),
    ("Зелёный", "#3ba55d"),
    ("Жёлтый", "#e6b422"),
    ("Оранжевый", "#f0883e"),
    ("Красный", "#ed4245"),
    ("Розовый", "#eb459e"),
    ("Фиолетовый", "#9b59f5"),
    ("Серый", "#8a94a6"),
)


def auto_color(key: str) -> str:
    """Стабильный цвет из палитры для ключа (id звука / имя категории)."""
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)][1]


def effective_color(sound_color: str, category_color: str, sound_id: str) -> str:
    """Свой цвет звука -> цвет категории -> авто по id."""
    return sound_color or category_color or auto_color(sound_id)


def with_alpha(color, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Линейная интерполяция цветов (включая альфа-канал)."""
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
        a.alphaF() + (b.alphaF() - a.alphaF()) * t,
    )


# ------------------------------------------------------------------ icons
def paint_icon(p: QPainter, name: str, rect: QRectF, color) -> None:
    """Рисует иконку name в rect. Имена: play, stop, plus, dots, loop, sliders, grid."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor(color)
    s = min(rect.width(), rect.height())
    c = rect.center()
    pen = QPen(col, max(1.4, s * 0.11))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        x0, x1, dy = c.x() - s * 0.14, c.x() + s * 0.24, s * 0.28
        p.drawPolygon(QPolygonF([QPointF(x0, c.y() - dy), QPointF(x1, c.y()), QPointF(x0, c.y() + dy)]))
    elif name == "stop":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        side = s * 0.40
        p.drawRoundedRect(QRectF(c.x() - side / 2, c.y() - side / 2, side, side), 2, 2)
    elif name == "plus":
        d = s * 0.26
        p.drawLine(QPointF(c.x() - d, c.y()), QPointF(c.x() + d, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x(), c.y() + d))
    elif name == "dots":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        for dx in (-0.26, 0.0, 0.26):
            p.drawEllipse(QPointF(c.x() + dx * s, c.y()), s * 0.075, s * 0.075)
    elif name == "loop":
        r = s * 0.30
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 60 * 16, 280 * 16)
        theta = math.radians(340)
        px, py = c.x() + r * math.cos(theta), c.y() - r * math.sin(theta)
        tx, ty = -math.sin(theta), -math.cos(theta)      # касательная (против часовой)
        nx, ny = -ty, tx
        k = s * 0.13
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(px + tx * k * 1.1, py + ty * k * 1.1),
            QPointF(px - tx * k * 0.2 + nx * k, py - ty * k * 0.2 + ny * k),
            QPointF(px - tx * k * 0.2 - nx * k, py - ty * k * 0.2 - ny * k),
        ]))
    elif name == "sliders":
        for dy, kx in ((-0.26, -0.12), (0.0, 0.14), (0.26, -0.04)):
            y = c.y() + dy * s
            p.drawLine(QPointF(c.x() - s * 0.34, y), QPointF(c.x() + s * 0.34, y))
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(c.x() + kx * s, y), s * 0.10, s * 0.10)
            p.restore()
    elif name == "grid":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        side, gap = s * 0.26, s * 0.10
        for ix in (0, 1):
            for iy in (0, 1):
                x = c.x() - gap / 2 - side + ix * (side + gap)
                y = c.y() - gap / 2 - side + iy * (side + gap)
                p.drawRoundedRect(QRectF(x, y, side, side), 2, 2)
    p.restore()


def icon(name: str, color: str = TEXT_MUTED, size: int = 18) -> QIcon:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_icon(p, name, QRectF(0, 0, size, size), QColor(color))
    p.end()
    return QIcon(pm)


def dot_icon(color: str, size: int = 12) -> QIcon:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return QIcon(pm)


# -------------------------------------------------------------------- QSS
_TOKENS = {
    "BG_SIDEBAR": BG_SIDEBAR, "BG_MAIN": BG_MAIN, "BG_ROW": BG_ROW, "BG_ROW_HOVER": BG_ROW_HOVER,
    "BORDER": BORDER, "TEXT": TEXT, "TEXT_MUTED": TEXT_MUTED, "ACCENT": ACCENT,
    "ACCENT_HOVER": ACCENT_HOVER, "DANGER": DANGER, "SUCCESS": SUCCESS,
}

_QSS = """
* { font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 13px; }
QWidget { color: @TEXT@; }
QMainWindow, QDialog { background: @BG_MAIN@; }
QWidget#sidebar { background: @BG_SIDEBAR@; }
QScrollArea { background: transparent; border: none; }
QLabel { background: transparent; }
QLabel#appTitle { font-size: 17px; font-weight: 700; }
QLabel#pageTitle { font-size: 22px; font-weight: 700; }
QLabel#sectionLabel { color: @TEXT_MUTED@; font-size: 11px; font-weight: 700; }
QLabel#muted { color: @TEXT_MUTED@; font-size: 12px; }
QLabel#soundName { font-size: 14px; font-weight: 600; }
QLabel#emptyState { color: @TEXT_MUTED@; font-size: 14px; }
QLabel#hint { color: @TEXT_MUTED@; }

QPushButton { background: #3a3c43; border: none; border-radius: 6px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { background: #44464e; }
QPushButton:pressed { background: #2f3136; }
QPushButton:disabled { color: #6d6f78; }
QPushButton[kind="primary"] { background: @ACCENT@; color: white; }
QPushButton[kind="primary"]:hover { background: @ACCENT_HOVER@; }
QPushButton[kind="danger"] { background: rgba(242, 63, 67, 0.14); color: #ff7b7e; }
QPushButton[kind="danger"]:hover { background: rgba(242, 63, 67, 0.26); }
QPushButton[kind="ghost"] { background: transparent; color: @TEXT_MUTED@; text-align: left; }
QPushButton[kind="ghost"]:hover { background: @BG_ROW_HOVER@; color: @TEXT@; }
QPushButton#hotkeysToggle:checked { background: rgba(35, 165, 89, 0.18); color: #3ddc84; }

QPushButton#hotkeyButton { background: @BG_SIDEBAR@; border: 1px solid @BORDER@; padding: 5px 10px; font-size: 12px; min-width: 96px; }
QPushButton#hotkeyButton:hover { border-color: #5a5d66; }
QPushButton#hotkeyButton[empty="true"] { color: @TEXT_MUTED@; font-weight: 400; border-style: dashed; }
QPushButton#hotkeyButton[recording="true"] { background: rgba(88, 101, 242, 0.20); border: 1px solid @ACCENT@; color: white; }

QToolButton { background: transparent; border: none; border-radius: 6px; }
QToolButton:hover { background: #3a3c43; }
QToolButton:checked { background: rgba(255, 255, 255, 0.08); }

QLineEdit { background: @BG_SIDEBAR@; border: 1px solid transparent; border-radius: 6px; padding: 7px 10px; selection-background-color: @ACCENT@; }
QLineEdit:focus { border: 1px solid @ACCENT@; }
QComboBox { background: @BG_SIDEBAR@; border: 1px solid transparent; border-radius: 6px; padding: 6px 10px; }
QComboBox:hover { border-color: @BORDER@; }
QComboBox:disabled { color: #6d6f78; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #111214; border: 1px solid @BG_SIDEBAR@; selection-background-color: @ACCENT@; outline: none; }

QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #6d6f78; background: @BG_SIDEBAR@; }
QCheckBox::indicator:checked { background: @ACCENT@; border-color: @ACCENT@; }

QSlider::groove:horizontal { height: 4px; background: #4e5058; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 2px; }
QSlider::handle:horizontal { background: #f2f3f5; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QSlider::handle:horizontal:hover { width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }

QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: #1a1b1e; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #121315; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QMenu { background: #111214; border: 1px solid @BG_SIDEBAR@; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 4px; }
QMenu::item:selected { background: @ACCENT@; color: white; }
QMenu::separator { height: 1px; background: @BG_ROW@; margin: 5px 4px; }
QMenu::icon { padding-left: 6px; }
QToolTip { background: #111214; color: @TEXT@; border: 1px solid @BG_ROW@; padding: 4px 6px; }
QStatusBar { background: transparent; color: @TEXT_MUTED@; }
QStatusBar::item { border: none; }
QDialogButtonBox QPushButton { min-width: 80px; }
"""


def build_stylesheet() -> str:
    qss = _QSS
    for key, value in _TOKENS.items():
        qss = qss.replace(f"@{key}@", value)
    return qss
