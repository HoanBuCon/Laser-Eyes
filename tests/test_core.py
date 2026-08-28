from __future__ import annotations

import tempfile
import unittest
from collections import deque
from pathlib import Path

import numpy as np

from exam_monitor.audio import AudioSpeechState, speech_is_confirmed
from exam_monitor.events import EventDetector, SessionStore
from exam_monitor.engine import GazeAnalyzer
from exam_monitor.models import AnalysisResult, EventType, SessionInfo, Severity
from exam_monitor.sources import DemoSource, _prepare_real_frame
from exam_monitor.theme import COLORS, THEMES, set_theme


class ThemeTests(unittest.TestCase):
    def test_light_and_dark_palettes_are_complete_and_switch_in_place(self):
        self.assertEqual(set(THEMES["light"]), set(THEMES["dark"]))
        colors_id = id(COLORS)
        try:
            set_theme("dark")
            self.assertEqual(id(COLORS), colors_id)
            self.assertEqual(COLORS["bg"], THEMES["dark"]["bg"])
            self.assertNotEqual(THEMES["light"]["text"], THEMES["dark"]["text"])
        finally:
            set_theme("light")


class EventDetectorTests(unittest.TestCase):
    def test_look_away_requires_duration(self):
        detector = EventDetector()
        result = AnalysisResult(
            timestamp=0.0,
            face_count=1,
            direction="left",
            eye_direction="left",
            head_direction="center",
            eye_zone_score=1.8,
            eyes_outside_zone=True,
            calibrated=True,
            confidence=0.91,
            brightness=120,
        )
        self.assertEqual(detector.update(result, "S1", now=10.0), [])
        events = detector.update(
            result,
            "S1",
            now=10.0 + detector.rules[EventType.LOOK_AWAY].threshold_seconds + 0.1,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.LOOK_AWAY)
        self.assertEqual(events[0].severity, Severity.MEDIUM)
        self.assertIn("ngoài vùng thi", events[0].reason)

    def test_uncalibrated_eye_motion_does_not_raise_look_away(self):
        detector = EventDetector()
        result = AnalysisResult(
            timestamp=0.0,
            face_count=1,
            person_count=1,
            eye_direction="right",
            head_direction="center",
            eye_zone_score=1.7,
            eyes_outside_zone=False,
            calibrated=False,
            brightness=110,
        )
        detector.update(result, "S1", now=1.0)
        self.assertEqual(detector.update(result, "S1", now=3.0), [])

    def test_no_face_resets_when_face_returns(self):
        detector = EventDetector()
        missing = AnalysisResult(timestamp=0, face_count=0, brightness=100)
        center = AnalysisResult(timestamp=0, face_count=1, direction="center", brightness=100)
        detector.update(missing, "S1", now=1.0)
        detector.update(center, "S1", now=2.0)
        self.assertEqual(detector.update(missing, "S1", now=3.0), [])
        self.assertEqual(detector.update(missing, "S1", now=5.0), [])
        events = detector.update(missing, "S1", now=6.1)
        self.assertEqual(events[0].event_type, EventType.NO_FACE)

    def test_two_or_more_people_raise_high_alert(self):
        detector = EventDetector()
        result = AnalysisResult(
            timestamp=0,
            face_count=2,
            person_count=3,
            direction="center",
            eye_direction="center",
            head_direction="center",
            brightness=110,
            confidence=0.9,
        )
        detector.update(result, "S1", now=1.0)
        events = detector.update(
            result,
            "S1",
            now=1.0 + detector.rules[EventType.MULTIPLE_FACES].threshold_seconds + 0.1,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.MULTIPLE_FACES)
        self.assertEqual(events[0].severity, Severity.HIGH)
        self.assertIn("2 người", events[0].reason)

    def test_head_turn_and_talking_are_separate_events(self):
        detector = EventDetector()
        turned = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            direction="right",
            eye_direction="center",
            head_direction="right",
            yaw=31,
            brightness=110,
            confidence=0.9,
        )
        detector.update(turned, "S1", now=1.0)
        events = detector.update(
            turned,
            "S1",
            now=1.0 + detector.rules[EventType.HEAD_TURN].threshold_seconds + 0.1,
        )
        self.assertEqual([event.event_type for event in events], [EventType.HEAD_TURN])

        detector.reset()
        talking = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            direction="center",
            eye_direction="center",
            head_direction="center",
            is_talking=True,
            talking_score=0.86,
            brightness=110,
            confidence=0.9,
        )
        detector.update(talking, "S1", now=4.0)
        events = detector.update(talking, "S1", now=5.1)
        self.assertEqual([event.event_type for event in events], [EventType.TALKING])

    def test_suspicious_object_raises_high_alert(self):
        detector = EventDetector()
        result = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            direction="center",
            eye_direction="center",
            head_direction="center",
            suspicious_objects=["cell phone"],
            brightness=110,
            confidence=0.9,
        )
        detector.update(result, "S1", now=1.0)
        events = detector.update(
            result,
            "S1",
            now=1.0 + detector.rules[EventType.SUSPICIOUS_OBJECT].threshold_seconds + 0.1,
        )
        self.assertEqual(events[0].event_type, EventType.SUSPICIOUS_OBJECT)
        self.assertEqual(events[0].severity, Severity.HIGH)

    def test_sustained_condition_emits_only_once_per_episode(self):
        detector = EventDetector()
        looking_away = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            eye_direction="left",
            head_direction="center",
            eye_zone_score=1.7,
            eyes_outside_zone=True,
            calibrated=True,
            brightness=110,
            confidence=0.9,
        )
        detector.update(looking_away, "S1", now=0.0)
        first = detector.update(looking_away, "S1", now=1.7)
        self.assertEqual([event.event_type for event in first], [EventType.LOOK_AWAY])
        self.assertEqual(detector.update(looking_away, "S1", now=20.0), [])

    def test_alert_rearms_only_after_stable_recovery(self):
        detector = EventDetector()
        looking_away = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            eye_direction="right",
            head_direction="center",
            eye_zone_score=1.6,
            eyes_outside_zone=True,
            calibrated=True,
            brightness=110,
            confidence=0.9,
        )
        attentive = AnalysisResult(
            timestamp=0,
            face_count=1,
            person_count=1,
            eye_direction="center",
            head_direction="center",
            calibrated=True,
            brightness=110,
            confidence=0.9,
        )
        detector.update(looking_away, "S1", now=0.0)
        self.assertEqual(len(detector.update(looking_away, "S1", now=1.7)), 1)

        detector.update(attentive, "S1", now=2.0)
        self.assertEqual(detector.update(looking_away, "S1", now=2.4), [])
        self.assertEqual(detector.update(looking_away, "S1", now=10.0), [])

        detector.update(attentive, "S1", now=11.0)
        detector.update(attentive, "S1", now=11.9)
        detector.update(looking_away, "S1", now=12.0)
        second = detector.update(looking_away, "S1", now=13.7)
        self.assertEqual([event.event_type for event in second], [EventType.LOOK_AWAY])


class AnalyzerSignalTests(unittest.TestCase):
    def test_audio_confirms_short_lip_motion_but_not_audio_or_mouth_alone(self):
        voice = AudioSpeechState(available=True, voice_active=True, voice_ratio=0.7)
        silence = AudioSpeechState(available=True, voice_active=False)
        no_microphone = AudioSpeechState(available=False)
        self.assertTrue(speech_is_confirmed(0.36, False, voice))
        self.assertFalse(speech_is_confirmed(0.08, False, voice))
        self.assertFalse(speech_is_confirmed(0.80, True, silence))
        self.assertTrue(speech_is_confirmed(0.80, True, no_microphone))

    def test_direction_thresholds_cover_four_directions(self):
        self.assertEqual(GazeAnalyzer._eye_direction(-0.6, 0.0), "left")
        self.assertEqual(GazeAnalyzer._eye_direction(0.6, 0.0), "right")
        self.assertEqual(GazeAnalyzer._head_direction(0.0, -25.0), "up")
        self.assertEqual(GazeAnalyzer._head_direction(0.0, 25.0), "down")
        self.assertLess(GazeAnalyzer._eye_zone_score(0.10, 0.10), 1.0)
        self.assertGreater(GazeAnalyzer._eye_zone_score(0.35, 0.0), 1.0)

    def test_repeated_mouth_motion_is_talking_but_jitter_is_not(self):
        analyzer = object.__new__(GazeAnalyzer)
        analyzer._mouth_history = deque(maxlen=90)
        analyzer._talking_score = 0.0
        talking = False
        speech = [0.035, 0.13, 0.05, 0.18, 0.04, 0.14, 0.055, 0.20] * 2
        for index, value in enumerate(speech):
            _, talking = analyzer._update_talking(value, index * 0.09)
        self.assertTrue(talking)

        analyzer._mouth_history.clear()
        analyzer._talking_score = 0.0
        for index, value in enumerate([0.05, 0.052, 0.048, 0.051] * 5):
            _, talking = analyzer._update_talking(value, index * 0.09)
        self.assertFalse(talking)

    def test_open_mouth_and_single_yawn_are_not_talking(self):
        analyzer = object.__new__(GazeAnalyzer)
        analyzer._mouth_history = deque(maxlen=90)
        analyzer._talking_score = 0.0
        talking = False
        held_open = [0.04] * 8 + [0.176, 0.184, 0.179, 0.182] * 6
        for index, value in enumerate(held_open):
            _, talking = analyzer._update_talking(value, index * 0.08)
        self.assertFalse(talking)

        analyzer._mouth_history.clear()
        analyzer._talking_score = 0.0
        yawn = [0.04] * 6 + [0.06, 0.09, 0.13, 0.18, 0.22] + [0.22] * 10 + [0.18, 0.13, 0.09, 0.06, 0.04] + [0.04] * 6
        for index, value in enumerate(yawn):
            _, talking = analyzer._update_talking(value, index * 0.08)
        self.assertFalse(talking)

    def test_closed_lips_with_landmark_spikes_are_not_talking(self):
        analyzer = object.__new__(GazeAnalyzer)
        analyzer._mouth_history = deque(maxlen=90)
        analyzer._talking_score = 0.0
        talking = False
        closed_with_spikes = [
            0.026, 0.031, 0.029, 0.034, 0.027, 0.082,
            0.030, 0.025, 0.033, 0.028, 0.036, 0.079,
        ] * 4
        for index, value in enumerate(closed_with_spikes):
            _, talking = analyzer._update_talking(value, index * 0.08)
        self.assertFalse(talking)

    def test_talking_state_clears_after_articulation_stops(self):
        analyzer = object.__new__(GazeAnalyzer)
        analyzer._mouth_history = deque(maxlen=90)
        analyzer._talking_score = 0.0
        talking = False
        speech = [0.035, 0.13, 0.05, 0.18, 0.04, 0.14] * 5
        for index, value in enumerate(speech):
            _, talking = analyzer._update_talking(value, index * 0.08)
        self.assertTrue(talking)
        offset = len(speech)
        for index, value in enumerate([0.032] * 12, start=offset):
            _, talking = analyzer._update_talking(value, index * 0.08)
        self.assertFalse(talking)

    def test_mirrored_iris_shift_right_maps_to_candidate_right(self):
        class Point:
            def __init__(self, x=0.5, y=0.5):
                self.x = x
                self.y = y

        face = [Point() for _ in range(478)]
        for a, center, b in ((33, 468, 133), (362, 473, 263)):
            face[a].x, face[b].x, face[center].x = 0.2, 0.8, 0.68
        for upper, center, lower in ((159, 468, 145), (386, 473, 374)):
            face[upper].y, face[lower].y, face[center].y = 0.4, 0.6, 0.5
        gaze_x, gaze_y, _ = GazeAnalyzer._iris_position(object.__new__(GazeAnalyzer), face)
        self.assertGreater(gaze_x, 0.0)
        self.assertEqual(GazeAnalyzer._eye_direction(gaze_x, gaze_y), "right")

    def test_new_calibration_clears_previous_session_tracking(self):
        analyzer = object.__new__(GazeAnalyzer)
        analyzer._calibration_samples = [(0.2, 0.1)]
        analyzer._baseline_x = 0.3
        analyzer._baseline_y = -0.2
        analyzer._smooth_x = 0.7
        analyzer._smooth_y = -0.6
        analyzer._calibrated = True
        analyzer._recent_confidence = deque([0.9], maxlen=20)
        analyzer._mouth_history = deque([(1.0, 0.2)], maxlen=90)
        analyzer._talking_score = 0.8
        analyzer._last_valid_result = AnalysisResult(timestamp=1.0, face_count=1)
        analyzer._last_face_seen_at = 1.0
        analyzer._last_object_detections = [("cell phone", 0.9, (0, 0, 10, 10))]
        analyzer._last_objects_at = 1.0
        analyzer._frame_index = 12

        analyzer.begin_calibration()

        self.assertFalse(analyzer.calibrated)
        self.assertEqual((analyzer._baseline_x, analyzer._baseline_y), (0.0, 0.0))
        self.assertEqual((analyzer._smooth_x, analyzer._smooth_y), (0.0, 0.0))
        self.assertIsNone(analyzer._last_valid_result)
        self.assertEqual(analyzer._last_object_detections, [])
        self.assertEqual(analyzer._frame_index, 0)


class SourceAndStoreTests(unittest.TestCase):
    def test_real_frame_is_mirrored_and_capped_for_processing(self):
        frame = np.zeros((600, 1200, 3), dtype=np.uint8)
        frame[:, :600] = (0, 0, 255)
        frame[:, 600:] = (255, 0, 0)
        prepared = _prepare_real_frame(frame)
        self.assertEqual(prepared.shape, (480, 960, 3))
        self.assertGreater(int(prepared[240, 100, 0]), 240)
        self.assertGreater(int(prepared[240, 860, 2]), 240)

    def test_demo_source_returns_frame_and_analysis(self):
        source = DemoSource(640, 360)
        ok, frame = source.read()
        self.assertTrue(ok)
        self.assertEqual(frame.shape, (360, 640, 3))
        self.assertEqual(source.last_result.face_count, 1)

    def test_session_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(Path(temp))
            session = SessionInfo(
                session_id="S1",
                candidate_id="SV01",
                exam_name="Demo",
                started_at="2026-08-13T10:00:00",
                ended_at="2026-08-13T10:05:00",
            )
            path = store.save_session(session)
            self.assertTrue(path.exists())
            loaded = store.list_sessions()
            self.assertEqual(loaded[0]["candidate_id"], "SV01")
            self.assertEqual(loaded[0]["risk_score"], 0)


if __name__ == "__main__":
    unittest.main()
