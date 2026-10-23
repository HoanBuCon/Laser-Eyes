"""Per-frame analysis trace (Review debug overlay)."""

from types import SimpleNamespace

from exam_monitor.models import AnalysisResult
from storage import analysis_trace


def test_classroom_record_marks_the_seat_of_each_box(tmp_path):
    seated = SimpleNamespace(bbox=(10, 20, 110, 220))
    roaming = SimpleNamespace(bbox=(300.4, 40, 380, 260))
    occupancies = {"S07": SimpleNamespace(assigned_detection=seated), "S08": SimpleNamespace(assigned_detection=None)}
    record = analysis_trace.classroom_record(1234.56, [seated, roaming], occupancies)
    assert record == {"t": 1234.6, "p": [[10, 20, 110, 220, "S07"], [300, 40, 380, 260, None]]}


def test_one_record_keeps_face_gaze_and_objects():
    result = AnalysisResult(timestamp=1.0, face_count=1, eye_gaze_x=0.42, eye_gaze_y=-0.1, eyes_outside_zone=True,
                            eye_direction="left", head_direction="center", calibrated=True,
                            face_box=(100, 80, 60, 70), object_detections=[("cell phone", 0.81, (5, 6, 7, 8))])
    record = analysis_trace.one_record(500, result, (640, 480))
    assert record["fb"] == [[100, 80, 60, 70]]
    assert (record["gx"], record["gy"], record["out"], record["eye"]) == (0.42, -0.1, True, "left")
    assert record["obj"] == [["cell phone", 0.81, [5, 6, 7, 8]]]
    assert (record["w"], record["h"], record["n"]) == (640, 480, 1)


def test_writer_and_window_read(tmp_path):
    path = tmp_path / "frames.jsonl"
    writer = analysis_trace.TraceWriter(path)
    for t in range(0, 10000, 500):
        writer.write({"t": float(t), "p": []})
    writer.close()
    analysis_trace.append(path, [{"t": 10000.0, "p": [[1, 2, 3, 4, None]]}])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"t": 10500, "p": [')  # a line cut short by a crash
    window = analysis_trace.read_window(path, 2000, 4000)
    assert [r["t"] for r in window] == [2000.0, 2500.0, 3000.0, 3500.0, 4000.0]
    assert analysis_trace.read_window(path, 9900, 20000)[-1]["p"] == [[1, 2, 3, 4, None]]
    assert analysis_trace.read_window(tmp_path / "missing.jsonl", 0, 1) == []
