from __future__ import annotations

import asyncio
import hmac
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .core.app_config import load_app_config
from .core.attendance_history import (
    events_for_date,
    month_records,
    record_for_date,
)

from .calendar_service import (
    month_view,
    selected_day,
    synchronize_year,
    update_day_plan,
    update_override,
)
from .jobs import JOB_KINDS, SENSITIVE_JOB_KINDS, JobManager, JobRejected
from .security import new_csrf_token, new_session_token, verify_password
from .paths import PACKAGE_DIR
from .settings import RemoteSettings, load_remote_settings
from .store import RemoteStore
from .system_status import status_snapshot
from .task_sync import synchronize_plan_tasks


SESSION_COOKIE = "attendance_hub_session"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ARTIFACT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+\.(?:png|xml)$")


class JobSubmission(BaseModel):
    confirm: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class CalendarOverrideUpdate(BaseModel):
    mode: str


class DayPlanUpdate(BaseModel):
    clock_in: str = ""
    clock_out: str = ""


class LoginLimiter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.attempts: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        with self.lock:
            values = self.attempts[address]
            while values and now - values[0] > 300:
                values.popleft()
            if len(values) >= 8:
                return False
            values.append(now)
            return True

    def clear(self, address: str) -> None:
        with self.lock:
            self.attempts.pop(address, None)


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def tailscale_user(request: Request) -> str:
    return request.headers.get("Tailscale-User-Login", "").strip().lower()


def enforce_tailscale_policy(request: Request, settings: RemoteSettings) -> None:
    address = client_address(request)
    identity = tailscale_user(request)
    if identity:
        if settings.allowed_tailscale_users and identity not in settings.allowed_tailscale_users:
            raise HTTPException(status_code=403, detail="Tailscale identity is not allowed")
        return
    if address in {"127.0.0.1", "::1", "testclient"}:
        return
    raise HTTPException(status_code=403, detail="Requests must arrive through local Tailscale Serve")


def create_app(settings: RemoteSettings | None = None) -> FastAPI:
    settings = settings or load_remote_settings(require_authentication=True)
    settings.require_authentication_config()
    store = RemoteStore(settings.database_file)
    jobs = JobManager(settings, store)
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "web" / "templates"))
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(
        title="Attendance Automation Hub",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.jobs = jobs
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.trusted_hosts),
    )
    app.add_middleware(GZipMiddleware, minimum_size=800)
    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "web" / "static")),
        name="static",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/login"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_for(request: Request) -> dict[str, Any] | None:
        enforce_tailscale_policy(request, settings)
        return store.get_session(request.cookies.get(SESSION_COOKIE, ""))

    def require_api_session(request: Request) -> dict[str, Any]:
        session = session_for(request)
        if not session:
            raise HTTPException(status_code=401, detail="Login required")
        return session

    def require_csrf(
        request: Request,
        session: dict[str, Any] = Depends(require_api_session),
        x_csrf_token: str = Header(default=""),
    ) -> dict[str, Any]:
        if not hmac.compare_digest(str(session["csrf_token"]), x_csrf_token):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        return session

    def audit(
        request: Request,
        session: dict[str, Any],
        action: str,
        object_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        store.add_audit(
            str(session["username"]),
            client_address(request),
            action,
            object_id,
            details,
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        enforce_tailscale_policy(request, settings)
        if session_for(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": ""},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        enforce_tailscale_policy(request, settings)
        address = client_address(request)
        if not limiter.allow(address):
            raise HTTPException(status_code=429, detail="Too many login attempts")
        valid = hmac.compare_digest(username, settings.admin_username) and verify_password(
            password,
            settings.password_salt,
            settings.password_hash,
        )
        if not valid:
            store.add_audit(username[:100], address, "login_failed")
            await asyncio.sleep(0.4)
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": "用户名或密码错误"},
                status_code=401,
            )
        limiter.clear(address)
        token = new_session_token()
        csrf = new_csrf_token()
        store.create_session(
            token,
            settings.admin_username,
            csrf,
            settings.session_hours,
            address,
            request.headers.get("user-agent", ""),
        )
        store.add_audit(settings.admin_username, address, "login_success")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=settings.cookie_secure or bool(tailscale_user(request)),
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "username": session["username"],
                "csrf_token": session["csrf_token"],
                "device_safety": load_app_config(),
            },
        )

    @app.post("/api/logout")
    async def logout(
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        audit(request, session, "logout")
        store.delete_session(request.cookies.get(SESSION_COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/status")
    async def api_status(_session: dict[str, Any] = Depends(require_api_session)):
        snapshot = await asyncio.to_thread(status_snapshot, settings)
        snapshot["active_job"] = store.active_job()
        return snapshot

    @app.get("/api/jobs")
    async def list_jobs(
        limit: int = 30,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        return {"jobs": store.list_jobs(limit)}

    @app.get("/api/jobs/{job_id}")
    async def get_job(
        job_id: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/api/jobs/{job_id}/log")
    async def get_job_log(
        job_id: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        path_text = str(job.get("log_file") or "")
        if not path_text:
            raise HTTPException(status_code=404, detail="This job has no log")
        root = settings.job_log_dir.resolve()
        path = Path(path_text).resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(status_code=404, detail="Job log not found")
        return {"id": job_id, "text": path.read_text(encoding="utf-8", errors="replace")[-1_000_000:]}

    @app.post("/api/jobs/{kind}", status_code=202)
    async def submit_job(
        kind: str,
        payload: JobSubmission,
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ):
        if kind not in JOB_KINDS:
            raise HTTPException(status_code=404, detail="Unsupported job kind")
        if kind in SENSITIVE_JOB_KINDS and not payload.confirm:
            raise HTTPException(status_code=400, detail="Explicit confirmation is required")
        try:
            job, duplicate = jobs.submit(
                kind,
                str(session["username"]),
                client_address(request),
                parameters=payload.parameters,
                idempotency_key=idempotency_key[:200] or None,
            )
        except JobRejected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit(request, session, "job_submit", str(job.get("id")), {"kind": kind, "duplicate": duplicate})
        return {"job": job, "duplicate": duplicate}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if not jobs.cancel(job_id):
            raise HTTPException(status_code=409, detail="Job is not cancellable")
        audit(request, session, "job_cancel", job_id)
        return {"ok": True}

    @app.get("/api/calendar/{year}/{month}")
    async def calendar_month(
        year: int,
        month: int,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        try:
            return await asyncio.to_thread(month_view, year, month)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/calendar/day/{target_date}")
    async def calendar_day(
        target_date: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        try:
            return await asyncio.to_thread(selected_day, target_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/calendar/day/{target_date}/override")
    async def calendar_override(
        target_date: str,
        payload: CalendarOverrideUpdate,
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        try:
            result = await asyncio.to_thread(update_override, target_date, payload.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(request, session, "calendar_override", target_date, {"mode": payload.mode})
        return result

    @app.put("/api/plans/{target_date}")
    async def save_plan(
        target_date: str,
        payload: DayPlanUpdate,
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        try:
            result = await asyncio.to_thread(
                update_day_plan,
                target_date,
                payload.clock_in or None,
                payload.clock_out or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(request, session, "plan_update", target_date, payload.model_dump())
        task_sync = await asyncio.to_thread(
            synchronize_plan_tasks,
            settings,
            target_date,
            result.get("clock_in", ""),
            result.get("clock_out", ""),
        )
        return {"date": target_date, "plan": result, "task_sync": task_sync}

    @app.post("/api/calendar/sync/{year}")
    async def sync_calendar(
        year: int,
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        try:
            result = await asyncio.to_thread(synchronize_year, year)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        audit(request, session, "calendar_sync", str(year))
        return result

    @app.get("/api/history/{year}/{month}")
    async def history_month(
        year: int,
        month: int,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        return {"records": await asyncio.to_thread(month_records, year, month)}

    @app.get("/api/history/day/{target_date}")
    async def history_day(
        target_date: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        try:
            date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid date") from exc
        return {
            "record": await asyncio.to_thread(record_for_date, target_date),
            "events": await asyncio.to_thread(events_for_date, target_date),
        }

    @app.get("/api/logs/{target_date}")
    async def daily_log(
        target_date: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        if not DATE_PATTERN.fullmatch(target_date):
            raise HTTPException(status_code=400, detail="Invalid date")
        parsed = date.fromisoformat(target_date)
        path = settings.project_dir / "logs" / parsed.strftime("%Y-%m") / f"{target_date}.log"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Log not found")
        text = path.read_text(encoding="ascii", errors="replace")
        return {"date": target_date, "text": text[-1_000_000:]}

    @app.get("/api/artifacts")
    async def artifacts(_session: dict[str, Any] = Depends(require_api_session)):
        root = settings.project_dir / "artifacts" / "flow_test"
        files = []
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
                if path.is_file() and ARTIFACT_PATTERN.fullmatch(path.name):
                    files.append({"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime})
        return {"files": files[:100]}

    @app.get("/api/artifacts/{filename}")
    async def artifact_file(
        filename: str,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        if not ARTIFACT_PATTERN.fullmatch(filename):
            raise HTTPException(status_code=400, detail="Invalid artifact name")
        root = (settings.project_dir / "artifacts" / "flow_test").resolve()
        path = (root / filename).resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        media_type = "image/png" if path.suffix.lower() == ".png" else "application/xml"
        return FileResponse(path, media_type=media_type, filename=filename)

    @app.get("/api/audit")
    async def audit_events(
        limit: int = 100,
        _session: dict[str, Any] = Depends(require_api_session),
    ):
        return {"events": store.list_audit(limit)}

    return app


def main() -> None:
    settings = load_remote_settings(require_authentication=True)
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
