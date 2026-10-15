"""Simulate a VIGIL One computer talking to VIGIL Hub (tests, rehearsals, demo fallback).

Pairs with a code (or reuses a token), opens a session marked SIMULATION, then
sends a synthetic live frame every 2 s and a signal with a snapshot now and
then, through the same HubClient and outbox the desktop app uses.

    venv\\Scripts\\python.exe scripts\\one_fake_client.py --hub http://127.0.0.1:8000 --code ABC123 --candidate SV-02 --seconds 120

With ``--video FILE`` it plays a recorded webcam video instead, exactly as the
candidate app does in SERVER analysis mode: the session is a real (not simulated)
one, the video goes up in short segments and the Hub analyses it.

    venv\\Scripts\\python.exe scripts\\one_fake_client.py --join ABC1234 --candidate HS01 --pin 1234 --video webcam.mp4
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exam_monitor.hub_client import HubClient  # noqa: E402

SIGNALS = [("look_away", "medium", "Mắt nhìn ngoài vùng thi", "Mắt ở ngoài vùng an toàn 3,1 giây."),
           ("head_turn", "medium", "Quay đầu khỏi màn hình", "Đầu quay sang trái 2,6 giây."),
           ("suspicious_object", "high", "Vật thể không được phép", "Phát hiện vật thể không được phép: điện thoại.")]


def frame(t: float, candidate: str, alert: bool) -> np.ndarray:
    img = np.full((360, 640, 3), (22, 22, 26), np.uint8)
    cx = int(320 + 40 * math.sin(t / 3))
    cv2.ellipse(img, (cx, 190), (85, 110), 0, 0, 360, (150, 150, 158), -1)
    eye_dx = int(18 * math.sin(t / 2))
    for ex in (cx - 32, cx + 32):
        cv2.ellipse(img, (ex, 165), (20, 11), 0, 0, 360, (235, 235, 235), -1)
        cv2.circle(img, (ex + eye_dx, 165), 6, (20, 20, 20), -1)
    color = (40, 40, 220) if alert else (90, 200, 120)
    cv2.rectangle(img, (cx - 110, 60), (cx + 110, 320), color, 2)
    cv2.putText(img, f"SIMULATION  {candidate}", (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    return img


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", default="http://127.0.0.1:8000")
    parser.add_argument("--code", help="Pairing code from the Hub (chief/admin)")
    parser.add_argument("--token", help="Reuse a device token instead of pairing")
    parser.add_argument("--join", help="Online exam: join code (use with --candidate and --pin)")
    parser.add_argument("--pin", help="Online exam: the candidate's PIN")
    parser.add_argument("--candidate", default="SV-SIM-01")
    parser.add_argument("--exam", default="Kỳ thi trình diễn VIGIL")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--signal-every", type=float, default=15)
    parser.add_argument("--video", help="Recorded webcam video: upload it for the Hub's analysis (SERVER mode)")
    parser.add_argument("--speed", type=float, default=1.0, help="--video playback speed (1 = real time)")
    parser.add_argument("--segment-seconds", type=float, default=0, help="--video segment length (default: exam policy)")
    parser.add_argument("--no-live", action="store_true", help="--video: upload the recording only, no live stream")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="vigil-one-sim-"))
    hub = HubClient(work)
    if args.join:
        joined = hub.join(args.hub, args.join, args.candidate, args.pin or "")
        hub.consent()
        args.exam = joined["exam"]["name"]
        print(f"Joined {args.exam} as {args.candidate}")
    elif args.token:
        hub.configure(args.hub, args.token)
    elif args.code:  # noqa: SIM114
        device = hub.pair(args.hub, args.code, f"Mô phỏng {args.candidate}")
        print(f"Paired as {device['name']}; token: {hub.token}")
    else:
        parser.error("--join, --code or --token is required")
    exam_id = next((e["id"] for e in hub.list_exams() if e.get("name") == args.exam), None)
    if args.video:
        return play_video(hub, args, exam_id, work)
    key = datetime.now().strftime("%Y%m%d-%H%M%S") + "-SIM" + uuid.uuid4().hex[:3].upper()
    hub.open_session(key, {"candidate_ref": args.candidate, "exam_id": exam_id, "exam_name": args.exam,
                           "source_kind": "SIMULATION", "simulation": True,
                           "started_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                           "client_time": datetime.now().astimezone().isoformat(timespec="milliseconds")})
    hub.start()
    started = time.monotonic()
    next_signal, sent = args.signal_every, 0
    try:
        while (elapsed := time.monotonic() - started) < args.seconds:
            alert = elapsed >= next_signal - 2
            img = frame(elapsed, args.candidate, alert)
            if elapsed >= next_signal:
                kind, sev, label, reason = SIGNALS[sent % len(SIGNALS)]
                shot = work / f"ev{sent}.jpg"
                cv2.imwrite(str(shot), img)
                now = datetime.now().astimezone()
                hub.add_event(key, {"event_id": f"{key}-{sent:04d}", "event_type": kind, "severity": sev,
                                    "started_at": now.isoformat(timespec="milliseconds"), "ended_at": now.isoformat(timespec="milliseconds"),
                                    "duration_seconds": 3.0, "session_elapsed_ms": round(elapsed * 1000), "confidence": 0.8,
                                    "label": label, "reason": reason}, str(shot))
                sent += 1
                next_signal += args.signal_every
            ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            hub.update_live(key, {"fps": 30.0, "eye": "NGOÀI • PHẢI" if alert else "TRONG VÙNG", "head": "THẲNG",
                                  "people": 1, "alerts": sent, "calibrated": True, "source_kind": "SIMULATION"},
                            enc.tobytes() if ok else None)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    hub.stop_session(key, {"frame_count": int((time.monotonic() - started) * 30), "average_fps": 30.0, "reason": "SUBMITTED"})
    hub.drain(timeout=5)
    print(f"Session {key}: {sent} signal(s); sync {hub.status().state}, {hub.status().pending} left")
    return 0


def play_video(hub: HubClient, args, exam_id, work: Path) -> int:
    """A recorded video standing in for the webcam: streamed live and recorded, like the candidate app."""
    from exam_monitor.live_stream import LiveStreamer
    from exam_monitor.session_recorder import SessionRecorder

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"Cannot open {args.video}")
        return 2
    try:
        policy = hub.http.get(hub._url("/client/policy"), headers=hub._headers(), timeout=5).json()
    except Exception:
        policy = {}
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    seconds = args.segment_seconds or float(policy.get("segment_seconds") or 10)
    key = datetime.now().strftime("%Y%m%d-%H%M%S") + "-VID" + uuid.uuid4().hex[:3].upper()
    hub.open_session(key, {"candidate_ref": args.candidate, "exam_id": exam_id, "exam_name": args.exam,
                           "source_kind": "VIDEO_FILE", "simulation": False, "analysis": "server",
                           "started_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                           "client_time": datetime.now().astimezone().isoformat(timespec="milliseconds")})
    hub.start()
    started = time.monotonic()
    recorder = SessionRecorder(work / "recordings" / key,
                               lambda seq, path, start_ms, dur: hub.add_video_segment(key, seq, start_ms, dur, path, speech=[]),
                               fps=float(policy.get("video_fps") or 10), width=int(policy.get("video_width") or 640),
                               segment_seconds=seconds, started=started)
    streamer = None if args.no_live else LiveStreamer(hub, key, fps=float(policy.get("analysis_fps") or 5), width=640)
    index, last_live = 0, 0.0
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                break
            video_t = index / src_fps
            index += 1
            if video_t > args.seconds:
                break
            # Pace on the video's clock (scaled by --speed), as a camera would deliver it
            wait = started + video_t / max(0.1, args.speed) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            recorder.push(img, started + video_t / max(0.1, args.speed))
            if streamer is not None:
                streamer.push(img, video_t / max(0.1, args.speed) * 1000.0, None)
            if time.monotonic() - last_live >= 2.0:
                last_live = time.monotonic()
                jpeg = None
                if streamer is None or not streamer.connected:  # the live stream already carries the picture
                    small = cv2.resize(img, (320, max(1, int(img.shape[0] * 320 / img.shape[1]))))
                    ok2, enc = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    jpeg = enc.tobytes() if ok2 else None
                hub.update_live(key, {"fps": round(src_fps, 1), "source_kind": "VIDEO_FILE"}, jpeg)
    except KeyboardInterrupt:
        pass
    if streamer is not None:
        print(f"Live stream: {streamer.sent} frames sent{'' if not streamer.last_error else ' (last error: ' + streamer.last_error + ')'}")
        streamer.close()
    recorder.close()
    hub.stop_session(key, {"frame_count": index, "average_fps": round(src_fps, 2), "reason": "SUBMITTED"})
    hub.drain(timeout=60)
    print(f"Session {key}: {index} frames uploaded for server analysis; sync {hub.status().state}, {hub.status().pending} left")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
