"""Load test for online exams: N simulated VIGIL One candidates against one Hub.

    venv\\Scripts\\python.exe scripts\\one_load_test.py --hub http://127.0.0.1:8000 --join CODE --prefix LT --count 30 --pin 9999 --seconds 120

Starts N copies of scripts/one_fake_client.py (each: live picture every 2-5 s as the Hub asks, a signal
with a snapshot every 20 s), samples the Hub's CPU / memory and the response time of the /online roster
API every 5 s, and checks at the end that every signal sent arrived and nobody was wrongly flagged as
disconnected.  Prints a summary and writes JSON next to --report.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(url: str) -> tuple[float, dict]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=15) as res:
        data = json.loads(res.read())
    return (time.perf_counter() - started) * 1000, data


def hub_process(port: int):
    import psutil

    # On Windows a venv's python.exe is a small launcher; the real interpreter is its child process
    matches = [p for p in psutil.process_iter(["cmdline"]) if "server.py" in " ".join(p.info["cmdline"] or [])
               and f"--port {port}" in " ".join(p.info["cmdline"] or [])]
    candidates = matches + [c for p in matches for c in p.children(recursive=True)]
    return max(candidates, key=lambda p: p.memory_info().rss, default=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", default="http://127.0.0.1:8000")
    parser.add_argument("--join", required=True)
    parser.add_argument("--exam-id", required=True)
    parser.add_argument("--prefix", default="LT")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--pin", default="9999")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--signal-every", type=float, default=20)
    parser.add_argument("--report", default="one_load_test.json")
    args = parser.parse_args()

    port = int(args.hub.rsplit(":", 1)[-1].split("/")[0])
    hub = hub_process(port)
    clients = []
    for i in range(1, args.count + 1):
        clients.append(subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "one_fake_client.py"), "--hub", args.hub, "--join", args.join,
             "--candidate", f"{args.prefix}{i:03d}", "--pin", args.pin, "--seconds", str(args.seconds),
             "--signal-every", str(args.signal_every)],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, cwd=ROOT))
        time.sleep(0.15)
    samples, lost_seen = [], 0
    started = time.monotonic()
    if hub:
        hub.cpu_percent(None)
    while any(c.poll() is None for c in clients):
        time.sleep(5)
        ms, roster = get(f"{args.hub}/api/v1/one/exams/{args.exam_id}/roster")
        counts = roster.get("counts", {})
        lost_seen = max(lost_seen, counts.get("LOST", 0))
        sample = {"t": round(time.monotonic() - started), "roster_ms": round(ms, 1), "live": counts.get("LIVE", 0),
                  "lost": counts.get("LOST", 0)}
        if hub:
            sample["hub_cpu_pct"] = hub.cpu_percent(None)
            sample["hub_rss_mb"] = round(hub.memory_info().rss / 1e6)
        samples.append(sample)
        print(sample, flush=True)
    time.sleep(3)
    _, roster = get(f"{args.hub}/api/v1/one/exams/{args.exam_id}/roster")
    _, queue = get(f"{args.hub}/api/v1/proctor/review-queue?product=ONE&status=ALL&limit=500")
    ours = [i for i in queue["items"] if str(i.get("subject_ref", "")).startswith(args.prefix)]
    offline = [i for i in ours if i.get("primary_signal") == "ONE_CLIENT_OFFLINE"]
    expected = args.count * int((args.seconds - 1) // args.signal_every)  # a signal at t = every, 2*every, ... < seconds
    summary = {
        "candidates": args.count,
        "seconds": args.seconds,
        "submitted": roster["counts"].get("SUBMITTED", 0),
        "signals_received": len([i for i in ours if i.get("primary_signal") != "ONE_CLIENT_OFFLINE"]),
        "signals_expected_about": expected,
        "evidence_ready": len([i for i in ours if i.get("evidence_status") == "READY"]),
        "false_offline_signals": len(offline),
        "max_lost_seen": lost_seen,
        "roster_ms_median": statistics.median(s["roster_ms"] for s in samples) if samples else None,
        "roster_ms_max": max((s["roster_ms"] for s in samples), default=None),
        "hub_cpu_pct_median": statistics.median(s["hub_cpu_pct"] for s in samples if "hub_cpu_pct" in s) if hub and samples else None,
        "hub_cpu_pct_max": max((s.get("hub_cpu_pct", 0) for s in samples), default=None),
        "hub_rss_mb_max": max((s.get("hub_rss_mb", 0) for s in samples), default=None),
    }
    Path(args.report).write_text(json.dumps({"summary": summary, "samples": samples}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
