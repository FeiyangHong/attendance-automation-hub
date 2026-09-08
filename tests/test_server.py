from fastapi.testclient import TestClient

from attendance_hub.security import new_password_record
from attendance_hub.server import SESSION_COOKIE, create_app
from attendance_hub.settings import RemoteSettings


def configured_settings(tmp_path):
    salt, digest = new_password_record("correct horse battery staple")
    return RemoteSettings(
        project_dir=tmp_path,
        password_salt=salt,
        password_hash=digest,
        trusted_hosts=("testserver", "localhost"),
    )


def login(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct horse battery staple"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    token = response.cookies[SESSION_COOKIE]
    session = client.app.state.store.get_session(token)
    return session["csrf_token"]


def test_auth_csrf_and_real_action_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENDANCE_HUB_APP_CONFIG", str(tmp_path / "missing.json"))
    app = create_app(configured_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/status").status_code == 401
        csrf = login(client)
        assert client.get("/api/jobs").status_code == 200
        assert client.post("/api/jobs/clock_in", json={"confirm": True}).status_code == 403
        response = client.post(
            "/api/jobs/clock_in",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "test-one"},
            json={"confirm": True},
        )
        assert response.status_code == 409
        assert "disabled" in response.json()["detail"]


def test_dashboard_contains_daily_execution_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENDANCE_HUB_APP_CONFIG", str(tmp_path / "missing.json"))
    app = create_app(configured_settings(tmp_path))
    with TestClient(app) as client:
        login(client)
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="morning-plan"' in response.text
        assert 'id="last-task-result"' in response.text
        assert 'id="daily-task-progress"' in response.text
        assert "每日自动任务" in response.text


def test_tailscale_identity_allowlist(tmp_path):
    settings = configured_settings(tmp_path)
    settings = RemoteSettings(
        **{**settings.__dict__, "allowed_tailscale_users": ("owner@example.com",)}
    )
    app = create_app(settings)
    with TestClient(app) as client:
        denied = client.get("/login", headers={"Tailscale-User-Login": "other@example.com"})
        assert denied.status_code == 403
        allowed = client.get("/login", headers={"Tailscale-User-Login": "owner@example.com"})
        assert allowed.status_code == 200


def test_invalid_log_date_and_artifact_name(tmp_path):
    app = create_app(configured_settings(tmp_path))
    with TestClient(app) as client:
        csrf = login(client)
        assert csrf
        assert client.get("/api/logs/not-a-date").status_code == 400
        assert client.get("/api/artifacts/not-allowed.txt").status_code == 400
