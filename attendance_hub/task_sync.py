from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from app_config import load_app_config

from .settings import RemoteSettings


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def synchronize_plan_tasks(
    settings: RemoteSettings,
    target_date: str,
    clock_in: str,
    clock_out: str,
) -> dict[str, Any]:
    """Update only this successor's one-time tasks after production cutover."""
    app_config = load_app_config(settings.app_config_file)
    if not (app_config.device_access_enabled and app_config.real_actions_enabled):
        return {
            "status": "skipped",
            "message": "System task sync remains disabled until explicit production cutover.",
        }

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
        str(settings.project_dir / "sync_daily_plan.ps1"),
        "-PlanDate",
        target_date,
        "-ClockIn",
        clock_in,
        "-ClockOut",
        clock_out,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=settings.project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return {"status": "failed", "message": str(exc)}
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )[-2000:]
    return {
        "status": "success" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "message": output or "Plan task synchronization completed.",
    }
