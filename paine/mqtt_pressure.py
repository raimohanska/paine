"""Shared single-reading MQTT publisher with Home Assistant sensor discovery."""

import json
import math
import re
from threading import Event


def discovery_config(config: dict[str, str]) -> tuple[str, str, dict]:
    """Build and validate non-secret sensor metadata."""
    sensor_id = config.get("MQTT_SENSOR_ID", "paine_pressure")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sensor_id):
        raise ValueError("MQTT_SENSOR_ID must contain only letters, digits, underscores, or hyphens")
    device_id = config.get("MQTT_DEVICE_ID", "paine_pressure_gauge")
    device_name = config.get("MQTT_DEVICE_NAME", "Paine pressure gauge")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", device_id):
        raise ValueError("MQTT_DEVICE_ID must contain only letters, digits, underscores, or hyphens")
    if not device_name.strip():
        raise ValueError("MQTT_DEVICE_NAME must not be empty")
    device = {
        "identifiers": [device_id],
        "name": device_name,
        "model": "Camera-based pressure gauge",
    }
    if config.get("MQTT_SUGGESTED_AREA"):
        device["suggested_area"] = config["MQTT_SUGGESTED_AREA"]
    state_topic = config.get("MQTT_STATE_TOPIC", "paine/pressure/state")
    prefix = config.get("MQTT_DISCOVERY_PREFIX", "homeassistant").rstrip("/")
    for topic in (state_topic, prefix):
        if not topic or any(character in topic for character in "+#\x00"):
            raise ValueError("MQTT topics must be nonempty and cannot contain wildcards or null characters")
    try:
        expire_after = int(config.get("MQTT_EXPIRE_AFTER", "300"))
    except ValueError:
        raise ValueError("MQTT_EXPIRE_AFTER must be a positive integer") from None
    if expire_after <= 0:
        raise ValueError("MQTT_EXPIRE_AFTER must be a positive integer")
    payload = {
        "name": config.get("MQTT_SENSOR_NAME", "Heating pressure"),
        "unique_id": sensor_id,
        "state_topic": state_topic,
        "device_class": "pressure",
        "unit_of_measurement": "bar",
        "state_class": "measurement",
        "expire_after": expire_after,
        "qos": 1,
        "device": device,
    }
    return f"{prefix}/sensor/{sensor_id}/config", state_topic, payload


def publish_pressure(config: dict[str, str], pressure: float, timeout: float = 10.0) -> None:
    """Publish discovery and a fresh reading, waiting for both QoS 1 ACKs."""
    import paho.mqtt.client as mqtt

    if not math.isfinite(pressure) or not 0 <= pressure <= 4:
        raise ValueError("Pressure must be a finite value between 0 and 4 bar")
    discovery_topic, state_topic, payload = discovery_config(config)
    host = config.get("MQTT_HOST", "").strip()
    if not host or any(character.isspace() for character in host) or "/" in host:
        raise ValueError("MQTT_HOST must be a hostname or IP address")
    tls = config.get("MQTT_TLS", "false").lower()
    if tls not in {"true", "false"}:
        raise ValueError("MQTT_TLS must be true or false")
    try:
        port = int(config.get("MQTT_PORT", "8883" if tls == "true" else "1883"))
    except ValueError:
        raise ValueError("MQTT_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("MQTT_PORT must be an integer between 1 and 65535")
    if config.get("MQTT_PASSWORD") and not config.get("MQTT_USER"):
        raise ValueError("MQTT_USER is required when MQTT_PASSWORD is set")

    connected = Event()
    accepted = []

    def on_connect(client, userdata, flags, reason_code, properties):
        accepted.append(not reason_code.is_failure)
        connected.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect_timeout = timeout
    client.on_connect = on_connect
    if config.get("MQTT_USER"):
        client.username_pw_set(config["MQTT_USER"], config.get("MQTT_PASSWORD"))
    if tls == "true":
        # Use system trust roots and verify the broker certificate/hostname.
        client.tls_set()
    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
        if not connected.wait(timeout) or not accepted[0]:
            raise RuntimeError("MQTT broker connection failed")
        for topic, message, retained in (
            (discovery_topic, json.dumps(payload), True),
            (state_topic, f"{pressure:.2f}", True),
        ):
            result = client.publish(topic, message, qos=1, retain=retained)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError("MQTT publish failed")
            result.wait_for_publish(timeout=timeout)
            if not result.is_published():
                raise RuntimeError("MQTT delivery acknowledgement timed out")
    finally:
        client.disconnect()
        client.loop_stop()
