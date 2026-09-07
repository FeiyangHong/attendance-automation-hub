import json

import pytest

from attendance_hub.settings import load_remote_settings


def test_remote_bind_is_loopback_only(tmp_path):
    config = tmp_path / "remote.json"
    config.write_text(json.dumps({"bind_host": "0.0.0.0"}), encoding="utf-8")
    with pytest.raises(ValueError, match="loopback"):
        load_remote_settings(config)


def test_authentication_config_is_required(tmp_path):
    config = tmp_path / "remote.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not configured"):
        load_remote_settings(config, require_authentication=True)
