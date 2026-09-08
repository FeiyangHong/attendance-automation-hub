from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..paths import PROJECT_DIR

DEFAULT_PLAN_FILE = PROJECT_DIR / "config" / "daily_plans.json"


def _normalize_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _normalize_time(value: str | None) -> str:
    if value is None or not str(value).strip():
        return ""
    return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")


def load_daily_plans(plan_file: Path = DEFAULT_PLAN_FILE) -> dict[str, Any]:
    if not plan_file.exists():
        return {"schema_version": 1, "plans": {}}
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    plans = data.get("plans", {})
    if not isinstance(plans, dict):
        raise ValueError("daily_plans.json has an invalid plans field")

    normalized: dict[str, dict[str, str]] = {}
    for date_text, raw_plan in plans.items():
        normalized_date = _normalize_date(date_text)
        if not isinstance(raw_plan, dict):
            raise ValueError(f"Invalid daily plan: {date_text}")
        clock_in = _normalize_time(raw_plan.get("clock_in"))
        clock_out = _normalize_time(raw_plan.get("clock_out"))
        if not clock_in and not clock_out:
            continue
        normalized[normalized_date] = {
            "clock_in": clock_in,
            "clock_out": clock_out,
            "updated_at": str(raw_plan.get("updated_at", "")),
            "updated_from": str(raw_plan.get("updated_from", "calendar")),
        }
    return {"schema_version": 1, "plans": normalized}


def save_daily_plans(
    data: dict[str, Any],
    plan_file: Path = DEFAULT_PLAN_FILE,
) -> None:
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    normalized = load_daily_plans_from_data(data)
    temporary_file = plan_file.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(normalized, ensure_ascii=True, indent=2) + "\n",
        encoding="ascii",
    )
    temporary_file.replace(plan_file)


def load_daily_plans_from_data(data: dict[str, Any]) -> dict[str, Any]:
    plans = data.get("plans", {})
    if not isinstance(plans, dict):
        raise ValueError("Daily plan data has an invalid plans field")
    normalized: dict[str, dict[str, str]] = {}
    for date_text, raw_plan in plans.items():
        normalized_date = _normalize_date(date_text)
        if not isinstance(raw_plan, dict):
            raise ValueError(f"Invalid daily plan: {date_text}")
        clock_in = _normalize_time(raw_plan.get("clock_in"))
        clock_out = _normalize_time(raw_plan.get("clock_out"))
        if not clock_in and not clock_out:
            continue
        normalized[normalized_date] = {
            "clock_in": clock_in,
            "clock_out": clock_out,
            "updated_at": str(raw_plan.get("updated_at", "")),
            "updated_from": str(raw_plan.get("updated_from", "calendar")),
        }
    return {"schema_version": 1, "plans": dict(sorted(normalized.items()))}


def day_plan(
    target_date: str | date,
    plan_file: Path = DEFAULT_PLAN_FILE,
) -> dict[str, str]:
    date_text = _normalize_date(target_date)
    return dict(load_daily_plans(plan_file)["plans"].get(date_text, {}))


def set_day_plan(
    target_date: str | date,
    *,
    clock_in: str | None = None,
    clock_out: str | None = None,
    updated_from: str = "calendar",
    plan_file: Path = DEFAULT_PLAN_FILE,
) -> dict[str, str]:
    date_text = _normalize_date(target_date)
    clock_in_text = _normalize_time(clock_in)
    clock_out_text = _normalize_time(clock_out)
    data = load_daily_plans(plan_file)
    if clock_in_text or clock_out_text:
        data["plans"][date_text] = {
            "clock_in": clock_in_text,
            "clock_out": clock_out_text,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_from": updated_from,
        }
    else:
        data["plans"].pop(date_text, None)
    save_daily_plans(data, plan_file)
    return dict(data["plans"].get(date_text, {}))


def month_plans(
    year: int,
    month: int,
    plan_file: Path = DEFAULT_PLAN_FILE,
) -> dict[str, dict[str, str]]:
    prefix = f"{int(year):04d}-{int(month):02d}-"
    plans = load_daily_plans(plan_file)["plans"]
    return {
        date_text: dict(plan)
        for date_text, plan in plans.items()
        if date_text.startswith(prefix)
    }
