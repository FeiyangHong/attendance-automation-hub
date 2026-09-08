import argparse
import re
import shutil
import subprocess
import time
from pathlib import Path

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy

from attendance_hub.core.app_config import (
    load_app_config,
    require_device_access,
    require_real_actions,
)
from attendance_hub.core.attendance_history import (
    record_confirmed,
    record_unconfirmed,
)


APP_CONFIG = load_app_config()
UDID = APP_CONFIG.display_udid
FEISHU_PACKAGE = APP_CONFIG.feishu_package

OUT_DIR = Path("artifacts") / "flow_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Feishu attendance clock-in/clock-out flow"
    )
    parser.add_argument(
        "--mode",
        choices=("clock-in", "clock-out"),
        default="clock-in",
        help="Choose the morning clock-in or evening clock-out flow",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run detection without clicking the attendance button",
    )
    return parser.parse_args()


ARGS = parse_args()

try:
    require_device_access(APP_CONFIG)
    if not ARGS.dry_run:
        require_real_actions(APP_CONFIG)
except RuntimeError as exc:
    raise SystemExit(f"[SAFETY] {exc}") from exc


def snapshot(driver, name):
    """
    保存当前页面截图 + XML。
    """
    png_path = OUT_DIR / f"{name}.png"
    xml_path = OUT_DIR / f"{name}.xml"

    driver.save_screenshot(str(png_path))

    xml_path.write_text(
        driver.page_source,
        encoding="utf-8"
    )

    print(f"[SNAPSHOT] {name}")


def read_initial_screen_off_state():
    """Read Android power state before Appium has a chance to wake the phone."""
    adb_exe = shutil.which("adb")
    if not adb_exe:
        print("[WARN] ADB was not found; screen state will use lock-state fallback.")
        return None

    try:
        result = subprocess.run(
            [adb_exe, "-s", UDID, "shell", "dumpsys", "power"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception as exc:
        print(f"[WARN] Unable to read the initial Android power state: {exc}")
        return None

    match = re.search(r"mWakefulness=(\w+)", result.stdout)
    if not match:
        print("[WARN] Android power output did not contain mWakefulness.")
        return None

    wakefulness = match.group(1)
    screen_was_off = wakefulness.lower() != "awake"
    print(
        f"[DEVICE] Initial wakefulness={wakefulness}; "
        f"screenWasOff={screen_was_off}."
    )
    return screen_was_off


def click_element_center(driver, element):
    """Click screen coordinates so Xiaomi multi-window nodes hit display 0."""
    rect = element.rect
    center_x = round(rect["x"] + rect["width"] / 2)
    center_y = round(rect["y"] + rect["height"] / 2)

    driver.execute_script(
        "mobile: clickGesture",
        {
            "x": center_x,
            "y": center_y,
        }
    )


def completed_attendance_times(candidate_info):
    """Return confirmed times shown by Feishu, ordered top-to-bottom."""
    completed = []
    for item in candidate_info:
        match = re.search(r"已打卡\s*(\d{1,2}:\d{2})", item.get("label", ""))
        if not match:
            continue
        try:
            y_position = int(item["element"].rect["y"])
        except Exception:
            bounds_match = re.search(r"\[(\d+),(\d+)\]", item.get("bounds", ""))
            y_position = int(bounds_match.group(2)) if bounds_match else 0
        normalized_time = normalize_attendance_time(match.group(1))
        completed.append((y_position, normalized_time))

    unique_completed = sorted(set(completed), key=lambda value: value[0])
    result = {}
    if unique_completed:
        result["clock_in"] = unique_completed[0][1]
    if len(unique_completed) >= 2:
        result["clock_out"] = unique_completed[-1][1]
    return result


def normalize_attendance_time(value):
    """Normalize a Feishu HH:mm value without assuming leading zeroes."""
    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid attendance time: {value}")
    return f"{hour:02d}:{minute:02d}"


def read_completed_attendance_times(driver):
    elements = driver.find_elements(
        AppiumBy.XPATH,
        '//*[contains(@text, "已打卡") '
        'or contains(@content-desc, "已打卡")]'
    )
    candidate_info = []
    seen_ids = set()
    for element in elements:
        try:
            if element.id in seen_ids or not element.is_displayed():
                continue
            seen_ids.add(element.id)
            text = element.get_attribute("text") or ""
            desc = element.get_attribute("contentDescription") or ""
            label = " ".join(value for value in (text, desc) if value)
            candidate_info.append(
                {
                    "element": element,
                    "label": label,
                    "bounds": element.get_attribute("bounds") or "",
                }
            )
        except Exception:
            continue
    return completed_attendance_times(candidate_info)


def save_confirmed_times(times, source, action):
    for kind, actual_time in times.items():
        try:
            record_confirmed(
                kind,
                actual_time,
                source,
                action=action,
                details="Confirmed from the Feishu attendance page",
            )
            print(
                f"[HISTORY] Confirmed {kind} actual_time={actual_time}; "
                f"source={source}."
            )
        except Exception as exc:
            print(f"[WARN] Unable to save confirmed attendance history: {exc}")


def confirm_and_save_after_click(driver, kind, source, action, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            times = read_completed_attendance_times(driver)
        except Exception:
            times = {}
        if kind in times:
            save_confirmed_times({kind: times[kind]}, source, action)
            return times[kind]
        time.sleep(1)

    try:
        record_unconfirmed(
            kind,
            action=action,
            details="Attendance control was clicked, but Feishu did not expose a confirmed time",
        )
        print(
            f"[HISTORY] {kind} click completed, but the actual time is unconfirmed."
        )
    except Exception as exc:
        print(f"[WARN] Unable to save unconfirmed attendance history: {exc}")
    return None


def visible_exact_elements(driver, text, resource_id_suffix=None):
    """Return visible exact-label nodes, optionally constrained by id suffix."""
    elements = driver.find_elements(
        AppiumBy.XPATH,
        f'//*[@text="{text}" or @content-desc="{text}"]'
    )
    visible = []
    seen_ids = set()

    for element in elements:
        try:
            resource_id = element.get_attribute("resourceId") or ""
            if resource_id_suffix and not resource_id.endswith(resource_id_suffix):
                continue
            if element.id in seen_ids or not element.is_displayed():
                continue
            rect = element.rect
            if rect["width"] <= 0 or rect["height"] <= 0:
                continue
            seen_ids.add(element.id)
            visible.append(element)
        except Exception:
            continue

    return visible


def page_has_label(driver, text):
    return bool(visible_exact_elements(driver, text))


def click_visible_label(driver, text, resource_id_suffix=None):
    elements = visible_exact_elements(driver, text, resource_id_suffix)
    if not elements:
        return False

    element = elements[0]
    print(
        f"[NAV] Clicking {ascii(text)}; "
        f"id={element.get_attribute('resourceId')!r}, bounds={element.rect}"
    )
    click_element_center(driver, element)
    return True


def dismiss_anti_mistouch(driver, timeout=5):
    """Exit Xiaomi anti-mistouch mode only when its exact warning is visible."""
    warning_text = "已进入防误触模式"
    if not page_has_label(driver, warning_text):
        return False

    print("[DEVICE] Xiaomi anti-mistouch mode detected.")
    snapshot(driver, "00_anti_mistouch_detected")

    # The Xiaomi overlay itself instructs the user to press Volume Up when the
    # proximity-sensor obstruction cannot be removed remotely.
    driver.press_keycode(24)  # Android KEYCODE_VOLUME_UP
    print("[DEVICE] Volume Up sent to exit anti-mistouch mode.")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if not page_has_label(driver, warning_text):
            print("[DEVICE] Anti-mistouch mode exited successfully.")
            return True

    snapshot(driver, "00_anti_mistouch_exit_failed")
    raise RuntimeError("Unable to exit Xiaomi anti-mistouch mode")


def read_initial_lock_state(driver):
    """Read whether the phone should be returned to a locked state later."""
    try:
        was_locked = driver.is_locked()
    except Exception as exc:
        print(f"[WARN] Unable to read the initial lock state: {exc}")
        was_locked = False

    print(f"[DEVICE] Initial locked/screen-off state={was_locked}.")
    return was_locked


def prepare_device_for_automation(driver):
    """Temporarily wake and unlock the phone for reliable UI automation."""
    driver.press_keycode(224)  # Android KEYCODE_WAKEUP
    time.sleep(1)
    dismiss_anti_mistouch(driver)

    try:
        if driver.is_locked():
            print("[DEVICE] Unlocking the phone for attendance automation.")
            driver.unlock()
            time.sleep(1)
            dismiss_anti_mistouch(driver)

        if driver.is_locked():
            snapshot(driver, "00_device_unlock_failed")
            raise RuntimeError("The phone is still locked after the unlock attempt")
    except RuntimeError:
        raise
    except Exception as exc:
        snapshot(driver, "00_device_unlock_failed")
        raise RuntimeError(f"Unable to unlock the phone: {exc}") from exc


def click_clock_out_confirmation(driver, action, timeout=6):
    """Click only a confirmation that is clearly tied to attendance."""
    deadline = time.monotonic() + timeout
    update_prompt_seen = False

    while time.monotonic() < deadline:
        # Keep compatibility with Feishu versions whose affirmative button
        # itself contains an explicit attendance label.
        for confirmation_text in (
            "确认更新打卡",
            "确认打卡",
            "继续打卡",
        ):
            confirmation_elements = visible_exact_elements(
                driver,
                confirmation_text,
            )
            if len(confirmation_elements) > 1:
                snapshot(driver, "05_clock_out_confirmation_ambiguous")
                raise RuntimeError(
                    "Multiple explicit attendance confirmation buttons found"
                )
            if len(confirmation_elements) == 1:
                click_element_center(driver, confirmation_elements[0])
                print(
                    "[CLICK] Explicit clock-out confirmation clicked: "
                    f"{ascii(confirmation_text)}"
                )
                return True

        if action == "update-clock":
            prompt_elements = driver.find_elements(
                AppiumBy.XPATH,
                '//*[contains(@text, "确定更新打卡吗") '
                'or contains(@content-desc, "确定更新打卡吗")]'
            )
            visible_prompts = []
            for element in prompt_elements:
                try:
                    rect = element.rect
                    if (
                        element.is_displayed()
                        and rect["width"] > 0
                        and rect["height"] > 0
                    ):
                        visible_prompts.append(element)
                except Exception:
                    continue

            if visible_prompts:
                update_prompt_seen = True
                confirm_elements = visible_exact_elements(driver, "确定")
                if len(confirm_elements) > 1:
                    snapshot(driver, "05_update_confirmation_ambiguous")
                    raise RuntimeError(
                        "Update dialog found, but its OK button is ambiguous"
                    )
                if len(confirm_elements) == 1:
                    click_element_center(driver, confirm_elements[0])
                    print(
                        "[CLICK] Update confirmation clicked after verifying "
                        "the dialog prompt."
                    )
                    return True

        time.sleep(0.5)

    if update_prompt_seen:
        snapshot(driver, "05_update_confirmation_missing")
        raise RuntimeError(
            "Update dialog found, but no unique visible OK button was found"
        )

    print("[INFO] No secondary attendance confirmation dialog detected.")
    return False


def attendance_page_is_ready(driver):
    punch_areas = driver.find_elements(
        AppiumBy.XPATH,
        '//*[@resource-id="punchArea"]'
    )
    if any(element.is_displayed() for element in punch_areas):
        return True

    # After a completed clock-out, Feishu switches to a summary layout that
    # no longer exposes punchArea. The exact update action is the stable marker
    # for that completed-attendance state.
    update_buttons = driver.find_elements(
        AppiumBy.XPATH,
        '//*[@text="更新打卡" or @content-desc="更新打卡"]'
    )
    return any(element.is_displayed() for element in update_buttons)


def wait_for_attendance_ready(driver, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if attendance_page_is_ready(driver):
            return True
        time.sleep(1)
    return False


def navigate_to_attendance(driver):
    """Reach Attendance from chats, Workbench, drawers, or nested pages."""
    for navigation_step in range(1, 11):
        print(f"[NAV] Evaluating current Feishu page, step={navigation_step}.")

        if dismiss_anti_mistouch(driver):
            driver.activate_app(FEISHU_PACKAGE)
            time.sleep(2)
            continue

        if attendance_page_is_ready(driver):
            print("[NAV] Attendance page is ready.")
            return

        # The account picker is a full page opened from the account drawer.
        if page_has_label(driver, "加入已有企业") or page_has_label(driver, "个人使用"):
            print("[NAV] Add-account page detected; pressing Back.")
            driver.back()
            time.sleep(2)
            continue

        # Handle the left account drawer before looking at obscured Workbench
        # nodes. UiAutomator may expose elements behind this overlay.
        if page_has_label(driver, "我的个人名片") and page_has_label(driver, "钱包"):
            print("[NAV] Account drawer detected; pressing Back.")
            driver.back()
            time.sleep(2)
            continue

        # Current Feishu versions can resume directly on Workbench. The app
        # icon label uses resource-id .../name; click it without opening More.
        if click_visible_label(driver, "假勤", resource_id_suffix="/name"):
            print("[NAV] Attendance app selected from Workbench.")
            if wait_for_attendance_ready(driver):
                print("[NAV] Attendance page finished loading.")
                return
            print("[NAV] Attendance did not become ready within 15 seconds.")
            continue

        # Older layouts expose Workbench as an item inside the More tab.
        if click_visible_label(driver, "工作台", resource_id_suffix="/textItem"):
            print("[NAV] Workbench selected from More.")
            time.sleep(3)
            continue

        # From Messages or another primary tab, open More once. This branch is
        # evaluated only after ruling out an already-visible Attendance icon.
        if click_visible_label(driver, "更多", resource_id_suffix="/textItem"):
            print("[NAV] More tab selected.")
            time.sleep(2)
            continue

        # Unknown nested pages (chat, mini-app, details) are handled by one
        # conservative Back action followed by a fresh state evaluation.
        print("[NAV] No known navigation marker found; pressing Back.")
        driver.back()
        time.sleep(2)

    snapshot(driver, "03_attendance_navigation_failed")
    raise RuntimeError("Unable to navigate safely to the Attendance page")


options = UiAutomator2Options()

options.platform_name = "Android"
options.automation_name = "UiAutomator2"

options.udid = UDID
options.no_reset = True

# Xiaomi HyperOS workaround
options.set_capability(
    "appium:skipDeviceInitialization",
    True
)

options.set_capability(
    "appium:printPageSourceOnFindFailure",
    True
)

initial_screen_was_off = read_initial_screen_off_state()

print("Connecting to Appium...")

driver = webdriver.Remote(
    "http://127.0.0.1:4723",
    options=options
)

# Xiaomi devices with a secondary display may expose the sub-screen window as
# the single active window after Appium restarts. Include all interactive
# windows so the main-display Feishu controls remain discoverable.
driver.update_settings({
    "enableMultiWindows": True
})
print("[APPIUM] Multi-window page source enabled")

restore_locked_state = (
    initial_screen_was_off is True
    or read_initial_lock_state(driver)
)

try:

    prepare_device_for_automation(driver)

    # ========================================
    # Step 0: 打开飞书
    # ========================================

    print("\n=== STEP 0: Open Feishu ===")

    driver.activate_app(
        FEISHU_PACKAGE
    )

    time.sleep(3)
    if dismiss_anti_mistouch(driver):
        # The overlay may temporarily take focus away from Feishu. Activate it
        # once more after the warning has been dismissed.
        driver.activate_app(FEISHU_PACKAGE)
        time.sleep(2)

    snapshot(
        driver,
        "00_feishu_home"
    )

    print(
        "Current package:",
        driver.current_package
    )


    # ========================================
    # Steps 1-3: state-aware navigation
    # ========================================

    print("\n=== STEPS 1-3: Navigate safely to Attendance ===")
    navigate_to_attendance(driver)
    snapshot(driver, "03_attendance")


    # ========================================
    # Step 4: 判断打卡状态
    # ========================================

    print(f"\n=== STEP 4: Detect attendance state ({ARGS.mode}) ===")

    clock_candidate_xpath = (
        '//*[contains(@text, "打卡") '
        'or contains(@content-desc, "打卡")]'
    )

    # The attendance mini-app sometimes renders later than its container.
    # Wait briefly before treating an empty result as an abnormal page.
    candidate_deadline = time.monotonic() + 15
    candidates = []

    while time.monotonic() < candidate_deadline:
        candidates = driver.find_elements(
            AppiumBy.XPATH,
            clock_candidate_xpath
        )

        if candidates:
            break

        time.sleep(1)

    print(f"[INFO] Found {len(candidates)} clock-related candidates")

    # UiAutomator2 对“从当前元素向上查 ancestor”的支持不稳定。
    # 改为从 punchArea 向下查找，并用稳定的 Appium element id 交叉匹配。
    punch_area_candidates = driver.find_elements(
        AppiumBy.XPATH,
        '//*[@resource-id="punchArea"]//*['
        'contains(@text, "打卡") '
        'or contains(@content-desc, "打卡")]'
    )
    punch_area_element_ids = {
        element.id
        for element in punch_area_candidates
    }

    # The completed clock-out summary has no punchArea node. Treat only the
    # exact visible update action as an additional safe attendance scope.
    update_action_candidates = driver.find_elements(
        AppiumBy.XPATH,
        '//*[@text="更新打卡" or @content-desc="更新打卡"]'
    )
    safe_action_element_ids = punch_area_element_ids | {
        element.id
        for element in update_action_candidates
        if element.is_displayed()
    }

    print(
        f"[INFO] Found {len(punch_area_candidates)} "
        "clock-related candidates inside punchArea"
    )

    candidate_info = []

    for i, element in enumerate(candidates):
        try:
            text = element.get_attribute("text") or ""
            desc = element.get_attribute("contentDescription") or ""

            # 不能使用 text or desc，否则其中一个字段可能被忽略
            label = " ".join(
                value for value in (text, desc)
                if value
            )

            # 真正的打卡按钮位于 punchArea 内。
            # 用它排除底部导航栏的“打卡”。
            inside_punch_area = element.id in safe_action_element_ids

            info = {
                "index": i,
                "element": element,
                "label": label,
                "text": text,
                "desc": desc,
                "resource_id": (
                    element.get_attribute("resourceId") or ""
                ),
                "class": (
                    element.get_attribute("className") or ""
                ),
                "bounds": (
                    element.get_attribute("bounds") or ""
                ),
                "inside_punch_area": inside_punch_area,
            }

            candidate_info.append(info)

            print(
                f'[CANDIDATE #{i}] '
                f'label={ascii(label)}, '
                f'id={info["resource_id"]!r}, '
                f'bounds={info["bounds"]}, '
                f'inside_punch_area={inside_punch_area}'
            )

        except Exception as exc:
            print(
                f"[WARN] Failed reading candidate #{i}: {exc}"
            )

    initially_confirmed_times = completed_attendance_times(candidate_info)
    if initially_confirmed_times:
        observation_source = (
            "test_observation" if ARGS.dry_run else "page_existing"
        )
        save_confirmed_times(
            initially_confirmed_times,
            observation_source,
            f"{ARGS.mode}_initial_observation",
        )

    # ========================================
    # 状态 1：出现“下班”
    # ========================================

    off_work_candidates = [
        item
        for item in candidate_info
        if "下班" in item["label"]
    ]
    update_clock_candidates = [
        item
        for item in candidate_info
        if item["inside_punch_area"]
        and "更新打卡" in item["label"]
        and "已打卡" not in item["label"]
    ]

    if ARGS.mode == "clock-out":
        if not candidate_info:
            print("[STATE] Unexpected page state")
            print("[ERROR] No clock-related element was found.")
            snapshot(driver, "04_clock_out_not_found")
            raise RuntimeError("No clock-related element was found")

        clock_out_buttons = [
            item
            for item in candidate_info
            if item["inside_punch_area"]
            and (
                "下班打卡" in item["label"]
                or "更新打卡" in item["label"]
            )
            and "已打卡" not in item["label"]
        ]

        if len(clock_out_buttons) != 1:
            print(
                "[ERROR] The active clock-out button cannot be identified "
                f"uniquely. count={len(clock_out_buttons)}"
            )
            snapshot(driver, "04_clock_out_button_ambiguous")
            raise RuntimeError(
                "The active clock-out button cannot be identified uniquely"
            )

        clock_out_button = clock_out_buttons[0]
        clock_out_action = (
            "update-clock" if "更新打卡" in clock_out_button["label"]
            else "clock-out"
        )
        print(
            f"[STATE] Clock-out action={clock_out_action}."
        )
        print(
            "[ACTION] About to click the clock-out/update button: "
            f'label={ascii(clock_out_button["label"])}, '
            f'bounds={clock_out_button["bounds"]}'
        )
        before_path = OUT_DIR / f"04_before_{clock_out_action}.png"
        driver.save_screenshot(str(before_path))
        print(f"[SCREENSHOT] {before_path}")

        if ARGS.dry_run:
            print(
                f"[DRY-RUN] {clock_out_action} state detection completed; "
                "the real attendance click was skipped."
            )
        else:
            click_element_center(driver, clock_out_button["element"])
            print(f"[CLICK] Attendance button clicked; action={clock_out_action}")

            # A generic "确定" is safe only after the exact update prompt has
            # been observed. This avoids acknowledging unrelated dialogs.
            click_clock_out_confirmation(driver, clock_out_action)

            time.sleep(2)
            snapshot(driver, f"05_after_{clock_out_action}")
            history_source = (
                "script_update"
                if clock_out_action == "update-clock"
                else "script_clock_out"
            )
            confirm_and_save_after_click(
                driver,
                "clock_out",
                history_source,
                clock_out_action,
            )

    elif off_work_candidates or update_clock_candidates:
        print("[STATE] Off-work or update-clock control detected")
        print("[RESULT] Morning clock-in is already complete; no action taken.")

        # Step 4 已经是最后一步。
        # 离开此分支后会自然进入 finally 并关闭 driver。

    # ========================================
    # 状态 2：完全找不到“打卡”
    # ========================================

    elif not candidate_info:
        print("[STATE] Unexpected page state")
        print("[ERROR] No clock-related element was found.")

        # snapshot 同时保存 PNG 和 XML
        snapshot(
            driver,
            "04_clock_in_not_found"
        )

        # 让外层定时脚本获得非零退出码，并在 09:30 前重试。
        raise RuntimeError("No clock-related element was found")

    # ========================================
    # 状态 3：可能是早卡状态
    # ========================================

    else:
        print("[STATE] Possible morning clock-in state")

        # 优先选择明确包含“上班”的打卡按钮
        morning_button = next(
            (
                item
                for item in candidate_info
                if item["inside_punch_area"]
                and "上班" in item["label"]
                and "打卡" in item["label"]
            ),
            None
        )

        # 兼容按钮只显示“打卡”或“立即打卡”的情况
        if morning_button is None:
            possible_buttons = [
                item
                for item in candidate_info
                if item["inside_punch_area"]
                and "下班" not in item["label"]
                and "更新打卡" not in item["label"]
                and "已打卡" not in item["label"]
                and "打卡范围" not in item["label"]
            ]

            if len(possible_buttons) == 1:
                morning_button = possible_buttons[0]

        # 有“打卡”文字不代表一定找到了可打卡按钮
        if morning_button is None:
            print(
                "[ERROR] Clock-related elements exist, but the morning "
                "clock-in button cannot be identified uniquely."
            )

            snapshot(
                driver,
                "04_morning_button_ambiguous"
            )

            # 无法安全确定按钮时不盲点，让外层定时脚本稍后重试。
            raise RuntimeError(
                "The morning clock-in button cannot be identified uniquely"
            )

        else:
            print(
                "[ACTION] About to click the morning clock-in button: "
                f'label={ascii(morning_button["label"])}, '
                f'bounds={morning_button["bounds"]}'
            )

            # 早卡分支只保存截图
            screenshot_path = (
                OUT_DIR /
                "04_before_morning_clock_in.png"
            )

            driver.save_screenshot(
                str(screenshot_path)
            )

            print(
                f"[SCREENSHOT] {screenshot_path}"
            )

            if ARGS.dry_run:
                print(
                    "[DRY-RUN] Timing and state detection completed; "
                    "the real clock-in click was skipped."
                )
            else:
                click_element_center(
                    driver,
                    morning_button["element"]
                )
                print("[CLICK] Morning clock-in button clicked")
                actual_time = confirm_and_save_after_click(
                    driver,
                    "clock_in",
                    "script_clock_in",
                    "clock-in",
                )
                if actual_time:
                    print(f"[RESULT] Morning actual clock-in time={actual_time}.")


finally:
    print("\n=== CLEANUP: Return to Android home screen ===")

    try:
        # Android KEYCODE_HOME = 3。无论流程走到哪个状态，
        # 都在退出 Appium 会话前回到手机桌面。
        driver.press_keycode(3)
        time.sleep(1)
        print("[HOME] Returned to the Android home screen")
    except Exception as exc:
        # 返回桌面失败时仍然必须关闭 Appium 会话，
        # 同时不要覆盖前面流程可能抛出的异常。
        print(f"[WARN] Failed to return to the Android home screen: {exc}")
    finally:
        if restore_locked_state:
            try:
                driver.lock()
                print("[DEVICE] Restored the original locked/screen-off state")
            except Exception as exc:
                print(
                    "[WARN] Failed to restore the original locked/screen-off "
                    f"state: {exc}"
                )
        driver.quit()
