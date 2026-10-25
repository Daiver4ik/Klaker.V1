"""Чтение/запись config.json.

- атомарная запись (tmp + os.replace), чтобы конфиг не побился при сбое;
- валидация и значения по умолчанию для любых «кривых» данных;
- отложенное сохранение (schedule_save) для частых изменений вроде ползунков;
- битый конфиг не теряется, а переименовывается в config.json.corrupt-<time>.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
PLAY_MODES = ("restart", "overlap", "toggle")
STOP_ALL_OWNER = "__stop_all__"  # «владелец» хоткея, если он назначен на «Остановить все»


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


@dataclass
class SoundItem:
    id: str
    name: str
    path: str
    hotkey: str = ""
    volume: float = 1.0          # 0.0 .. 2.0
    loop: bool = False
    mode: str = "restart"        # restart | overlap | toggle

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["SoundItem"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str) or not raw["path"]:
            return None
        mode = raw.get("mode")
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            name=str(raw.get("name") or Path(raw["path"]).stem),
            path=raw["path"],
            hotkey=str(raw.get("hotkey") or ""),
            volume=_clamp(raw.get("volume"), 0.0, 2.0, 1.0),
            loop=bool(raw.get("loop", False)),
            mode=mode if mode in PLAY_MODES else "restart",
        )


@dataclass
class Settings:
    monitor_enabled: bool = True           # слышать звуки сами (наушники/колонки)
    monitor_device: Optional[str] = None   # None = системное устройство по умолчанию
    mic_enabled: bool = False              # выводить звуки в «микрофон» (виртуальный кабель)
    mic_device: Optional[str] = None
    passthrough_enabled: bool = False      # подмешивать реальный микрофон в виртуальный
    input_device: Optional[str] = None     # None = микрофон по умолчанию
    master_volume: float = 1.0
    monitor_volume: float = 1.0
    input_volume: float = 1.0
    allow_overlap: bool = True             # можно ли играть несколько разных звуков одновременно
    stop_all_hotkey: str = ""
    close_to_tray: bool = False
    samplerate: int = 48000

    @classmethod
    def from_dict(cls, raw: Any) -> "Settings":
        s = cls()
        if not isinstance(raw, dict):
            return s
        s.monitor_enabled = bool(raw.get("monitor_enabled", s.monitor_enabled))
        s.monitor_device = _opt_str(raw.get("monitor_device"))
        s.mic_enabled = bool(raw.get("mic_enabled", s.mic_enabled))
        s.mic_device = _opt_str(raw.get("mic_device"))
        s.passthrough_enabled = bool(raw.get("passthrough_enabled", s.passthrough_enabled))
        s.input_device = _opt_str(raw.get("input_device"))
        s.master_volume = _clamp(raw.get("master_volume"), 0.0, 1.0, 1.0)
        s.monitor_volume = _clamp(raw.get("monitor_volume"), 0.0, 1.0, 1.0)
        s.input_volume = _clamp(raw.get("input_volume"), 0.0, 1.0, 1.0)
        s.allow_overlap = bool(raw.get("allow_overlap", s.allow_overlap))
        s.stop_all_hotkey = str(raw.get("stop_all_hotkey") or "")
        s.close_to_tray = bool(raw.get("close_to_tray", s.close_to_tray))
        sr = raw.get("samplerate")
        s.samplerate = sr if sr in (44100, 48000) else 48000
        return s


_SOUND_EDITABLE = {"name", "hotkey", "volume", "loop", "mode", "path"}


class ConfigManager:
    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
        self.path = Path(path)
        self.settings = Settings()
        self.sounds: list[SoundItem] = []
        self._lock = threading.RLock()
        self._timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------ I/O
    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                log.info("Конфиг не найден, создаю новый: %s", self.path)
                self.save()
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("корень конфига должен быть объектом")
            except (OSError, ValueError) as e:
                backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
                log.error("Конфиг повреждён (%s). Копия: %s", e, backup)
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                self.settings, self.sounds = Settings(), []
                self.save()
                return

            self.settings = Settings.from_dict(raw.get("settings"))
            sounds: list[SoundItem] = []
            seen: set[str] = set()
            for item in raw.get("sounds", []) if isinstance(raw.get("sounds"), list) else []:
                sound = SoundItem.from_dict(item)
                if sound is None:
                    continue
                if sound.id in seen:
                    sound.id = uuid.uuid4().hex
                seen.add(sound.id)
                sounds.append(sound)
            self.sounds = sounds

    def save(self) -> None:
        with self._lock:
            data = {
                "version": CONFIG_VERSION,
                "settings": asdict(self.settings),
                "sounds": [asdict(s) for s in self.sounds],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except OSError:
                log.exception("Не удалось сохранить конфиг")
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def schedule_save(self, delay: float = 0.6) -> None:
        """Сохранить через delay секунд; повторные вызовы сдвигают таймер."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(delay, self.save)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        """Сохранить немедленно (вызывать при выходе)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self.save()

    # --------------------------------------------------------------- sounds
    def sounds_snapshot(self) -> list[SoundItem]:
        with self._lock:
            return list(self.sounds)

    def get_sound(self, sound_id: str) -> Optional[SoundItem]:
        with self._lock:
            return next((s for s in self.sounds if s.id == sound_id), None)

    def add_sound(self, path: str, name: Optional[str] = None) -> SoundItem:
        sound = SoundItem(id=uuid.uuid4().hex, name=name or Path(path).stem, path=str(path))
        with self._lock:
            self.sounds.append(sound)
        self.schedule_save()
        return sound

    def remove_sound(self, sound_id: str) -> bool:
        with self._lock:
            before = len(self.sounds)
            self.sounds = [s for s in self.sounds if s.id != sound_id]
            removed = len(self.sounds) != before
        if removed:
            self.schedule_save()
        return removed

    def update_sound(self, sound_id: str, **changes: Any) -> Optional[SoundItem]:
        with self._lock:
            sound = self.get_sound(sound_id)
            if sound is None:
                return None
            for key, value in changes.items():
                if key not in _SOUND_EDITABLE:
                    raise AttributeError(f"Нельзя менять поле звука: {key}")
                if key == "volume":
                    value = _clamp(value, 0.0, 2.0, sound.volume)
                elif key == "mode" and value not in PLAY_MODES:
                    continue
                setattr(sound, key, value)
        self.schedule_save()
        return sound

    # ------------------------------------------------------------- settings
    def update_settings(self, **changes: Any) -> Settings:
        with self._lock:
            for key, value in changes.items():
                if not hasattr(self.settings, key):
                    raise AttributeError(f"Неизвестная настройка: {key}")
                setattr(self.settings, key, value)
        self.schedule_save()
        return self.settings

    # -------------------------------------------------------------- hotkeys
    def find_hotkey_owner(
        self,
        combo: str,
        exclude_sound_id: Optional[str] = None,
        ignore_stop_all: bool = False,
    ) -> Optional[str]:
        """id звука (или STOP_ALL_OWNER), которому уже назначена комбинация."""
        if not combo:
            return None
        with self._lock:
            for s in self.sounds:
                if s.id != exclude_sound_id and s.hotkey == combo:
                    return s.id
            if not ignore_stop_all and self.settings.stop_all_hotkey == combo:
                return STOP_ALL_OWNER
        return None
