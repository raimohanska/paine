import json
from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client as mqtt
import pytest

from capture_camera import load_credentials
from mqtt_pressure import discovery_config, publish_pressure


def test_discovery_defaults():
    topic, state, payload = discovery_config({})
    assert topic == "homeassistant/sensor/paine_pressure/config"
    assert state == payload["state_topic"] == "paine/pressure/state"
    assert payload["unique_id"] == "paine_pressure"
    assert payload["device_class"] == "pressure"
    assert payload["unit_of_measurement"] == "bar"
    assert payload["state_class"] == "measurement"
    assert payload["expire_after"] == 300
    assert payload["device"]["identifiers"] == ["paine_pressure_gauge"]
    assert payload["device"]["name"] == "Paine pressure gauge"


def test_device_metadata():
    _, _, payload = discovery_config({
        "MQTT_DEVICE_ID": "garage_gauge", "MQTT_DEVICE_NAME": "Garage gauge",
        "MQTT_SUGGESTED_AREA": "Garage", "MQTT_SENSOR_ID": "existing_sensor",
    })
    assert payload["unique_id"] == "existing_sensor"
    assert payload["device"]["identifiers"] == ["garage_gauge"]
    assert payload["device"]["name"] == "Garage gauge"
    assert payload["device"]["suggested_area"] == "Garage"


@pytest.mark.parametrize("config", [
    {"MQTT_SENSOR_ID": "bad/id"}, {"MQTT_STATE_TOPIC": "bad/#"},
    {"MQTT_EXPIRE_AFTER": "0"}, {"MQTT_EXPIRE_AFTER": "invalid"},
    {"MQTT_DEVICE_ID": "bad/id"}, {"MQTT_DEVICE_NAME": " "},
])
def test_invalid_discovery(config):
    with pytest.raises(ValueError):
        discovery_config(config)


def mock_client(monkeypatch, failure=False, delivered=True):
    client = Mock()
    monkeypatch.setattr(mqtt, "Client", Mock(return_value=client))
    client.loop_start.side_effect = lambda: client.on_connect(
        client, None, None, SimpleNamespace(is_failure=failure), None,
    )
    result = Mock(rc=mqtt.MQTT_ERR_SUCCESS)
    result.is_published.return_value = delivered
    client.publish.return_value = result
    return client, result


def test_publish_and_acknowledge(monkeypatch):
    client, result = mock_client(monkeypatch)
    publish_pressure({"MQTT_HOST": "broker", "MQTT_TLS": "true",
                      "MQTT_USER": "test-user", "MQTT_PASSWORD": "test-password"}, 0.59)
    client.connect.assert_called_once_with("broker", 8883, keepalive=60)
    client.tls_set.assert_called_once()
    client.username_pw_set.assert_called_once_with("test-user", "test-password")
    discovery, state = client.publish.call_args_list
    assert discovery.kwargs == {"qos": 1, "retain": True}
    assert json.loads(discovery.args[1])["expire_after"] == 300
    assert state.args == ("paine/pressure/state", "0.59")
    assert state.kwargs == {"qos": 1, "retain": True}
    assert result.wait_for_publish.call_count == 2
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()


def test_rejected_connection(monkeypatch):
    client, _ = mock_client(monkeypatch, failure=True)
    with pytest.raises(RuntimeError, match="connection failed"):
        publish_pressure({"MQTT_HOST": "broker"}, 0.59)
    client.publish.assert_not_called()
    client.disconnect.assert_called_once()


def test_unacknowledged_publish(monkeypatch):
    client, _ = mock_client(monkeypatch, delivered=False)
    with pytest.raises(RuntimeError, match="timed out"):
        publish_pressure({"MQTT_HOST": "broker"}, 0.59)
    assert client.publish.call_count == 1
    client.loop_stop.assert_called_once()


def test_config_loader(tmp_path):
    # Synthetic non-secret config, never the user's .env.
    path = tmp_path / "test-config.txt"
    path.write_text('CAMERA_HOST=camera\nCAMERA_USER=test\nCAMERA_PASSWORD=test\n'
                    'MQTT_HOST=broker\nMQTT_SENSOR_NAME="Heating pressure"\n')
    config = load_credentials(path)
    assert config["MQTT_HOST"] == "broker"
    assert config["MQTT_SENSOR_NAME"] == "Heating pressure"
