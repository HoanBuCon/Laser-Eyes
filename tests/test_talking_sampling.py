"""Speech from lip movement with the real algorithm at the rates the Hub really analyses.

The pipeline tests use a scripted analyser; this one feeds a synthetic speaking mouth through
GazeAnalyzer._update_talking itself, so a sampling-rate assumption cannot hide again."""

from __future__ import annotations

from collections import deque

import numpy as np
import pytest

from exam_monitor.audio import AudioSpeechState, speech_is_confirmed
from exam_monitor.engine import GazeAnalyzer


def _analyzer() -> GazeAnalyzer:
    analyzer = object.__new__(GazeAnalyzer)  # only the lip-motion state, no face model needed
    analyzer._mouth_history = deque(maxlen=90)
    analyzer._talking_score = 0.0
    return analyzer


def _run(fps: float, mouth, seconds: float = 6.0, seed: int = 0) -> tuple[float, int]:
    rng = np.random.default_rng(seed)
    analyzer, best, strict = _analyzer(), 0.0, 0
    for i in range(int(seconds * fps)):
        t = i / fps
        score, talking = analyzer._update_talking(float(max(0.0, mouth(t) + rng.normal(0, 0.008))), t)
        best, strict = max(best, score), strict + bool(talking)
    return best, strict


def _speaking(syllables_hz: float):
    return lambda t: 0.04 + 0.16 * max(0.0, np.sin(2 * np.pi * (syllables_hz * t + 0.3)))


@pytest.mark.parametrize("fps", [8, 10, 15, 30])
@pytest.mark.parametrize("syllables", [3.0, 4.0, 5.0])
def test_speech_is_seen_from_the_lips_at_the_hub_rate_and_above(fps, syllables):
    best, strict = _run(fps, _speaking(syllables))
    assert best >= 0.66 and strict > 0  # even without a microphone


@pytest.mark.parametrize("syllables", [3.0, 4.0])
def test_at_5_fps_lips_still_confirm_speech_the_microphone_hears(syllables):
    best, _ = _run(5, _speaking(syllables))
    voice = AudioSpeechState(available=True, voice_active=True)
    assert speech_is_confirmed(best, False, voice)  # was 0.0 before: a 0.75 s window held < 5 frames


@pytest.mark.parametrize("fps", [5, 10, 30])
@pytest.mark.parametrize("mouth", [
    lambda t: 0.05,                                                                       # still
    lambda t: 0.05 + (0.45 * np.sin(np.pi * (t - 2.0) / 2.0) if 2.0 <= t < 4.0 else 0.0),  # a yawn
], ids=["still", "yawn"])
def test_a_still_mouth_or_a_yawn_is_not_speech(fps, mouth):
    best, strict = _run(fps, mouth)
    assert strict == 0 and best < 0.26


def test_the_hub_analyses_fast_enough_for_lips_by_default():
    from storage import online_exam_service as online
    from storage.db_models import Exam

    assert online.DEFAULT_POLICY["analysis_fps"] >= 8
    old = Exam(name="x", one_policy_json='{"analysis_fps": 5}')  # saved with the earlier default
    assert online.policy_for(old)["analysis_fps"] == 10
