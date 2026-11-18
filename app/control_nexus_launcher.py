from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    if (base / "web_ui.py").exists():
        return base
    if (base.parent / "web_ui.py").exists():
        return base.parent
    return base


ROOT = _app_dir()
RUNTIME = ROOT / ".runtime_control"
PID_FILE = RUNTIME / "windows_backend.pid"
LOG_FILE = RUNTIME / "windows_backend.log"
LAUNCHER_LOG = RUNTIME / "windows_launcher.log"
_VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
# The Windows EXE launcher may start this script with a system Python when a
# local .venv isn't present. In that case reuse the current interpreter for
# the backend and child bot processes.
PYTHONW = _VENV_PYTHONW if _VENV_PYTHONW.exists() else Path(sys.executable).resolve()
WEB_UI = ROOT / "web_ui.py"
SECRET_FILE = ROOT / ".webui_secret"
BROWSER_PROFILE = RUNTIME / "browser_profile"


def _log(text: str) -> None:
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with LAUNCHER_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {text}\n")
    except Exception:
        pass


def _message(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
        except Exception:
            pass
    print(f"{title}: {text}")


def _backend_ready(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _start_backend() -> subprocess.Popen[bytes] | None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if _backend_ready(0.5):
        _log("Backend already running; attaching launcher lifecycle to it")
        return None
    if not PYTHONW.exists():
        raise RuntimeError("Python was not found. Install Python 3.11+ or create .venv\\Scripts\\pythonw.exe in the Control Nexus folder.")
    if not WEB_UI.exists():
        raise RuntimeError("web_ui.py was not found in the Control Nexus folder.")

    log = open(LOG_FILE, "ab", buffering=0)
    flags = 0
    startupinfo = None
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.Popen(
        [str(PYTHONW), str(WEB_UI), "--host", HOST, "--port", str(PORT)],
        cwd=str(ROOT),
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        startupinfo=startupinfo,
        close_fds=(os.name != "nt"),
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _log(f"Started backend PID {proc.pid}")

    deadline = time.time() + 25
    while time.time() < deadline:
        if _backend_ready(0.75):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"Control Nexus backend stopped during startup. Check {LOG_FILE.name}.")
        time.sleep(0.35)
    raise RuntimeError(f"Control Nexus backend did not become ready. Check {LOG_FILE.name}.")


def _browser_candidates() -> list[Path]:
    env = os.environ
    raw = [
        Path(env.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(env.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(env.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(env.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    return [p for p in raw if str(p) and p.exists()]


def _open_app_window() -> subprocess.Popen[bytes] | None:
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    for browser in _browser_candidates():
        try:
            args = [
                str(browser),
                f"--app={URL}",
                f"--user-data-dir={BROWSER_PROFILE}",
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
            ]
            if "edge" in browser.name.lower():
                args.append("--disable-features=msEdgeFirstRunExperience")
            proc = subprocess.Popen(
                args,
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log(f"Opened app window with {browser.name}, PID {proc.pid}")
            return proc
        except OSError as exc:
            _log(f"Browser launch failed for {browser}: {exc}")
            continue
    webbrowser.open(URL)
    _log("Opened Control Nexus in default browser; window-close detection is unavailable")
    return None


def _secret() -> str:
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _api_json(path: str, *, method: str = "GET", body: bytes | None = None, auth: bool = False, timeout: float = 2.0) -> dict:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        token = _secret()
        if token:
            headers["X-WebUI-Token"] = token
    request = urllib.request.Request(URL + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def _main_bot_running() -> bool:
    try:
        data = _api_json("/api/status", timeout=2.0)
        return bool((data.get("status") or {}).get("running"))
    except Exception:
        return False


def _request_graceful_bot_stop() -> None:
    if not _main_bot_running():
        return
    try:
        _api_json("/api/stop", method="POST", body=b"{}", auth=True, timeout=3.0)
        _log("App window closed: requested graceful bot stop (no new entries)")
    except urllib.error.HTTPError as exc:
        _log(f"Graceful bot stop HTTP error: {exc.code}")
    except Exception as exc:
        _log(f"Graceful bot stop request failed: {exc}")


def _wait_for_managed_positions() -> None:
    if not _main_bot_running():
        return
    _log("Waiting for managed positions to close before backend shutdown")
    _request_graceful_bot_stop()
    while _backend_ready(1.0) and _main_bot_running():
        time.sleep(1.0)
    _log("Main bot process has finished; backend can shut down")


def _backend_pid() -> int | None:
    try:
        value = int(PID_FILE.read_text(encoding="utf-8").strip())
        return value if value > 0 else None
    except Exception:
        return None


def _stop_backend_tree() -> None:
    pid = _backend_pid()
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        else:
            os.kill(pid, 15)
        _log(f"Stopped backend process tree PID {pid}")
    except Exception as exc:
        _log(f"Backend shutdown failed for PID {pid}: {exc}")
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _wait_for_app_close(browser_proc: subprocess.Popen[bytes] | None) -> None:
    if browser_proc is None:
        # Windows normally has Edge. With an unsupported default-browser fallback,
        # keep the lifecycle launcher alive until the backend itself is stopped.
        while _backend_ready(1.0):
            time.sleep(1.0)
        return
    while browser_proc.poll() is None:
        time.sleep(0.5)
    _log("Control Nexus app window/browser process closed")


def main() -> int:
    backend_started = False
    try:
        _start_backend()
        backend_started = True
        browser_proc = _open_app_window()
        _wait_for_app_close(browser_proc)
        _wait_for_managed_positions()
        return 0
    except Exception as exc:
        _log(f"Launcher error: {exc}")
        _message("Control Nexus", str(exc))
        return 1
    finally:
        if backend_started or _backend_ready(0.5):
            # Normal app-window closure drains managed positions first. This
            # fallback also avoids abandoning a live bot if the launcher hits
            # an unexpected error during shutdown.
            if _main_bot_running():
                _wait_for_managed_positions()
            _stop_backend_tree()


if __name__ == "__main__":
    raise SystemExit(main())
