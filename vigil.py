"""VIGIL AI — the one way to start the system.

    python vigil.py                  everything on this computer: VIGIL Hub (website, VIGIL Classroom,
                                     VIGIL One analysis + live view) and the VIGIL One candidate app
    python vigil.py --demo           the same on the demo database (accounts, rooms, an online exam)
    python vigil.py --sim            ... with a simulated camera in VIGIL One (no webcam needed)
    python vigil.py --hub-only       only VIGIL Hub and its website (the proctors' computer)
    python vigil.py --one-only --hub http://<hub-ip>:8000
                                     only the VIGIL One candidate app (a candidate's computer)
    python vigil.py --lan --demo-token <secret>
                                     Hub reachable from the other computers of the network
    python vigil.py --legacy         the earlier VIGIL One desktop (analysis on this computer) instead
                                     of the candidate app; kept for comparison, not for exams

Works the same in PowerShell, cmd and Git Bash.  Ctrl+C stops everything.

If a Hub is already running on the port with older code or another database (left from an
earlier run, before a git pull or a branch switch), it is stopped and started again: an old
Hub silently refuses what a newer VIGIL One sends.
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
WORK_DB = ROOT / "data" / "vigil_proctoring.db"


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


# ---------------------------------------------------------------- the Hub on the port

def hub_health(port: int) -> dict | None:
    """The running Hub's /health answer, or None when nothing answers on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as res:
            if res.status != 200:
                return None
            try:
                return json.loads(res.read().decode("utf-8") or "{}")
            except ValueError:
                return {}
    except OSError:
        return None


def stale_reason(health: dict, build: str, database: str) -> str | None:
    """Why a running Hub cannot be reused (None: it runs this code on this database)."""
    if not health.get("build"):
        return "it runs an older version of VIGIL (no build information)"
    if health["build"] != build:
        return "it was started before the code changed (git pull / branch switch)"
    if health.get("database") and health["database"] != database:
        return f"it uses another database ({health['database']}, this run wants {database})"
    return None


def _port_owner(port: int):
    """The process listening on the port (psutil), or None."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN and conn.pid:
                return psutil.Process(conn.pid)
    except (psutil.Error, OSError):
        return None
    return None


def _is_our_hub(process) -> bool:
    try:
        cmdline = " ".join(process.cmdline()).replace("\\", "/")
    except Exception:
        return False
    return "server.py" in cmdline and (str(ROOT).replace("\\", "/") in cmdline or Path(process.cwd()).resolve() == ROOT)


def _stop_old_hub(port: int, reason: str) -> bool:
    process = _port_owner(port)
    if process is None or not _is_our_hub(process):
        _say(f"Port {port} is used by a VIGIL Hub that cannot be reused: {reason}.")
        if process is None:
            _say("Stop it (close its window, or on Windows: netstat -ano | findstr :%d, then taskkill /PID <pid> /F) "
                 "and run this again, or use another port: --port %d" % (port, port + 1))
        else:
            _say(f"It is not this folder's server (process {process.pid}). Stop it or use another port: --port {port + 1}")
        return False
    _say(f"Stopping the VIGIL Hub left on port {port} (process {process.pid}): {reason} ...")
    try:
        for child in process.children(recursive=True):
            child.terminate()
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    for _ in range(20):
        if hub_health(port) is None:
            return True
        time.sleep(0.5)
    return hub_health(port) is None


def _wait_for_hub(port: int, server: subprocess.Popen, timeout: float = 180) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        if hub_health(port) is not None:
            return True
        time.sleep(0.5)
    return False


def _prepare_demo_db() -> None:
    if DEMO_DB.exists():
        return
    _say(f"Preparing the demo database {DEMO_DB.relative_to(ROOT)} (accounts admin / chief / proctor) ...")
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--database", str(DEMO_DB), "--no-pairing-code"], cwd=ROOT)


# ---------------------------------------------------------------- legacy desktop pairing

def _settings() -> dict:
    try:
        return json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _pair_legacy_app(port: int) -> None:
    """The earlier desktop app connects with a device token: give it one for the local Hub."""
    settings = _settings()
    hub_url = f"http://127.0.0.1:{port}"
    if settings.get("hub_token") and settings.get("hub_url") == hub_url:
        request = urllib.request.Request(f"{hub_url}/api/v1/one/client/me",
                                         headers={"Authorization": f"Bearer {settings['hub_token']}"})
        try:
            with urllib.request.urlopen(request, timeout=3) as res:
                if res.status == 200:
                    return
        except OSError:
            pass
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
    except Exception as exc:
        _say(f"Could not pair the legacy app automatically ({exc}); pair it in its Cài đặt page.")
        return
    settings.update({"hub_url": hub.base_url, "hub_token": hub.token})
    path = ROOT / "data" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _stop(process: subprocess.Popen | None, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    _say(f"Stopping {name} ...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vigil.py",
        description="Start VIGIL AI. With no option: VIGIL Hub (website, Classroom, One analysis) and the VIGIL One "
                    "candidate app on this computer.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hub-only", action="store_true", help="Only VIGIL Hub and its website")
    mode.add_argument("--one-only", action="store_true", help="Only the VIGIL One candidate app (use --hub)")
    parser.add_argument("--hub", help="--one-only: address of VIGIL Hub, e.g. http://192.168.1.10:8000")
    parser.add_argument("--demo", action="store_true", help="Use the demo database data/vigil_demo.db (prepared if missing)")
    parser.add_argument("--sim", action="store_true", help="VIGIL One with a simulated camera (no webcam needed)")
    parser.add_argument("--legacy", action="store_true", help="The earlier VIGIL One desktop instead of the candidate app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true", help="Hub reachable from other computers (needs --demo-token)")
    parser.add_argument("--demo-token", default=os.getenv("VIGIL_DEMO_TOKEN"), help="Token for LAN access to the website/API")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the website")
    # Earlier names, still accepted
    parser.add_argument("--candidate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--one-sim", action="store_true", help=argparse.SUPPRESS)
    return parser


def one_command(args, port: int) -> list[str]:
    """How main.py is started for VIGIL One."""
    if args.legacy:
        return [sys.executable, str(ROOT / "main.py"), *(["--demo"] if args.sim else [])]
    command = [sys.executable, str(ROOT / "main.py"), "--sim" if args.sim else "--candidate"]
    hub = args.hub or (None if args.one_only else f"http://127.0.0.1:{port}")
    return command + (["--hub", hub] if hub else [])


def main(argv: list[str] | None = None) -> int:
    _relaunch_in_project_venv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = build_parser()
    args = parser.parse_args(argv)
    args.sim = args.sim or args.one_sim
    if args.lan and not args.demo_token:
        parser.error("--lan needs --demo-token (or VIGIL_DEMO_TOKEN): other computers must not reach an open API")
    if args.one_only and not args.hub:
        _say("--one-only without --hub: VIGIL One uses the Hub address saved on this computer (vigil-one.json).")
    if args.demo:
        os.environ["DATABASE_URL"] = f"sqlite:///{DEMO_DB.as_posix()}"
        _prepare_demo_db()
    database = (DEMO_DB if args.demo else Path(os.environ.get("DATABASE_URL", "").split("///", 1)[-1] or WORK_DB)).name

    server = one = None
    try:
        if not args.one_only:
            from vigil_build import fingerprint

            health = hub_health(args.port)
            reason = stale_reason(health, fingerprint(), database) if health is not None else None
            if health is not None and reason is not None and not _stop_old_hub(args.port, reason):
                return 1
            if health is not None and reason is None:
                _say(f"VIGIL Hub is already running on port {args.port} with this code and database: using it.")
            else:
                cmd = [sys.executable, str(ROOT / "server.py"), "--port", str(args.port)]
                if args.lan:
                    cmd += ["--host", "0.0.0.0", "--demo-token", args.demo_token]
                _say(f"Starting VIGIL Hub on http://localhost:{args.port} ({database}) ...")
                server = subprocess.Popen(cmd, cwd=ROOT, env=os.environ.copy())
                if not _wait_for_hub(args.port, server):
                    _say("VIGIL Hub did not start; see the messages above.")
                    return 1
            if not args.no_browser:
                webbrowser.open(f"http://localhost:{args.port}/")
            if args.legacy and not args.hub_only:
                _pair_legacy_app(args.port)

        if not args.hub_only:
            _say("Starting VIGIL One " + ("(legacy desktop) ..." if args.legacy else
                                          "(candidate app: join code + candidate code + PIN) ..."))
            one = subprocess.Popen(one_command(args, args.port), cwd=ROOT)

        if server is not None or not args.one_only:
            _say(f"Running. Website: http://localhost:{args.port}  |  Ctrl+C here stops everything.")
            if args.lan:
                _say(f"Candidates' computers: python vigil.py --one-only --hub http://<this computer's IP>:{args.port}")
        while True:
            if server is not None and server.poll() is not None:
                _say("VIGIL Hub stopped.")
                break
            if one is not None and one.poll() is not None:
                one = None
                if server is None and args.one_only:
                    break
                _say("VIGIL One closed; the Hub keeps running (Ctrl+C to stop it).")
            if server is None and one is None:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _stop(one, "VIGIL One")
        _stop(server, "VIGIL Hub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
