from pathlib import Path

from attendance_hub.core import app_config, attendance_history, daily_plans, holiday_sync


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_runtime_entrypoints_remain_at_repository_root():
    for name in (
        "04_feishu_flow.py",
        "run_random.ps1",
        "run_clock_out.ps1",
        "run_remote_service.ps1",
        "launch_control_panel.vbs",
    ):
        assert (PROJECT_DIR / name).is_file()


def test_core_default_paths_stay_under_repository_runtime_directories():
    assert app_config.DEFAULT_CONFIG_FILE == PROJECT_DIR / "config" / "app_config.json"
    assert daily_plans.DEFAULT_PLAN_FILE == PROJECT_DIR / "config" / "daily_plans.json"
    assert holiday_sync.DEFAULT_CACHE_FILE == PROJECT_DIR / "config" / "official_holidays.json"
    assert attendance_history.DEFAULT_DB_FILE == PROJECT_DIR / "data" / "attendance_history.db"
