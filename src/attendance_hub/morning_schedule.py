from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .core.morning_window import MorningWindow, load_morning_window, save_morning_window
from .paths import PROJECT_DIR

_save_lock = threading.Lock()


def update_morning_window(
    start: str,
    end: str,
    project_dir: Path = PROJECT_DIR,
    task_name: str = "Attendance Hub Morning Clock-In",
) -> dict[str, Any]:
    """Persist a validated window and update an existing task without enabling it."""
    window = MorningWindow(start=start, end=end)
    config_path = project_dir / "config" / "morning_window.json"
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    with _save_lock:
        previous = load_morning_window(config_path)
        save_morning_window(window, config_path)
        try:
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(project_dir / "scripts" / "setup" / "install_daily_task.ps1"),
                    "-TaskName",
                    task_name,
                    "-UpdateOnly",
                ],
                cwd=project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or "Unable to update the daily task."
                )
        except Exception:
            save_morning_window(previous, config_path)
            raise
    return {"window": window.as_dict(), "message": completed.stdout.strip()}
