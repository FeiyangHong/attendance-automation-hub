from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .core.app_config import load_app_config

from .settings import RemoteSettings
from .store import RemoteStore


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

JOB_KINDS = {
    "clock_in",
    "clock_out",
    "dry_run_clock_in",
    "dry_run_clock_out",
    "diagnostic",
    "scrcpy_start",
    "task_enable",
    "task_disable",
}
REAL_ACTION_KINDS = {"clock_in", "clock_out"}
SENSITIVE_JOB_KINDS = REAL_ACTION_KINDS | {"task_enable", "task_disable"}
DEVICE_ACTION_KINDS = REAL_ACTION_KINDS | {
    "dry_run_clock_in",
    "dry_run_clock_out",
    "diagnostic",
    "scrcpy_start",
}


class JobRejected(RuntimeError):
    pass


class JobManager:
    def __init__(self, settings: RemoteSettings, store: RemoteStore):
        self.settings = settings
        self.store = store
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.process_lock = threading.Lock()
        self.current_process: subprocess.Popen[str] | None = None
        self.current_job_id: str | None = None

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.store.recover_interrupted_jobs()
        self.stop_event.clear()
        self.worker = threading.Thread(
            target=self._worker_loop,
            name="attendance-hub-job-worker",
            daemon=True,
        )
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.queue.put(None)
        self.cancel_current()
        if self.worker:
            self.worker.join(timeout=8)

    def submit(
        self,
        kind: str,
        requested_by: str,
        remote_address: str,
        *,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if kind not in JOB_KINDS:
            raise JobRejected(f"Unsupported job kind: {kind}")
        self._check_safety(kind)
        if idempotency_key:
            existing = self.store.job_by_idempotency_key(idempotency_key)
            if existing:
                return existing, True
        duplicate = self.store.recent_duplicate(
            kind, self.settings.duplicate_window_seconds
        )
        if duplicate:
            return duplicate, True
        try:
            job = self.store.create_job(
                kind,
                requested_by,
                remote_address,
                parameters,
                idempotency_key,
            )
        except Exception as exc:
            if idempotency_key:
                existing = self.store.job_by_idempotency_key(idempotency_key)
                if existing:
                    return existing, True
            raise JobRejected(f"Unable to create job: {exc}") from exc
        self.queue.put(str(job["id"]))
        return job, False

    def _check_safety(self, kind: str) -> None:
        app_config = load_app_config()
        if kind in DEVICE_ACTION_KINDS and not app_config.device_access_enabled:
            raise JobRejected("Device access is disabled in app_config.json")
        if kind in SENSITIVE_JOB_KINDS and not app_config.real_actions_enabled:
            raise JobRejected("Real attendance actions are disabled in app_config.json")

    def cancel(self, job_id: str) -> bool:
        changed = self.store.request_cancel(job_id)
        with self.process_lock:
            if changed and self.current_job_id == job_id and self.current_process:
                self._terminate_process_tree(self.current_process)
        return changed

    def cancel_current(self) -> None:
        with self.process_lock:
            if self.current_job_id:
                self.store.request_cancel(self.current_job_id)
            if self.current_process:
                self._terminate_process_tree(self.current_process)

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            job_id = self.queue.get()
            if job_id is None:
                return
            try:
                self._run_job(job_id)
            except Exception as exc:
                self.store.finish_job(
                    job_id,
                    "failed",
                    f"Remote job manager error: {exc}",
                    1,
                    "internal_error",
                )

    def _run_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        if self.store.cancel_requested(job_id):
            self.store.finish_job(
                job_id, "cancelled", "Cancelled before start.", 130, "cancelled"
            )
            return
        kind = str(job["kind"])
        if kind == "scrcpy_start":
            self._start_scrcpy(job_id)
            return
        command = self._command_for(kind)
        log_file = self.settings.job_log_dir / f"{job_id}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["ATTENDANCE_HUB_JOB_ID"] = job_id
        process = subprocess.Popen(
            command,
            cwd=self.settings.project_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
        with self.process_lock:
            self.current_process = process
            self.current_job_id = job_id
        self.store.set_job_running(job_id, process.pid, str(log_file))

        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + self.settings.max_job_seconds
        lines: list[str] = []
        timed_out = False
        cancelled = False
        with log_file.open("w", encoding="utf-8", newline="") as stream:
            while process.poll() is None:
                self._drain_output(output_queue, stream, lines)
                if self.store.cancel_requested(job_id) or self.stop_event.is_set():
                    cancelled = True
                    self._terminate_process_tree(process)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate_process_tree(process)
                    break
                time.sleep(0.2)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process)
                process.wait(timeout=5)
            reader.join(timeout=2)
            self._drain_output(output_queue, stream, lines)

        exit_code = process.returncode
        with self.process_lock:
            self.current_process = None
            self.current_job_id = None
        if cancelled:
            self.store.finish_job(
                job_id, "cancelled", "Job was cancelled.", 130, "cancelled"
            )
        elif timed_out:
            self.store.finish_job(
                job_id,
                "failed",
                f"Job exceeded {self.settings.max_job_seconds} seconds.",
                124,
                "timeout",
            )
        elif exit_code == 0:
            self.store.finish_job(job_id, "success", self._summary(lines, True), 0)
        else:
            self.store.finish_job(
                job_id,
                "failed",
                self._summary(lines, False),
                exit_code,
                self._failure_category(lines, exit_code),
            )

    @staticmethod
    def _drain_output(
        output_queue: queue.Queue[str | None],
        stream: Any,
        lines: list[str],
    ) -> None:
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                return
            if line is None:
                return
            stream.write(line)
            stream.flush()
            lines.append(line.rstrip())
            if len(lines) > 200:
                del lines[:50]

    @staticmethod
    def _summary(lines: list[str], success: bool) -> str:
        meaningful = [line.strip() for line in lines if line.strip()]
        if not meaningful:
            return "Job completed successfully." if success else "Job failed without output."
        marker_terms = (
            "completed successfully",
            "already complete",
            "ERROR",
            "FATAL",
            "failed",
            "timed out",
            "SAFETY",
        )
        candidates = [
            line for line in meaningful if any(term.lower() in line.lower() for term in marker_terms)
        ]
        return (candidates[-1] if candidates else meaningful[-1])[-800:]

    @staticmethod
    def _failure_category(lines: list[str], exit_code: int | None) -> str:
        text = "\n".join(lines).lower()
        rules = (
            ("cross_day", ("cross-day", "date changed")),
            ("task_conflict", ("device busy", "another scheduler", "already running")),
            ("timeout", ("timed out", "timeout", "exceeded")),
            ("device_disconnected", ("device offline", "device unauthorized", "adb", "not connected")),
            ("appium_failure", ("appium", "uiautomator2")),
            ("navigation_failure", ("navigation", "navigate")),
            ("confirmation_failure", ("confirmation", "confirm")),
            ("state_recognition_failure", ("candidate", "identify", "page abnormal")),
            ("safety_blocked", ("safety", "disabled")),
        )
        for category, markers in rules:
            if any(marker in text for marker in markers):
                return category
        return "process_failure" if exit_code else ""

    def _command_for(self, kind: str) -> list[str]:
        powershell = str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        common = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
        project = self.settings.project_dir
        commands = {
            "clock_in": common
            + [str(project / "scripts" / "runtime" / "run_morning.ps1"), "-ManualClockIn"],
            "clock_out": common
            + [
                str(project / "scripts" / "runtime" / "run_clock_out.ps1"),
                "-Source",
                "remote",
            ],
            "dry_run_clock_in": common
            + [
                str(project / "scripts" / "runtime" / "run_morning.ps1"),
                "-TestMode",
                "-Immediate",
            ],
            "dry_run_clock_out": common
            + [
                str(project / "scripts" / "runtime" / "run_clock_out.ps1"),
                "-TestMode",
                "-Source",
                "remote",
            ],
            "diagnostic": common
            + [str(project / "scripts" / "operations" / "diagnose_environment.ps1")],
            "task_enable": [powershell, "-NoProfile", "-NonInteractive", "-Command", f"Enable-ScheduledTask -TaskName '{self.settings.task_name.replace(chr(39), chr(39) * 2)}' | Out-Null"],
            "task_disable": [powershell, "-NoProfile", "-NonInteractive", "-Command", f"Disable-ScheduledTask -TaskName '{self.settings.task_name.replace(chr(39), chr(39) * 2)}' | Out-Null"],
        }
        try:
            return commands[kind]
        except KeyError as exc:
            raise JobRejected(f"No command is configured for {kind}") from exc

    def _start_scrcpy(self, job_id: str) -> None:
        powershell = str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(
                self.settings.project_dir
                / "scripts"
                / "operations"
                / "start_scrcpy.ps1"
            ),
        ]
        process = subprocess.Popen(
            command,
            cwd=self.settings.project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        self.store.set_job_running(job_id, process.pid, "")
        try:
            output, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            self.store.finish_job(
                job_id, "failed", "scrcpy launcher timed out.", 124, "timeout"
            )
            return
        summary = (output or "").strip()[-800:]
        if process.returncode == 0:
            self.store.finish_job(job_id, "success", summary or "scrcpy was started.", 0)
        else:
            self.store.finish_job(
                job_id,
                "failed",
                summary or "scrcpy failed to start.",
                process.returncode,
                self._failure_category([summary], process.returncode),
            )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            process.terminate()
