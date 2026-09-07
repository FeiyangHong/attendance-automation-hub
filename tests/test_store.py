from attendance_hub.store import RemoteStore


def test_sessions_jobs_idempotency_and_audit(tmp_path):
    store = RemoteStore(tmp_path / "remote.db")
    store.create_session("token", "admin", "csrf", 1, "127.0.0.1", "test")
    assert store.get_session("token")["csrf_token"] == "csrf"
    assert store.get_session("wrong") is None

    job = store.create_job("clock_in", "admin", "127.0.0.1", {}, "same-key")
    assert store.job_by_idempotency_key("same-key")["id"] == job["id"]
    store.set_job_running(job["id"], 123, str(tmp_path / "job.log"))
    assert store.active_job()["status"] == "running"
    store.finish_job(job["id"], "failed", "expected", 1, "process_failure")
    assert store.recent_duplicate("clock_in", 60) is None
    assert store.get_job(job["id"])["failure_category"] == "process_failure"

    store.add_audit("admin", "127.0.0.1", "test")
    assert store.list_audit(1)[0]["action"] == "test"


def test_recover_interrupted_jobs(tmp_path):
    store = RemoteStore(tmp_path / "remote.db")
    job = store.create_job("diagnostic", "admin", "local")
    assert store.recover_interrupted_jobs() == 1
    assert store.get_job(job["id"])["exit_code"] == 125
