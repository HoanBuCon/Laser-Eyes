"""Start the whole VIGIL AI system from the workspace root.

    python vigil.py                 VIGIL Hub (web + Classroom) and VIGIL One on this computer
    python vigil.py --demo          same, on the demo database (data/vigil_demo.db, prepared if missing)
    python vigil.py --hub-only      only the server and its website
    python vigil.py --one-only      only the VIGIL One desktop (connects to its configured Hub)
    python vigil.py --one-sim       VIGIL One starts a simulated session right away
    python vigil.py --candidate     VIGIL One in candidate mode (online exam: join code + candidate code + PIN)
    python vigil.py --lan           Hub reachable from other computers (needs --demo-token or VIGIL_DEMO_TOKEN)

Works the same in PowerShell and cmd (no ".\\" needed).  Ctrl+C stops everything.
When both run on this computer and VIGIL One is not paired yet, it is paired
with the local Hub automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO_DB = ROOT / "data" / "vigil_demo.db"


def _relaunch_in_project_venv() -> None:
    """Use the project's venv even when started with another Python (same rule as main.py)."""
    if os.environ.get("VIGIL_VENV_REEXEC") == "1":
        return
    for name in ("venv", ".venv"):
        venv_dir = ROOT / name
        python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.exists():
            try:
                if Path(sys.prefix).resolve() == venv_dir.resolve():
                    return
            except OSError:
                pass
            env = {**os.environ, "VIGIL_VENV_REEXEC": "1"}
            raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT, env=env))
    print("[VIGIL] No venv found: run setup_env.bat first (using the current Python for now).")


def _say(text: str) -> None:
    print(f"[VIGIL] {text}", flush=True)


def _hub_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as res:
            return res.status == 200
    except OSError:
        return False


def _wait_for_hub(port: int, server: subprocess.Popen, timeout: float = 120) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        if _hub_ready(port):
            return True
        time.sleep(0.5)
    return False


def _prepare_demo_db() -> None:
    if DEMO_DB.exists():
        return
    _say(f"Preparing the demo database {DEMO_DB.relative_to(ROOT)} (accounts admin / chief / proctor) ...")
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--database", str(DEMO_DB), "--no-pairing-code"], cwd=ROOT)


def _settings() -> dict:
    try:
        return json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _pair_one_with_local_hub(port: int) -> None:
    """Give VIGIL One a device token for the Hub on this computer, if it has none."""
    settings = _settings()
    hub_url = f"http://127.0.0.1:{port}"
    if settings.get("hub_token") and settings.get("hub_url") == hub_url:
        request = urllib.request.Request(f"{hub_url}/api/v1/one/client/me",
                                         headers={"Authorization": f"Bearer {settings['hub_token']}"})
        try:
            with urllib.request.urlopen(request, timeout=3) as res:
                if res.status == 200:
                    return  # already paired with this Hub (and this database)
        except OSError:
            pass  # unknown to this Hub (e.g. other database) or revoked: pair again
    try:
        from exam_monitor.hub_client import HubClient
        from storage.database import SessionLocal
        from storage.one_service import create_pairing_code

        db = SessionLocal()
        try:
            code, _ = create_pairing_code(db, "vigil.py (same computer)")
        finally:
            db.close()
        with tempfile.TemporaryDirectory() as scratch:
            hub = HubClient(Path(scratch))
            hub.pair(hub_url, code, "VIGIL One (máy chủ)")
    except Exception as exc:  # the system still runs; pairing can be done in Settings
        _say(f"Could not pair VIGIL One automatically ({exc}); pair it in VIGIL One > Cài đặt.")
        return
    settings.update({"hub_url": hub.base_url, "hub_token": hub.token})
    path = ROOT / "data" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"VIGIL One paired with the local Hub ({hub_url}).")


def _stop(process: subprocess.Popen | None, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    _say(f"Stopping {name} ...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    _relaunch_in_project_venv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description="Start VIGIL AI: VIGIL Hub (web, Classroom) and VIGIL One.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hub-only", action="store_true", help="Only the server and its website")
    mode.add_argument("--one-only", action="store_true", help="Only the VIGIL One desktop")
    parser.add_argument("--demo", action="store_true", help="Use the demo database data/vigil_demo.db")
    parser.add_argument("--one-sim", action="store_true", help="VIGIL One starts a simulated session")
    parser.add_argument("--candidate", action="store_true", help="VIGIL One in candidate mode (online exam)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true", help="Listen on all network interfaces (needs a demo token)")
    parser.add_argument("--demo-token", default=os.getenv("VIGIL_DEMO_TOKEN"), help="Token for LAN access to the web/API")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the website")
    args = parser.parse_args()

    if args.demo:
        os.environ["DATABASE_URL"] = f"sqlite:///{DEMO_DB.as_posix()}"
        _prepare_demo_db()
    if args.lan and not args.demo_token:
        parser.error("--lan needs --demo-token (or VIGIL_DEMO_TOKEN): other computers must not reach an open API")

    server = one = None
    try:
        if not args.one_only:
            if _hub_ready(args.port):
                _say(f"A VIGIL Hub is already running on port {args.port}: using it, with the code and options it was started with.")
                _say("If it is left over from an earlier run (old code, another --demo-token / --lan), close that window "
                     f"or stop it, then run this again; or use a free port: --port {args.port + 1}")
            else:
                cmd = [sys.executable, str(ROOT / "server.py"), "--port", str(args.port)]
                if args.lan:
                    cmd += ["--host", "0.0.0.0", "--demo-token", args.demo_token]
                _say(f"Starting VIGIL Hub on http://localhost:{args.port}"
                     + (f" ({os.environ['DATABASE_URL']})" if args.demo else "") + " ...")
                server = subprocess.Popen(cmd, cwd=ROOT, env=os.environ.copy())
                if not _wait_for_hub(args.port, server):
                    _say("VIGIL Hub did not start; see the messages above.")
                    return 1
            if not args.no_browser:
                webbrowser.open(f"http://localhost:{args.port}/")
            if not args.hub_only and not args.candidate:
                _pair_one_with_local_hub(args.port)  # candidates join with a code instead

        if not args.hub_only:
            _say("Starting VIGIL One ...")
            one_args = ["--candidate", "--hub", f"http://127.0.0.1:{args.port}"] if args.candidate else []
            if args.one_sim:
                one_args = ["--sim", "--hub", f"http://127.0.0.1:{args.port}"] if args.candidate else ["--demo"]
            one = subprocess.Popen([sys.executable, str(ROOT / "main.py"), *one_args], cwd=ROOT)

        _say("Running. Website: http://localhost:%d  |  Ctrl+C here stops everything." % args.port)
        while True:
            if server is not None and server.poll() is not None:
                _say("VIGIL Hub stopped.")
                break
            if one is not None and one.poll() is not None:
                one = None
                if server is None:
                    break
                _say("VIGIL One closed; the Hub keeps running (Ctrl+C to stop it).")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _stop(one, "VIGIL One")
        _stop(server, "VIGIL Hub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
