"""Two cameras configured with scalar fields in HA's normal options form."""

import json
from pathlib import Path

import pytest
import yaml

from paine.app_config import load_gauges


def form_options(tmp_path, **overrides):
    options = yaml.safe_load(Path("paine/config.yaml").read_text())["options"]
    options.update(camera_host="first-camera:8554", camera_user="first-user",
                   camera_password="first-password", second_camera_host="second-camera",
                   second_camera_user="second-user", second_camera_password="second-password",
                   second_two_bar_angle=350, second_three_bar_angle=55,
                   mqtt_user="broker-user", mqtt_password="broker-password")
    options.update(overrides)
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    return path


def test_normal_form_has_scalar_second_camera_fields_and_no_web_ui():
    metadata = yaml.safe_load(Path("paine/config.yaml").read_text())
    for key in ("camera_host", "camera_user", "camera_password", "sensor_id",
                "two_bar_angle", "three_bar_angle"):
        assert key in metadata["options"]
        assert f"second_{key}" in metadata["options"]
        assert isinstance(metadata["schema"][f"second_{key}"], str)
    assert metadata["schema"]["second_camera_password"] == "password"
    assert metadata["options"]["second_camera_host"] == ""
    assert not metadata.get("ingress")
    assert not metadata.get("ports")
    assert not metadata.get("hassio_api")
    assert not Path("paine/config_ui.py").exists()
    assert not Path("paine/supervisor_config.py").exists()
    assert f'io.hass.version="{metadata["version"]}"' in Path("paine/Dockerfile").read_text()


def test_two_camera_form_credentials_calibration_identity_and_shared_defaults(tmp_path):
    path = form_options(tmp_path)
    original = path.read_bytes()
    first, second = load_gauges(path)
    assert path.read_bytes() == original
    assert first.config["CAMERA_HOST"] == "first-camera:8554"
    assert second.config["CAMERA_HOST"] == "second-camera"
    assert first.config["CAMERA_USER"] == "first-user"
    assert second.config["CAMERA_USER"] == "second-user"
    assert first.config["CAMERA_PASSWORD"] == "first-password"
    assert second.config["CAMERA_PASSWORD"] == "second-password"
    assert (first.two_bar_angle, first.three_bar_angle) == (356, 61)
    assert (second.two_bar_angle, second.three_bar_angle) == (350, 55)
    assert first.config["MQTT_STATE_TOPIC"] == "paine/pressure/state"
    assert second.config["MQTT_STATE_TOPIC"] == "paine/paine_pressure_2/state"
    assert first.config["MQTT_DEVICE_ID"] == "paine_pressure_gauge"
    assert second.config["MQTT_DEVICE_ID"] == "paine_pressure_2_device"
    assert second.sensor_id == "paine_pressure_2"
    assert first.config["MQTT_PASSWORD"] == second.config["MQTT_PASSWORD"] == "broker-password"
    assert first.interval == second.interval == 60
    assert first.storage_key != second.storage_key


def test_second_camera_disabled_by_blank_address(tmp_path):
    gauge, = load_gauges(form_options(tmp_path, second_camera_host="", second_sensor_id="invalid/id"))
    assert gauge.sensor_id == "paine_pressure"


def test_only_second_slot_does_not_create_an_empty_first_camera(tmp_path):
    gauge, = load_gauges(form_options(tmp_path, camera_host=""))
    assert gauge.sensor_id == "paine_pressure_2"


@pytest.mark.parametrize("key", ["second_two_bar_angle", "second_three_bar_angle"])
def test_second_slot_never_inherits_first_cameras_calibration(tmp_path, key):
    path = form_options(tmp_path)
    options = json.loads(path.read_text())
    del options[key]
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError, match="own two_bar_angle and three_bar_angle"):
        load_gauges(path)


@pytest.mark.parametrize("key,value", [
    ("second_camera_user", ""), ("second_camera_password", ""),
    ("second_two_bar_angle", 61), ("second_sensor_id", "invalid/id"),
    ("second_sensor_id", "paine_pressure"),
    ("second_device_id", "paine_pressure_gauge"),
    ("second_state_topic", "paine/pressure/state"),
])
def test_enabled_second_camera_is_validated(tmp_path, key, value):
    with pytest.raises(ValueError):
        load_gauges(form_options(tmp_path, **{key: value}))


def test_camera_one_custom_identity_unchanged(tmp_path):
    first, second = load_gauges(form_options(
        tmp_path, sensor_id="old_sensor", sensor_name="Old pressure", device_id="old_device",
        device_name="Old device", state_topic="old/state", two_bar_angle=358, three_bar_angle=63,
    ))
    assert first.sensor_id == "old_sensor"
    assert first.config["MQTT_DEVICE_ID"] == "old_device"
    assert first.config["MQTT_STATE_TOPIC"] == "old/state"
    assert (first.two_bar_angle, first.three_bar_angle) == (358, 63)
    assert (second.two_bar_angle, second.three_bar_angle) == (350, 55)


def test_list_profiles_remain_compatible_without_ghost_camera_one(tmp_path):
    profile = {"sensor_id": "list_camera", "camera_host": "list-host", "camera_user": "list-user",
               "camera_password": "list-password", "two_bar_angle": 350, "three_bar_angle": 55}
    gauge, = load_gauges(form_options(tmp_path, camera_host="", second_camera_host="", gauges=[profile]))
    assert gauge.sensor_id == "list_camera"


def test_existing_explicit_second_profile_is_not_duplicated(tmp_path):
    profile = {"sensor_id": "paine_pressure_2", "camera_host": "explicit-host", "camera_user": "user",
               "camera_password": "password", "two_bar_angle": 358, "three_bar_angle": 63}
    first, second = load_gauges(form_options(tmp_path, gauges=[profile]))
    assert first.sensor_id == "paine_pressure"
    assert second.config["CAMERA_HOST"] == "explicit-host"
    assert (second.two_bar_angle, second.three_bar_angle) == (358, 63)
