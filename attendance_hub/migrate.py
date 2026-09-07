from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from .configure import atomic_write_json
from .settings import PROJECT_DIR


PUBLIC_CONFIG_FILES = (
    "calendar_overrides.json",
    "daily_plans.json",
    "official_holidays.json",
)


def sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)


def migrate(legacy: Path, include_logs: bool = False) -> list[str]:
    legacy = legacy.resolve()
    if legacy == PROJECT_DIR.resolve() or not (legacy / "04_feishu_flow.py").is_file():
        raise ValueError("Legacy repository path is invalid")
    messages: list[str] = []
    for name in PUBLIC_CONFIG_FILES:
        source = legacy / "config" / name
        if source.is_file():
            shutil.copy2(source, PROJECT_DIR / "config" / name)
            messages.append(f"copied config/{name}")

    old_app_config = legacy / "config" / "app_config.json"
    if old_app_config.is_file():
        data = json.loads(old_app_config.read_text(encoding="utf-8"))
        data["device_access_enabled"] = False
        data["real_actions_enabled"] = False
        atomic_write_json(PROJECT_DIR / "config" / "app_config.json", data)
        messages.append("copied device identity with both safety locks forced off")

    history = legacy / "data" / "attendance_history.db"
    if history.is_file():
        sqlite_backup(history, PROJECT_DIR / "data" / "attendance_history.db")
        messages.append("copied attendance history using SQLite online backup")

    if include_logs and (legacy / "logs").is_dir():
        shutil.copytree(legacy / "logs", PROJECT_DIR / "logs", dirs_exist_ok=True)
        messages.append("copied historical logs")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy data without changing the legacy repository")
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--include-logs", action="store_true")
    args = parser.parse_args()
    for message in migrate(args.legacy, args.include_logs):
        print(message)
    print("Migration import completed. Safety locks remain off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
