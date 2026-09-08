from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from .configure import atomic_write_json
from .core.attendance_history import import_existing_logs
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


def import_legacy_logs(legacy: Path) -> list[str]:
    legacy = legacy.resolve()
    source_root = legacy / "logs"
    if not (legacy / "04_feishu_flow.py").is_file() or not source_root.is_dir():
        raise ValueError("Legacy repository or log directory is invalid")

    archive_root = PROJECT_DIR / "logs" / "legacy" / legacy.name
    shutil.copytree(source_root, archive_root, dirs_exist_ok=True)
    stats = import_existing_logs(
        archive_root,
        PROJECT_DIR / "data" / "attendance_history.db",
        source_name=f"legacy/{legacy.name}",
    )
    return [
        f"archived legacy logs under {archive_root}",
        (
            "imported legacy attendance history: "
            f"files={stats['files_imported']}; "
            f"observations={stats['observations_added']}"
        ),
    ]


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
        messages.extend(import_legacy_logs(legacy))
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy data without changing the legacy repository")
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--include-logs", action="store_true")
    parser.add_argument("--logs-only", action="store_true")
    args = parser.parse_args()
    if args.logs_only:
        messages = import_legacy_logs(args.legacy)
    else:
        messages = migrate(args.legacy, args.include_logs)
    for message in messages:
        print(message)
    if args.logs_only:
        print("Log import completed. Existing configuration and safety locks were unchanged.")
    else:
        print("Migration import completed. Safety locks remain off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
