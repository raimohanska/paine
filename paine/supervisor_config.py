"""Persist app configuration through Supervisor, preserving raw !secret references."""

import hashlib
import json
import os
from threading import Lock
from urllib.request import ProxyHandler, Request, build_opener

from .app_config import migrate_options, parse_gauges


class ConfigurationError(Exception):
    """An intentionally credential-free error suitable for the editor/log."""


class SupervisorConfig:
    def __init__(self):
        self._token = os.environ.get("SUPERVISOR_TOKEN")
        self._opener = build_opener(ProxyHandler({}))
        self._lock = Lock()

    def _request(self, endpoint, body=None):
        if not self._token:
            raise ConfigurationError("Supervisor connection is unavailable")
        try:
            request = Request(
                "http://supervisor/addons/self/" + endpoint,
                headers={"Authorization": f"Bearer {self._token}",
                         "Content-Type": "application/json"},
                data=None if body is None else json.dumps(body).encode(),
            )
            with self._opener.open(request, timeout=15) as response:
                result = json.load(response)
            if result.get("result") != "ok":
                raise ValueError()
            return result.get("data", {})
        except Exception:
            # Supervisor errors can contain submitted credentials. Never relay them.
            raise ConfigurationError("Supervisor configuration request failed; check app options") from None

    def _read(self):
        options = self._request("info")["options"]
        if not isinstance(options, dict):
            raise ConfigurationError("Supervisor returned invalid app options")
        return options

    @staticmethod
    def revision(options):
        return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()

    def read(self):
        with self._lock:
            raw = self._read()
            normalized, _ = migrate_options(raw)
            return {"options": normalized, "revision": self.revision(raw)}

    def _validate(self, options):
        if not isinstance(options, dict) or not isinstance(options.get("gauges"), list):
            raise ConfigurationError("Configure a list of cameras")
        # The self/options POST validates the schema atomically before storing.
        # Do not use options/validate: that nested endpoint needs the much broader
        # manager role, unlike self/info and self/options. Never resolve stored
        # !secret references into plaintext just to validate them locally.
        # Credential references are strings and can still be checked locally.
        # References for numeric fields/identities require runtime resolution.
        validation_keys = {"sensor_id", "device_id", "state_topic", "mqtt_host", "mqtt_port",
                           "mqtt_tls", "capture_interval", "capture_timeout", "shot_count",
                           "two_bar_angle", "three_bar_angle"}
        unresolved = any(
            isinstance(value, str) and value.startswith("!secret ")
            for entry in [options, *options["gauges"]] if isinstance(entry, dict)
            for key, value in entry.items() if key in validation_keys
        )
        if not unresolved:
            try:
                parse_gauges(options)
            except Exception:
                raise ConfigurationError("Invalid camera profiles; check calibration, unique IDs and MQTT topics") from None

    def save(self, options, revision):
        with self._lock:
            current = self._read()
            if revision != self.revision(current):
                raise ConfigurationError("Settings changed elsewhere. Reload the editor before saving")
            normalized, _ = migrate_options(options)
            self._validate(normalized)
            self._request("options", {"options": normalized})

    def migrate(self, report):
        """Idempotent startup migration; failures leave the legacy runtime usable."""
        try:
            with self._lock:
                raw = self._read()
                normalized, changed = migrate_options(raw)
                if not changed:
                    return
                # Do not discard an incomplete legacy camera's entered credentials.
                if any(key in raw for key in ("camera_user", "camera_password")) and not raw.get("camera_host"):
                    report("Incomplete legacy camera settings retained; complete them before migration")
                    return
                self._validate(normalized)
                self._request("options", {"options": normalized})
            report("Saved configuration migrated to camera profiles; calibration and MQTT identities preserved")
        except Exception:
            report("WARNING: Could not persist camera migration; legacy parsing remains active. Retrying on next start")
