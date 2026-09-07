import time
import xml.etree.ElementTree as ET
from pathlib import Path

from appium import webdriver
from appium.options.android import UiAutomator2Options

from app_config import load_app_config, require_device_access


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
options.no_reset = True

# Xiaomi / HyperOS workaround
options.set_capability(
    "appium:skipDeviceInitialization",
    True
)

driver = webdriver.Remote(
    "http://127.0.0.1:4723",
    options=options
)

try:
    driver.activate_app(FEISHU_PACKAGE)
    time.sleep(3)

    xml = driver.page_source

    xml_path = OUT_DIR / "home_probe.xml"
    xml_path.write_text(xml, encoding="utf-8")

    driver.save_screenshot(
        str(OUT_DIR / "home_probe.png")
    )

    print("\n=== Visible / useful UI nodes ===\n")

    root = ET.fromstring(xml)

    for node in root.iter():

        text = node.attrib.get("text", "")
        desc = node.attrib.get("content-desc", "")
        resource_id = node.attrib.get("resource-id", "")
        clickable = node.attrib.get("clickable", "")
        cls = node.attrib.get("class", "")

        if text or desc or resource_id:

            print(
                f"text={text!r:20} "
                f"desc={desc!r:20} "
                f"id={resource_id!r:45} "
                f"clickable={clickable:5} "
                f"class={cls}"
            )

finally:
    driver.quit()
