from __future__ import annotations

import argparse
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..paths import PROJECT_DIR

DEFAULT_CACHE_FILE = PROJECT_DIR / "config" / "official_holidays.json"
SEARCH_API = "https://sousuo.www.gov.cn/search-gov/data"
SEARCH_REFERER = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
)
CHINA_TIMEZONE = timezone(timedelta(hours=8))


class HolidaySyncError(RuntimeError):
    pass


def _request_bytes(url: str, *, referer: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def _plain_text(value: str) -> str:
    value = re.sub(r"<script\b[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style\b[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _normalized_title(value: str) -> str:
    return re.sub(r"\s+", "", _plain_text(value))


def _is_official_url(url: str) -> bool:
    hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    return hostname == "gov.cn" or hostname.endswith(".gov.cn")


def _big5_gateway_url(url: str) -> str:
    if "big5.www.gov.cn/gate/big5/" in url:
        return url
    parsed = urllib.parse.urlparse(url)
    path = parsed.netloc + parsed.path
    if parsed.query:
        path += "?" + parsed.query
    return "https://big5.www.gov.cn/gate/big5/" + path


def discover_official_notice(year: int) -> dict[str, str]:
    expected_title = f"国务院办公厅关于{year}年部分节假日安排的通知"
    params = {
        "t": "zhengcelibrary",
        "q": expected_title,
        "sort": "score",
        "sortType": "1",
        "searchfield": "title",
        "p": "1",
        "n": "20",
        "type": "gwyzcwjk",
    }
    url = SEARCH_API + "?" + urllib.parse.urlencode(params)

    try:
        payload = json.loads(_request_bytes(url, referer=SEARCH_REFERER).decode("utf-8"))
    except Exception as exc:
        raise HolidaySyncError(f"Official policy search failed: {exc}") from exc

    if payload.get("code") != 200:
        raise HolidaySyncError(f"Official policy search returned code {payload.get('code')!r}")

    candidates: list[dict[str, Any]] = []
    category_map = payload.get("searchVO", {}).get("catMap", {})
    if isinstance(category_map, dict):
        for category in category_map.values():
            if isinstance(category, dict):
                rows = category.get("listVO", [])
                if isinstance(rows, list):
                    candidates.extend(row for row in rows if isinstance(row, dict))

    expected_normalized = _normalized_title(expected_title)
    for row in candidates:
        title = _plain_text(str(row.get("title", "")))
        source_url = str(
            row.get("url") or row.get("link") or row.get("docurl") or ""
        ).strip()
        organization = _plain_text(str(row.get("puborg", "")))

        if _normalized_title(title) != expected_normalized:
            continue
        if not source_url or not _is_official_url(source_url):
            continue
        if organization and "国务院办公厅" not in organization:
            continue

        return {
            "title": title,
            "source_url": source_url,
            "fetch_url": _big5_gateway_url(source_url),
        }

    raise HolidaySyncError(f"No exact official notice found for {year}")


def _expand_date_range(year: int, start: date, end: date) -> list[str]:
    if end < start:
        raise HolidaySyncError(f"Invalid holiday range: {start} to {end}")
    days: list[str] = []
    current = start
    while current <= end:
        if current.year != year:
            raise HolidaySyncError("Holiday range crosses the requested year")
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def parse_notice(year: int, notice_html: str) -> dict[str, list[str]]:
    text = _plain_text(notice_html)
    if str(year) not in text or "节假日安排" not in text:
        # The gateway uses Traditional Chinese on some responses.
        if str(year) not in text or "節假日安排" not in text:
            raise HolidaySyncError("The downloaded page is not the requested holiday notice")

    holidays: set[str] = set()
    workdays: set[str] = set()

    range_pattern = re.compile(
        r"(?P<start_month>\d{1,2})月(?P<start_day>\d{1,2})日"
        r"(?:（[^）]*）)?\s*至\s*"
        r"(?:(?P<end_month>\d{1,2})月)?(?P<end_day>\d{1,2})日"
        r"(?:（[^）]*）)?[^。；]{0,40}?放假"
    )

    for match in range_pattern.finditer(text):
        start_month = int(match.group("start_month"))
        end_month = int(match.group("end_month") or start_month)
        start = date(year, start_month, int(match.group("start_day")))
        end = date(year, end_month, int(match.group("end_day")))
        holidays.update(_expand_date_range(year, start, end))

    for sentence in re.split(r"[。；\n]+", text):
        if "上班" not in sentence:
            continue
        for month_text, day_text in re.findall(r"(\d{1,2})月(\d{1,2})日", sentence):
            workdays.add(date(year, int(month_text), int(day_text)).isoformat())

    if len(holidays) < 7:
        raise HolidaySyncError(
            f"Holiday notice validation failed: only {len(holidays)} holiday dates parsed"
        )
    if holidays & workdays:
        overlap = ", ".join(sorted(holidays & workdays))
        raise HolidaySyncError(f"Holiday/workday overlap found: {overlap}")

    return {
        "holidays": sorted(holidays),
        "workdays": sorted(workdays),
    }


def load_official_calendar(cache_file: Path = DEFAULT_CACHE_FILE) -> dict[str, Any]:
    if not cache_file.exists():
        return {"schema_version": 1, "years": {}}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HolidaySyncError(f"Unable to read official calendar cache: {exc}") from exc
    if not isinstance(data.get("years"), dict):
        raise HolidaySyncError("Official calendar cache has an invalid years field")
    return data


def save_official_calendar(data: dict[str, Any], cache_file: Path = DEFAULT_CACHE_FILE) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
    temporary_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(cache_file)


def sync_year(year: int, cache_file: Path = DEFAULT_CACHE_FILE) -> dict[str, Any]:
    notice = discover_official_notice(year)
    try:
        notice_html = _request_bytes(notice["fetch_url"]).decode("utf-8", "replace")
    except Exception as exc:
        raise HolidaySyncError(f"Official notice download failed: {exc}") from exc

    parsed = parse_notice(year, notice_html)
    record: dict[str, Any] = {
        "title": notice["title"],
        "source_url": notice["source_url"],
        "fetch_url": notice["fetch_url"],
        "synced_at": datetime.now(CHINA_TIMEZONE).isoformat(timespec="seconds"),
        "holidays": parsed["holidays"],
        "workdays": parsed["workdays"],
    }

    cache = load_official_calendar(cache_file)
    cache["schema_version"] = 1
    cache.setdefault("years", {})[str(year)] = record
    save_official_calendar(cache, cache_file)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync China official holiday calendar")
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_FILE)
    args = parser.parse_args()

    try:
        record = sync_year(args.year, args.cache)
    except HolidaySyncError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(
        "Official calendar synced: "
        f"year={args.year}; holidays={len(record['holidays'])}; "
        f"workdays={len(record['workdays'])}; source={record['source_url']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
