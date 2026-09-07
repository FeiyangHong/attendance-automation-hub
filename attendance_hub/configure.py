from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from app_config import DEFAULT_CONFIG_FILE

from .security import new_password_record
from .settings import DEFAULT_CONFIG_FILE as REMOTE_CONFIG_FILE


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure Attendance Automation Hub without storing plaintext secrets"
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument("--device-udid", default="")
    parser.add_argument("--feishu-package", default="com.ss.android.lark")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--tailscale-user",
        action="append",
        default=[],
        help="Allowed Tailscale login email; may be supplied more than once",
    )
    parser.add_argument("--cookie-secure", action="store_true")
    parser.add_argument("--enable-device", action="store_true")
    parser.add_argument("--enable-real-actions", action="store_true")
    parser.add_argument(
        "--confirm-production",
        default="",
        help="Must equal CUTOVER to enable real actions",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.enable_real_actions and args.confirm_production != "CUTOVER":
        raise SystemExit(
            "Refusing to enable real actions without --confirm-production CUTOVER"
        )
    if args.enable_real_actions and not args.enable_device:
        raise SystemExit("Real actions require --enable-device")
    if args.enable_device and not args.device_udid.strip():
        raise SystemExit("Device access requires --device-udid")
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Port must be between 1024 and 65535")

    password = os.environ.pop("ATTENDANCE_HUB_PASSWORD", "")
    if not password:
        password = getpass.getpass("Remote password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Remote password must contain at least 12 characters")
    salt, digest = new_password_record(password)

    app_config = {
        "device_udid": args.device_udid.strip(),
        "feishu_package": args.feishu_package.strip(),
        "device_access_enabled": bool(args.enable_device),
        "real_actions_enabled": bool(args.enable_real_actions),
    }
    remote_config = {
        "bind_host": "127.0.0.1",
        "port": args.port,
        "admin_username": args.username.strip(),
        "password_salt": salt,
        "password_hash": digest,
        "session_hours": 12,
        "cookie_secure": bool(args.cookie_secure),
        "trusted_hosts": ["127.0.0.1", "localhost", "*.ts.net"],
        "allowed_tailscale_users": sorted(
            {item.strip().lower() for item in args.tailscale_user if item.strip()}
        ),
        "task_name": "Attendance Hub Morning Clock-In",
        "planned_clock_out_prefix": "Attendance Hub Planned Clock-Out ",
        "remote_service_task_name": "Attendance Automation Hub Web",
        "duplicate_window_seconds": 45,
        "max_job_seconds": 420,
    }
    atomic_write_json(DEFAULT_CONFIG_FILE, app_config)
    atomic_write_json(REMOTE_CONFIG_FILE, remote_config)
    print(f"Wrote {DEFAULT_CONFIG_FILE}")
    print(f"Wrote {REMOTE_CONFIG_FILE}")
    print(
        "Device access: "
        f"{'enabled' if args.enable_device else 'disabled'}; real actions: "
        f"{'enabled' if args.enable_real_actions else 'disabled'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
