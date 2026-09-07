from __future__ import annotations

import calendar as month_calendar
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime, time, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from app_config import load_app_config
from attendance_history import (
    events_for_date,
    import_existing_logs,
    month_records,
    recent_records,
    record_for_date,
    source_label,
)
from daily_plans import day_plan, load_daily_plans, month_plans, set_day_plan
from holiday_sync import HolidaySyncError, load_official_calendar, sync_year


PROJECT_DIR = Path(__file__).resolve().parent
RUNNER_SCRIPT = PROJECT_DIR / "run_random.ps1"
INSTALL_SCRIPT = PROJECT_DIR / "install_daily_task.ps1"
CLOCK_OUT_RUNNER_SCRIPT = PROJECT_DIR / "run_clock_out.ps1"
CLOCK_OUT_SCHEDULE_SCRIPT = PROJECT_DIR / "schedule_clock_out.ps1"
DAILY_PLAN_SYNC_SCRIPT = PROJECT_DIR / "sync_daily_plan.ps1"
CLOCK_OUT_RESULT_FILE = PROJECT_DIR / "config" / "clock_out_last_result.json"
MORNING_PLAN_FILE = PROJECT_DIR / "config" / "morning_current_plan.json"
LOG_ROOT = PROJECT_DIR / "logs"
ARTIFACTS_DIR = PROJECT_DIR / "artifacts" / "flow_test"
CALENDAR_FILE = PROJECT_DIR / "config" / "calendar_overrides.json"
OFFICIAL_CALENDAR_FILE = PROJECT_DIR / "config" / "official_holidays.json"
TASK_NAME = "Attendance Hub Morning Clock-In"
CLOCK_OUT_TASK_NAME = "Attendance Hub One-Time Clock-Out"
PLANNED_CLOCK_OUT_PREFIX = "Attendance Hub Planned Clock-Out "
APP_CONFIG = load_app_config()
UDID = APP_CONFIG.display_udid

POWERSHELL_EXE = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

SCRCPY_HOME = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "scrcpy-win64-v4.1"
)

SCRCPY_EXE = Path(shutil.which("scrcpy") or SCRCPY_HOME / "scrcpy.exe")
ADB_EXE = Path(shutil.which("adb") or SCRCPY_HOME / "adb.exe")
APPIUM_CMD = Path(os.environ.get("APPDATA", "")) / "npm" / "appium.cmd"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


COLORS = {
    "window": "#08111f",
    "panel": "#0f1b2d",
    "panel_alt": "#132238",
    "border": "#213554",
    "text": "#e9f0fb",
    "muted": "#8da2bf",
    "accent": "#5b8cff",
    "accent_hover": "#76a0ff",
    "success": "#43d39e",
    "warning": "#ffbd59",
    "danger": "#ff6b7a",
    "button": "#1a2c47",
    "button_hover": "#243b5e",
    "terminal": "#07101c",
}


def powershell_command(arguments: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(POWERSHELL_EXE),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            *arguments,
        ],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def query_task(task_name: str = TASK_NAME) -> dict:
    escaped_task_name = task_name.replace("'", "''")
    script = rf"""
$task = Get-ScheduledTask -TaskName '{escaped_task_name}' -ErrorAction SilentlyContinue
if ($null -eq $task) {{
    [pscustomobject]@{{ Exists = $false }} | ConvertTo-Json -Compress
    exit 0
}}
$info = Get-ScheduledTaskInfo -TaskName '{escaped_task_name}'
[pscustomobject]@{{
    Exists = $true
    State = [string]$task.State
    NextRunTime = if ($info.NextRunTime -gt [datetime]::MinValue) {{
        $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')
    }} else {{ '' }}
    LastRunTime = if ($info.LastRunTime -gt [datetime]::MinValue) {{
        $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
    }} else {{ '' }}
    LastTaskResult = $info.LastTaskResult
}} | ConvertTo-Json -Compress
"""
    result = powershell_command(["-Command", script])

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to query scheduled task")

    return json.loads(result.stdout.strip())


def query_next_clock_out_task() -> dict:
    prefix = PLANNED_CLOCK_OUT_PREFIX.replace("'", "''")
    legacy_name = CLOCK_OUT_TASK_NAME.replace("'", "''")
    script = rf"""
$records = @(
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object {{ $_.TaskName -like '{prefix}*' }} |
        ForEach-Object {{
            $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
            if ($info.NextRunTime -gt [datetime]::MinValue) {{
                [pscustomobject]@{{
                    TaskName = $_.TaskName
                    State = [string]$_.State
                    NextRunTime = $info.NextRunTime
                    LastRunTime = $info.LastRunTime
                    LastTaskResult = $info.LastTaskResult
                }}
            }}
        }}
)
$next = $records | Sort-Object NextRunTime | Select-Object -First 1
if ($null -eq $next) {{
    $legacy = Get-ScheduledTask -TaskName '{legacy_name}' -ErrorAction SilentlyContinue
    if ($null -ne $legacy) {{
        $info = Get-ScheduledTaskInfo -TaskName '{legacy_name}'
        $next = [pscustomobject]@{{
            TaskName = '{legacy_name}'
            State = [string]$legacy.State
            NextRunTime = $info.NextRunTime
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
        }}
    }}
}}
if ($null -eq $next) {{
    [pscustomobject]@{{ Exists = $false }} | ConvertTo-Json -Compress
    exit 0
}}
[pscustomobject]@{{
    Exists = $true
    TaskName = $next.TaskName
    State = $next.State
    NextRunTime = if ($next.NextRunTime -gt [datetime]::MinValue) {{
        $next.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')
    }} else {{ '' }}
    LastRunTime = if ($next.LastRunTime -gt [datetime]::MinValue) {{
        $next.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
    }} else {{ '' }}
    LastTaskResult = $next.LastTaskResult
}} | ConvertTo-Json -Compress
"""
    result = powershell_command(["-Command", script])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to query clock-out plans")
    return json.loads(result.stdout.strip())


def load_clock_out_result() -> dict:
    if not CLOCK_OUT_RESULT_FILE.exists():
        return {}
    try:
        data = json.loads(CLOCK_OUT_RESULT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        # The runner may be replacing the small status file while the UI is
        # refreshing. A later refresh will read the complete record.
        return {}


def load_morning_plan() -> dict:
    if not MORNING_PLAN_FILE.exists():
        return {}
    try:
        data = json.loads(MORNING_PLAN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def acknowledge_clock_out_result(result_id: str):
    data = load_clock_out_result()
    if data.get("id") != result_id:
        return
    data["acknowledged"] = True
    temporary_file = CLOCK_OUT_RESULT_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    temporary_file.replace(CLOCK_OUT_RESULT_FILE)


def appium_is_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 4723), timeout=0.7):
            return True
    except OSError:
        return False


def adb_state() -> str:
    if not APP_CONFIG.device_access_enabled:
        return "disabled"
    if not ADB_EXE.is_file():
        return "adb missing"

    result = subprocess.run(
        [str(ADB_EXE), "-s", UDID, "get-state"],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.stdout.strip() if result.returncode == 0 else "offline"


def scrcpy_is_running() -> bool:
    result = subprocess.run(
        ["tasklist.exe", "/fi", "imagename eq scrcpy.exe", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0 and "scrcpy.exe" in result.stdout.lower()


def load_calendar_overrides() -> dict[str, list[str]]:
    defaults = {
        "force_workdays": [],
        "force_holidays": [],
    }

    if not CALENDAR_FILE.exists():
        return defaults

    data = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    workdays = sorted(set(data.get("force_workdays", [])))
    holidays = sorted(set(data.get("force_holidays", [])))

    for value in [*workdays, *holidays]:
        date.fromisoformat(value)

    return {
        "force_workdays": workdays,
        "force_holidays": holidays,
    }


def save_calendar_overrides(data: dict[str, list[str]]):
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "force_workdays": sorted(set(data.get("force_workdays", []))),
        "force_holidays": sorted(set(data.get("force_holidays", []))),
    }
    temporary_file = CALENDAR_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    temporary_file.replace(CALENDAR_FILE)


def calendar_decision(target_date: date) -> tuple[bool, str]:
    decision = calendar_day_status(target_date)
    return decision["should_run"], decision["reason"]


def calendar_day_status(
    target_date: date,
    overrides: dict[str, list[str]] | None = None,
    official: dict | None = None,
) -> dict[str, object]:
    if overrides is None:
        overrides = load_calendar_overrides()
    if official is None:
        official = load_official_calendar(OFFICIAL_CALENDAR_FILE)
    date_text = target_date.isoformat()

    if date_text in overrides["force_workdays"]:
        return {
            "should_run": True,
            "code": "manual_workday",
            "label": "临时执行",
            "reason": "人工设置：临时执行",
        }
    if date_text in overrides["force_holidays"]:
        return {
            "should_run": False,
            "code": "manual_holiday",
            "label": "临时跳过",
            "reason": "人工设置：临时不执行",
        }

    year_record = official.get("years", {}).get(str(target_date.year), {})
    official_workdays = set(year_record.get("workdays", []))
    official_holidays = set(year_record.get("holidays", []))

    if date_text in official_workdays:
        return {
            "should_run": True,
            "code": "official_workday",
            "label": "调休上班",
            "reason": "国务院安排：调休上班日",
        }
    if date_text in official_holidays:
        return {
            "should_run": False,
            "code": "official_holiday",
            "label": "法定休息",
            "reason": "国务院安排：放假日",
        }
    if target_date.weekday() >= 5:
        return {
            "should_run": False,
            "code": "default_weekend",
            "label": "周末跳过",
            "reason": "默认规则：周末不执行",
        }
    return {
        "should_run": True,
        "code": "default_weekday",
        "label": "正常执行",
        "reason": "默认规则：工作日执行",
    }


def next_actual_clock_in_date(
    reference: datetime | None = None,
    morning_plan: dict | None = None,
    overrides: dict[str, list[str]] | None = None,
    official: dict | None = None,
) -> dict[str, object]:
    now = reference or datetime.now()
    today = now.date()
    plan = morning_plan if morning_plan is not None else load_morning_plan()
    plan_date = str(plan.get("date", "")).strip()
    plan_status = str(plan.get("status", "")).strip()
    plan_is_today = plan_date == today.isoformat()
    plan_is_active = plan_is_today and plan_status in {"waiting", "running"}
    plan_is_finished = plan_is_today and plan_status in {
        "success",
        "failed",
        "skipped",
    }

    window_end = datetime.combine(today, time(9, 30))
    if plan_is_finished or (now > window_end and not plan_is_active):
        first_candidate = today + timedelta(days=1)
    else:
        first_candidate = today

    if overrides is None:
        overrides = load_calendar_overrides()
    if official is None:
        official = load_official_calendar(OFFICIAL_CALENDAR_FILE)

    for day_offset in range(732):
        candidate = first_candidate + timedelta(days=day_offset)
        day_status = calendar_day_status(candidate, overrides, official)
        if bool(day_status["should_run"]):
            return {
                "date": candidate,
                "code": day_status["code"],
                "label": day_status["label"],
                "reason": day_status["reason"],
            }

    raise RuntimeError("Unable to find an executable date in the next two years")


def official_calendar_record(year: int) -> dict:
    calendar_data = load_official_calendar(OFFICIAL_CALENDAR_FILE)
    record = calendar_data.get("years", {}).get(str(year), {})
    return record if isinstance(record, dict) else {}


def official_calendar_needs_sync(year: int, maximum_age_days: int = 30) -> bool:
    try:
        record = official_calendar_record(year)
        if not record.get("holidays") or not record.get("source_url"):
            return True
        synced_at = datetime.fromisoformat(str(record.get("synced_at", "")))
        age = datetime.now(synced_at.tzinfo) - synced_at
        return age.days >= maximum_age_days
    except Exception:
        return True


class ControlPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.scrcpy_process: subprocess.Popen | None = None
        self.refresh_running = False
        self.calendar_window: tk.Toplevel | None = None
        self.history_window: tk.Toplevel | None = None
        self.daily_plan_window: tk.Toplevel | None = None
        self.calendar_sync_running = False
        self.history_initialized = False
        self.calendar_selected_date = date.today()
        self.calendar_view_year = date.today().year
        self.calendar_view_month = date.today().month
        self.history_view_year = date.today().year
        self.history_view_month = date.today().month

        self.root.title("Feishu Automation Control Center")
        self.root.geometry("1180x900")
        self.root.minsize(1020, 800)
        self.root.configure(bg=COLORS["window"])

        self.task_exists = False
        self.task_enabled = False

        self._configure_styles()
        self._build_ui()
        self._pump_events()
        self.refresh_status()
        self.root.after(700, self._start_automatic_calendar_sync)
        self.root.after(5000, self._periodic_refresh)

    def _configure_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=COLORS["button"],
            troughcolor=COLORS["terminal"],
            bordercolor=COLORS["terminal"],
            arrowcolor=COLORS["muted"],
        )
        style.configure(
            "History.Treeview",
            background=COLORS["terminal"],
            fieldbackground=COLORS["terminal"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            rowheight=27,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "History.Treeview",
            background=[("selected", COLORS["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "History.Treeview.Heading",
            background=COLORS["button"],
            foreground=COLORS["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _build_ui(self):
        shell = tk.Frame(self.root, bg=COLORS["window"])
        shell.pack(fill="both", expand=True)
        main_scrollbar = ttk.Scrollbar(
            shell,
            orient="vertical",
            style="Dark.Vertical.TScrollbar",
        )
        main_scrollbar.pack(side="right", fill="y")
        self.main_canvas = tk.Canvas(
            shell,
            bg=COLORS["window"],
            highlightthickness=0,
            borderwidth=0,
            yscrollcommand=main_scrollbar.set,
        )
        self.main_canvas.pack(side="left", fill="both", expand=True)
        main_scrollbar.configure(command=self.main_canvas.yview)

        viewport = tk.Frame(self.main_canvas, bg=COLORS["window"])
        self.main_canvas_window = self.main_canvas.create_window(
            (0, 0),
            window=viewport,
            anchor="nw",
        )
        viewport.bind(
            "<Configure>",
            lambda _event: self.main_canvas.configure(
                scrollregion=self.main_canvas.bbox("all")
            ),
        )
        self.main_canvas.bind(
            "<Configure>",
            lambda event: self.main_canvas.itemconfigure(
                self.main_canvas_window,
                width=event.width,
            ),
        )
        self.root.bind(
            "<MouseWheel>",
            lambda event: self.main_canvas.yview_scroll(
                int(-event.delta / 120),
                "units",
            ),
        )

        outer = tk.Frame(viewport, bg=COLORS["window"])
        outer.pack(fill="both", expand=True, padx=28, pady=22)

        header = tk.Frame(outer, bg=COLORS["window"])
        header.pack(fill="x", pady=(0, 18))

        tk.Label(
            header,
            text="飞书自动化控制中心",
            font=("Microsoft YaHei UI", 22, "bold"),
            fg=COLORS["text"],
            bg=COLORS["window"],
        ).pack(anchor="w")

        tk.Label(
            header,
            text="每日任务、设备状态、日志和手机远程控制集中管理",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        ).pack(anchor="w", pady=(5, 0))

        content = tk.Frame(outer, bg=COLORS["window"])
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1, uniform="cards")
        content.grid_columnconfigure(1, weight=1, uniform="cards")
        content.grid_rowconfigure(4, weight=1)

        self._build_task_card(content)
        self._build_device_card(content)
        self._build_clock_out_card(content)
        self._build_action_card(content)
        self._build_history_card(content)
        self._build_log_card(content)

        footer = tk.Frame(outer, bg=COLORS["window"])
        footer.pack(fill="x", pady=(14, 0))
        self.footer_status = tk.Label(
            footer,
            text="正在读取系统状态...",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        )
        self.footer_status.pack(side="left")

        self._button(
            footer,
            "刷新状态",
            self.refresh_status,
            compact=True,
        ).pack(side="right")

    def _card(self, parent, title: str, row: int, column: int, columnspan: int = 1):
        card = tk.Frame(
            parent,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="nsew",
            padx=(0, 9) if column == 0 and columnspan == 1 else (9, 0),
            pady=9,
        )

        tk.Label(
            card,
            text=title,
            font=("Microsoft YaHei UI", 12, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).pack(anchor="w", padx=18, pady=(16, 11))
        return card

    def _status_row(self, parent, label_text: str):
        row = tk.Frame(parent, bg=COLORS["panel"])
        row.pack(fill="x", padx=18, pady=4)
        tk.Label(
            row,
            text=label_text,
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left")
        value = tk.Label(
            row,
            text="读取中",
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        value.pack(side="right")
        return value

    def _button(
        self,
        parent,
        text: str,
        command,
        primary: bool = False,
        compact: bool = False,
        danger: bool = False,
    ):
        if danger:
            background = "#b83f50"
            hover = "#d65364"
        else:
            background = COLORS["accent"] if primary else COLORS["button"]
            hover = COLORS["accent_hover"] if primary else COLORS["button_hover"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            font=("Microsoft YaHei UI", 9, "bold"),
            fg="#ffffff",
            bg=background,
            activeforeground="#ffffff",
            activebackground=hover,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=13 if compact else 16,
            pady=6 if compact else 9,
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=background))
        return button

    def _build_task_card(self, parent):
        card = self._card(parent, "每日自动化", 0, 0)
        self.task_state_label = self._status_row(card, "任务状态")
        self.next_run_label = self._status_row(card, "下次系统唤醒")
        self.next_clock_in_label = self._status_row(card, "下次实际打卡日期")
        self.morning_plan_label = self._status_row(card, "本次计划")
        self.last_result_label = self._status_row(card, "上次结果")
        self.calendar_today_label = self._status_row(card, "今日日历")

        actions = tk.Frame(card, bg=COLORS["panel"])
        actions.pack(fill="x", padx=18, pady=(13, 17))
        self.install_button = self._button(
            actions,
            "安装 / 更新任务",
            self.install_task,
            primary=True,
        )
        self.install_button.pack(side="left", padx=(0, 8))
        self.toggle_button = self._button(
            actions,
            "启用 / 停用",
            self.toggle_task,
        )
        self.toggle_button.pack(side="left")

    def _build_device_card(self, parent):
        card = self._card(parent, "设备与服务", 0, 1)
        self.device_state_label = self._status_row(card, f"Android 设备 {UDID}")
        self.appium_state_label = self._status_row(card, "Appium 127.0.0.1:4723")
        self.scrcpy_state_label = self._status_row(card, "手机画面窗口")

        actions = tk.Frame(card, bg=COLORS["panel"])
        actions.pack(fill="x", padx=18, pady=(13, 17))
        self.phone_button = self._button(
            actions,
            "打开手机画面",
            self.launch_phone_screen,
            primary=True,
        )
        self.phone_button.pack(side="left", padx=(0, 8))
        self._button(actions, "启动 Appium", self.start_appium).pack(side="left")

    def _build_clock_out_card(self, parent):
        card = self._card(parent, "下班打卡", 1, 0, columnspan=2)

        status_row = tk.Frame(card, bg=COLORS["panel"])
        status_row.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            status_row,
            text="下一条日期计划",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left")
        self.clock_out_task_state_label = tk.Label(
            status_row,
            text="读取中",
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        self.clock_out_task_state_label.pack(side="left", padx=(10, 24))
        tk.Label(
            status_row,
            text="计划时间",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left")
        self.clock_out_next_run_label = tk.Label(
            status_row,
            text="--",
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        self.clock_out_next_run_label.pack(side="left", padx=(10, 0))

        controls = tk.Frame(card, bg=COLORS["panel"])
        controls.pack(fill="x", padx=18, pady=(0, 17))

        self.clock_out_now_button = self._button(
            controls,
            "立即下班 / 更新",
            self.run_clock_out_now,
            danger=True,
        )
        self.clock_out_now_button.pack(side="left", padx=(0, 8))
        self.clock_out_test_button = self._button(
            controls,
            "安全测试识别",
            self.test_clock_out_detection,
        )
        self.clock_out_test_button.pack(side="left", padx=(0, 20))

        default_time = datetime.now().replace(minute=0, second=0, microsecond=0)
        if default_time <= datetime.now():
            default_time += timedelta(hours=1)
        self.clock_out_year_var = tk.StringVar(value=f"{default_time.year:04d}")
        self.clock_out_month_var = tk.StringVar(value=f"{default_time.month:02d}")
        self.clock_out_day_var = tk.StringVar(value=f"{default_time.day:02d}")
        self.clock_out_hour_var = tk.StringVar(value=f"{default_time.hour:02d}")
        self.clock_out_minute_var = tk.StringVar(value=f"{default_time.minute:02d}")

        tk.Label(
            controls,
            text="日期",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(0, 6))
        self._clock_out_spinbox(
            controls,
            self.clock_out_year_var,
            2020,
            2100,
            width=5,
            number_format="%04.0f",
            command=self._refresh_clock_out_day_range,
            wrap=False,
        ).pack(side="left")
        self._clock_out_separator(controls, "-").pack(side="left")
        self._clock_out_spinbox(
            controls,
            self.clock_out_month_var,
            1,
            12,
            width=3,
            number_format="%02.0f",
            command=self._refresh_clock_out_day_range,
        ).pack(side="left")
        self._clock_out_separator(controls, "-").pack(side="left")
        self.clock_out_day_spinbox = self._clock_out_spinbox(
            controls,
            self.clock_out_day_var,
            1,
            31,
            width=3,
            number_format="%02.0f",
        )
        self.clock_out_day_spinbox.pack(side="left", padx=(0, 10))
        self._refresh_clock_out_day_range()
        tk.Label(
            controls,
            text="时间",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(0, 6))
        self._clock_out_spinbox(
            controls,
            self.clock_out_hour_var,
            0,
            23,
            width=3,
            number_format="%02.0f",
        ).pack(side="left")
        self._clock_out_separator(controls, ":").pack(side="left")
        self._clock_out_spinbox(
            controls,
            self.clock_out_minute_var,
            0,
            59,
            width=3,
            number_format="%02.0f",
        ).pack(side="left", padx=(0, 10))
        self.clock_out_schedule_button = self._button(
            controls,
            "设置定时下班",
            self.schedule_clock_out,
            primary=True,
            compact=True,
        )
        self.clock_out_schedule_button.pack(side="left", padx=(0, 8))
        self._button(
            controls,
            "取消所选日期",
            self.cancel_clock_out_schedule,
            compact=True,
        ).pack(side="left")

    def _clock_out_spinbox(
        self,
        parent,
        variable: tk.StringVar,
        minimum: int,
        maximum: int,
        width: int,
        number_format: str,
        command=None,
        wrap: bool = True,
    ):
        spinbox = tk.Spinbox(
            parent,
            textvariable=variable,
            from_=minimum,
            to=maximum,
            increment=1,
            format=number_format,
            wrap=wrap,
            command=command,
            width=width,
            font=("Cascadia Mono", 10),
            fg=COLORS["text"],
            bg=COLORS["terminal"],
            buttonbackground=COLORS["button"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent"],
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
        )
        if command:
            spinbox.bind("<FocusOut>", lambda _event: command())
            spinbox.bind("<Return>", lambda _event: command())
        return spinbox

    def _clock_out_separator(self, parent, text: str):
        return tk.Label(
            parent,
            text=text,
            font=("Cascadia Mono", 10, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
            padx=2,
        )

    def _refresh_clock_out_day_range(self):
        try:
            year = int(self.clock_out_year_var.get())
            month = int(self.clock_out_month_var.get())
            maximum_day = month_calendar.monthrange(year, month)[1]
        except (TypeError, ValueError):
            return

        self.clock_out_day_spinbox.configure(to=maximum_day)
        try:
            current_day = int(self.clock_out_day_var.get())
        except ValueError:
            current_day = 1
        current_day = min(max(1, current_day), maximum_day)
        self.clock_out_day_var.set(f"{current_day:02d}")

    def _build_action_card(self, parent):
        card = self._card(parent, "快捷操作", 2, 0, columnspan=2)
        actions = tk.Frame(card, bg=COLORS["panel"])
        actions.pack(fill="x", padx=18, pady=(0, 17))

        self.clock_in_now_button = self._button(
            actions,
            "立即上班打卡",
            self.run_clock_in_now,
            danger=True,
        )
        self.clock_in_now_button.pack(side="left", padx=(0, 8))
        self.test_button = self._button(
            actions,
            "立即安全测试",
            self.run_safe_test,
            primary=True,
        )
        self.test_button.pack(side="left", padx=(0, 8))
        self._button(actions, "打开今日日志", self.open_today_log).pack(side="left", padx=(0, 8))
        self._button(actions, "工作日管理", self.open_calendar_editor).pack(side="left", padx=(0, 8))
        self._button(actions, "打开截图目录", lambda: self.open_path(ARTIFACTS_DIR)).pack(side="left", padx=(0, 8))
        self._button(actions, "任务计划程序", self.open_task_scheduler).pack(side="left")

    def _build_history_card(self, parent):
        card = self._card(parent, "最近打卡记录", 3, 0, columnspan=2)
        table_frame = tk.Frame(card, bg=COLORS["panel"])
        table_frame.pack(fill="x", padx=18)

        recent_scrollbar = ttk.Scrollbar(
            table_frame,
            style="Dark.Vertical.TScrollbar",
        )
        recent_scrollbar.pack(side="right", fill="y")
        self.recent_history_tree = ttk.Treeview(
            table_frame,
            columns=("date", "weekday", "clock_in", "clock_out", "status"),
            show="headings",
            height=4,
            style="History.Treeview",
            selectmode="browse",
            yscrollcommand=recent_scrollbar.set,
        )
        headings = {
            "date": "日期",
            "weekday": "星期",
            "clock_in": "上班实际时间",
            "clock_out": "下班 / 最后更新",
            "status": "确认状态",
        }
        widths = {
            "date": 150,
            "weekday": 90,
            "clock_in": 180,
            "clock_out": 200,
            "status": 140,
        }
        for column, heading in headings.items():
            self.recent_history_tree.heading(column, text=heading)
            self.recent_history_tree.column(
                column,
                width=widths[column],
                anchor="center",
                stretch=True,
            )
        self.recent_history_tree.pack(side="left", fill="x", expand=True)
        recent_scrollbar.configure(command=self.recent_history_tree.yview)
        self.recent_history_tree.bind(
            "<Double-1>", lambda _event: self.open_history_window()
        )

        actions = tk.Frame(card, bg=COLORS["panel"])
        actions.pack(fill="x", padx=18, pady=(9, 14))
        self._button(
            actions,
            "查看全部历史",
            self.open_history_window,
            primary=True,
            compact=True,
        ).pack(side="left", padx=(0, 8))
        self._button(
            actions,
            "打开所选日期日志",
            self.open_selected_history_log,
            compact=True,
        ).pack(side="left", padx=(0, 8))
        self.history_summary_label = tk.Label(
            actions,
            text="正在读取历史记录...",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        )
        self.history_summary_label.pack(side="right")

    def _build_log_card(self, parent):
        card = self._card(parent, "运行动态", 4, 0, columnspan=2)
        log_frame = tk.Frame(card, bg=COLORS["panel"])
        log_frame.pack(fill="both", expand=True, padx=18, pady=(0, 17))

        scrollbar = ttk.Scrollbar(log_frame, style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            log_frame,
            bg=COLORS["terminal"],
            fg="#c7d6ea",
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent"],
            font=("Cascadia Mono", 9),
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            height=12,
            wrap="word",
            state="disabled",
            yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)
        self.append_log("Control center ready. Safe test mode never clicks the real clock-in button.")

    def append_log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{timestamp}  {message.rstrip()}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def _history_time_text(record: dict, prefix: str) -> str:
        actual_time = str(record.get(f"{prefix}_time") or "")
        status = str(record.get(f"{prefix}_status") or "missing")
        if status == "unconfirmed":
            return f"{actual_time}（更新待确认）" if actual_time else "待确认"
        return actual_time or "--"

    @staticmethod
    def _calendar_history_time_text(record: dict, prefix: str) -> str:
        actual_time = str(record.get(f"{prefix}_time") or "")
        status = str(record.get(f"{prefix}_status") or "missing")
        if status == "unconfirmed":
            return f"{actual_time}?" if actual_time else "待确认"
        return actual_time or "--"

    @staticmethod
    def _history_status_text(record: dict) -> str:
        statuses = {
            str(record.get("clock_in_status") or "missing"),
            str(record.get("clock_out_status") or "missing"),
        }
        if "unconfirmed" in statuses:
            return "存在待确认记录"
        if statuses == {"confirmed"}:
            return "上下班已确认"
        if "confirmed" in statuses:
            return "部分已确认"
        return "尚无确认时间"

    def _apply_recent_history(self, payload: dict):
        for item_id in self.recent_history_tree.get_children():
            self.recent_history_tree.delete(item_id)

        if not payload.get("ok"):
            self.history_summary_label.configure(
                text="历史记录读取失败",
                fg=COLORS["danger"],
            )
            return

        records = payload.get("records") or []
        weekday_names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        for record in records:
            record_date = date.fromisoformat(str(record["attendance_date"]))
            self.recent_history_tree.insert(
                "",
                "end",
                iid=record_date.isoformat(),
                values=(
                    record_date.isoformat(),
                    weekday_names[record_date.weekday()],
                    self._history_time_text(record, "clock_in"),
                    self._history_time_text(record, "clock_out"),
                    self._history_status_text(record),
                ),
            )

        import_stats = payload.get("import_stats") or {}
        imported_count = int(import_stats.get("observations_added", 0) or 0)
        summary = f"已显示最近 {len(records)} 天"
        if imported_count:
            summary += f" · 本次从日志恢复 {imported_count} 条"
        self.history_summary_label.configure(text=summary, fg=COLORS["muted"])

    def open_selected_history_log(self):
        selected = self.recent_history_tree.selection()
        if not selected:
            messagebox.showinfo("没有选择", "请先选择一条打卡记录。")
            return
        selected_date = date.fromisoformat(selected[0])
        log_file = (
            LOG_ROOT
            / selected_date.strftime("%Y-%m")
            / f"{selected_date:%Y-%m-%d}.log"
        )
        self.open_path(log_file if log_file.exists() else log_file.parent)

    def open_history_window(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.deiconify()
            self.history_window.lift()
            self.history_window.focus_force()
            return

        recent_selection = self.recent_history_tree.selection()
        if recent_selection:
            selected_date = date.fromisoformat(recent_selection[0])
            self.history_view_year = selected_date.year
            self.history_view_month = selected_date.month

        window = tk.Toplevel(self.root)
        self.history_window = window
        window.title("打卡历史")
        window.geometry("1000x720")
        window.minsize(860, 620)
        window.configure(bg=COLORS["window"])
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_history_window)

        container = tk.Frame(window, bg=COLORS["window"])
        container.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(
            container,
            text="实际打卡历史",
            font=("Microsoft YaHei UI", 19, "bold"),
            fg=COLORS["text"],
            bg=COLORS["window"],
        ).pack(anchor="w")
        tk.Label(
            container,
            text="时间来自飞书页面显示；计划时间和脚本完成时间不会写入实际打卡栏。",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        ).pack(anchor="w", pady=(4, 12))

        navigation = tk.Frame(container, bg=COLORS["panel"])
        navigation.pack(fill="x", pady=(0, 10))
        self._button(
            navigation,
            "‹ 上一月",
            lambda: self._history_change_month(-1),
            compact=True,
        ).pack(side="left", padx=12, pady=10)
        self._button(
            navigation,
            "回到本月",
            self._history_go_today,
            compact=True,
        ).pack(side="left", pady=10)
        self._button(
            navigation,
            "刷新",
            self._reload_history_window,
            compact=True,
        ).pack(side="left", padx=(8, 0), pady=10)
        self._button(
            navigation,
            "下一月 ›",
            lambda: self._history_change_month(1),
            compact=True,
        ).pack(side="right", padx=12, pady=10)
        self.history_month_label = tk.Label(
            navigation,
            text="",
            font=("Microsoft YaHei UI", 15, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        self.history_month_label.place(relx=0.5, rely=0.5, anchor="center")

        table_panel = tk.Frame(
            container,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        table_panel.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_panel, style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.history_tree = ttk.Treeview(
            table_panel,
            columns=("date", "weekday", "clock_in", "clock_out", "status"),
            show="headings",
            style="History.Treeview",
            selectmode="browse",
            yscrollcommand=scrollbar.set,
        )
        for column, title, width in (
            ("date", "日期", 160),
            ("weekday", "星期", 100),
            ("clock_in", "上班实际时间", 180),
            ("clock_out", "下班 / 最后更新", 200),
            ("status", "确认状态", 170),
        ):
            self.history_tree.heading(column, text=title)
            self.history_tree.column(column, width=width, anchor="center")
        self.history_tree.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.history_tree.yview)
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selection_changed)

        detail_panel = tk.Frame(container, bg=COLORS["panel_alt"])
        detail_panel.pack(fill="x", pady=(10, 0))
        self.history_detail_text = tk.Text(
            detail_panel,
            height=7,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=9,
            state="disabled",
        )
        self.history_detail_text.pack(side="left", fill="both", expand=True)
        detail_actions = tk.Frame(detail_panel, bg=COLORS["panel_alt"])
        detail_actions.pack(side="right", padx=12, pady=10)
        self._button(
            detail_actions,
            "打开当日日志",
            self._open_history_selected_log,
            compact=True,
        ).pack()

        self._reload_history_window()

    def _close_history_window(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.destroy()
        self.history_window = None

    def _history_change_month(self, delta: int):
        month_index = self.history_view_year * 12 + self.history_view_month - 1 + delta
        self.history_view_year, zero_based_month = divmod(month_index, 12)
        self.history_view_month = zero_based_month + 1
        self._reload_history_window()

    def _history_go_today(self):
        today = date.today()
        self.history_view_year = today.year
        self.history_view_month = today.month
        self._reload_history_window()

    def _reload_history_window(self):
        if not self.history_window or not self.history_window.winfo_exists():
            return
        self.history_month_label.configure(
            text=f"{self.history_view_year} 年 {self.history_view_month} 月"
        )
        for item_id in self.history_tree.get_children():
            self.history_tree.delete(item_id)

        try:
            records = month_records(self.history_view_year, self.history_view_month)
        except Exception as exc:
            messagebox.showerror("历史记录读取失败", str(exc), parent=self.history_window)
            return

        weekday_names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        for record in records:
            record_date = date.fromisoformat(str(record["attendance_date"]))
            self.history_tree.insert(
                "",
                "end",
                iid=record_date.isoformat(),
                values=(
                    record_date.isoformat(),
                    weekday_names[record_date.weekday()],
                    self._history_time_text(record, "clock_in"),
                    self._history_time_text(record, "clock_out"),
                    self._history_status_text(record),
                ),
            )

        children = self.history_tree.get_children()
        if children:
            self.history_tree.selection_set(children[-1])
            self.history_tree.focus(children[-1])
            self.history_tree.see(children[-1])
            self._history_selection_changed()
        else:
            self._set_history_detail("本月还没有可确认的打卡时间。")

    def _set_history_detail(self, text: str):
        self.history_detail_text.configure(state="normal")
        self.history_detail_text.delete("1.0", "end")
        self.history_detail_text.insert("1.0", text)
        self.history_detail_text.configure(state="disabled")

    def _history_selection_changed(self, _event=None):
        selected = self.history_tree.selection()
        if not selected:
            return
        date_text = selected[0]
        record = record_for_date(date_text)
        events = events_for_date(date_text)
        detail_lines = [
            f"{date_text}  ·  {self._history_status_text(record)}",
            (
                "上班："
                f"{self._history_time_text(record, 'clock_in')}  ·  "
                f"{source_label(record.get('clock_in_source'))}"
            ),
            (
                "下班："
                f"{self._history_time_text(record, 'clock_out')}  ·  "
                f"{source_label(record.get('clock_out_source'))}"
            ),
            "",
            "时间变化记录：",
        ]
        for event in events:
            kind_text = "上班" if event["kind"] == "clock_in" else "下班"
            actual_text = event.get("actual_time") or "待确认"
            observed_at = str(event.get("observed_at") or "").replace("T", " ")[:19]
            status_text = "已确认" if event.get("status") == "confirmed" else "待确认"
            detail_lines.append(
                f"  {kind_text} {actual_text} · {status_text} · "
                f"{source_label(event.get('source'))} · 识别于 {observed_at}"
            )
        self._set_history_detail("\n".join(detail_lines))

    def _open_history_selected_log(self):
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("没有选择", "请先选择一条打卡记录。", parent=self.history_window)
            return
        selected_date = date.fromisoformat(selected[0])
        log_file = (
            LOG_ROOT
            / selected_date.strftime("%Y-%m")
            / f"{selected_date:%Y-%m-%d}.log"
        )
        self.open_path(log_file if log_file.exists() else log_file.parent)

    def _set_status(self, label: tk.Label, text: str, status: str = "normal"):
        colors = {
            "success": COLORS["success"],
            "warning": COLORS["warning"],
            "danger": COLORS["danger"],
            "normal": COLORS["text"],
        }
        label.configure(text=text, fg=colors[status])

    def _periodic_refresh(self):
        self.refresh_status()
        self.root.after(5000, self._periodic_refresh)

    def refresh_status(self):
        if self.refresh_running:
            return
        self.refresh_running = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            task = query_task()
        except Exception as exc:
            task = {"Exists": False, "Error": str(exc)}

        try:
            clock_out_task = query_next_clock_out_task()
        except Exception as exc:
            clock_out_task = {"Exists": False, "Error": str(exc)}

        try:
            device = adb_state()
        except Exception as exc:
            device = f"error: {exc}"

        status = {
            "task": task,
            "morning_plan": load_morning_plan(),
            "clock_out_task": clock_out_task,
            "clock_out_result": load_clock_out_result(),
            "device": device,
            "appium": appium_is_listening(),
            "scrcpy": scrcpy_is_running(),
        }

        try:
            import_stats = {}
            if not self.history_initialized:
                import_stats = import_existing_logs(LOG_ROOT)
                self.history_initialized = True
            status["attendance_history"] = {
                "ok": True,
                "records": recent_records(7),
                "import_stats": import_stats,
            }
        except Exception as exc:
            status["attendance_history"] = {
                "ok": False,
                "error": str(exc),
            }

        try:
            next_clock_in = next_actual_clock_in_date(
                morning_plan=status["morning_plan"]
            )
            status["next_clock_in"] = {
                "ok": True,
                **next_clock_in,
            }
        except Exception as exc:
            status["next_clock_in"] = {
                "ok": False,
                "error": str(exc),
            }

        try:
            should_run, calendar_reason = calendar_decision(date.today())
            status["calendar"] = {
                "ok": True,
                "should_run": should_run,
                "reason": calendar_reason,
            }
        except Exception as exc:
            status["calendar"] = {
                "ok": False,
                "reason": str(exc),
            }

        self.events.put(("status", status))

    def _apply_status(self, status: dict):
        task = status["task"]
        self.task_exists = bool(task.get("Exists"))
        task_state = task.get("State", "Not installed")
        self.task_enabled = self.task_exists and task_state != "Disabled"

        if self.task_exists:
            state_color = "success" if self.task_enabled else "warning"
            self._set_status(self.task_state_label, task_state, state_color)
            self._set_status(self.next_run_label, task.get("NextRunTime") or "--")
            next_clock_in = status.get("next_clock_in") or {}
            if not self.task_enabled:
                self._set_status(self.next_clock_in_label, "任务已停用", "warning")
            elif next_clock_in.get("ok"):
                target_date = next_clock_in["date"]
                weekday_names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
                target_text = (
                    f"{target_date:%Y-%m-%d}（{weekday_names[target_date.weekday()]}）"
                    f" · {next_clock_in['label']}"
                )
                self._set_status(self.next_clock_in_label, target_text, "success")
            else:
                self._set_status(self.next_clock_in_label, "日历计算失败", "danger")
            result = task.get("LastTaskResult")
            result_text = "Success (0)" if result == 0 else str(result)
            result_color = "success" if result == 0 else "warning"
            self._set_status(self.last_result_label, result_text, result_color)
            self.toggle_button.configure(text="停用任务" if self.task_enabled else "启用任务")
        else:
            self._set_status(self.task_state_label, "未安装", "danger")
            self._set_status(self.next_run_label, "--")
            self._set_status(self.next_clock_in_label, "--")
            self._set_status(self.last_result_label, "--")
            self.toggle_button.configure(text="启用 / 停用")

        morning_plan = status.get("morning_plan") or {}
        plan_target = str(morning_plan.get("target_time", "")).strip()
        plan_status = str(morning_plan.get("status", "")).strip()
        try:
            plan_attempts = int(morning_plan.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            plan_attempts = 0
        status_labels = {
            "waiting": ("等待执行", "warning"),
            "running": (
                f"正在执行，第 {plan_attempts} 次" if plan_attempts else "正在执行",
                "warning",
            ),
            "success": ("已完成", "success"),
            "failed": ("执行失败", "danger"),
            "skipped": ("今日跳过", "warning"),
        }
        if plan_status in status_labels:
            plan_suffix, plan_color = status_labels[plan_status]
            plan_text = f"{plan_target} · {plan_suffix}" if plan_target else plan_suffix
            self._set_status(self.morning_plan_label, plan_text, plan_color)
        else:
            self._set_status(self.morning_plan_label, "--")

        self._apply_recent_history(status.get("attendance_history") or {})

        clock_out_task = status["clock_out_task"]
        if clock_out_task.get("Error"):
            self._set_status(self.clock_out_task_state_label, "读取失败", "danger")
            self._set_status(self.clock_out_next_run_label, "--")
        elif not clock_out_task.get("Exists"):
            self._set_status(self.clock_out_task_state_label, "未设置", "normal")
            self._set_status(self.clock_out_next_run_label, "--")
        else:
            next_run = clock_out_task.get("NextRunTime") or ""
            state = str(clock_out_task.get("State", ""))
            if state == "Running":
                state_text, state_color = "正在执行", "warning"
            elif next_run:
                state_text, state_color = "已设置", "success"
            else:
                result = clock_out_task.get("LastTaskResult")
                state_text = "已完成" if result == 0 else "已结束"
                state_color = "success" if result == 0 else "warning"
            self._set_status(self.clock_out_task_state_label, state_text, state_color)
            self._set_status(self.clock_out_next_run_label, next_run or "无后续计划")

        clock_out_result = status.get("clock_out_result") or {}
        result_status = str(clock_out_result.get("status", ""))
        result_source = str(clock_out_result.get("source", ""))
        if (
            result_source == "scheduled"
            and result_status in {"failed", "busy"}
            and not (clock_out_task.get("NextRunTime") or "")
        ):
            attempts = int(clock_out_result.get("attempts", 0) or 0)
            failure_label = f"失败（已尝试 {attempts} 次）" if attempts else "启动失败"
            self._set_status(self.clock_out_task_state_label, failure_label, "danger")
            failure_time = str(clock_out_result.get("timestamp", "")).replace("T", " ")[:19]
            self._set_status(self.clock_out_next_run_label, f"失败时间 {failure_time}")

            if not bool(clock_out_result.get("acknowledged")):
                result_id = str(clock_out_result.get("id", ""))
                message = str(clock_out_result.get("message", "未知错误"))
                messagebox.showwarning(
                    "下班打卡失败提醒",
                    f"自动下班/更新打卡没有成功。\n\n{message}\n\n"
                    "相关进程已经退出，请查看运行日志和异常截图。",
                )
                if result_id:
                    try:
                        acknowledge_clock_out_result(result_id)
                    except OSError as exc:
                        self.append_log(f"Unable to acknowledge clock-out alert: {exc}")

        calendar_status = status["calendar"]
        if calendar_status["ok"]:
            should_run = calendar_status["should_run"]
            prefix = "执行" if should_run else "跳过"
            self._set_status(
                self.calendar_today_label,
                f'{prefix} · {calendar_status["reason"]}',
                "success" if should_run else "warning",
            )
        else:
            self._set_status(self.calendar_today_label, "配置错误", "danger")

        device_ok = status["device"] == "device"
        self._set_status(
            self.device_state_label,
            "已连接" if device_ok else status["device"],
            "success" if device_ok else "danger",
        )
        self._set_status(
            self.appium_state_label,
            "运行中" if status["appium"] else "未运行（任务会自动启动）",
            "success" if status["appium"] else "warning",
        )
        self._set_status(
            self.scrcpy_state_label,
            "已打开" if status["scrcpy"] else "未打开",
            "success" if status["scrcpy"] else "normal",
        )

        self.footer_status.configure(
            text=f"最后刷新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.refresh_running = False

    def _pump_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._apply_status(payload)
                elif kind == "log":
                    self.append_log(str(payload))
                elif kind == "test_done":
                    self.test_button.configure(state="normal", text="立即安全测试")
                    self.clock_in_now_button.configure(state="normal")
                    self.clock_out_now_button.configure(state="normal")
                    self.clock_out_test_button.configure(state="normal")
                    self.append_log(str(payload))
                    self.refresh_status()
                elif kind == "action_done":
                    self.append_log(str(payload))
                    self.refresh_status()
                elif kind == "calendar_sync_done":
                    self._apply_calendar_sync_result(payload)
                elif kind == "clock_out_done":
                    self._apply_clock_out_result(payload)
                elif kind == "clock_out_schedule_done":
                    self._apply_clock_out_schedule_result(payload)
                elif kind == "daily_plan_done":
                    self._apply_daily_plan_result(payload)
                elif kind == "clock_in_done":
                    self._apply_clock_in_result(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_events)

    def install_task(self):
        if not INSTALL_SCRIPT.is_file():
            messagebox.showerror("文件缺失", str(INSTALL_SCRIPT))
            return
        self.append_log("Installing or updating the daily scheduled task...")
        threading.Thread(target=self._install_task_worker, daemon=True).start()

    def _install_task_worker(self):
        result = powershell_command(["-File", str(INSTALL_SCRIPT)], timeout=60)
        if result.returncode == 0:
            message = "Daily scheduled task installed/updated successfully."
        else:
            message = f"Task installation failed: {result.stderr.strip()}"
        self.events.put(("action_done", message))

    def toggle_task(self):
        if not self.task_exists:
            messagebox.showinfo("任务未安装", "请先点击“安装 / 更新任务”。")
            return

        verb = "Disable-ScheduledTask" if self.task_enabled else "Enable-ScheduledTask"
        action_name = "Disabling" if self.task_enabled else "Enabling"
        self.append_log(f"{action_name} the daily task...")

        def worker():
            result = powershell_command(
                ["-Command", f"{verb} -TaskName '{TASK_NAME}' | Out-Null"]
            )
            message = (
                "Daily task state updated."
                if result.returncode == 0
                else f"Unable to update task: {result.stderr.strip()}"
            )
            self.events.put(("action_done", message))

        threading.Thread(target=worker, daemon=True).start()

    def launch_phone_screen(self):
        if scrcpy_is_running() or (
            self.scrcpy_process and self.scrcpy_process.poll() is None
        ):
            messagebox.showinfo("手机画面已打开", "scrcpy 窗口当前正在运行。")
            return

        if not APP_CONFIG.device_access_enabled:
            messagebox.showerror("安全锁已启用", "新仓库当前禁止访问手机。")
            return
        launcher = PROJECT_DIR / "start_scrcpy.ps1"
        if not launcher.is_file():
            messagebox.showerror("文件缺失", str(launcher))
            return

        try:
            self.scrcpy_process = subprocess.Popen(
                [
                    str(POWERSHELL_EXE),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(launcher),
                ],
                cwd=str(PROJECT_DIR),
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            self.append_log("Interactive phone screen launched with scrcpy.")
            self.root.after(900, self.refresh_status)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def start_appium(self):
        if not APP_CONFIG.device_access_enabled:
            messagebox.showerror("安全锁已启用", "新仓库当前禁止访问手机。")
            return
        if appium_is_listening():
            messagebox.showinfo("Appium", "Appium 已经在 127.0.0.1:4723 运行。")
            return
        if not APPIUM_CMD.is_file():
            messagebox.showerror("未找到 Appium", str(APPIUM_CMD))
            return

        command_processor = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        try:
            subprocess.Popen(
                [
                    command_processor,
                    "/d",
                    "/c",
                    str(APPIUM_CMD),
                    "--address",
                    "127.0.0.1",
                    "--port",
                    "4723",
                ],
                cwd=str(PROJECT_DIR),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
            self.append_log("Appium start requested; waiting for port 4723.")
            self.root.after(2500, self.refresh_status)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def run_clock_out_now(self):
        confirmed = messagebox.askyesno(
            "确认下班或更新打卡",
            "程序将立即打开飞书：\n"
            "• 尚未下班时点击“下班打卡”\n"
            "• 已经下班时点击“更新打卡”\n\n"
            "这会写入真实打卡记录，是否继续？",
            icon="warning",
        )
        if confirmed:
            self._start_clock_out(test_mode=False)

    def test_clock_out_detection(self):
        confirmed = messagebox.askyesno(
            "测试识别提醒",
            "测试模式不会由脚本点击“下班打卡”或“更新打卡”。\n\n"
            "但是，如果飞书开启了“极速打卡”，仅打开假勤页面也可能由飞书"
            "自动写入或刷新打卡时间。\n\n是否继续？",
            icon="warning",
        )
        if confirmed:
            self._start_clock_out(test_mode=True)

    def _start_clock_out(self, test_mode: bool):
        if not CLOCK_OUT_RUNNER_SCRIPT.is_file():
            messagebox.showerror("文件缺失", str(CLOCK_OUT_RUNNER_SCRIPT))
            return

        self.clock_out_now_button.configure(state="disabled")
        self.clock_out_test_button.configure(state="disabled")
        self.clock_in_now_button.configure(state="disabled")
        self.test_button.configure(state="disabled")
        action = "safe clock-out detection test" if test_mode else "immediate clock-out"
        self.append_log(f"Starting {action}...")
        threading.Thread(
            target=self._clock_out_worker,
            args=(test_mode,),
            daemon=True,
        ).start()

    def _clock_out_worker(self, test_mode: bool):
        command = [
            str(POWERSHELL_EXE),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(CLOCK_OUT_RUNNER_SCRIPT),
        ]
        if test_mode:
            command.append("-TestMode")

        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("log", line.rstrip()))
        exit_code = process.wait()
        self.events.put(
            (
                "clock_out_done",
                {"exit_code": exit_code, "test_mode": test_mode},
            )
        )

    def _apply_clock_out_result(self, payload: object):
        result = dict(payload) if isinstance(payload, dict) else {}
        self.clock_out_now_button.configure(state="normal")
        self.clock_out_test_button.configure(state="normal")
        self.clock_in_now_button.configure(state="normal")
        self.test_button.configure(state="normal")
        exit_code = int(result.get("exit_code", 1))
        test_mode = bool(result.get("test_mode"))

        if exit_code == 0:
            if test_mode:
                message = (
                    "下班打卡或更新打卡按钮识别成功；脚本没有点击。\n\n"
                    "注意：如果飞书开启极速打卡，打开假勤页面本身仍可能自动刷新时间。"
                )
            else:
                message = "下班打卡流程已执行完成，请结合日志或手机画面确认结果。"
            self.append_log(
                "Clock-out detection test succeeded."
                if test_mode
                else "Immediate clock-out flow completed."
            )
            messagebox.showinfo("执行完成", message)
        else:
            self.append_log(f"Clock-out flow failed with exit code {exit_code}.")
            messagebox.showerror(
                "执行失败",
                "没有安全完成下班打卡。程序没有猜测点击，请查看运行日志和截图。",
            )
        self.refresh_status()

    def run_clock_in_now(self):
        confirmed = messagebox.askyesno(
            "确认立即上班打卡",
            "程序将立即打开飞书并检查上班状态：\n"
            "• 如果极速打卡已经成功，只核验并退出\n"
            "• 如果确认尚未上班打卡，点击上班打卡按钮\n\n"
            "这是手动操作，会绕过工作日和 09:00–09:30 时间限制，"
            "并可能写入真实打卡记录。是否继续？",
            icon="warning",
        )
        if not confirmed:
            return
        if not RUNNER_SCRIPT.is_file():
            messagebox.showerror("文件缺失", str(RUNNER_SCRIPT))
            return

        self.clock_in_now_button.configure(state="disabled")
        self.test_button.configure(state="disabled")
        self.clock_out_now_button.configure(state="disabled")
        self.clock_out_test_button.configure(state="disabled")
        self.append_log("Starting immediate manual clock-in...")
        threading.Thread(target=self._clock_in_worker, daemon=True).start()

    def _clock_in_worker(self):
        try:
            process = subprocess.Popen(
                [
                    str(POWERSHELL_EXE),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(RUNNER_SCRIPT),
                    "-ManualClockIn",
                ],
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            self.events.put(("log", f"Unable to start immediate clock-in: {exc}"))
            self.events.put(
                ("clock_in_done", {"exit_code": 1, "outcome": "start-failed"})
            )
            return

        outcome = "unknown"
        assert process.stdout is not None
        for line in process.stdout:
            clean_line = line.rstrip()
            self.events.put(("log", clean_line))
            if "Morning clock-in is already complete" in clean_line:
                outcome = "already-complete"
            elif "[CLICK] Morning clock-in button clicked" in clean_line:
                outcome = "clicked"

        exit_code = process.wait()
        self.events.put(
            (
                "clock_in_done",
                {"exit_code": exit_code, "outcome": outcome},
            )
        )

    def _apply_clock_in_result(self, payload: object):
        result = dict(payload) if isinstance(payload, dict) else {}
        self.clock_in_now_button.configure(state="normal")
        self.test_button.configure(state="normal")
        self.clock_out_now_button.configure(state="normal")
        self.clock_out_test_button.configure(state="normal")

        exit_code = int(result.get("exit_code", 1))
        outcome = str(result.get("outcome", "unknown"))
        if exit_code == 0:
            if outcome == "already-complete":
                message = "检测到上班卡已经完成（包括极速打卡情况），没有重复点击。"
            elif outcome == "clicked":
                message = "已识别并点击上班打卡按钮，请结合日志确认最终记录。"
            else:
                message = "上班打卡流程已执行完成，请结合日志确认最终记录。"
            self.append_log(f"Immediate clock-in completed: {outcome}.")
            messagebox.showinfo("执行完成", message)
        else:
            self.append_log(f"Immediate clock-in failed with exit code {exit_code}.")
            messagebox.showerror(
                "执行失败",
                "没有安全完成上班打卡。程序没有猜测点击，请查看运行日志和截图。",
            )
        self.refresh_status()

    def _sync_day_plan_tasks(
        self,
        target_date: date,
        clock_in: str,
        clock_out: str,
    ) -> subprocess.CompletedProcess:
        return powershell_command(
            [
                "-File",
                str(DAILY_PLAN_SYNC_SCRIPT),
                "-PlanDate",
                target_date.isoformat(),
                "-ClockIn",
                clock_in,
                "-ClockOut",
                clock_out,
            ],
            timeout=60,
        )

    def _save_day_plan_worker(
        self,
        target_date: date,
        clock_in: str,
        clock_out: str,
        *,
        origin: str,
        operation: str,
    ):
        previous = {}
        try:
            previous = day_plan(target_date)
            saved = set_day_plan(
                target_date,
                clock_in=clock_in,
                clock_out=clock_out,
                updated_from=origin,
            )
            result = self._sync_day_plan_tasks(
                target_date,
                str(saved.get("clock_in", "")),
                str(saved.get("clock_out", "")),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            ok = True
            message = result.stdout.strip()
        except Exception as exc:
            ok = False
            message = str(exc)
            try:
                restored = set_day_plan(
                    target_date,
                    clock_in=str(previous.get("clock_in", "")),
                    clock_out=str(previous.get("clock_out", "")),
                    updated_from=str(previous.get("updated_from", "rollback")),
                )
                self._sync_day_plan_tasks(
                    target_date,
                    str(restored.get("clock_in", "")),
                    str(restored.get("clock_out", "")),
                )
            except Exception as rollback_exc:
                message += f"; rollback also failed: {rollback_exc}"

        event_kind = (
            "clock_out_schedule_done"
            if origin == "clock_out_panel"
            else "daily_plan_done"
        )
        self.events.put(
            (
                event_kind,
                {
                    "ok": ok,
                    "operation": operation,
                    "message": message,
                    "plan_date": target_date.isoformat(),
                    "clock_in": clock_in,
                    "clock_out": clock_out,
                    "run_time": (
                        f"{target_date.isoformat()} {clock_out}"
                        if clock_out
                        else ""
                    ),
                },
            )
        )

    def schedule_clock_out(self):
        try:
            run_time = datetime(
                int(self.clock_out_year_var.get()),
                int(self.clock_out_month_var.get()),
                int(self.clock_out_day_var.get()),
                int(self.clock_out_hour_var.get()),
                int(self.clock_out_minute_var.get()),
            )
        except (TypeError, ValueError):
            messagebox.showerror(
                "日期或时间无效",
                "请使用数字框右侧的上下箭头调整，或直接输入有效的年月日、小时和分钟。",
            )
            return

        if run_time <= datetime.now():
            messagebox.showerror(
                "时间已经过去",
                "定时时间必须晚于当前时间；如果需要现在打卡，请使用“立即下班打卡”。",
            )
            return
        if not DAILY_PLAN_SYNC_SCRIPT.is_file():
            messagebox.showerror("文件缺失", str(DAILY_PLAN_SYNC_SCRIPT))
            return

        self.clock_out_schedule_button.configure(state="disabled", text="正在设置...")
        self.append_log(f"Saving exact clock-out plan for {run_time:%Y-%m-%d %H:%M}...")
        threading.Thread(
            target=self._schedule_clock_out_worker,
            args=(run_time,),
            daemon=True,
        ).start()

    def _schedule_clock_out_worker(self, run_time: datetime):
        try:
            existing = day_plan(run_time.date())
        except Exception as exc:
            self.events.put(
                (
                    "clock_out_schedule_done",
                    {
                        "ok": False,
                        "operation": "schedule_clock_out",
                        "message": str(exc),
                    },
                )
            )
            return
        self._save_day_plan_worker(
            run_time.date(),
            str(existing.get("clock_in", "")),
            run_time.strftime("%H:%M"),
            origin="clock_out_panel",
            operation="schedule_clock_out",
        )

    def cancel_clock_out_schedule(self):
        try:
            target_date = date(
                int(self.clock_out_year_var.get()),
                int(self.clock_out_month_var.get()),
                int(self.clock_out_day_var.get()),
            )
        except (TypeError, ValueError):
            messagebox.showerror("日期无效", "请先选择有效日期。")
            return
        try:
            existing = day_plan(target_date)
        except Exception as exc:
            messagebox.showerror("日期计划读取失败", str(exc))
            return
        if not existing.get("clock_out"):
            messagebox.showinfo(
                "没有日期计划",
                f"{target_date:%Y-%m-%d} 没有按日期设置的下班时间。",
            )
            return
        if not messagebox.askyesno(
            "取消定时",
            f"确定取消 {target_date:%Y-%m-%d} 的下班打卡计划吗？\n"
            "同一天的上班计划会保留。",
        ):
            return
        self.append_log(f"Cancelling exact clock-out plan for {target_date:%Y-%m-%d}...")
        threading.Thread(
            target=self._cancel_clock_out_worker,
            args=(target_date,),
            daemon=True,
        ).start()

    def _cancel_clock_out_worker(self, target_date: date):
        existing = day_plan(target_date)
        self._save_day_plan_worker(
            target_date,
            str(existing.get("clock_in", "")),
            "",
            origin="clock_out_panel",
            operation="cancel_clock_out",
        )

    def _apply_clock_out_schedule_result(self, payload: object):
        result = dict(payload) if isinstance(payload, dict) else {}
        self.clock_out_schedule_button.configure(state="normal", text="设置定时下班")
        message = str(result.get("message", "")).strip()
        if result.get("ok"):
            self.append_log(message or "Clock-out schedule updated.")
            if result.get("operation") == "schedule_clock_out":
                messagebox.showinfo(
                    "定时设置成功",
                    f"将在 {result.get('run_time')} 自动执行一次下班打卡。",
                )
            else:
                messagebox.showinfo(
                    "定时已取消",
                    f"{result.get('plan_date')} 的下班打卡计划已取消。",
                )
        else:
            self.append_log(f"Clock-out schedule operation failed: {message}")
            messagebox.showerror("操作失败", message or "无法更新定时任务。")
        if self.calendar_window and self.calendar_window.winfo_exists():
            self._calendar_reload_month()
        self.refresh_status()

    def run_safe_test(self):
        if not RUNNER_SCRIPT.is_file():
            messagebox.showerror("文件缺失", str(RUNNER_SCRIPT))
            return

        self.test_button.configure(state="disabled", text="测试运行中...")
        self.clock_in_now_button.configure(state="disabled")
        self.clock_out_now_button.configure(state="disabled")
        self.clock_out_test_button.configure(state="disabled")
        self.append_log("Starting immediate safe test (real clock-in click disabled).")
        threading.Thread(target=self._safe_test_worker, daemon=True).start()

    def _safe_test_worker(self):
        process = subprocess.Popen(
            [
                str(POWERSHELL_EXE),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(RUNNER_SCRIPT),
                "-TestMode",
                "-Immediate",
            ],
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )

        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("log", line.rstrip()))

        exit_code = process.wait()
        result = (
            "Safe test completed successfully."
            if exit_code == 0
            else f"Safe test failed with exit code {exit_code}."
        )
        self.events.put(("test_done", result))

    def _open_calendar_editor_legacy(self):
        if self.calendar_window and self.calendar_window.winfo_exists():
            self.calendar_window.deiconify()
            self.calendar_window.lift()
            self.calendar_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.calendar_window = window
        window.title("工作日与节假日管理")
        window.geometry("920x620")
        window.minsize(820, 540)
        window.configure(bg=COLORS["window"])
        window.transient(self.root)

        container = tk.Frame(window, bg=COLORS["window"])
        container.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(
            container,
            text="工作日与节假日管理",
            font=("Microsoft YaHei UI", 18, "bold"),
            fg=COLORS["text"],
            bg=COLORS["window"],
        ).pack(anchor="w")
        tk.Label(
            container,
            text="默认周一至周五执行、周末跳过；下面的人工设置拥有更高优先级。",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        ).pack(anchor="w", pady=(5, 15))

        form = tk.Frame(
            container,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        form.pack(fill="x", pady=(0, 14))

        tk.Label(
            form,
            text="日期",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, padx=(16, 8), pady=16, sticky="w")

        self.calendar_date_var = tk.StringVar(value=date.today().isoformat())
        date_entry = tk.Entry(
            form,
            textvariable=self.calendar_date_var,
            font=("Cascadia Mono", 10),
            fg=COLORS["text"],
            bg=COLORS["terminal"],
            insertbackground=COLORS["text"],
            relief="flat",
            width=14,
        )
        date_entry.grid(row=0, column=1, padx=(0, 16), pady=16, ipady=7)

        tk.Label(
            form,
            text="类型",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=2, padx=(0, 8), pady=16, sticky="w")

        self.calendar_kind_var = tk.StringVar(value="临时工作日（执行）")
        kind_box = ttk.Combobox(
            form,
            textvariable=self.calendar_kind_var,
            values=("临时工作日（执行）", "节假日 / 休息日（跳过）"),
            state="readonly",
            width=25,
            font=("Microsoft YaHei UI", 9),
        )
        kind_box.grid(row=0, column=3, padx=(0, 12), pady=16, ipady=4)

        self._button(
            form,
            "添加 / 更新",
            self._calendar_add_or_update,
            primary=True,
            compact=True,
        ).grid(row=0, column=4, padx=(0, 16), pady=16)

        table_frame = tk.Frame(container, bg=COLORS["panel"])
        table_frame.pack(fill="both", expand=True)
        calendar_scroll = ttk.Scrollbar(table_frame, style="Dark.Vertical.TScrollbar")
        calendar_scroll.pack(side="right", fill="y")

        style = ttk.Style()
        style.configure(
            "Calendar.Treeview",
            background=COLORS["panel"],
            fieldbackground=COLORS["panel"],
            foreground=COLORS["text"],
            rowheight=34,
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Calendar.Treeview.Heading",
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Calendar.Treeview",
            background=[("selected", COLORS["accent"])],
            foreground=[("selected", "#ffffff")],
        )

        self.calendar_tree = ttk.Treeview(
            table_frame,
            columns=("date", "weekday", "kind", "effect"),
            show="headings",
            style="Calendar.Treeview",
            yscrollcommand=calendar_scroll.set,
        )
        self.calendar_tree.heading("date", text="日期")
        self.calendar_tree.heading("weekday", text="星期")
        self.calendar_tree.heading("kind", text="人工类型")
        self.calendar_tree.heading("effect", text="任务行为")
        self.calendar_tree.column("date", width=150, anchor="center")
        self.calendar_tree.column("weekday", width=110, anchor="center")
        self.calendar_tree.column("kind", width=235, anchor="center")
        self.calendar_tree.column("effect", width=130, anchor="center")
        self.calendar_tree.pack(side="left", fill="both", expand=True)
        calendar_scroll.configure(command=self.calendar_tree.yview)
        self.calendar_tree.bind("<<TreeviewSelect>>", self._calendar_selection_changed)

        bottom = tk.Frame(container, bg=COLORS["window"])
        bottom.pack(fill="x", pady=(14, 0))
        self.calendar_summary_label = tk.Label(
            bottom,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        )
        self.calendar_summary_label.pack(side="left")
        self._button(
            bottom,
            "删除选中",
            self._calendar_delete_selected,
            compact=True,
        ).pack(side="right", padx=(8, 0))
        self._button(
            bottom,
            "清理过期日期",
            self._calendar_remove_past,
            compact=True,
        ).pack(side="right")

        self._reload_calendar_tree()

    def _calendar_data_or_error(self) -> dict[str, list[str]] | None:
        try:
            return load_calendar_overrides()
        except Exception as exc:
            messagebox.showerror("日历配置错误", str(exc), parent=self.calendar_window)
            return None

    def _calendar_add_or_update(self):
        try:
            target_date = date.fromisoformat(self.calendar_date_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "日期格式错误",
                "请输入 YYYY-MM-DD 格式，例如 2026-10-01。",
                parent=self.calendar_window,
            )
            return

        data = self._calendar_data_or_error()
        if data is None:
            return

        date_text = target_date.isoformat()
        for key in ("force_workdays", "force_holidays"):
            if date_text in data[key]:
                data[key].remove(date_text)

        if self.calendar_kind_var.get().startswith("临时工作日"):
            data["force_workdays"].append(date_text)
            effect = "execute"
        else:
            data["force_holidays"].append(date_text)
            effect = "skip"

        try:
            save_calendar_overrides(data)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.calendar_window)
            return

        self.append_log(f"Calendar override saved: {date_text} -> {effect}.")
        self._reload_calendar_tree()
        self.refresh_status()

    def _calendar_delete_selected(self):
        selected = self.calendar_tree.selection()
        if not selected:
            messagebox.showinfo("没有选择", "请先选择要删除的日期。", parent=self.calendar_window)
            return

        data = self._calendar_data_or_error()
        if data is None:
            return

        removed_dates = []
        for item_id in selected:
            date_text = self.calendar_tree.item(item_id, "values")[0]
            removed_dates.append(date_text)
            for key in ("force_workdays", "force_holidays"):
                if date_text in data[key]:
                    data[key].remove(date_text)

        save_calendar_overrides(data)
        self.append_log(f"Calendar overrides removed: {', '.join(removed_dates)}.")
        self._reload_calendar_tree()
        self.refresh_status()

    def _calendar_remove_past(self):
        data = self._calendar_data_or_error()
        if data is None:
            return

        today_text = date.today().isoformat()
        removed_count = 0
        for key in ("force_workdays", "force_holidays"):
            original = data[key]
            retained = [value for value in original if value >= today_text]
            removed_count += len(original) - len(retained)
            data[key] = retained

        save_calendar_overrides(data)
        self.append_log(f"Removed {removed_count} expired calendar override(s).")
        self._reload_calendar_tree()
        self.refresh_status()

    def _calendar_selection_changed(self, _event=None):
        selected = self.calendar_tree.selection()
        if not selected:
            return
        values = self.calendar_tree.item(selected[0], "values")
        self.calendar_date_var.set(values[0])
        if values[3] == "执行":
            self.calendar_kind_var.set("临时工作日（执行）")
        else:
            self.calendar_kind_var.set("节假日 / 休息日（跳过）")

    def _reload_calendar_tree(self):
        data = self._calendar_data_or_error()
        if data is None:
            return

        for item_id in self.calendar_tree.get_children():
            self.calendar_tree.delete(item_id)

        weekday_names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        records = [
            (value, "临时工作日", "执行")
            for value in data["force_workdays"]
        ] + [
            (value, "节假日 / 休息日", "跳过")
            for value in data["force_holidays"]
        ]

        for date_text, kind, effect in sorted(records):
            parsed_date = date.fromisoformat(date_text)
            self.calendar_tree.insert(
                "",
                "end",
                values=(date_text, weekday_names[parsed_date.weekday()], kind, effect),
            )

        workday_count = len(data["force_workdays"])
        holiday_count = len(data["force_holidays"])
        self.calendar_summary_label.configure(
            text=f"临时工作日 {workday_count} 个 · 节假日/休息日 {holiday_count} 个"
        )

    def open_calendar_editor(self):
        if self.calendar_window and self.calendar_window.winfo_exists():
            self.calendar_window.deiconify()
            self.calendar_window.lift()
            self.calendar_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.calendar_window = window
        window.title("月历 · 执行日期管理")
        window.geometry("1060x820")
        window.minsize(940, 740)
        window.configure(bg=COLORS["window"])
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_calendar_window)

        shell = tk.Frame(window, bg=COLORS["window"])
        shell.pack(fill="both", expand=True)
        calendar_scrollbar = ttk.Scrollbar(
            shell,
            orient="vertical",
            style="Dark.Vertical.TScrollbar",
        )
        calendar_scrollbar.pack(side="right", fill="y")
        self.calendar_canvas = tk.Canvas(
            shell,
            bg=COLORS["window"],
            highlightthickness=0,
            borderwidth=0,
            yscrollcommand=calendar_scrollbar.set,
        )
        self.calendar_canvas.pack(side="left", fill="both", expand=True)
        calendar_scrollbar.configure(command=self.calendar_canvas.yview)
        viewport = tk.Frame(self.calendar_canvas, bg=COLORS["window"])
        self.calendar_canvas_window = self.calendar_canvas.create_window(
            (0, 0), window=viewport, anchor="nw"
        )
        viewport.bind(
            "<Configure>",
            lambda _event: self.calendar_canvas.configure(
                scrollregion=self.calendar_canvas.bbox("all")
            ),
        )
        self.calendar_canvas.bind(
            "<Configure>",
            lambda event: self.calendar_canvas.itemconfigure(
                self.calendar_canvas_window, width=event.width
            ),
        )
        window.bind(
            "<MouseWheel>",
            lambda event: self.calendar_canvas.yview_scroll(
                int(-event.delta / 120), "units"
            ),
        )

        container = tk.Frame(viewport, bg=COLORS["window"])
        container.pack(fill="both", expand=True, padx=24, pady=20)

        title_row = tk.Frame(container, bg=COLORS["window"])
        title_row.pack(fill="x")
        title_text = tk.Frame(title_row, bg=COLORS["window"])
        title_text.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_text,
            text="每月执行日历",
            font=("Microsoft YaHei UI", 19, "bold"),
            fg=COLORS["text"],
            bg=COLORS["window"],
        ).pack(anchor="w")
        tk.Label(
            title_text,
            text="单击查看详情；双击或右键日期可设置当天的精确上下班计划。",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        ).pack(anchor="w", pady=(4, 0))

        self.calendar_sync_button = self._button(
            title_row,
            "同步国务院安排",
            self._calendar_manual_sync,
            primary=True,
            compact=True,
        )
        self.calendar_sync_button.pack(side="right")

        navigation = tk.Frame(container, bg=COLORS["panel"])
        navigation.pack(fill="x", pady=(16, 10))
        self._button(
            navigation, "‹ 上一月", lambda: self._calendar_change_month(-1), compact=True
        ).pack(side="left", padx=12, pady=10)
        self._button(
            navigation, "回到今天", self._calendar_go_today, compact=True
        ).pack(side="left", pady=10)
        self._button(
            navigation, "下一月 ›", lambda: self._calendar_change_month(1), compact=True
        ).pack(side="right", padx=12, pady=10)
        self.calendar_show_times_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            navigation,
            text="显示打卡时间",
            variable=self.calendar_show_times_var,
            command=self._calendar_reload_month,
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
            activeforeground=COLORS["text"],
            activebackground=COLORS["panel"],
            selectcolor=COLORS["terminal"],
            cursor="hand2",
        ).pack(side="right", padx=(0, 6), pady=10)
        self.calendar_month_label = tk.Label(
            navigation,
            text="",
            font=("Microsoft YaHei UI", 15, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        self.calendar_month_label.place(relx=0.5, rely=0.5, anchor="center")

        calendar_panel = tk.Frame(
            container,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        calendar_panel.pack(fill="both")

        self.calendar_grid = tk.Frame(calendar_panel, bg=COLORS["panel"])
        self.calendar_grid.pack(fill="both", expand=True, padx=12, pady=10)
        for column in range(7):
            self.calendar_grid.grid_columnconfigure(column, weight=1, uniform="calendar")
        for row in range(1, 7):
            self.calendar_grid.grid_rowconfigure(
                row,
                weight=1,
                uniform="calendar",
                minsize=94,
            )

        for column, weekday in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            tk.Label(
                self.calendar_grid,
                text=f"周{weekday}",
                font=("Microsoft YaHei UI", 9, "bold"),
                fg=COLORS["warning"] if column >= 5 else COLORS["muted"],
                bg=COLORS["panel"],
            ).grid(row=0, column=column, sticky="nsew", pady=(2, 8))

        selection_panel = tk.Frame(
            container,
            bg=COLORS["panel_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        selection_panel.pack(fill="x", pady=(10, 8))
        selection_text = tk.Frame(selection_panel, bg=COLORS["panel_alt"])
        selection_text.pack(side="left", fill="x", expand=True, padx=15, pady=11)
        self.calendar_selected_label = tk.Label(
            selection_text,
            text="",
            font=("Microsoft YaHei UI", 11, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel_alt"],
        )
        self.calendar_selected_label.pack(anchor="w")
        self.calendar_selected_detail = tk.Label(
            selection_text,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel_alt"],
            justify="left",
            wraplength=470,
        )
        self.calendar_selected_detail.pack(anchor="w", pady=(3, 0))

        selection_actions = tk.Frame(selection_panel, bg=COLORS["panel_alt"])
        selection_actions.pack(side="right", padx=12, pady=10)
        self._button(
            selection_actions,
            "编辑当日计划",
            lambda: self.open_daily_plan_editor(self.calendar_selected_date),
            primary=True,
            compact=True,
        ).pack(side="left", padx=(0, 7))
        self._button(
            selection_actions,
            "临时执行",
            lambda: self._calendar_set_override("workday"),
            compact=True,
        ).pack(side="left", padx=(0, 7))
        self._button(
            selection_actions,
            "临时跳过",
            lambda: self._calendar_set_override("holiday"),
            compact=True,
        ).pack(side="left", padx=(0, 7))
        self._button(
            selection_actions,
            "恢复官方/默认",
            lambda: self._calendar_set_override("default"),
            compact=True,
        ).pack(side="left")

        bottom = tk.Frame(container, bg=COLORS["window"])
        bottom.pack(fill="x")
        legend = tk.Frame(bottom, bg=COLORS["window"])
        legend.pack(side="left")
        for text, color in (
            ("正常执行", "#1d5a48"),
            ("周末跳过", "#283750"),
            ("法定休息", "#784135"),
            ("调休上班", "#23647a"),
            ("临时执行", "#3158ad"),
            ("临时跳过", "#743b57"),
        ):
            item = tk.Frame(legend, bg=COLORS["window"])
            item.pack(side="left", padx=(0, 10))
            tk.Label(item, text="  ", bg=color, width=2).pack(side="left")
            tk.Label(
                item,
                text=text,
                font=("Microsoft YaHei UI", 8),
                fg=COLORS["muted"],
                bg=COLORS["window"],
            ).pack(side="left", padx=(4, 0))

        source_frame = tk.Frame(bottom, bg=COLORS["window"])
        source_frame.pack(side="right")
        self.calendar_sync_status_label = tk.Label(
            source_frame,
            text="",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["window"],
        )
        self.calendar_sync_status_label.pack(side="left")
        source_link = tk.Label(
            source_frame,
            text="查看官方通知",
            font=("Microsoft YaHei UI", 8, "underline"),
            fg=COLORS["accent_hover"],
            bg=COLORS["window"],
            cursor="hand2",
        )
        source_link.pack(side="left", padx=(8, 0))
        source_link.bind("<Button-1>", lambda _event: self._open_calendar_source())

        self._calendar_reload_month()

    def _close_calendar_window(self):
        if self.daily_plan_window and self.daily_plan_window.winfo_exists():
            self._close_daily_plan_window()
        if self.calendar_window and self.calendar_window.winfo_exists():
            self.calendar_window.destroy()
        self.calendar_window = None

    def _calendar_change_month(self, delta: int):
        month_index = self.calendar_view_year * 12 + self.calendar_view_month - 1 + delta
        self.calendar_view_year, zero_based_month = divmod(month_index, 12)
        self.calendar_view_month = zero_based_month + 1
        self._calendar_reload_month()

    def _calendar_go_today(self):
        today = date.today()
        self.calendar_view_year = today.year
        self.calendar_view_month = today.month
        self.calendar_selected_date = today
        self._calendar_reload_month()

    def _calendar_select_date(self, selected_date: date):
        self.calendar_selected_date = selected_date
        self._calendar_reload_month()

    def _show_daily_plan_menu(self, event, selected_date: date):
        self.calendar_selected_date = selected_date
        self._calendar_reload_month()
        menu = tk.Menu(
            self.calendar_window,
            tearoff=False,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            activebackground=COLORS["accent"],
            activeforeground="#ffffff",
        )
        menu.add_command(
            label="编辑当日上下班计划",
            command=lambda: self.open_daily_plan_editor(selected_date),
        )
        if day_plan(selected_date):
            menu.add_command(
                label="清除当日计划",
                command=lambda: self._clear_daily_plan(selected_date),
            )
        menu.add_separator()
        menu.add_command(
            label="设为临时执行",
            command=lambda: self._calendar_set_override_for_date(
                selected_date, "workday"
            ),
        )
        menu.add_command(
            label="设为临时跳过",
            command=lambda: self._calendar_set_override_for_date(
                selected_date, "holiday"
            ),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_daily_plan_editor(self, target_date: date):
        if self.daily_plan_window and self.daily_plan_window.winfo_exists():
            self.daily_plan_window.destroy()

        existing = day_plan(target_date)
        window = tk.Toplevel(self.calendar_window or self.root)
        self.daily_plan_window = window
        self.daily_plan_editor_date = target_date
        window.title(f"编辑当日计划 · {target_date:%Y-%m-%d}")
        window.geometry("590x430")
        window.resizable(False, False)
        window.configure(bg=COLORS["window"])
        window.transient(self.calendar_window or self.root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", self._close_daily_plan_window)

        container = tk.Frame(window, bg=COLORS["window"])
        container.pack(fill="both", expand=True, padx=24, pady=22)
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        tk.Label(
            container,
            text=f"{target_date:%Y-%m-%d}  {weekday_names[target_date.weekday()]}",
            font=("Microsoft YaHei UI", 17, "bold"),
            fg=COLORS["text"],
            bg=COLORS["window"],
        ).pack(anchor="w")
        tk.Label(
            container,
            text=(
                "精确上班时间会替代当天 09:00–09:30 的随机计划；"
                "精确下班时间与主界面定时下班使用同一计划，后来保存的值生效。"
            ),
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["window"],
            justify="left",
            wraplength=530,
        ).pack(anchor="w", pady=(7, 16))

        default_clock_in = str(existing.get("clock_in") or "09:15")
        default_clock_out = str(existing.get("clock_out") or "19:00")
        self.plan_clock_in_enabled = tk.BooleanVar(
            value=bool(existing.get("clock_in")) or not existing
        )
        self.plan_clock_out_enabled = tk.BooleanVar(
            value=bool(existing.get("clock_out")) or not existing
        )
        self.plan_clock_in_hour = tk.StringVar(value=default_clock_in[:2])
        self.plan_clock_in_minute = tk.StringVar(value=default_clock_in[3:5])
        self.plan_clock_out_hour = tk.StringVar(value=default_clock_out[:2])
        self.plan_clock_out_minute = tk.StringVar(value=default_clock_out[3:5])

        plan_panel = tk.Frame(
            container,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        plan_panel.pack(fill="x")
        self._build_daily_plan_time_row(
            plan_panel,
            "上班精确时间",
            self.plan_clock_in_enabled,
            self.plan_clock_in_hour,
            self.plan_clock_in_minute,
        )
        self._build_daily_plan_time_row(
            plan_panel,
            "下班精确时间",
            self.plan_clock_out_enabled,
            self.plan_clock_out_hour,
            self.plan_clock_out_minute,
        )

        tk.Label(
            container,
            text=(
                "电脑在目标时刻关闭时，任务会在当天稍后开机后补执行；"
                "如果已经跨到第二天则自动放弃，避免错日打卡。"
            ),
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["warning"],
            bg=COLORS["window"],
            justify="left",
            wraplength=530,
        ).pack(anchor="w", pady=(13, 0))

        actions = tk.Frame(container, bg=COLORS["window"])
        actions.pack(fill="x", pady=(18, 0))
        self.daily_plan_save_button = self._button(
            actions,
            "保存并更新任务",
            self._save_daily_plan_from_editor,
            primary=True,
        )
        self.daily_plan_save_button.pack(side="left", padx=(0, 8))
        self._button(
            actions,
            "清除当日计划",
            lambda: self._clear_daily_plan(target_date),
            danger=True,
        ).pack(side="left", padx=(0, 8))
        self._button(actions, "取消", self._close_daily_plan_window).pack(side="right")

    def _close_daily_plan_window(self):
        if self.daily_plan_window and self.daily_plan_window.winfo_exists():
            self.daily_plan_window.destroy()
        self.daily_plan_window = None

    def _build_daily_plan_time_row(
        self,
        parent,
        label_text: str,
        enabled_var: tk.BooleanVar,
        hour_var: tk.StringVar,
        minute_var: tk.StringVar,
    ):
        row = tk.Frame(parent, bg=COLORS["panel"])
        row.pack(fill="x", padx=16, pady=12)
        tk.Checkbutton(
            row,
            text=label_text,
            variable=enabled_var,
            font=("Microsoft YaHei UI", 10, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
            activeforeground=COLORS["text"],
            activebackground=COLORS["panel"],
            selectcolor=COLORS["terminal"],
        ).pack(side="left")
        self._clock_out_spinbox(
            row, minute_var, 0, 59, width=3, number_format="%02.0f"
        ).pack(side="right")
        self._clock_out_separator(row, ":").pack(side="right")
        self._clock_out_spinbox(
            row, hour_var, 0, 23, width=3, number_format="%02.0f"
        ).pack(side="right")

    def _save_daily_plan_from_editor(self):
        target_date = self.daily_plan_editor_date
        existing = day_plan(target_date)
        try:
            clock_in = (
                f"{int(self.plan_clock_in_hour.get()):02d}:"
                f"{int(self.plan_clock_in_minute.get()):02d}"
                if self.plan_clock_in_enabled.get()
                else ""
            )
            clock_out = (
                f"{int(self.plan_clock_out_hour.get()):02d}:"
                f"{int(self.plan_clock_out_minute.get()):02d}"
                if self.plan_clock_out_enabled.get()
                else ""
            )
            for value in (clock_in, clock_out):
                if value:
                    datetime.strptime(value, "%H:%M")
        except (TypeError, ValueError):
            messagebox.showerror(
                "时间无效",
                "请输入有效的小时和分钟。",
                parent=self.daily_plan_window,
            )
            return
        if not clock_in and not clock_out:
            messagebox.showinfo(
                "没有启用计划",
                "请至少启用一个时间，或者使用“清除当日计划”。",
                parent=self.daily_plan_window,
            )
            return

        now = datetime.now()
        for kind, value, old_value in (
            ("上班", clock_in, str(existing.get("clock_in", ""))),
            ("下班", clock_out, str(existing.get("clock_out", ""))),
        ):
            if not value or value == old_value:
                continue
            target = datetime.strptime(
                f"{target_date.isoformat()} {value}", "%Y-%m-%d %H:%M"
            )
            if target <= now:
                messagebox.showerror(
                    "时间已经过去",
                    f"{kind}计划 {target:%Y-%m-%d %H:%M} 已经过期，不能创建新任务。",
                    parent=self.daily_plan_window,
                )
                return

        self.daily_plan_save_button.configure(state="disabled", text="正在保存...")
        threading.Thread(
            target=self._save_day_plan_worker,
            args=(target_date, clock_in, clock_out),
            kwargs={"origin": "calendar", "operation": "save_day_plan"},
            daemon=True,
        ).start()

    def _clear_daily_plan(self, target_date: date):
        if not day_plan(target_date):
            messagebox.showinfo(
                "没有计划",
                f"{target_date:%Y-%m-%d} 没有精确上下班计划。",
                parent=self.daily_plan_window or self.calendar_window,
            )
            return
        if not messagebox.askyesno(
            "清除当日计划",
            f"确定清除 {target_date:%Y-%m-%d} 的精确上下班计划吗？",
            parent=self.daily_plan_window or self.calendar_window,
        ):
            return
        if hasattr(self, "daily_plan_save_button"):
            self.daily_plan_save_button.configure(state="disabled")
        threading.Thread(
            target=self._save_day_plan_worker,
            args=(target_date, "", ""),
            kwargs={"origin": "calendar", "operation": "clear_day_plan"},
            daemon=True,
        ).start()

    def _apply_daily_plan_result(self, payload: object):
        result = dict(payload) if isinstance(payload, dict) else {}
        if hasattr(self, "daily_plan_save_button"):
            self.daily_plan_save_button.configure(
                state="normal", text="保存并更新任务"
            )
        if result.get("ok"):
            self.append_log(
                "Daily exact plan updated: "
                f"{result.get('plan_date')} clockIn={result.get('clock_in') or '--'} "
                f"clockOut={result.get('clock_out') or '--'}."
            )
            if self.daily_plan_window and self.daily_plan_window.winfo_exists():
                self._close_daily_plan_window()
            messagebox.showinfo(
                "日期计划已更新",
                f"{result.get('plan_date')} 的精确上下班计划和系统任务已经更新。",
                parent=self.calendar_window or self.root,
            )
        else:
            messagebox.showerror(
                "日期计划保存失败",
                str(result.get("message") or "无法更新系统任务。"),
                parent=self.daily_plan_window or self.calendar_window or self.root,
            )
        if self.calendar_window and self.calendar_window.winfo_exists():
            self._calendar_reload_month()
        self.refresh_status()

    def _calendar_reload_month(self):
        if not self.calendar_window or not self.calendar_window.winfo_exists():
            return

        try:
            overrides = load_calendar_overrides()
            official = load_official_calendar(OFFICIAL_CALENDAR_FILE)
        except Exception as exc:
            messagebox.showerror("日历配置错误", str(exc), parent=self.calendar_window)
            return

        try:
            attendance_records = {
                str(item["attendance_date"]): item
                for item in month_records(
                    self.calendar_view_year,
                    self.calendar_view_month,
                )
            }
        except Exception:
            attendance_records = {}
        try:
            scheduled_plans = month_plans(
                self.calendar_view_year,
                self.calendar_view_month,
            )
        except Exception:
            scheduled_plans = {}

        expanded_cells = bool(scheduled_plans) or bool(
            self.calendar_show_times_var.get() and attendance_records
        )
        cell_minimum_height = 100 if expanded_cells else 76
        for row in range(1, 7):
            self.calendar_grid.grid_rowconfigure(row, minsize=cell_minimum_height)

        self.calendar_month_label.configure(
            text=f"{self.calendar_view_year} 年 {self.calendar_view_month} 月"
        )

        for child in self.calendar_grid.winfo_children():
            if int(child.grid_info().get("row", 0)) > 0:
                child.destroy()

        cell_colors = {
            "default_weekday": "#1d5a48",
            "default_weekend": "#283750",
            "official_holiday": "#784135",
            "official_workday": "#23647a",
            "manual_workday": "#3158ad",
            "manual_holiday": "#743b57",
        }
        weeks = month_calendar.Calendar(firstweekday=0).monthdayscalendar(
            self.calendar_view_year, self.calendar_view_month
        )
        while len(weeks) < 6:
            weeks.append([0] * 7)

        execute_count = 0
        skip_count = 0
        today = date.today()
        for row, week in enumerate(weeks[:6], start=1):
            for column, day_number in enumerate(week):
                if day_number == 0:
                    tk.Frame(self.calendar_grid, bg=COLORS["panel"]).grid(
                        row=row, column=column, sticky="nsew", padx=3, pady=3
                    )
                    continue

                day_date = date(
                    self.calendar_view_year, self.calendar_view_month, day_number
                )
                status = calendar_day_status(day_date, overrides, official)
                if status["should_run"]:
                    execute_count += 1
                else:
                    skip_count += 1

                border_color = COLORS["panel"]
                border_width = 2
                if day_date == today:
                    border_color = COLORS["warning"]
                if day_date == self.calendar_selected_date:
                    border_color = COLORS["accent_hover"]
                    border_width = 3

                outer = tk.Frame(
                    self.calendar_grid,
                    bg=border_color,
                    padx=border_width,
                    pady=border_width,
                )
                outer.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
                background = cell_colors[str(status["code"])]
                button_text = f"{day_number}\n{status['label']}"
                attendance_record = attendance_records.get(day_date.isoformat(), {})
                scheduled_plan = scheduled_plans.get(day_date.isoformat(), {})
                if scheduled_plan:
                    if scheduled_plan.get("clock_in"):
                        button_text += f"\n计划 上 {scheduled_plan['clock_in']}"
                    if scheduled_plan.get("clock_out"):
                        button_text += f"\n计划 下 {scheduled_plan['clock_out']}"
                if self.calendar_show_times_var.get() and attendance_record:
                    clock_in_text = self._calendar_history_time_text(
                        attendance_record, "clock_in"
                    )
                    clock_out_text = self._calendar_history_time_text(
                        attendance_record, "clock_out"
                    )
                    button_text += (
                        f"\n实际 上 {clock_in_text}"
                        f"\n实际 下 {clock_out_text}"
                    )
                button = tk.Button(
                    outer,
                    text=button_text,
                    command=lambda value=day_date: self._calendar_select_date(value),
                    font=("Microsoft YaHei UI", 9, "bold"),
                    fg="#ffffff",
                    bg=background,
                    activeforeground="#ffffff",
                    activebackground=background,
                    relief="flat",
                    borderwidth=0,
                    cursor="hand2",
                )
                button.pack(fill="both", expand=True)
                button.bind(
                    "<Double-Button-1>",
                    lambda _event, value=day_date: self.open_daily_plan_editor(value),
                )
                button.bind(
                    "<Button-3>",
                    lambda event, value=day_date: self._show_daily_plan_menu(
                        event, value
                    ),
                )

        selected_status = calendar_day_status(
            self.calendar_selected_date, overrides, official
        )
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        self.calendar_selected_label.configure(
            text=(
                f"{self.calendar_selected_date:%Y-%m-%d}  "
                f"{weekday_names[self.calendar_selected_date.weekday()]}  ·  "
                f"{selected_status['label']}"
            )
        )
        selected_attendance = attendance_records.get(
            self.calendar_selected_date.isoformat(), {}
        )
        if selected_attendance:
            attendance_text = (
                "实际记录：上 "
                f"{self._history_time_text(selected_attendance, 'clock_in')} / 下 "
                f"{self._history_time_text(selected_attendance, 'clock_out')}"
            )
        else:
            attendance_text = "实际记录：暂无"
        selected_plan = scheduled_plans.get(
            self.calendar_selected_date.isoformat(), {}
        )
        if selected_plan.get("clock_in"):
            action_text = "精确上班计划会执行，并覆盖当天随机/日历状态"
        else:
            action_text = (
                "会执行随机上班打卡"
                if selected_status["should_run"]
                else "不会执行自动上班打卡"
            )
        if selected_plan:
            plan_text = (
                "精确计划：上 "
                f"{selected_plan.get('clock_in') or '--'} / 下 "
                f"{selected_plan.get('clock_out') or '--'}"
            )
        else:
            plan_text = "精确计划：未设置"
        self.calendar_selected_detail.configure(
            text=(
                f"{selected_status['reason']}；{action_text}。"
                f"{plan_text}；{attendance_text}。人工设置优先于官方日历。"
            )
        )

        record = official.get("years", {}).get(str(self.calendar_view_year), {})
        if record:
            synced_at = str(record.get("synced_at", "")).replace("T", " ")[:19]
            self.calendar_sync_status_label.configure(
                text=(
                    f"本月：执行 {execute_count} 天 / 跳过 {skip_count} 天  ·  "
                    f"官方数据更新 {synced_at}"
                )
            )
        else:
            self.calendar_sync_status_label.configure(
                text=(
                    f"本月：执行 {execute_count} 天 / 跳过 {skip_count} 天  ·  "
                    f"{self.calendar_view_year} 年官方数据尚未同步"
                )
            )

        if hasattr(self, "calendar_sync_button"):
            self.calendar_sync_button.configure(
                state="disabled" if self.calendar_sync_running else "normal",
                text="正在同步..." if self.calendar_sync_running else "同步国务院安排",
            )

    def _calendar_set_override_for_date(self, target_date: date, action: str):
        self.calendar_selected_date = target_date
        self._calendar_set_override(action)

    def _calendar_set_override(self, action: str):
        data = self._calendar_data_or_error()
        if data is None:
            return

        date_text = self.calendar_selected_date.isoformat()
        plan_must_be_cleared = action == "holiday" and bool(
            day_plan(self.calendar_selected_date)
        )
        if plan_must_be_cleared and not messagebox.askyesno(
            "临时跳过并清除计划",
            f"{date_text} 已设置精确上下班计划。\n\n"
            "设为临时跳过会同时清除当天计划和对应系统任务，是否继续？",
            parent=self.calendar_window,
        ):
            return
        for key in ("force_workdays", "force_holidays"):
            if date_text in data[key]:
                data[key].remove(date_text)

        if action == "workday":
            data["force_workdays"].append(date_text)
            description = "execute"
        elif action == "holiday":
            data["force_holidays"].append(date_text)
            description = "skip"
        else:
            description = "official/default"

        try:
            save_calendar_overrides(data)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.calendar_window)
            return

        self.append_log(f"Calendar override: {date_text} -> {description}.")
        self._calendar_reload_month()
        self.refresh_status()
        if plan_must_be_cleared:
            threading.Thread(
                target=self._save_day_plan_worker,
                args=(self.calendar_selected_date, "", ""),
                kwargs={
                    "origin": "calendar",
                    "operation": "clear_due_skip",
                },
                daemon=True,
            ).start()

    def _start_automatic_calendar_sync(self):
        current_year = date.today().year
        if official_calendar_needs_sync(current_year):
            self._begin_calendar_sync(current_year, automatic=True)

    def _calendar_manual_sync(self):
        self._begin_calendar_sync(self.calendar_view_year, automatic=False)

    def _begin_calendar_sync(self, year: int, automatic: bool):
        if self.calendar_sync_running:
            return
        self.calendar_sync_running = True
        if self.calendar_window and self.calendar_window.winfo_exists():
            self._calendar_reload_month()
        self.append_log(f"Syncing official holiday calendar for {year} from gov.cn...")
        threading.Thread(
            target=self._calendar_sync_worker,
            args=(year, automatic),
            daemon=True,
        ).start()

    def _calendar_sync_worker(self, year: int, automatic: bool):
        try:
            record = sync_year(year, OFFICIAL_CALENDAR_FILE)
            payload = {
                "ok": True,
                "year": year,
                "automatic": automatic,
                "holiday_count": len(record["holidays"]),
                "workday_count": len(record["workdays"]),
            }
        except Exception as exc:
            payload = {
                "ok": False,
                "year": year,
                "automatic": automatic,
                "error": str(exc),
            }
        self.events.put(("calendar_sync_done", payload))

    def _apply_calendar_sync_result(self, payload: object):
        result = dict(payload) if isinstance(payload, dict) else {"ok": False}
        self.calendar_sync_running = False
        if result.get("ok"):
            self.append_log(
                "Official calendar sync completed: "
                f"year={result['year']}; holidays={result['holiday_count']}; "
                f"workdays={result['workday_count']}."
            )
        else:
            error = str(result.get("error", "unknown error"))
            self.append_log(
                f"Official calendar sync failed for {result.get('year')}: {error}"
            )
            if not result.get("automatic"):
                messagebox.showerror(
                    "同步失败",
                    f"无法从国务院官网同步：\n{error}\n\n已有缓存不会被删除。",
                    parent=self.calendar_window or self.root,
                )

        if self.calendar_window and self.calendar_window.winfo_exists():
            self._calendar_reload_month()
        self.refresh_status()

    def _open_calendar_source(self):
        try:
            record = official_calendar_record(self.calendar_view_year)
        except HolidaySyncError as exc:
            messagebox.showerror("官方数据错误", str(exc), parent=self.calendar_window)
            return
        source_url = str(record.get("source_url", ""))
        if source_url:
            webbrowser.open(source_url)
        else:
            messagebox.showinfo(
                "尚未同步",
                f"{self.calendar_view_year} 年还没有可用的官方通知缓存。",
                parent=self.calendar_window,
            )

    def open_today_log(self):
        today = datetime.now()
        log_file = LOG_ROOT / today.strftime("%Y-%m") / f"{today:%Y-%m-%d}.log"
        self.open_path(log_file if log_file.exists() else log_file.parent)

    def open_path(self, path: Path):
        path.mkdir(parents=True, exist_ok=True) if path.suffix == "" else None
        if not path.exists():
            messagebox.showerror("路径不存在", str(path))
            return
        os.startfile(str(path))

    def open_task_scheduler(self):
        subprocess.Popen(
            ["mmc.exe", "taskschd.msc"],
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )


def main():
    root = tk.Tk()
    panel = ControlPanel(root)
    if "--open-calendar" in sys.argv:
        panel.open_calendar_editor()
    root.mainloop()


if __name__ == "__main__":
    main()
