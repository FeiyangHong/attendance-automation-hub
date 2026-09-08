from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .core.app_config import load_app_config
from .core.attendance_history import recent_records
from .core.daily_plans import day_plan

from .calendar_service import day_status, next_execution_date
from .settings import RemoteSettings


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _find_scrcpy_tool(name: str) -> str | None:
    direct = shutil.which(name)
    if direct:
        return direct
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    pattern = f"Genymobile.scrcpy_*/*/{name}.exe"
    matches = sorted(local.glob(pattern), reverse=True)
    return str(matches[0]) if matches else None


def _find_tailscale() -> str | None:
    direct = shutil.which("tailscale")
    if direct:
        return direct
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Tailscale" / "tailscale.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Tailscale" / "tailscale.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale" / "tailscale.exe",
    ]
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Services\Tailscale",
            ) as key:
                image_path = str(winreg.QueryValueEx(key, "ImagePath")[0]).strip()
            if image_path.startswith('"'):
                executable = image_path.split('"', 2)[1]
            else:
                executable = image_path.split(" ", 1)[0]
            candidates.insert(
                0,
                Path(os.path.expandvars(executable)).parent / "tailscale.exe",
            )
        except (OSError, ValueError):
            pass
    return next((str(path) for path in candidates if path.is_file()), None)


def _device_status() -> dict[str, Any]:
    config = load_app_config()
    result: dict[str, Any] = {
        "configured": bool(config.device_udid),
        "access_enabled": config.device_access_enabled,
        "real_actions_enabled": config.real_actions_enabled,
        "udid": config.display_udid,
        "state": "disabled" if not config.device_access_enabled else "unknown",
    }
    if not config.device_access_enabled:
        return result
    adb = _find_scrcpy_tool("adb")
    if not adb:
        result.update(state="missing", error="ADB was not found")
        return result
    try:
        completed = subprocess.run(
            [adb, "-s", config.device_udid, "get-state"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        output = (completed.stdout or completed.stderr).strip()
        result.update(
            state="device" if completed.returncode == 0 and output == "device" else "offline",
            detail=output,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(state="error", error=str(exc))
    return result


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def _query_task(task_name: str) -> dict[str, Any]:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    safe_name = task_name.replace("'", "''")
    script = f"""
$task = Get-ScheduledTask -TaskName '{safe_name}' -ErrorAction SilentlyContinue
if (-not $task) {{ @{{ Exists=$false }} | ConvertTo-Json -Compress; exit 0 }}
$info = Get-ScheduledTaskInfo -TaskName '{safe_name}'
@{{
  Exists=$true
  State=[string]$task.State
  Enabled=[bool]$task.Settings.Enabled
  NextRunTime=if($info.NextRunTime -gt [datetime]::MinValue){{$info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}else{{''}}
  LastRunTime=if($info.LastRunTime -gt [datetime]::MinValue){{$info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}else{{''}}
  LastTaskResult=[int]$info.LastTaskResult
  Action=[string]$task.Actions[0].Arguments
}} | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            return {"exists": False, "error": completed.stderr.strip()}
        return json.loads(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"exists": False, "error": str(exc)}


def _tailscale_status() -> dict[str, Any]:
    executable = _find_tailscale()
    if not executable:
        return {"installed": False, "online": False}
    try:
        completed = subprocess.run(
            [executable, "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            return {"installed": True, "online": False, "error": completed.stderr.strip()}
        data = json.loads(completed.stdout)
        self_record = data.get("Self", {})
        return {
            "installed": True,
            "online": bool(self_record.get("Online", False)),
            "dns_name": str(self_record.get("DNSName", "")).rstrip("."),
            "addresses": self_record.get("TailscaleIPs", []),
        }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"installed": True, "online": False, "error": str(exc)}


def status_snapshot(settings: RemoteSettings) -> dict[str, Any]:
    project = settings.project_dir
    today = date.today()
    morning_plan = _read_json(project / "config" / "morning_current_plan.json")
    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "device": _device_status(),
        "appium": {
            "listening": _port_open("127.0.0.1", 4723),
            "address": "127.0.0.1:4723",
        },
        "task": _query_task(settings.task_name),
        "remote_service_task": _query_task(settings.remote_service_task_name),
        "tailscale": _tailscale_status(),
        "scrcpy": {"installed": bool(_find_scrcpy_tool("scrcpy"))},
        "today": {
            "date": today.isoformat(),
            "calendar": day_status(today),
            "plan": day_plan(today),
        },
        "morning_plan": morning_plan,
        "next_execution": next_execution_date(morning_plan=morning_plan),
        "clock_out_result": _read_json(project / "config" / "clock_out_last_result.json"),
        "recent_attendance": recent_records(7),
    }
