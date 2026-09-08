from pathlib import Path

from attendance_hub.core import app_config, attendance_history, daily_plans, holiday_sync
from attendance_hub.jobs import JobManager
from attendance_hub.settings import RemoteSettings


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_repository_root_contains_only_public_entrypoints():
    for name in (
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "hub.ps1",
        "Attendance Hub.vbs",
    ):
        assert (PROJECT_DIR / name).is_file()


def test_internal_runtime_files_use_the_src_and_scripts_layout():
    for relative_name in (
        "src/attendance_hub/automation/feishu_flow.py",
        "src/attendance_hub/desktop/control_panel.py",
        "src/attendance_hub/web/templates/dashboard.html",
        "scripts/runtime/run_morning.ps1",
        "scripts/runtime/run_clock_out.ps1",
        "scripts/runtime/run_web.ps1",
    ):
        assert (PROJECT_DIR / relative_name).is_file()


def test_core_default_paths_stay_under_repository_runtime_directories():
    assert app_config.DEFAULT_CONFIG_FILE == PROJECT_DIR / "config" / "app_config.json"
    assert daily_plans.DEFAULT_PLAN_FILE == PROJECT_DIR / "config" / "daily_plans.json"
    assert holiday_sync.DEFAULT_CACHE_FILE == PROJECT_DIR / "config" / "official_holidays.json"
    assert attendance_history.DEFAULT_DB_FILE == PROJECT_DIR / "data" / "attendance_history.db"


def test_remote_jobs_resolve_to_existing_runtime_scripts():
    manager = object.__new__(JobManager)
    manager.settings = RemoteSettings(project_dir=PROJECT_DIR)
    for kind in ("clock_in", "clock_out", "dry_run_clock_in", "dry_run_clock_out"):
        command = manager._command_for(kind)
        script = next(Path(argument) for argument in command if argument.endswith(".ps1"))
        assert script.is_file()
