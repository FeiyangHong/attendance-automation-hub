from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = PROJECT_DIR / "config" / "app_config.json"


@dataclass(frozen=True)
class AppConfig:
    device_udid: str = ""
    feishu_package: str = "com.ss.android.lark"
    device_access_enabled: bool = False
    real_actions_enabled: bool = False

    @property
    def display_udid(self) -> str:
        return self.device_udid or "UNCONFIGURED"


def load_app_config(config_file: Path | None = None) -> AppConfig:
    selected = config_file or Path(
        os.environ.get("ATTENDANCE_HUB_APP_CONFIG", DEFAULT_CONFIG_FILE)
    )
    if not selected.exists():
        return AppConfig()

    data = json.loads(selected.read_text(encoding="utf-8"))
    config = AppConfig(
        device_udid=str(data.get("device_udid", "")).strip(),
        feishu_package=str(
            data.get("feishu_package", "com.ss.android.lark")
        ).strip(),
        device_access_enabled=bool(data.get("device_access_enabled", False)),
        real_actions_enabled=bool(data.get("real_actions_enabled", False)),
    )
    if config.device_access_enabled and not config.device_udid:
        raise ValueError(
            "device_udid is required when device_access_enabled is true"
        )
    if not config.feishu_package:
        raise ValueError("feishu_package cannot be empty")
    return config


def require_device_access(config: AppConfig) -> None:
    if not config.device_access_enabled:
        raise RuntimeError(
            "Device access is disabled in config/app_config.json. "
            "This development repository cannot connect to the phone."
        )


def require_real_actions(config: AppConfig) -> None:
    require_device_access(config)
    if not config.real_actions_enabled:
        raise RuntimeError(
            "Real attendance actions are disabled in config/app_config.json."
        )
