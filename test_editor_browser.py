"""Optional Chromium smoke tests: install Playwright and its Chromium browser."""

from copy import deepcopy
from pathlib import Path

import pytest

from paine.config_ui import ASSETS


playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def editor_browser():
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page, errors
        browser.close()


def open_editor(page, gauges):
    stored = {
        "options": {"mqtt_host": "broker", "mqtt_port": 1883, "mqtt_user": "user",
                    "mqtt_password": "!secret mqtt_password", "mqtt_tls": False,
                    "capture_interval": 60, "capture_timeout": 10, "shot_count": 3,
                    "gauges": deepcopy(gauges)},
        "revision": "original",
    }
    saves = []
    prefix = "http://paine.test/api/hassio_ingress/synthetic/"

    def route(request_route):
        request = request_route.request
        path = "/" + request.url.removeprefix(prefix)
        if path in ASSETS:
            name, content_type = ASSETS[path]
            request_route.fulfill(body=Path("paine", name).read_bytes(), content_type=content_type)
        elif path == "/api/config":
            if request.method == "POST":
                assert request.headers["x-paine-editor"] == "1"
                submitted = request.post_data_json
                assert submitted["revision"] == stored["revision"]
                stored["options"] = submitted["options"]
                stored["revision"] = "saved"
                saves.append(deepcopy(submitted))
                request_route.fulfill(json={"message": "Saved. Restart Paine from its Info page."})
            else:
                request_route.fulfill(json=stored)
        else:
            request_route.fulfill(status=404)

    page.route(prefix + "**", route)
    page.goto(prefix)
    page.locator("#editor").wait_for(state="visible")
    return stored, saves


def test_browser_add_edit_save_remove_preserves_identity_and_references(editor_browser):
    page, errors = editor_browser
    first = {"sensor_id": "old_pressure", "sensor_name": "<img src=x onerror=alert(1)>",
             "camera_host": "old-camera:8554", "camera_user": "old-user",
             "camera_password": "!secret camera_password", "two_bar_angle": 350,
             "three_bar_angle": 55, "device_id": "old_device", "device_name": "Old device",
             "state_topic": "paine/pressure/state"}
    stored, saves = open_editor(page, [first])
    assert page.locator("#cameras img").count() == 0  # Names are text, not injected HTML.
    assert page.locator('#cameras input[name="camera_password"]').get_attribute("type") == "password"
    page.get_by_role("button", name="Add camera", exact=True).click()
    second = page.locator("#cameras .card").nth(1)
    second.locator('[name="sensor_id"]').fill("boiler_pressure")
    second.locator('[name="camera_host"]').fill("second-camera:554")
    second.locator('[name="camera_user"]').fill("second-user")
    second.locator('[name="camera_password"]').fill("synthetic-password")
    second.locator('[name="two_bar_angle"]').fill("356")
    second.locator('[name="three_bar_angle"]').fill("61")
    second.locator("summary").click()
    second.locator('[name="capture_interval"]').fill("90")
    page.get_by_role("button", name="Save settings", exact=True).click()
    page.wait_for_function("document.getElementById('status').textContent.includes('Restart')")
    assert stored["options"]["gauges"][0] == first
    assert stored["options"]["gauges"][1]["capture_interval"] == 90
    assert stored["options"]["mqtt_password"] == "!secret mqtt_password"
    assert len(saves) == 1
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#cameras .card").nth(1).get_by_role("button", name="Remove camera").click()
    page.get_by_role("button", name="Save settings", exact=True).click()
    page.wait_for_function("document.getElementById('status').textContent.includes('Restart')")
    assert len(stored["options"]["gauges"]) == 1
    assert stored["options"]["gauges"][0] == first
    assert not errors


def test_browser_first_run_requires_camera_fields_before_saving(editor_browser):
    page, errors = editor_browser
    _, saves = open_editor(page, [])
    page.get_by_role("button", name="Add camera", exact=True).click()
    page.get_by_role("button", name="Save settings", exact=True).click()
    assert not saves
    assert page.locator('#cameras input[name="two_bar_angle"]').get_attribute("required") is not None
    assert not errors
