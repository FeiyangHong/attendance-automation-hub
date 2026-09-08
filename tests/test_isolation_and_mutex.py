import json
import sqlite3
from pathlib import Path

import pytest

from attendance_hub.jobs import JobManager, JobRejected
from attendance_hub.migrate import import_legacy_logs, migrate
from attendance_hub.settings import RemoteSettings
from attendance_hub.store import RemoteStore
from attendance_hub import system_status


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_disabled_device_status_never_runs_adb(tmp_path, monkeypatch):
    config = tmp_path / "app.json"
    config.write_text(json.dumps({"device_access_enabled": False}), encoding="utf-8")
    monkeypatch.setenv("ATTENDANCE_HUB_APP_CONFIG", str(config))
    monkeypatch.setattr(
        system_status.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("ADB must not run while device access is disabled"),
    )
    assert system_status._device_status()["state"] == "disabled"


def test_remote_diagnostic_is_blocked_while_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENDANCE_HUB_APP_CONFIG", str(tmp_path / "missing.json"))
    settings = RemoteSettings(project_dir=tmp_path)
    manager = JobManager(settings, RemoteStore(tmp_path / "remote.db"))
    with pytest.raises(JobRejected, match="disabled"):
        manager.submit("diagnostic", "admin", "local")


def test_all_phone_entrypoints_use_the_same_device_mutex():
    expected = "Local\\AttendanceAutomationHubDevice"
    entrypoints = (
        PROJECT_DIR / "scripts" / "runtime" / "run_morning.ps1",
        PROJECT_DIR / "scripts" / "runtime" / "run_clock_out.ps1",
        PROJECT_DIR / "scripts" / "operations" / "start_scrcpy.ps1",
    )
    for entrypoint in entrypoints:
        assert expected in entrypoint.read_text(encoding="utf-8")


def test_failure_classification_is_specific():
    assert JobManager._failure_category(["DEVICE BUSY"], 3) == "task_conflict"
    assert JobManager._failure_category(["Appium did not become ready"], 1) == "appium_failure"
    assert JobManager._failure_category(["CROSS-DAY SAFETY"], 5) == "cross_day"


def test_migration_forces_safety_locks_off(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "config").mkdir(parents=True)
    (legacy / "data").mkdir()
    (legacy / "04_feishu_flow.py").write_text("# marker", encoding="utf-8")
    (legacy / "config" / "app_config.json").write_text(
        json.dumps(
            {
                "device_udid": "serial",
                "device_access_enabled": True,
                "real_actions_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(legacy / "data" / "attendance_history.db") as database:
        database.execute("CREATE TABLE sample(value TEXT)")
        database.execute("INSERT INTO sample VALUES ('preserved')")

    monkeypatch.setattr("attendance_hub.migrate.PROJECT_DIR", target)
    migrate(legacy)
    result = json.loads((target / "config" / "app_config.json").read_text(encoding="utf-8"))
    assert result["device_udid"] == "serial"
    assert result["device_access_enabled"] is False
    assert result["real_actions_enabled"] is False
    with sqlite3.connect(target / "data" / "attendance_history.db") as database:
        assert database.execute("SELECT value FROM sample").fetchone()[0] == "preserved"


def test_logs_only_migration_preserves_existing_configuration(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "logs" / "2026-09").mkdir(parents=True)
    (legacy / "04_feishu_flow.py").write_text("# marker", encoding="utf-8")
    (legacy / "logs" / "2026-09" / "2026-09-01.log").write_text(
        "2026-09-01 09:05:00 Scheduler completed successfully.\n",
        encoding="ascii",
    )
    (target / "config").mkdir(parents=True)
    app_config = target / "config" / "app_config.json"
    expected = {"device_access_enabled": True, "real_actions_enabled": True}
    app_config.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr("attendance_hub.migrate.PROJECT_DIR", target)
    messages = import_legacy_logs(legacy)

    assert json.loads(app_config.read_text(encoding="utf-8")) == expected
    assert (
        target
        / "logs"
        / "legacy"
        / "legacy"
        / "2026-09"
        / "2026-09-01.log"
    ).is_file()
    assert len(messages) == 2
