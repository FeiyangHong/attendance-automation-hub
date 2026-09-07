from __future__ import annotations

import ast
import re
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_FILE = PROJECT_DIR / "data" / "attendance_history.db"
DEFAULT_LOG_ROOT = PROJECT_DIR / "logs"

SOURCE_LABELS = {
    "page_existing": "页面已存在（可能极速/手工）",
    "script_clock_in": "脚本上班打卡",
    "script_clock_out": "脚本下班打卡",
    "script_update": "脚本更新打卡",
    "test_observation": "测试时页面识别",
    "log_import": "旧日志导入",
    "unconfirmed_click": "点击后待确认",
}
SOURCE_PRIORITY = {
    "log_import": 0,
    "page_existing": 1,
    "test_observation": 1,
    "unconfirmed_click": 1,
    "script_clock_in": 2,
    "script_clock_out": 2,
    "script_update": 2,
}

_LOG_TIMESTAMP_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s"
)
_CANDIDATE_RE = re.compile(
    r"\[CANDIDATE #[^\]]+\].*?"
    r"label=(?P<label>'(?:\\.|[^'])*').*?"
    r"bounds=\[(?P<x1>\d+),(?P<y1>\d+)\]"
    r"\[(?P<x2>\d+),(?P<y2>\d+)\]"
)
_ACTUAL_TIME_RE = re.compile(r"已打卡\s*(?P<time>\d{1,2}:\d{2})")


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _normalize_time(value: str) -> str:
    parsed = datetime.strptime(value.strip(), "%H:%M")
    return parsed.strftime("%H:%M")


def source_label(source: str | None) -> str:
    return SOURCE_LABELS.get(str(source or ""), str(source or "--"))


def _connect(db_file: Path = DEFAULT_DB_FILE) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_file, timeout=8)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 8000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_attendance (
            attendance_date TEXT PRIMARY KEY,
            clock_in_time TEXT,
            clock_in_status TEXT NOT NULL DEFAULT 'missing',
            clock_in_source TEXT,
            clock_in_observed_at TEXT,
            clock_out_time TEXT,
            clock_out_status TEXT NOT NULL DEFAULT 'missing',
            clock_out_source TEXT,
            clock_out_observed_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            attendance_date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('clock_in', 'clock_out')),
            actual_time TEXT,
            status TEXT NOT NULL CHECK (status IN ('confirmed', 'unconfirmed')),
            source TEXT NOT NULL,
            action TEXT,
            observed_at TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_attendance_events_date
            ON attendance_events(attendance_date, observed_at, id);

        CREATE TABLE IF NOT EXISTS history_meta (
            meta_key TEXT PRIMARY KEY,
            meta_value TEXT NOT NULL
        );
        """
    )
    connection.commit()
    return connection


def ensure_database(db_file: Path = DEFAULT_DB_FILE) -> None:
    with _connect(db_file):
        pass


def _ensure_daily_row(
    connection: sqlite3.Connection,
    attendance_date: str,
    updated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO daily_attendance(attendance_date, updated_at)
        VALUES (?, ?)
        ON CONFLICT(attendance_date) DO NOTHING
        """,
        (attendance_date, updated_at),
    )


def record_confirmed(
    kind: str,
    actual_time: str,
    source: str,
    *,
    attendance_date: str | date | None = None,
    observed_at: str | None = None,
    action: str = "",
    details: str = "",
    db_file: Path = DEFAULT_DB_FILE,
) -> bool:
    if kind not in {"clock_in", "clock_out"}:
        raise ValueError(f"Unsupported attendance kind: {kind}")

    date_text = _normalize_date(attendance_date or date.today())
    time_text = _normalize_time(actual_time)
    observed_text = observed_at or _now_text()
    datetime.fromisoformat(observed_text)
    event_key = f"confirmed:{date_text}:{kind}:{time_text}"
    prefix = "clock_in" if kind == "clock_in" else "clock_out"

    with _connect(db_file) as connection:
        cursor = connection.execute(
            """
            INSERT INTO attendance_events(
                event_key, attendance_date, kind, actual_time, status,
                source, action, observed_at, details
            ) VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)
            ON CONFLICT(event_key) DO NOTHING
            """,
            (
                event_key,
                date_text,
                kind,
                time_text,
                source,
                action,
                observed_text,
                details,
            ),
        )
        inserted = cursor.rowcount > 0

        if not inserted:
            existing_event = connection.execute(
                "SELECT source FROM attendance_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            existing_source = str(existing_event["source"] or "")
            if SOURCE_PRIORITY.get(source, 1) > SOURCE_PRIORITY.get(existing_source, 1):
                connection.execute(
                    """
                    UPDATE attendance_events
                    SET source = ?, action = ?, observed_at = ?, details = ?
                    WHERE event_key = ?
                    """,
                    (source, action, observed_text, details, event_key),
                )

        _ensure_daily_row(connection, date_text, observed_text)
        row = connection.execute(
            f"""
            SELECT {prefix}_time AS actual_time,
                   {prefix}_status AS status,
                   {prefix}_source AS source,
                   {prefix}_observed_at AS observed_at
            FROM daily_attendance
            WHERE attendance_date = ?
            """,
            (date_text,),
        ).fetchone()

        current_time = str(row["actual_time"] or "")
        current_status = str(row["status"] or "missing")
        current_source = str(row["source"] or "")
        current_observed_at = str(row["observed_at"] or "")
        should_replace = not current_time or (
            time_text != current_time and observed_text >= current_observed_at
        )
        should_enrich = time_text == current_time and (
            SOURCE_PRIORITY.get(source, 1) > SOURCE_PRIORITY.get(current_source, 1)
        )

        if should_replace or should_enrich:
            connection.execute(
                f"""
                UPDATE daily_attendance
                SET {prefix}_time = ?,
                    {prefix}_status = 'confirmed',
                    {prefix}_source = ?,
                    {prefix}_observed_at = ?,
                    updated_at = ?
                WHERE attendance_date = ?
                """,
                (
                    time_text,
                    source,
                    observed_text,
                    observed_text,
                    date_text,
                ),
            )
        elif current_status != "unconfirmed" or (
            observed_text >= current_observed_at
            and SOURCE_PRIORITY.get(source, 1)
            >= SOURCE_PRIORITY.get(current_source, 1)
        ):
            connection.execute(
                f"""
                UPDATE daily_attendance
                SET {prefix}_status = 'confirmed', updated_at = ?
                WHERE attendance_date = ?
                """,
                (observed_text, date_text),
            )

    return inserted


def record_unconfirmed(
    kind: str,
    source: str = "unconfirmed_click",
    *,
    attendance_date: str | date | None = None,
    observed_at: str | None = None,
    action: str = "",
    details: str = "",
    db_file: Path = DEFAULT_DB_FILE,
) -> None:
    if kind not in {"clock_in", "clock_out"}:
        raise ValueError(f"Unsupported attendance kind: {kind}")

    date_text = _normalize_date(attendance_date or date.today())
    observed_text = observed_at or _now_text()
    datetime.fromisoformat(observed_text)
    event_key = f"unconfirmed:{date_text}:{kind}:{uuid.uuid4().hex}"
    prefix = "clock_in" if kind == "clock_in" else "clock_out"

    with _connect(db_file) as connection:
        connection.execute(
            """
            INSERT INTO attendance_events(
                event_key, attendance_date, kind, actual_time, status,
                source, action, observed_at, details
            ) VALUES (?, ?, ?, NULL, 'unconfirmed', ?, ?, ?, ?)
            """,
            (
                event_key,
                date_text,
                kind,
                source,
                action,
                observed_text,
                details,
            ),
        )
        _ensure_daily_row(connection, date_text, observed_text)
        connection.execute(
            f"""
            UPDATE daily_attendance
            SET {prefix}_status = 'unconfirmed',
                {prefix}_source = ?,
                {prefix}_observed_at = ?,
                updated_at = ?
            WHERE attendance_date = ?
            """,
            (source, observed_text, observed_text, date_text),
        )


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def recent_records(
    limit: int = 7,
    db_file: Path = DEFAULT_DB_FILE,
) -> list[dict[str, Any]]:
    with _connect(db_file) as connection:
        rows = connection.execute(
            """
            SELECT * FROM daily_attendance
            ORDER BY attendance_date DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return _rows_to_dicts(rows)


def month_records(
    year: int,
    month: int,
    db_file: Path = DEFAULT_DB_FILE,
) -> list[dict[str, Any]]:
    month_prefix = f"{int(year):04d}-{int(month):02d}-%"
    with _connect(db_file) as connection:
        rows = connection.execute(
            """
            SELECT * FROM daily_attendance
            WHERE attendance_date LIKE ?
            ORDER BY attendance_date
            """,
            (month_prefix,),
        ).fetchall()
    return _rows_to_dicts(rows)


def record_for_date(
    attendance_date: str | date,
    db_file: Path = DEFAULT_DB_FILE,
) -> dict[str, Any]:
    date_text = _normalize_date(attendance_date)
    with _connect(db_file) as connection:
        row = connection.execute(
            "SELECT * FROM daily_attendance WHERE attendance_date = ?",
            (date_text,),
        ).fetchone()
    return dict(row) if row else {}


def events_for_date(
    attendance_date: str | date,
    db_file: Path = DEFAULT_DB_FILE,
) -> list[dict[str, Any]]:
    date_text = _normalize_date(attendance_date)
    with _connect(db_file) as connection:
        rows = connection.execute(
            """
            SELECT * FROM attendance_events
            WHERE attendance_date = ?
            ORDER BY observed_at, id
            """,
            (date_text,),
        ).fetchall()
    return _rows_to_dicts(rows)


def _meta_value(connection: sqlite3.Connection, key: str) -> str:
    row = connection.execute(
        "SELECT meta_value FROM history_meta WHERE meta_key = ?",
        (key,),
    ).fetchone()
    return str(row["meta_value"]) if row else ""


def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO history_meta(meta_key, meta_value)
        VALUES (?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
        """,
        (key, value),
    )


def _parse_log_observations(log_file: Path) -> list[dict[str, str]]:
    groups: dict[str, list[tuple[int, str]]] = {}
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            timestamp_match = _LOG_TIMESTAMP_RE.match(line)
            candidate_match = _CANDIDATE_RE.search(line)
            if not timestamp_match or not candidate_match:
                continue

            try:
                label = ast.literal_eval(candidate_match.group("label"))
            except (SyntaxError, ValueError):
                continue
            actual_match = _ACTUAL_TIME_RE.search(str(label))
            if not actual_match:
                continue

            timestamp = timestamp_match.group("timestamp")
            y_position = int(candidate_match.group("y1"))
            groups.setdefault(timestamp, []).append(
                (y_position, _normalize_time(actual_match.group("time")))
            )

    observations: list[dict[str, str]] = []
    for timestamp, values in sorted(groups.items()):
        unique_values = sorted(set(values), key=lambda item: item[0])
        if not unique_values:
            continue

        observed_at = timestamp.replace(" ", "T") + "+08:00"
        attendance_date = timestamp[:10]
        observations.append(
            {
                "kind": "clock_in",
                "actual_time": unique_values[0][1],
                "attendance_date": attendance_date,
                "observed_at": observed_at,
            }
        )
        if len(unique_values) >= 2:
            observations.append(
                {
                    "kind": "clock_out",
                    "actual_time": unique_values[-1][1],
                    "attendance_date": attendance_date,
                    "observed_at": observed_at,
                }
            )
    return observations


def import_existing_logs(
    log_root: Path = DEFAULT_LOG_ROOT,
    db_file: Path = DEFAULT_DB_FILE,
) -> dict[str, int]:
    ensure_database(db_file)
    stats = {
        "files_scanned": 0,
        "files_imported": 0,
        "observations_added": 0,
    }
    if not log_root.exists():
        return stats

    for log_file in sorted(log_root.rglob("*.log")):
        stats["files_scanned"] += 1
        file_stat = log_file.stat()
        signature = f"{file_stat.st_size}:{file_stat.st_mtime_ns}"
        relative_name = log_file.relative_to(log_root).as_posix()
        meta_key = f"log_signature:{relative_name}"

        with _connect(db_file) as connection:
            if _meta_value(connection, meta_key) == signature:
                continue

        observations = _parse_log_observations(log_file)
        for observation in observations:
            if record_confirmed(
                observation["kind"],
                observation["actual_time"],
                "log_import",
                attendance_date=observation["attendance_date"],
                observed_at=observation["observed_at"],
                action="log_import",
                details=f"Imported from {relative_name}",
                db_file=db_file,
            ):
                stats["observations_added"] += 1

        with _connect(db_file) as connection:
            _set_meta(connection, meta_key, signature)
        stats["files_imported"] += 1

    return stats


if __name__ == "__main__":
    result = import_existing_logs()
    print(
        "Attendance history import completed: "
        f"files={result['files_imported']}; "
        f"observations={result['observations_added']}"
    )
