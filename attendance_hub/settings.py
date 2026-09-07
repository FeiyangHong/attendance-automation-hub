from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = PROJECT_DIR / "config" / "remote_config.json"


@dataclass(frozen=True)
class RemoteSettings:
    project_dir: Path = PROJECT_DIR
    bind_host: str = "127.0.0.1"
    port: int = 8765
    admin_username: str = "admin"
    password_salt: str = ""
    password_hash: str = ""
    session_hours: int = 12
    cookie_secure: bool = False
    trusted_hosts: tuple[str, ...] = (
        "127.0.0.1",
        "localhost",
        "*.ts.net",
    )
    allowed_tailscale_users: tuple[str, ...] = field(default_factory=tuple)
    task_name: str = "Attendance Hub Morning Clock-In"
    planned_clock_out_prefix: str = "Attendance Hub Planned Clock-Out "
    remote_service_task_name: str = "Attendance Automation Hub Web"
    duplicate_window_seconds: int = 45
    max_job_seconds: int = 420

    @property
    def data_dir(self) -> Path:
        return self.project_dir / "data"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "remote_control.db"

    @property
    def job_log_dir(self) -> Path:
        return self.data_dir / "remote_jobs"

    @property
    def app_config_file(self) -> Path:
        return self.project_dir / "config" / "app_config.json"

    def require_authentication_config(self) -> None:
        if not self.password_salt or not self.password_hash:
            raise RuntimeError(
                "Remote login is not configured. Run configure_remote.ps1 first."
            )


def load_remote_settings(
    config_file: Path | None = None,
    *,
    require_authentication: bool = False,
) -> RemoteSettings:
    selected = config_file or Path(
        os.environ.get("ATTENDANCE_HUB_REMOTE_CONFIG", DEFAULT_CONFIG_FILE)
    )
    data: dict[str, object] = {}
    if selected.exists():
        data = json.loads(selected.read_text(encoding="utf-8"))

    settings = RemoteSettings(
        bind_host=str(data.get("bind_host", "127.0.0.1")).strip(),
        port=int(data.get("port", 8765)),
        admin_username=str(data.get("admin_username", "admin")).strip(),
        password_salt=str(data.get("password_salt", "")).strip(),
        password_hash=str(data.get("password_hash", "")).strip(),
        session_hours=int(data.get("session_hours", 12)),
        cookie_secure=bool(data.get("cookie_secure", False)),
        trusted_hosts=tuple(
            str(item).strip()
            for item in data.get(
                "trusted_hosts", ["127.0.0.1", "localhost", "*.ts.net"]
            )
            if str(item).strip()
        ),
        allowed_tailscale_users=tuple(
            str(item).strip().lower()
            for item in data.get("allowed_tailscale_users", [])
            if str(item).strip()
        ),
        task_name=str(
            data.get("task_name", "Attendance Hub Morning Clock-In")
        ).strip(),
        planned_clock_out_prefix=str(
            data.get(
                "planned_clock_out_prefix",
                "Attendance Hub Planned Clock-Out ",
            )
        ),
        remote_service_task_name=str(
            data.get(
                "remote_service_task_name",
                "Attendance Automation Hub Web",
            )
        ).strip(),
        duplicate_window_seconds=max(
            5, int(data.get("duplicate_window_seconds", 45))
        ),
        max_job_seconds=max(30, int(data.get("max_job_seconds", 420))),
    )
    if settings.bind_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("bind_host must remain loopback-only")
    if not 1024 <= settings.port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    if not 1 <= settings.session_hours <= 168:
        raise ValueError("session_hours must be between 1 and 168")
    if not settings.admin_username:
        raise ValueError("admin_username cannot be empty")
    if require_authentication:
        settings.require_authentication_config()
    return settings
