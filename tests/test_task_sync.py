import json

from attendance_hub.settings import RemoteSettings
from attendance_hub.task_sync import synchronize_plan_tasks


def test_task_sync_is_disabled_before_cutover(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app_config.json").write_text(
        json.dumps({"device_access_enabled": False}), encoding="utf-8"
    )
    settings = RemoteSettings(project_dir=tmp_path)
    result = synchronize_plan_tasks(settings, "2026-09-08", "09:10", "18:30")
    assert result["status"] == "skipped"
