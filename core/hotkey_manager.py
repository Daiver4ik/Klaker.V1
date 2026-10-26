"""
The "Klaler" project, an analogue of the SoundPad application!
Author: Daiver4ik
Description: Copyright protected!
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable, Optional

from pynput import keyboard

log = logging.getLogger(__name__)

MODIFIERS = ("ctrl", "alt", "shift", "cmd")
_IS_WIN = sys.platform == "win32"

_MOD_ALIASES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}
_WIN_MOD_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "cmd": (0x5B, 0x5C)}
_WIN_OEM = {
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
_WIN_NUMPAD_OPS = {0x6A: "num_mul", 0x6B: "num_add", 0x6D: "num_sub", 0x6E: "num_dec", 0x6F: "num_div"}


def _async_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)  # type: ignore[attr-defined]


def _describe_key(key) -> tuple[Optional[str], Optional[int]]:
    """Возвращает (нормализованное имя клавиши, virtual-key код или None)."""
    if isinstance(key, keyboard.Key):
        name = key.name
        vk = getattr(key.value, "vk", None)
        return _MOD_ALIASES.get(name, name), vk

    vk = getattr(key, "vk", None)
    char = getattr(key, "char", None)
    if _IS_WIN and vk is not None:
        if 0x60 <= vk <= 0x69:
            return f"num{vk - 0x60}", vk
        if vk in _WIN_NUMPAD_OPS:
            return _WIN_NUMPAD_OPS[vk], vk
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk).lower(), vk
        if vk in _WIN_OEM:
            return _WIN_OEM[vk], vk
        return f"vk{vk}", vk
    if char and char.isprintable() and not char.isspace():
        name = char.lower()
        return ("plus" if name == "+" else name), vk
    if vk is not None:
        return f"vk{vk}", vk
    return None, None


def format_hotkey(combo: str) -> str:
    """'ctrl+alt+num1' -> 'Ctrl + Alt + Num 1' (для отображения)."""
    if not combo:
        return ""
    parts = []
    for p in combo.split("+"):
        if p.startswith("num") and p[3:].isdigit():
            parts.append(f"Num {p[3:]}")
        elif p == "plus":
            parts.append("+")
        elif p == "cmd":
            parts.append("Win" if _IS_WIN else "Cmd")
        elif len(p) == 1:
            parts.append(p.upper())
        else:
            parts.append(p.replace("_", " ").title())
    return " + ".join(parts)


class HotkeyManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bindings: dict[str, Callable[[], None]] = {}
        self._mods: set[str] = set()
        self._down: dict[str, Optional[int]] = {}
        self._record_cb: Optional[Callable[[Optional[str]], None]] = None
        self._listener: Optional[keyboard.Listener] = None
        self.enabled = True

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lock:
            if self._listener is not None and self._listener.is_alive():
                return
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.daemon = True
            self._listener.start()
            log.info("Глобальный перехват клавиш запущен")

    def stop(self) -> None:
        with self._lock:
            listener, self._listener = self._listener, None
            self._record_cb = None
            self._mods.clear()
            self._down.clear()
        if listener is not None:
            listener.stop()

    # ------------------------------------------------------------- bindings
    def set_bindings(self, mapping: dict[str, Callable[[], None]]) -> None:
        """Полностью заменяет набор хоткеев: {'ctrl+1': callback, ...}."""
        with self._lock:
            self._bindings = {k: v for k, v in mapping.items() if k}

    # ------------------------------------------------------------ recording
    def start_recording(self, callback: Callable[[Optional[str]], None]) -> None:
        """Следующая нажатая комбинация уйдёт в callback (из потока перехвата!).

        callback(None) — отмена (Esc), callback("") — очистить (Backspace).
        Пока идёт запись, обычные хоткеи не срабатывают.
        """
        with self._lock:
            previous, self._record_cb = self._record_cb, callback
        if previous is not None:
            self._safe_call(previous, None)

    def cancel_recording(self) -> None:
        with self._lock:
            self._record_cb = None

    @property
    def is_recording(self) -> bool:
        return self._record_cb is not None

    # ------------------------------------------------------------- internals
    @staticmethod
    def _safe_call(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("Ошибка в обработчике хоткея")

    def _on_press(self, key) -> None:
        try:
            self._handle_press(key)
        except Exception:
            log.exception("Ошибка обработки нажатия")

    def _on_release(self, key) -> None:
        try:
            name, _ = _describe_key(key)
            if name is None:
                return
            with self._lock:
                if name in MODIFIERS:
                    self._mods.discard(name)
                else:
                    self._down.pop(name, None)
        except Exception:
            log.exception("Ошибка обработки отпускания")

    def _handle_press(self, key) -> None:
        name, vk = _describe_key(key)
        if name is None:
            return

        record_cb = None
        action: Optional[Callable[[], None]] = None
        record_result: Optional[str] = None

        with self._lock:
            if name in MODIFIERS:
                self._mods.add(name)
                return

            if _IS_WIN:
                # Источник истины по модификаторам — ОС, а не наш учёт событий.
                self._mods = {
                    m for m, vks in _WIN_MOD_VKS.items() if any(_async_down(v) for v in vks)
                }
                # Забываем «залипшие» клавиши, отпускание которых мы пропустили.
                for n, v in list(self._down.items()):
                    if v is not None and not _async_down(v):
                        del self._down[n]

            if name in self._down:      # автоповтор
                return
            self._down[name] = vk

            mods = [m for m in MODIFIERS if m in self._mods]
            combo = "+".join(mods + [name])

            if self._record_cb is not None:
                record_cb, self._record_cb = self._record_cb, None
                if not mods and name == "esc":
                    record_result = None
                elif not mods and name == "backspace":
                    record_result = ""
                else:
                    record_result = combo
            elif self.enabled:
                action = self._bindings.get(combo)

        if record_cb is not None:
            self._safe_call(record_cb, record_result)
        elif action is not None:
            self._safe_call(action)
