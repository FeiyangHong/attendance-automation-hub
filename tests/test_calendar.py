from datetime import datetime

import pytest

from attendance_hub import calendar_service


def test_cross_midnight_clock_out_is_blocked():
    with pytest.raises(ValueError, match="cross midnight"):
        calendar_service.update_day_plan("2026-09-08", "09:10", "23:55")
    with pytest.raises(ValueError, match="cross midnight"):
        calendar_service.update_day_plan("2026-09-08", "23:59", "")


def test_plan_update_uses_remote_source(monkeypatch):
    captured = {}

    def fake_set_day_plan(target, **values):
        captured.update(values)
        return {"clock_in": values["clock_in"], "clock_out": values["clock_out"]}

    monkeypatch.setattr(calendar_service, "set_day_plan", fake_set_day_plan)
    result = calendar_service.update_day_plan("2026-09-08", "09:10", "18:30")
    assert result == {"clock_in": "09:10", "clock_out": "18:30"}
    assert captured["updated_from"] == "remote_web"


def test_exact_plan_has_priority_after_random_window(monkeypatch):
    monkeypatch.setattr(
        calendar_service,
        "day_plan",
        lambda target: {"clock_in": "20:00"} if target.isoformat() == "2026-09-08" else {},
    )
    monkeypatch.setattr(
        calendar_service,
        "day_status",
        lambda target: {"should_run": True, "label": "工作日", "reason": "test"},
    )
    result = calendar_service.next_execution_date(datetime(2026, 9, 8, 19, 0))
    assert result["date"] == "2026-09-08"
    assert result["label"] == "单日计划"
