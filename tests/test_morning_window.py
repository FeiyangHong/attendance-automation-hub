import subprocess
import os
import json
from datetime import datetime
from pathlib import Path

import pytest

from attendance_hub import calendar_service, morning_schedule
from attendance_hub.core.morning_window import (
    MorningWindow,
    load_morning_window,
    save_morning_window,
)


@pytest.mark.parametrize(
    "start,end",
    [
        ("9:00", "09:30"),
        ("25:00", "25:30"),
        ("09:30", "09:00"),
        ("09:00", "09:00"),
        ("23:00", "23:55"),
        ("23:00", "00:30"),
    ],
)
def test_invalid_window_is_rejected(start, end):
    with pytest.raises(ValueError):
        MorningWindow(start, end)


def test_window_defaults_and_persistence(tmp_path):
    path = tmp_path / "window.json"
    assert load_morning_window(path) == MorningWindow()
    save_morning_window(MorningWindow("08:10", "08:40"), path)
    assert load_morning_window(path) == MorningWindow("08:10", "08:40")


def test_window_sync_updates_only_existing_task_and_rolls_back_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "config" / "morning_window.json"
    save_morning_window(MorningWindow("08:10", "08:40"), path)
    commands = []

    def fail(command, **_kwargs):
        commands.append(command)
        assert load_morning_window(path) == MorningWindow("10:00", "10:30")
        return subprocess.CompletedProcess(command, 1, "", "Access denied")

    monkeypatch.setattr(morning_schedule.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Access denied"):
        morning_schedule.update_morning_window("10:00", "10:30", tmp_path)
    assert load_morning_window(path) == MorningWindow("08:10", "08:40")
    assert "-UpdateOnly" in commands[0]


def test_next_date_uses_configured_deadline_and_keeps_active_plan(monkeypatch):
    monkeypatch.setattr(calendar_service, "day_plan", lambda _target: {})
    monkeypatch.setattr(calendar_service, "day_status", lambda _target: {"should_run": True})
    now = datetime(2026, 10, 8, 9, 45)
    later_window = MorningWindow("09:30", "10:00")
    assert calendar_service.next_execution_date(now, window=later_window)["date"] == "2026-10-08"
    assert calendar_service.next_execution_date(now, window=MorningWindow())["date"] == "2026-10-09"
    active = {"date": "2026-10-08", "status": "waiting"}
    assert (
        calendar_service.next_execution_date(now, active, MorningWindow())["date"] == "2026-10-08"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler")
@pytest.mark.parametrize("enabled", [True, False])
def test_task_window_update_preserves_enabled_state_and_extends_limit(enabled):
    project = Path(__file__).resolve().parents[1]
    installer = project / "scripts" / "setup" / "install_daily_task.ps1"
    script = """
Import-Module ScheduledTasks
function Test-Path { return $true }
function Get-Content { return '{"start":"08:00","end":"10:00"}' }
function Get-ScheduledTask {
    return [pscustomobject]@{
        Settings=[pscustomobject]@{Enabled=ENABLED;ExecutionTimeLimit='PT1H'}
        Triggers=@();Description='old'
    }
}
function Set-ScheduledTask {
    param($InputObject)
    [pscustomobject]@{
        Enabled=$InputObject.Settings.Enabled
        Limit=$InputObject.Settings.ExecutionTimeLimit
        Boundary=$InputObject.Triggers[0].StartBoundary
    } | ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
}
& 'INSTALLER' -TaskName 'Attendance Hub Test Window Isolated' -UpdateOnly
""".replace("ENABLED", "$true" if enabled else "$false").replace("INSTALLER", str(installer))
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert f'"Enabled":{str(enabled).lower()}' in result.stdout
    assert '"Limit":"PT2H30M"' in result.stdout
    captured = json.loads(result.stdout.splitlines()[0])
    boundary = datetime.fromisoformat(captured["Boundary"].replace("Z", "+00:00"))
    assert boundary.astimezone().strftime("%H:%M") == "08:00"
