from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from ..config import ExperimentConfig
from ..runner import make_trial_plan


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_command(pid: int) -> str | None:
    command = (["powershell", "-NoProfile", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"]
               if sys.platform == "win32" else ["ps", "-p", str(pid), "-o", "command="])
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


class RunManager:
    def __init__(self, results_dir: Path):
        self.root = results_dir.expanduser().resolve()
        self.children: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def run_dir(self, run_id: str) -> Path:
        if not run_id or any(char in run_id for char in ("/", "\\", "\0")):
            raise ValueError("Invalid run folder")
        path = (self.root / run_id).resolve()
        if path.parent != self.root:
            raise ValueError("Invalid run folder")
        return path

    def pid(self, run_id: str) -> int | None:
        child = self.children.get(run_id)
        if child is not None:
            if child.poll() is None:
                return child.pid
            self.children.pop(run_id, None)
            return None
        rd = self.run_dir(run_id)
        status = read_json(rd / "status.json", {})
        if status.get("state") in {"completed", "stopped", "error", "interrupted"}:
            return None
        metadata = read_json(rd / "dashboard-process.json", {})
        pid = status.get("pid") or metadata.get("pid")
        if type(pid) is not int or not process_alive(pid):
            return None
        command = process_command(pid)
        if command is not None and (run_id not in command or not any(
            name in command for name in ("sales_experiment.runner", "sales_experiment.dashboard.worker")
        )):
            return None
        return pid

    def is_running(self, run_id: str) -> bool:
        return self.pid(run_id) is not None

    def start(self, cfg: ExperimentConfig) -> str:
        cfg.validate()
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise ValueError("Set OPENAI_API_KEY in your environment file before starting an experiment")
        with self.lock:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            rd = self.run_dir(run_id)
            rd.mkdir(parents=True, exist_ok=False)
            cfg.write_json(rd / "config.json")
            make_trial_plan(cfg, rd)
            (rd / "status.json").write_text(json.dumps({
                "state": "starting", "completed_trials": 0, "total_trials": cfg.total_trials,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }), encoding="utf-8")
            self._spawn(run_id, resume=False)
            return run_id

    def resume(self, run_id: str) -> None:
        with self.lock:
            if self.is_running(run_id):
                raise ValueError("This experiment is already running")
            rd = self.run_dir(run_id)
            ExperimentConfig.from_json_file(rd / "config.json")
            if not (rd / "trial_plan.csv").is_file():
                raise ValueError("The saved trial plan is missing")
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise ValueError("Set OPENAI_API_KEY in your environment file before resuming")
            self._spawn(run_id, resume=True)

    def _spawn(self, run_id: str, *, resume: bool) -> None:
        rd = self.run_dir(run_id)
        (rd / ".stop-requested").unlink(missing_ok=True)
        status = read_json(rd / "status.json", {})
        status.update(state="starting", pid=None, running_trials=0, active_trials=[], ended_at=None)
        (rd / "status.json").write_text(json.dumps(status), encoding="utf-8")
        command = [sys.executable, "-u", "-m", "sales_experiment.dashboard.worker", "--run-dir", str(rd)]
        if resume:
            command.append("--resume")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        try:
            with (rd / "experiment.log").open("ab") as log:
                log.write(f"Experiment {'resumed' if resume else 'started'} at {datetime.now().astimezone().isoformat(timespec='seconds')}\n".encode())
                log.flush()
                child = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[2]),
                                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **options)
            self.children[run_id] = child
            (rd / "dashboard-process.json").write_text(json.dumps({"pid": child.pid}), encoding="utf-8")
        except OSError as exc:
            status = read_json(rd / "status.json", {})
            status.update(state="error", error=str(exc))
            (rd / "status.json").write_text(json.dumps(status), encoding="utf-8")
            raise

    def stop(self, run_id: str) -> None:
        with self.lock:
            if self.pid(run_id) is None:
                raise ValueError("This experiment is not running")
            (self.run_dir(run_id) / ".stop-requested").touch()
