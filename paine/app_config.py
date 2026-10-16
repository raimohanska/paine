"""Home Assistant single/multiple-gauge configuration and internal defaults."""

from dataclasses import dataclass, field as dataclass_field
import json
import math
from pathlib import Path

from .mqtt_pressure import discovery_config

STREAM = 1
DISCOVERY_PREFIX = "homeassistant"
MIN_EXPIRE_AFTER = 300
EXPIRY_INTERVALS = 3

OPTION_KEYS = {
    "camera_host": "CAMERA_HOST", "camera_user": "CAMERA_USER",
    "camera_password": "CAMERA_PASSWORD", "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT", "mqtt_user": "MQTT_USER",
    "mqtt_password": "MQTT_PASSWORD", "sensor_name": "MQTT_SENSOR_NAME",
    "sensor_id": "MQTT_SENSOR_ID", "state_topic": "MQTT_STATE_TOPIC",
    "device_id": "MQTT_DEVICE_ID", "device_name": "MQTT_DEVICE_NAME",
}

DEFAULTS = {
    "mqtt_host": "core-mosquitto", "mqtt_port": 1883,
    "sensor_id": "paine_pressure", "sensor_name": "Heating pressure",
    "state_topic": "paine/pressure/state", "device_id": "paine_pressure_gauge",
    "device_name": "Paine pressure gauge",
}

LEGACY_FIELDS = {
    "camera_host", "camera_user", "camera_password", "sensor_id", "sensor_name",
    "state_topic", "device_id", "device_name", "two_bar_angle", "three_bar_angle",
}
REMOVED_FIELDS = {"stream", "suggested_area", "discovery_prefix", "expire_after"}


def migrate_options(options):
    """Normalize old/new formats in memory without rewriting stored settings."""
    if not isinstance(options, dict) or not isinstance(options.get("gauges", []), list):
        return options, False
    entries = list(options.get("gauges", []))
    if options.get("camera_host"):
        sensor_id = options.get("sensor_id", DEFAULTS["sensor_id"])
        legacy = {key: options[key] for key in LEGACY_FIELDS if key in options}
        for key in ("sensor_id", "sensor_name", "state_topic", "device_id", "device_name"):
            legacy.setdefault(key, DEFAULTS[key])
        legacy.setdefault("two_bar_angle", 356)
        legacy.setdefault("three_bar_angle", 61)
        matching = [index for index, entry in enumerate(entries)
                    if isinstance(entry, dict) and entry.get("sensor_id") == sensor_id]
        if matching:
            # Explicit camera changes win; unspecified HA identities/topics stay
            # stable while a user adopts the new list format.
            for index in matching:
                entries[index] = {**legacy, **entries[index]}
        else:
            entries.insert(0, legacy)
    result = {key: value for key, value in options.items() if key not in LEGACY_FIELDS | REMOVED_FIELDS}
    result["gauges"] = entries
    return result, result != options


@dataclass(frozen=True)
class GaugeSettings:
    config: dict[str, str] = dataclass_field(repr=False)
    interval: int
    timeout: float
    two_bar_angle: float
    three_bar_angle: float
    shot_count: int
    storage_key: str | None = None

    @property
    def sensor_id(self):
        return self.config["MQTT_SENSOR_ID"]

    def as_tuple(self):
        return (self.config, self.interval, STREAM, self.timeout,
                self.two_bar_angle, self.three_bar_angle, self.shot_count)


def _parse(options, storage_key=None):
    options = {**DEFAULTS, **options}
    config = {key: str(options[option]) for option, key in OPTION_KEYS.items() if option in options}
    for field in ("camera_host", "camera_user", "camera_password", "mqtt_host"):
        if not isinstance(options.get(field), str) or not options[field].strip():
            raise ValueError(f"Configure {field} before starting the app")
    tls = options.get("mqtt_tls", False)
    if not isinstance(tls, bool):
        raise ValueError("mqtt_tls must be true or false")
    config["MQTT_TLS"] = "true" if tls else "false"
    try:
        interval = int(options.get("capture_interval", 60))
        shot_count = int(options.get("shot_count", 3))
        timeout = float(options.get("capture_timeout", 10))
        two = float(options.get("two_bar_angle", 356))
        three = float(options.get("three_bar_angle", 61))
        port = int(options["mqtt_port"])
    except (TypeError, ValueError):
        raise ValueError("Capture timing, shot count, calibration, and MQTT port must be numeric") from None
    if not 15 <= interval <= 86400:
        raise ValueError("capture_interval must be between 15 and 86400 seconds")
    if not 1 <= shot_count <= 9:
        raise ValueError("shot_count must be between 1 and 9")
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("capture_timeout must be greater than zero and at most 300")
    if not (0 <= two < 360 and 0 <= three < 360 and 0 < (three - two) % 360 < 90):
        raise ValueError("Invalid 2-bar and 3-bar tick calibration")
    if not 1 <= port <= 65535:
        raise ValueError("MQTT port must be between 1 and 65535")
    if config.get("MQTT_PASSWORD") and not config.get("MQTT_USER"):
        raise ValueError("mqtt_user is required when mqtt_password is set")
    # Ignore removed options even if an upgraded Supervisor options file retains
    # them. Keep policy fixed instead of accidentally using stale per-gauge values.
    config["MQTT_DISCOVERY_PREFIX"] = DISCOVERY_PREFIX
    config["MQTT_EXPIRE_AFTER"] = str(max(MIN_EXPIRE_AFTER, EXPIRY_INTERVALS * interval))
    discovery_config(config)  # Validate identifiers, metadata, and topics.
    return GaugeSettings(config, interval, timeout, two, three, shot_count, storage_key)


def load_gauges(path: Path) -> list[GaugeSettings]:
    """Read profiles and/or a legacy flat camera without changing the source file."""
    options = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(options, dict):
        raise ValueError("App configuration must be an object")
    options, _ = migrate_options(options)
    entries = options.get("gauges", [])
    if not isinstance(entries, list):
        raise ValueError("gauges must be a list")
    if not entries:
        raise ValueError("Configure at least one entry in gauges")
    gauges = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each gauge must be a configuration object")
        for field in ("camera_host", "camera_user", "camera_password", "sensor_id"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Each gauge must supply {field}")
        if "two_bar_angle" not in entry or "three_bar_angle" not in entry:
            raise ValueError("Each gauge must supply its own two_bar_angle and three_bar_angle")
        sensor_id = entry["sensor_id"]
        name = entry.get("sensor_name", sensor_id.replace("_", " ").replace("-", " "))
        # Broker/timing defaults may be shared; never inherit another camera's
        # identity, topic, or calibration from legacy top-level gauge fields.
        merged = {
            **options, **entry,
            "sensor_id": sensor_id,
            "sensor_name": name,
            "state_topic": entry.get("state_topic", f"paine/{sensor_id}/state"),
            "device_id": entry.get("device_id", f"{sensor_id}_device"),
            "device_name": entry.get("device_name", name),
            "two_bar_angle": entry.get("two_bar_angle", 356),
            "three_bar_angle": entry.get("three_bar_angle", 61),
        }
        gauges.append(_parse(merged, storage_key=sensor_id))
    sensor_ids = set()
    device_ids = set()
    state_topics = set()
    discovery_topics = set()
    for gauge in gauges:
        config = gauge.config
        discovery_topic, state_topic, _ = discovery_config(config)
        broker = (config["MQTT_HOST"].strip().lower(), int(config["MQTT_PORT"]))
        if (gauge.sensor_id in sensor_ids or config["MQTT_DEVICE_ID"] in device_ids
                or (*broker, state_topic) in state_topics):
            raise ValueError("Gauges must have distinct sensor IDs, device IDs, and broker state topics")
        sensor_ids.add(gauge.sensor_id)
        device_ids.add(config["MQTT_DEVICE_ID"])
        state_topics.add((*broker, state_topic))
        discovery_topics.add((*broker, discovery_topic))
    if state_topics & discovery_topics:
        raise ValueError("Gauge state topics cannot collide with discovery topics")
    return gauges


def load_options(path: Path):
    """Compatibility helper for a single configured gauge."""
    gauges = load_gauges(path)
    if len(gauges) != 1:
        raise ValueError("Select a gauge when more than one is configured")
    return gauges[0].as_tuple()
