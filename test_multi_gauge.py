import json
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
import yaml

from paine import run
from paine.app_config import load_gauges, load_options


def multi_options(tmp_path, entries=None, **overrides):
    options = yaml.safe_load(Path("paine/config.yaml").read_text())["options"]
    options.update(mqtt_user="shared-user", mqtt_password="shared-password")
    options["gauges"] = entries if entries is not None else [
        {"sensor_id": "garage", "camera_host": "camera-a:8554", "camera_user": "camera-a-user",
         "camera_password": "camera-a-password", "two_bar_angle": 356, "three_bar_angle": 61},
        {"sensor_id": "boiler", "camera_host": "camera-b", "camera_user": "camera-b-user",
         "camera_password": "camera-b-password", "two_bar_angle": 358, "three_bar_angle": 65,
         "capture_interval": 90},
    ]
    options.update(overrides)
    path = tmp_path / "multi-options.json"
    path.write_text(json.dumps(options))
    return path


def test_two_gauges_have_independent_camera_calibration_and_identity(tmp_path):
    first, second = load_gauges(multi_options(tmp_path))
    assert first.config["CAMERA_HOST"] == "camera-a:8554"
    assert second.config["CAMERA_HOST"] == "camera-b"
    assert first.config["CAMERA_PASSWORD"] != second.config["CAMERA_PASSWORD"]
    assert first.two_bar_angle == 356
    assert second.two_bar_angle == 358
    assert first.three_bar_angle == 61
    assert second.three_bar_angle == 65
    assert first.config["MQTT_STATE_TOPIC"] == "paine/garage/state"
    assert second.config["MQTT_STATE_TOPIC"] == "paine/boiler/state"
    assert first.config["MQTT_DEVICE_ID"] != second.config["MQTT_DEVICE_ID"]
    assert first.config["MQTT_USER"] == second.config["MQTT_USER"] == "shared-user"
    assert first.storage_key == "garage"
    assert second.storage_key == "boiler"


def test_per_gauge_broker_override(tmp_path):
    path = multi_options(tmp_path)
    options = json.loads(path.read_text())
    options["gauges"][1].update(mqtt_host="other-broker", mqtt_port=8883,
                                 mqtt_user="other-user", mqtt_password="other-password", mqtt_tls=True)
    path.write_text(json.dumps(options))
    first, second = load_gauges(path)
    assert first.config["MQTT_HOST"] == "core-mosquitto"
    assert second.config["MQTT_HOST"] == "other-broker"
    assert second.config["MQTT_USER"] == "other-user"
    assert second.config["MQTT_TLS"] == "true"


def legacy_options():
    return {
        "camera_host": "existing-camera:8554", "camera_user": "existing-user",
        "camera_password": "existing-password", "mqtt_host": "existing-broker",
        "mqtt_port": 1883, "mqtt_user": "existing-mqtt-user",
        "mqtt_password": "existing-mqtt-password", "sensor_id": "paine_pressure1",
        "sensor_name": "Existing pressure", "device_id": "paine_pressure_gauge",
        "device_name": "Existing device", "state_topic": "paine/pressure/state",
        "two_bar_angle": 350, "three_bar_angle": 55, "capture_interval": 60,
        "shot_count": 3, "stream": 1, "expire_after": 300,
        "discovery_prefix": "homeassistant", "suggested_area": "Garage",
    }


def test_old_flat_config_works_after_upgrade_without_rewriting(tmp_path):
    path = tmp_path / "old-options.json"
    old = legacy_options()
    path.write_text(json.dumps(old))
    original = path.read_bytes()
    gauge, = load_gauges(path)
    assert path.read_bytes() == original
    assert gauge.config["CAMERA_HOST"] == old["camera_host"]
    assert gauge.config["CAMERA_PASSWORD"] == old["camera_password"]
    assert gauge.config["MQTT_PASSWORD"] == old["mqtt_password"]
    assert gauge.sensor_id == old["sensor_id"]
    assert gauge.config["MQTT_DEVICE_ID"] == old["device_id"]
    assert gauge.config["MQTT_STATE_TOPIC"] == old["state_topic"]
    assert (gauge.two_bar_angle, gauge.three_bar_angle) == (350, 55)
    assert "existing-password" not in repr(gauge)


def test_add_second_gauge_without_losing_old_flat_camera(tmp_path):
    path = multi_options(tmp_path)
    new_gauge = json.loads(path.read_text())["gauges"][1]
    old = legacy_options()
    old["gauges"] = [new_gauge]
    path.write_text(json.dumps(old))
    first, second = load_gauges(path)
    assert first.sensor_id == "paine_pressure1"
    assert second.sensor_id == "boiler"
    assert first.config["CAMERA_HOST"] == "existing-camera:8554"
    assert second.config["CAMERA_HOST"] == "camera-b"


def test_explicit_existing_profile_does_not_duplicate_legacy_camera(tmp_path):
    old = legacy_options()
    old["gauges"] = [{
        "sensor_id": old["sensor_id"], "camera_host": "replacement-camera",
        "camera_user": "replacement-user", "camera_password": "replacement-password",
        "two_bar_angle": 356, "three_bar_angle": 61,
    }]
    path = tmp_path / "mixed-options.json"
    path.write_text(json.dumps(old))
    gauge, = load_gauges(path)
    assert gauge.config["CAMERA_HOST"] == "replacement-camera"
    assert gauge.config["MQTT_DEVICE_ID"] == old["device_id"]
    assert gauge.config["MQTT_STATE_TOPIC"] == old["state_topic"]


def test_documented_two_gauge_example_loads(tmp_path):
    text = Path("paine/CONFIGURATION.md").read_text()
    example = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    path = tmp_path / "documented-options.json"
    path.write_text(json.dumps(yaml.safe_load(example)))
    assert len(load_gauges(path)) == 2


@pytest.mark.parametrize("key,value", [
    ("sensor_id", "garage"), ("device_id", "garage_device"),
    ("state_topic", "paine/garage/state"),
])
def test_duplicate_gauge_identity_is_rejected(tmp_path, key, value):
    path = multi_options(tmp_path)
    options = json.loads(path.read_text())
    options["gauges"][1][key] = value
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError, match="distinct"):
        load_gauges(path)


@pytest.mark.parametrize("key", ["camera_host", "camera_user", "camera_password",
                                "sensor_id", "two_bar_angle", "three_bar_angle"])
def test_required_camera_specific_settings_cannot_be_inherited(tmp_path, key):
    path = multi_options(tmp_path)
    options = json.loads(path.read_text())
    del options["gauges"][1][key]
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError):
        load_gauges(path)


def test_state_topic_cannot_overwrite_discovery(tmp_path):
    path = multi_options(tmp_path)
    options = json.loads(path.read_text())
    options["gauges"][1]["state_topic"] = "homeassistant/sensor/garage/config"
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError, match="discovery"):
        load_gauges(path)


def test_removed_options_are_not_used_or_exposed(tmp_path):
    metadata = yaml.safe_load(Path("paine/config.yaml").read_text())
    removed = {"stream", "suggested_area", "discovery_prefix", "expire_after"}
    for keys in (metadata["options"], metadata["schema"], metadata["schema"]["gauges"][0]):
        assert not removed.intersection(keys)
    path = multi_options(tmp_path, stream=2, discovery_prefix="old-prefix",
                         expire_after=1, suggested_area="Old room")
    for settings in load_gauges(path):
        assert settings.as_tuple()[2] == 1
        assert settings.config["MQTT_DISCOVERY_PREFIX"] == "homeassistant"
        assert settings.config["MQTT_EXPIRE_AFTER"] == "300"
        assert "MQTT_SUGGESTED_AREA" not in settings.config


def test_expiry_automatically_covers_long_intervals(tmp_path):
    first, _ = load_gauges(multi_options(tmp_path, capture_interval=900))
    assert first.config["MQTT_EXPIRE_AFTER"] == "2700"


def test_single_gauge_helper_requires_selection_for_multiple_gauges(tmp_path):
    with pytest.raises(ValueError, match="Select"):
        load_options(multi_options(tmp_path))


def test_workers_select_correct_gauge_and_separate_files(tmp_path, monkeypatch, capsys):
    path = multi_options(tmp_path)
    capture = Mock(return_value=(270, 0.7))
    publish = Mock()
    monkeypatch.setattr(run, "capture_pressure_batch", capture)
    monkeypatch.setattr(run, "publish_pressure", publish)
    for sensor_id in ("garage", "boiler"):
        assert run.capture_once(path, tmp_path, gauge_id=sensor_id) == 0
        config, output, stream, _, two, three = capture.call_args.args
        assert config["MQTT_SENSOR_ID"] == sensor_id
        assert output == tmp_path / "gauges" / sensor_id / "snapshot.jpg"
        assert stream == 1
        assert (two, three) == ((356, 61) if sensor_id == "garage" else (358, 65))
        assert not capture.call_args.kwargs["allow_legacy_fallback"]
        assert publish.call_args.args[0]["MQTT_SENSOR_ID"] == sensor_id
    output = capsys.readouterr().out
    assert "[garage]" in output and "[boiler]" in output
    assert "camera-a-password" not in output and "camera-b-password" not in output


def test_scheduler_has_independent_intervals_and_caches(tmp_path, monkeypatch):
    clock = [0.0]
    calls = []
    caches = {}
    stop = Mock()
    stop.is_set.side_effect = lambda: len(calls) >= 5
    stop.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    monkeypatch.setattr(run.time, "monotonic", lambda: clock[0])

    def capture(options_path, data_path, timeout, stop, cache, shot_count, gauge_id=None):
        calls.append((gauge_id, clock[0]))
        if gauge_id in caches:
            assert cache is caches[gauge_id]
        else:
            assert not cache
            caches[gauge_id] = cache
            cache["test_owner"] = gauge_id
        assert cache["test_owner"] == gauge_id
        return True

    monkeypatch.setattr(run, "run_capture", capture)
    assert run.run_loop(multi_options(tmp_path), tmp_path, stop) == 0
    assert calls == [("garage", 0), ("boiler", 0), ("garage", 60), ("boiler", 90), ("garage", 120)]
    assert caches["garage"] is not caches["boiler"]


def test_failed_worker_does_not_stop_other_gauges(tmp_path, monkeypatch, capsys):
    stop = Event()
    calls = []

    def capture(*args, gauge_id=None):
        calls.append(gauge_id)
        if gauge_id == "garage":
            raise RuntimeError("secret connection details")
        stop.set()
        return True

    monkeypatch.setattr(run, "run_capture", capture)
    assert run.run_loop(multi_options(tmp_path), tmp_path, stop) == 0
    assert calls == ["garage", "boiler"]
    assert "secret connection details" not in capsys.readouterr().out
