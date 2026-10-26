"""
The "Klaler" project, an analogue of the SoundPad application!
Author: Daiver4ik
Description: Copyright protected!
"""
from __future__ import annotations

import collections
import itertools
import logging
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

try:  # опционально: более качественный ресемплинг
    from scipy.signal import resample_poly
except ImportError:  # pragma: no cover
    resample_poly = None

log = logging.getLogger(__name__)

DEFAULT_SAMPLERATE = 48000
FADE_SECONDS = 0.012                      # короткое затухание при остановке (без щелчков)
CACHE_LIMIT_BYTES = 512 * 1024 * 1024


class AudioEngineError(Exception):
    """Ошибка с понятным пользователю текстом."""


@dataclass
class AudioConfig:
    samplerate: int = DEFAULT_SAMPLERATE
    monitor_enabled: bool = True
    monitor_device: Optional[str] = None      # None = системное по умолчанию
    mic_enabled: bool = False
    mic_device: Optional[str] = None
    passthrough_enabled: bool = False
    input_device: Optional[str] = None


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    hostapi: str


# ----------------------------------------------------------------- devices
def _list_devices(kind: str) -> list[DeviceInfo]:
    channels_key = "max_output_channels" if kind == "output" else "max_input_channels"
    try:
        devices, apis = sd.query_devices(), sd.query_hostapis()
    except Exception:
        log.exception("Не удалось получить список устройств")
        return []
    found = [
        DeviceInfo(i, d["name"], apis[d["hostapi"]]["name"])
        for i, d in enumerate(devices)
        if d[channels_key] > 0
    ]
    if sys.platform == "win32":  # MME/DirectSound/WDM-KS только дублируют устройства
        wasapi = [d for d in found if "WASAPI" in d.hostapi]
        if wasapi:
            found = wasapi
    return found


def list_output_devices() -> list[DeviceInfo]:
    return _list_devices("output")


def list_input_devices() -> list[DeviceInfo]:
    return _list_devices("input")


def _default_device_index(kind: str) -> Optional[int]:
    key = "default_output_device" if kind == "output" else "default_input_device"
    try:
        if sys.platform == "win32":
            for api in sd.query_hostapis():
                if "WASAPI" in api["name"] and api[key] >= 0:
                    return api[key]
        idx = sd.default.device[1 if kind == "output" else 0]
        if isinstance(idx, int) and idx >= 0:
            return idx
        idx = sd.query_hostapis(sd.default.hostapi)[key]
        return idx if idx >= 0 else None
    except Exception:
        return None


def _resolve_device(name: Optional[str], kind: str) -> int:
    if not name:
        idx = _default_device_index(kind)
        if idx is None:
            raise AudioEngineError("Не найдено устройство по умолчанию. Выберите устройство в настройках.")
        return idx
    devices = _list_devices(kind)
    for d in devices:
        if d.name == name:
            return d.index
    for d in devices:
        if d.name.lower() == name.lower():
            return d.index
    raise AudioEngineError(f"Устройство «{name}» не найдено (отключено?).")


def _extra_settings(device_index: int):
    api = sd.query_hostapis(sd.query_devices(device_index)["hostapi"])["name"]
    return sd.WasapiSettings(auto_convert=True) if "WASAPI" in api else None


# ---------------------------------------------------------------- decoding
def _to_stereo(data: np.ndarray) -> np.ndarray:
    ch = data.shape[1]
    if ch == 2:
        return data
    if ch == 1:
        return np.repeat(data, 2, axis=1)
    return np.stack((data[:, 0::2].mean(axis=1), data[:, 1::2].mean(axis=1)), axis=1)


def _resample(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(data) == 0:
        return data
    if resample_poly is not None:
        g = math.gcd(int(src), int(dst))
        return resample_poly(data, int(dst) // g, int(src) // g, axis=0).astype(np.float32, copy=False)
    n_out = max(1, int(round(len(data) * dst / src)))
    x_old = np.arange(len(data))
    x_new = np.linspace(0, len(data) - 1, n_out)
    out = np.empty((n_out, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, data[:, ch])
    return out


def decode_file(path: str, samplerate: int) -> np.ndarray:
    if not os.path.isfile(path):
        raise AudioEngineError(f"Файл не найден: {path}")
    try:
        data, file_sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception as e:
        raise AudioEngineError(f"Не удалось прочитать «{os.path.basename(path)}»: {e}") from e
    data = _resample(_to_stereo(data), int(file_sr), samplerate)
    return np.ascontiguousarray(data, dtype=np.float32)


class _SoundCache:
    def __init__(self, max_bytes: int = CACHE_LIMIT_BYTES) -> None:
        self._items: "collections.OrderedDict[tuple, np.ndarray]" = collections.OrderedDict()
        self._bytes = 0
        self._max = max_bytes
        self._lock = threading.Lock()

    def get(self, path: str, samplerate: int) -> np.ndarray:
        try:
            st = os.stat(path)
        except OSError as e:
            raise AudioEngineError(f"Файл недоступен: {path} ({e.strerror})") from e
        key = (path, st.st_mtime_ns, st.st_size, samplerate)
        with self._lock:
            arr = self._items.get(key)
            if arr is not None:
                self._items.move_to_end(key)
                return arr
        arr = decode_file(path, samplerate)  # тяжёлая часть — вне блокировки
        with self._lock:
            if key in self._items:
                return self._items[key]
            self._items[key] = arr
            self._bytes += arr.nbytes
            while self._bytes > self._max and len(self._items) > 1:
                _, old = self._items.popitem(last=False)
                self._bytes -= old.nbytes
        return arr

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0


# ------------------------------------------------------------ mic passthrough
class _MicInput:
    """Реальный микрофон -> кольцевой буфер -> подмешивание в выход «микрофон»."""

    def __init__(self, samplerate: int) -> None:
        self.sr = samplerate
        self._cap = samplerate  # 1 секунда
        self._ring = np.zeros((self._cap, 2), dtype=np.float32)
        self._w = 0
        self._r = 0
        self._primed = False
        self._channels = 1
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None

    def start(self, device: int) -> None:
        info = sd.query_devices(device)
        self._channels = 1 if info["max_input_channels"] < 2 else 2
        try:
            self._stream = sd.InputStream(
                samplerate=self.sr, device=device, channels=self._channels, dtype="float32",
                latency="low", callback=self._callback, extra_settings=_extra_settings(device),
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise AudioEngineError(f"Не удалось открыть микрофон «{info['name']}»: {e}") from e

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Ошибка закрытия входного потока")

    def _callback(self, indata, frames, time_info, status) -> None:
        if frames > self._cap:
            indata, frames = indata[-self._cap:], self._cap
        block = np.repeat(indata, 2, axis=1) if self._channels == 1 else indata
        with self._lock:
            idx = np.arange(self._w, self._w + frames) % self._cap
            self._ring[idx] = block
            self._w += frames

    def read(self, frames: int) -> Optional[np.ndarray]:
        """Блок из frames кадров или None (тишина, если данных не хватает)."""
        with self._lock:
            avail = self._w - self._r
            if not self._primed:
                if avail < frames * 2:      # накапливаем небольшой запас от дрожания
                    return None
                self._primed = True
            if avail < frames:
                self._primed = False
                return None
            limit = max(int(self.sr * 0.1), frames * 3)
            if avail > limit:               # часы устройств разъехались — срезаем задержку
                self._r = self._w - frames * 2
            idx = np.arange(self._r, self._r + frames) % self._cap
            out = self._ring[idx]
            self._r += frames
            return out


# ---------------------------------------------------------------- playback
class _Cursor:
    __slots__ = ("play_id", "data", "pos", "volume", "loop", "fading", "fade_pos", "done")

    def __init__(self, play_id: int, data: np.ndarray, volume: float, loop: bool) -> None:
        self.play_id = play_id
        self.data = data
        self.pos = 0
        self.volume = volume
        self.loop = loop
        self.fading = False
        self.fade_pos = 0
        self.done = False


class _Play:
    __slots__ = ("sound_id", "remaining")

    def __init__(self, sound_id: str, remaining: int) -> None:
        self.sound_id = sound_id
        self.remaining = remaining


class _Output:
    def __init__(self, engine: "AudioEngine", label: str) -> None:
        self.engine = engine
        self.label = label
        self.volume = 1.0
        self.cursors: list[_Cursor] = []
        self.stream: Optional[sd.OutputStream] = None
        self.passthrough: Optional[_MicInput] = None
        self.fade_len = int(DEFAULT_SAMPLERATE * FADE_SECONDS)

    @property
    def active(self) -> bool:
        return self.stream is not None

    def open(self, device: int, samplerate: int) -> None:
        info = sd.query_devices(device)
        self.fade_len = max(1, int(samplerate * FADE_SECONDS))
        try:
            stream = sd.OutputStream(
                samplerate=samplerate, device=device, channels=2, dtype="float32",
                latency="low", callback=self._callback, extra_settings=_extra_settings(device),
            )
            stream.start()
        except Exception as e:
            raise AudioEngineError(f"Не удалось открыть «{info['name']}»: {e}") from e
        self.stream = stream
        log.info("%s: %s @ %d Гц", self.label, info["name"], samplerate)

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Ошибка закрытия потока")

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            log.debug("%s: %s", self.label, status)
        outdata.fill(0.0)
        eng = self.engine

        pt = self.passthrough
        if pt is not None:
            block = pt.read(frames)
            if block is not None:
                outdata += block * eng._input_volume

        ended: list[str] = []
        with eng._lock:
            if self.cursors:
                for cur in self.cursors:
                    self._mix_cursor(outdata, cur, self.volume * cur.volume)
                alive = []
                for cur in self.cursors:
                    if cur.done:
                        sid = eng._release_play_locked(cur.play_id)
                        if sid is not None:
                            ended.append(sid)
                    else:
                        alive.append(cur)
                self.cursors = alive

        np.clip(outdata, -1.0, 1.0, out=outdata)
        for sid in ended:
            eng._emit(eng.on_stopped, sid)

    def _mix_cursor(self, out: np.ndarray, cur: _Cursor, gain: float) -> None:
        frames = out.shape[0]
        data = cur.data
        n = data.shape[0]
        written = 0
        while written < frames:
            if cur.pos >= n:
                if cur.loop and n > 0 and not cur.fading:
                    cur.pos = 0
                else:
                    cur.done = True
                    return
            take = min(n - cur.pos, frames - written)
            chunk = data[cur.pos:cur.pos + take]
            if cur.fading:
                ramp = 1.0 - np.arange(cur.fade_pos, cur.fade_pos + take, dtype=np.float32) / self.fade_len
                np.clip(ramp, 0.0, 1.0, out=ramp)
                out[written:written + take] += chunk * (ramp[:, None] * gain)
                cur.fade_pos += take
                if cur.fade_pos >= self.fade_len:
                    cur.done = True
                    return
            else:
                out[written:written + take] += chunk * gain
            cur.pos += take
            written += take


# ------------------------------------------------------------------ engine
class AudioEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()       # общее состояние: курсоры, plays
        self._cfg_lock = threading.Lock()    # сериализует configure/shutdown
        self._samplerate = DEFAULT_SAMPLERATE
        self._master = 1.0
        self._monitor_vol = 1.0
        self._input_volume = 1.0
        self._allow_overlap = True

        self._monitor: Optional[_Output] = None
        self._mic: Optional[_Output] = None
        self._mic_input: Optional[_MicInput] = None

        self._plays: dict[int, _Play] = {}
        self._play_ids = itertools.count(1)
        self._cache = _SoundCache()
        self._peaks: dict[tuple, np.ndarray] = {}
        self._play_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sp-play")
        self._decode_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sp-decode")

        self.on_started: Optional[Callable[[str], None]] = None
        self.on_stopped: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    # ----------------------------------------------------------- configuration
    def configure(self, cfg: AudioConfig) -> list[str]:
        """(Пере)открывает устройства. Возвращает список ошибок (пустой = всё ок)."""
        errors: list[str] = []
        with self._cfg_lock:
            self.stop_all(immediate=True)
            self._close_outputs()

            if cfg.samplerate != self._samplerate:
                self._samplerate = cfg.samplerate
                self._cache.clear()

            monitor = mic = None
            mic_input: Optional[_MicInput] = None

            if cfg.monitor_enabled:
                try:
                    monitor = _Output(self, "монитор")
                    monitor.open(_resolve_device(cfg.monitor_device, "output"), self._samplerate)
                except AudioEngineError as e:
                    monitor = None
                    errors.append(f"Монитор: {e}")

            if cfg.mic_enabled:
                try:
                    mic = _Output(self, "микрофон")
                    if cfg.passthrough_enabled:
                        try:
                            mic_input = _MicInput(self._samplerate)
                            mic_input.start(_resolve_device(cfg.input_device, "input"))
                            mic.passthrough = mic_input
                        except AudioEngineError as e:
                            mic_input = None
                            errors.append(f"Вход: {e}")
                    mic.open(_resolve_device(cfg.mic_device, "output"), self._samplerate)
                except AudioEngineError as e:
                    if mic_input is not None:
                        mic_input.close()
                        mic_input = None
                    mic = None
                    errors.append(f"Микрофон (вывод): {e}")

            with self._lock:
                self._monitor, self._mic, self._mic_input = monitor, mic, mic_input
            self._apply_volumes()

            if monitor is None and mic is None and not errors:
                errors.append("Не выбрано ни одного устройства вывода — звук не будет слышен.")
        return errors

    def _close_outputs(self) -> None:
        with self._lock:
            outs = (self._monitor, self._mic)
            mic_input = self._mic_input
            self._monitor = self._mic = self._mic_input = None
        for out in outs:      # закрываем вне блокировки: аудио-колбэк берёт её же
            if out is not None:
                out.close()
        if mic_input is not None:
            mic_input.close()

    def shutdown(self) -> None:
        with self._cfg_lock:
            self.stop_all(immediate=True)
            self._close_outputs()
        self._play_pool.shutdown(wait=False, cancel_futures=True)
        self._decode_pool.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------------- volumes
    def _apply_volumes(self) -> None:
        with self._lock:
            if self._monitor is not None:
                self._monitor.volume = self._master * self._monitor_vol
            if self._mic is not None:
                self._mic.volume = self._master

    def set_master_volume(self, value: float) -> None:
        self._master = max(0.0, min(1.0, value))
        self._apply_volumes()

    def set_monitor_volume(self, value: float) -> None:
        self._monitor_vol = max(0.0, min(1.0, value))
        self._apply_volumes()

    def set_input_volume(self, value: float) -> None:
        self._input_volume = max(0.0, min(1.0, value))

    def set_allow_overlap(self, value: bool) -> None:
        self._allow_overlap = bool(value)

    # ------------------------------------------------------------------ playback
    def preload(self, path: str) -> None:
        """Декодировать файл заранее, чтобы хоткей срабатывал без задержки."""
        self._decode_pool.submit(self._preload_job, path)

    def _preload_job(self, path: str) -> None:
        try:
            self._cache.get(path, self._samplerate)
        except AudioEngineError as e:
            log.warning("preload: %s", e)
        except Exception:
            log.exception("preload failed: %s", path)

    def play(self, sound_id: str, path: str, volume: float = 1.0, loop: bool = False,
             mode: str = "restart") -> None:
        """Потокобезопасно; возвращается сразу. mode: restart | overlap | toggle."""
        self._play_pool.submit(self._play_job, sound_id, path, volume, loop, mode)

    def _play_job(self, sound_id: str, path: str, volume: float, loop: bool, mode: str) -> None:
        try:
            if self.is_playing(sound_id):
                if mode == "toggle":
                    self.stop(sound_id)
                    return
                if mode == "restart":
                    self.stop(sound_id)
            if not self._allow_overlap:
                self._stop_others(sound_id)
            data = self._cache.get(path, self._samplerate)
            self._start(sound_id, data, volume, loop)
        except AudioEngineError as e:
            self._emit(self.on_error, str(e))
        except Exception as e:
            log.exception("Ошибка воспроизведения")
            self._emit(self.on_error, f"Ошибка воспроизведения «{os.path.basename(path)}»: {e}")

    def _active_outputs(self) -> list[_Output]:
        return [o for o in (self._monitor, self._mic) if o is not None and o.active]

    def _start(self, sound_id: str, data: np.ndarray, volume: float, loop: bool) -> None:
        with self._lock:
            outs = self._active_outputs()
            if not outs:
                raise AudioEngineError("Нет активных устройств вывода. Откройте настройки и выберите устройство.")
            was_playing = any(p.sound_id == sound_id for p in self._plays.values())
            pid = next(self._play_ids)
            self._plays[pid] = _Play(sound_id, len(outs))
            for out in outs:
                out.cursors.append(_Cursor(pid, data, volume, loop))
        if not was_playing:
            self._emit(self.on_started, sound_id)

    def _release_play_locked(self, play_id: int) -> Optional[str]:
        """Вызывать под self._lock. Вернёт sound_id, если звук полностью закончился."""
        play = self._plays.get(play_id)
        if play is None:
            return None
        play.remaining -= 1
        if play.remaining > 0:
            return None
        del self._plays[play_id]
        if any(p.sound_id == play.sound_id for p in self._plays.values()):
            return None  # того же звука ещё играет другой экземпляр
        return play.sound_id

    def _fade_where(self, predicate: Callable[[_Play], bool]) -> None:
        with self._lock:
            pids = {pid for pid, p in self._plays.items() if predicate(p)}
            for out in self._active_outputs():
                for cur in out.cursors:
                    if cur.play_id in pids:
                        cur.fading = True

    def stop(self, sound_id: str) -> None:
        self._fade_where(lambda p: p.sound_id == sound_id)

    def _stop_others(self, sound_id: str) -> None:
        self._fade_where(lambda p: p.sound_id != sound_id)

    def stop_all(self, immediate: bool = False) -> None:
        if not immediate:
            self._fade_where(lambda p: True)
            return
        with self._lock:
            stopped = {p.sound_id for p in self._plays.values()}
            self._plays.clear()
            for out in (self._monitor, self._mic):
                if out is not None:
                    out.cursors.clear()
        for sid in stopped:
            self._emit(self.on_stopped, sid)

    def is_playing(self, sound_id: str) -> bool:
        with self._lock:
            return any(p.sound_id == sound_id for p in self._plays.values())

    # --------------------------------------------- прогресс и волна для UI
    def get_progress(self, sound_id: str) -> Optional[tuple[float, float]]:
        """(позиция_сек, длительность_сек) последнего экземпляра звука или None."""
        with self._lock:
            for out in (self._monitor, self._mic):
                if out is None:
                    continue
                latest: Optional[_Cursor] = None
                for cur in out.cursors:
                    play = self._plays.get(cur.play_id)
                    if play is not None and play.sound_id == sound_id and (
                        latest is None or cur.play_id > latest.play_id
                    ):
                        latest = cur
                if latest is not None:
                    sr = float(self._samplerate)
                    return latest.pos / sr, latest.data.shape[0] / sr
        return None

    def request_peaks(self, path: str, buckets: int,
                      callback: Callable[[Optional[np.ndarray], float], None]) -> None:
        """Асинхронно считает огибающую для волны.

        callback(peaks, длительность_сек): peaks — float32 массив длины buckets в диапазоне 0..1
        или None, если файл не прочитался. Вызывается из фонового потока.
        """
        self._decode_pool.submit(self._peaks_job, path, buckets, callback)

    def _peaks_job(self, path: str, buckets: int, callback) -> None:
        try:
            data = self._cache.get(path, self._samplerate)
            n = data.shape[0]
            duration = n / self._samplerate
            key = (path, n, buckets)
            peaks = self._peaks.get(key)
            if peaks is None:
                step = n // buckets
                if step > 0:
                    chunks = data[: step * buckets].reshape(buckets, step, 2)
                    peaks = np.maximum(chunks.max(axis=(1, 2)), -chunks.min(axis=(1, 2)))
                elif n > 0:  # очень короткий звук — растягиваем
                    mono = np.abs(data).max(axis=1)
                    peaks = np.interp(np.linspace(0, n - 1, buckets), np.arange(n), mono)
                else:
                    peaks = np.zeros(buckets)
                top = float(peaks.max())
                if top > 0:
                    peaks = np.sqrt(peaks / top)  # sqrt — тихие участки заметнее
                peaks = peaks.astype(np.float32)
                self._peaks[key] = peaks
            self._emit(callback, peaks, duration)
        except AudioEngineError as e:
            log.warning("peaks: %s", e)
            self._emit(callback, None, 0.0)
        except Exception:
            log.exception("peaks failed: %s", path)
            self._emit(callback, None, 0.0)

    # --------------------------------------------- live-изменения играющих звуков
    def set_sound_volume(self, sound_id: str, volume: float) -> None:
        with self._lock:
            pids = {pid for pid, p in self._plays.items() if p.sound_id == sound_id}
            for out in self._active_outputs():
                for cur in out.cursors:
                    if cur.play_id in pids:
                        cur.volume = volume

    def set_sound_loop(self, sound_id: str, loop: bool) -> None:
        with self._lock:
            pids = {pid for pid, p in self._plays.items() if p.sound_id == sound_id}
            for out in self._active_outputs():
                for cur in out.cursors:
                    if cur.play_id in pids:
                        cur.loop = loop

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _emit(callback: Optional[Callable], *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("Ошибка в колбэке движка")
