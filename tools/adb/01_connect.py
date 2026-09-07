# ruff: noqa: E402
import time
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from appium import webdriver
from appium.options.android import UiAutomator2Options

from attendance_hub.core.app_config import load_app_config, require_device_access


APP_CONFIG = load_app_config()
require_device_access(APP_CONFIG)
UDID = APP_CONFIG.display_udid
FEISHU_PACKAGE = APP_CONFIG.feishu_package

OUT_DIR = Path("artifacts")
OUT_DIR.mkdir(exist_ok=True)


options = UiAutomator2Options()

options.platform_name = "Android"
options.automation_name = "UiAutomator2"

options.udid = UDID

# 保留飞书登录状态和现有数据
options.no_reset = True

# HyperOS workaround:
# 跳过 io.appium.settings 初始化
options.set_capability(
    "appium:skipDeviceInitialization",
    True
)

options.set_capability(
    "appium:printPageSourceOnFindFailure",
    True
)


print("Connecting to Appium...")

driver = webdriver.Remote(
    "http://127.0.0.1:4723",
    options=options
)

try:
    print("Appium session created.")

    print("Opening Feishu...")

    driver.activate_app(FEISHU_PACKAGE)

    time.sleep(5)

    print("Current package:")
    print(driver.current_package)

    try:
        print("Current activity:")
        print(driver.current_activity)
    except Exception as e:
        print("Could not read current activity:", e)

    screenshot_path = OUT_DIR / "feishu_home.png"

    driver.save_screenshot(
        str(screenshot_path)
    )

    xml = driver.page_source

    xml_path = OUT_DIR / "feishu_home.xml"

    xml_path.write_text(
        xml,
        encoding="utf-8"
    )

    print()
    print("SUCCESS")
    print(f"Screenshot: {screenshot_path}")
    print(f"UI XML:     {xml_path}")

finally:
    driver.quit()
