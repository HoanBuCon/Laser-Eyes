from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioSpeechState:
    available: bool = False
    voice_active: bool = False
    voice_ratio: float = 0.0
    level_db: float = -90.0
    device_name: str = ""
    error: str = ""


def speech_is_confirmed(
    visual_score: float,
    visual_talking: bool,
    audio_state: AudioSpeechState,
) -> bool:
    """Require voice + lip motion when audio exists; retain strict visual fallback."""
    if not audio_state.available:
        return visual_talking
    return audio_state.voice_active and visual_score >= 0.26


class VoiceActivityDetector:
    """Small local WebRTC VAD microphone reader (16 kHz, 20 ms frames)."""

    def __init__(self, aggressiveness: int = 2):
        self.sample_rate = 16_000
        self.block_size = 320
        self._aggressiveness = max(0, min(3, int(aggressiveness)))
        self._lock = threading.Lock()
        self._recent_voice: deque[tuple[float, bool]] = deque(maxlen=30)
        self._level_db = -90.0
        self._last_voice_at = -1e9
        self._device_name = ""
        self._error = ""
        self._stream = None

    def start(self) -> AudioSpeechState:
        try:
            import sounddevice as sd
            import webrtcvad

            self._vad = webrtcvad.Vad(self._aggressiveness)
            device = sd.query_devices(kind="input")
            self._device_name = str(device.get("name", "Microphone"))
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            self._stream.start()
            return self.snapshot()
        except Exception as exc:
            self._error = str(exc)
            self.stop()
            return self.snapshot()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def snapshot(self) -> AudioSpeechState:
        now = time.monotonic()
        with self._lock:
            while self._recent_voice and now - self._recent_voice[0][0] > 0.65:
                self._recent_voice.popleft()
            flags = [flag for _, flag in self._recent_voice]
            recent_flags = flags[-12:]
            ratio = sum(recent_flags) / max(1, len(recent_flags))
            active = (
                len(recent_flags) >= 4
                and sum(recent_flags) >= 3
                and now - self._last_voice_at <= 0.35
                and self._level_db > -55.0
            )
            return AudioSpeechState(
                available=self._stream is not None,
                voice_active=active,
                voice_ratio=float(ratio),
                level_db=float(self._level_db),
                device_name=self._device_name,
                error=self._error,
            )

    def _callback(self, indata, frames, _time_info, status) -> None:
        if status:
            self._error = str(status)
        try:
            pcm = bytes(indata)
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
            level_db = 20.0 * math.log10(max(1.0, rms) / 32768.0)
            voiced = bool(self._vad.is_speech(pcm, self.sample_rate)) and level_db > -55.0
            with self._lock:
                self._level_db = self._level_db * 0.72 + level_db * 0.28
                self._recent_voice.append((time.monotonic(), voiced))
                if voiced:
                    self._last_voice_at = time.monotonic()
        except Exception as exc:
            self._error = str(exc)
