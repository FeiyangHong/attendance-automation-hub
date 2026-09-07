from __future__ import annotations

import calendar
import json
from datetime import date, datetime, time, timedelta
from typing import Any

from attendance_history import month_records, record_for_date
from daily_plans import day_plan, month_plans, set_day_plan
from holiday_sync import load_official_calendar, sync_year

from .settings import PROJECT_DIR


OVERRIDES_FILE = PROJECT_DIR / "config" / "calendar_overrides.json"
OFFICIAL_FILE = PROJECT_DIR / "config" / "official_holidays.json"


def load_overrides() -> dict[str, list[str]]:
    if not OVERRIDES_FILE.exists():
        return {"force_workdays": [], "force_holidays": []}
    data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    workdays = sorted({date.fromisoformat(item).isoformat() for item in data.get("force_workdays", [])})
    holidays = sorted({date.fromisoformat(item).isoformat() for item in data.get("force_holidays", [])})
    return {"force_workdays": workdays, "force_holidays": holidays}


def save_overrides(data: dict[str, list[str]]) -> None:
    normalized = {
        "force_workdays": sorted(set(data.get("force_workdays", []))),
        "force_holidays": sorted(set(data.get("force_holidays", []))),
    }
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OVERRIDES_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OVERRIDES_FILE)


def day_status(
    target: date,
    overrides: dict[str, list[str]] | None = None,
    official: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = overrides or load_overrides()
    official = official or load_official_calendar(OFFICIAL_FILE)
    value = target.isoformat()
    if value in overrides["force_workdays"]:
        return {"should_run": True, "code": "manual_workday", "label": "临时执行", "reason": "人工设置：临时执行"}
    if value in overrides["force_holidays"]:
        return {"should_run": False, "code": "manual_holiday", "label": "临时跳过", "reason": "人工设置：临时不执行"}

    year_record = official.get("years", {}).get(str(target.year), {})
    if value in set(year_record.get("workdays", [])):
        return {"should_run": True, "code": "official_workday", "label": "调休上班", "reason": "国务院安排：调休上班日"}
    if value in set(year_record.get("holidays", [])):
        return {"should_run": False, "code": "official_holiday", "label": "法定休息", "reason": "国务院安排：放假日"}
    if target.weekday() >= 5:
        return {"should_run": False, "code": "default_weekend", "label": "周末休息", "reason": "默认规则：周末"}
    return {"should_run": True, "code": "default_weekday", "label": "工作日", "reason": "默认规则：周一至周五"}


def month_view(year: int, month: int) -> dict[str, Any]:
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("Invalid calendar month")
    overrides = load_overrides()
    official = load_official_calendar(OFFICIAL_FILE)
    plans = month_plans(year, month)
    records = {item["attendance_date"]: item for item in month_records(year, month)}
    days: list[dict[str, Any]] = []
    for day_number in range(1, calendar.monthrange(year, month)[1] + 1):
        target = date(year, month, day_number)
        value = target.isoformat()
        days.append(
            {
                "date": value,
                "day": day_number,
                "weekday": target.weekday(),
                **day_status(target, overrides, official),
                "plan": plans.get(value, {}),
                "attendance": records.get(value, {}),
            }
        )
    official_record = official.get("years", {}).get(str(year), {})
    return {
        "year": year,
        "month": month,
        "days": days,
        "official_synced_at": official_record.get("synced_at", ""),
    }


def update_override(target_text: str, mode: str) -> dict[str, Any]:
    target = date.fromisoformat(target_text)
    if mode not in {"default", "workday", "holiday"}:
        raise ValueError("mode must be default, workday, or holiday")
    data = load_overrides()
    value = target.isoformat()
    data["force_workdays"] = [item for item in data["force_workdays"] if item != value]
    data["force_holidays"] = [item for item in data["force_holidays"] if item != value]
    if mode == "workday":
        data["force_workdays"].append(value)
    elif mode == "holiday":
        data["force_holidays"].append(value)
    save_overrides(data)
    return day_status(target, data)


def update_day_plan(
    target_text: str,
    clock_in: str | None,
    clock_out: str | None,
) -> dict[str, Any]:
    target = date.fromisoformat(target_text)
    for label, value in (("Clock-in", clock_in), ("Clock-out", clock_out)):
        if value:
            parsed_time = datetime.strptime(value, "%H:%M").time()
            if parsed_time.hour == 23 and parsed_time.minute >= 55:
                raise ValueError(
                    f"{label} plans from 23:55 onward are blocked because the flow may cross midnight"
                )
    return set_day_plan(
        target,
        clock_in=clock_in,
        clock_out=clock_out,
        updated_from="remote_web",
    )


def selected_day(target_text: str) -> dict[str, Any]:
    target = date.fromisoformat(target_text)
    return {
        "date": target.isoformat(),
        "status": day_status(target),
        "plan": day_plan(target),
        "attendance": record_for_date(target),
    }


def synchronize_year(year: int) -> dict[str, Any]:
    if not 2000 <= year <= 2100:
        raise ValueError("Invalid year")
    return sync_year(year, OFFICIAL_FILE)


def next_execution_date(
    reference: datetime | None = None,
    morning_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = reference or datetime.now().astimezone()
    today = now.date()
    plan = morning_plan or {}
    finished_today = (
        str(plan.get("date", "")) == today.isoformat()
        and str(plan.get("status", "")) in {"success", "failed", "skipped"}
    )
    first = today
    today_exact = day_plan(today)
    if finished_today or (not today_exact.get("clock_in") and now.time() > time(9, 30)):
        first += timedelta(days=1)
    for offset in range(732):
        candidate = first + timedelta(days=offset)
        exact = day_plan(candidate)
        status = day_status(candidate)
        if exact.get("clock_in"):
            return {
                "date": candidate.isoformat(),
                "label": "单日计划",
                "reason": f"Exact clock-in at {exact['clock_in']}",
            }
        if status["should_run"]:
            return {"date": candidate.isoformat(), **status}
    raise RuntimeError("Unable to find an executable date in the next two years")
