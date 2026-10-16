"""Editor and persistence checks use synthetic settings, never live Supervisor."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from paine import run
from paine.app_config import migrate_options
from paine.config_ui import ASSETS, MAX_BODY, make_handler
from paine.supervisor_config import ConfigurationError, SupervisorConfig


@pytest.fixture
def old_options():
    return {
        "camera_host": "old-camera:8554", "camera_user": "old-user",
        "camera_password": "!secret camera_password", "sensor_id": "existing_pressure",
        "sensor_name": "Existing pressure", "device_id": "existing_gauge",
        "device_name": "Existing gauge", "state_topic": "paine/pressure/state",
        "two_bar_angle": 350, "three_bar_angle": 55,
        "mqtt_host": "broker", "mqtt_user": "mqtt-user", "mqtt_port": 1883,
        "mqtt_password": "!secret mqtt_password", "capture_interval": 90,
        "shot_count": 3, "capture_timeout": 10, "mqtt_tls": False,
        "stream": 1, "suggested_area": "Garage", "expire_after": 300,
    }


@pytest.fixture
def supervisor(monkeypatch, old_options):
    """Emulate raw persisted options and the self-options API."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "synthetic-token")
    client = SupervisorConfig()
    stored = deepcopy(old_options)

    def request(endpoint, body=None):
        if endpoint == "info":
            return {"options": deepcopy(stored)}
        if endpoint == "options":
            stored.clear()
            stored.update(deepcopy(body["options"]))
            return {}
        raise AssertionError(endpoint)

    client._request = Mock(side_effect=request)
    return client, stored


def test_startup_migration_persists_identities_and_secret_references(supervisor, old_options):
    client, stored = supervisor
    report = Mock()
    client.migrate(report)
    assert "camera_host" not in stored
    assert "suggested_area" not in stored
    first, = stored["gauges"]
    for key in ("camera_host", "camera_user", "camera_password", "sensor_id", "sensor_name",
                "device_id", "device_name", "state_topic", "two_bar_angle", "three_bar_angle"):
        assert first[key] == old_options[key]
    assert stored["mqtt_password"] == "!secret mqtt_password"
    assert stored["capture_interval"] == 90
    assert "migrated" in report.call_args.args[0]
    client._request.reset_mock()
    client.migrate(report)
    assert [call.args[0] for call in client._request.call_args_list] == ["info"]


def test_migration_preserves_second_profile_and_explicit_overrides(supervisor):
    client, stored = supervisor
    stored["gauges"] = [
        {"sensor_id": "existing_pressure", "camera_host": "replacement-camera"},
        {"sensor_id": "second_pressure", "camera_host": "second-camera", "camera_user": "user2",
         "camera_password": "!secret other_camera", "two_bar_angle": 356, "three_bar_angle": 61},
    ]
    client.migrate(Mock())
    first, second = stored["gauges"]
    assert first["camera_host"] == "replacement-camera"
    assert first["device_id"] == "existing_gauge"
    assert first["state_topic"] == "paine/pressure/state"
    assert second["sensor_id"] == "second_pressure"


def test_failed_migration_does_not_overwrite_options_or_expose_errors(supervisor):
    client, stored = supervisor
    original = deepcopy(stored)
    client._request.side_effect = RuntimeError("secret-credential")
    report = Mock()
    client.migrate(report)
    assert stored == original
    assert "secret-credential" not in report.call_args.args[0]
    assert "legacy parsing remains active" in report.call_args.args[0]


def test_incomplete_legacy_camera_is_not_discarded(supervisor):
    client, stored = supervisor
    stored["camera_host"] = ""
    original = deepcopy(stored)
    client.migrate(Mock())
    assert stored == original
    # The visual form must still retain entered credentials while the user fills
    # in the missing address; only automatic persistence is deferred.
    camera, = client.read()["options"]["gauges"]
    assert camera["camera_user"] == original["camera_user"]
    assert camera["camera_password"] == original["camera_password"]


def test_visual_editor_saves_two_cameras_and_preserves_hidden_fields(supervisor):
    client, stored = supervisor
    result = client.read()
    first = deepcopy(result["options"]["gauges"][0])
    result["options"]["gauges"].append({
        "sensor_id": "second_pressure", "camera_host": "second-camera:554", "camera_user": "user2",
        "camera_password": "!secret other_camera", "two_bar_angle": 356, "three_bar_angle": 61,
        "capture_interval": 120,
    })
    client.save(result["options"], result["revision"])
    assert stored["gauges"][0] == first
    assert len(stored["gauges"]) == 2
    assert stored["mqtt_password"] == "!secret mqtt_password"


def test_editor_rejects_stale_revision(supervisor):
    client, stored = supervisor
    result = client.read()
    stored["capture_interval"] = 120
    with pytest.raises(ConfigurationError, match="changed elsewhere"):
        client.save(result["options"], result["revision"])
    assert "camera_host" in stored


def test_editor_checks_duplicate_ids_even_with_password_references(supervisor):
    client, stored = supervisor
    result = client.read()
    result["options"]["gauges"].append(deepcopy(result["options"]["gauges"][0]))
    with pytest.raises(ConfigurationError, match="unique IDs"):
        client.save(result["options"], result["revision"])
    assert "camera_host" in stored


def test_invalid_supervisor_validation_cannot_persist(supervisor):
    client, stored = supervisor
    result = client.read()
    def request(endpoint, body=None):
        if endpoint == "info":
            return {"options": stored}
        raise ConfigurationError("Supervisor configuration request failed; check app options")

    client._request.side_effect = request
    with pytest.raises(ConfigurationError, match="Supervisor configuration request failed"):
        client.save(result["options"], result["revision"])
    assert "camera_host" in stored


def test_supervisor_transport_keeps_credentials_local_and_errors_sanitized(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "synthetic-token")
    opener = Mock()
    response = Mock()
    response.__enter__ = Mock(return_value=BytesIO(b'{"result":"ok","data":{"test":true}}'))
    response.__exit__ = Mock(return_value=False)
    opener.open.return_value = response
    factory = Mock(return_value=opener)
    monkeypatch.setattr("paine.supervisor_config.build_opener", factory)
    client = SupervisorConfig()
    assert client._request("options", {"options": {}}) == {"test": True}
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://supervisor/addons/self/options"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert factory.call_args.args[0].proxies == {}
    opener.open.side_effect = RuntimeError("synthetic-token secret-password")
    with pytest.raises(ConfigurationError) as error:
        client._request("info")
    assert "synthetic-token" not in str(error.value)
    assert "secret-password" not in str(error.value)


def handler(config, path="/api/config", body=None, peer="172.30.32.2", headers=True):
    """Exercise actual handler methods without listening on a network port."""
    from email.message import Message

    instance = make_handler(config).__new__(make_handler(config))
    instance.client_address = (peer, 1234)
    instance.path = path
    instance.headers = Message()
    if headers:
        instance.headers["X-Paine-Editor"] = "1"
        instance.headers["Content-Type"] = "application/json"
    encoded = b"" if body is None else json.dumps(body).encode()
    instance.headers["Content-Length"] = str(len(encoded))
    instance.rfile = BytesIO(encoded)
    instance.wfile = BytesIO()
    instance.send_response = Mock()
    instance.send_header = Mock()
    instance.end_headers = Mock()
    return instance


def test_ingress_only_rejects_direct_requests_and_forged_forwarding_headers():
    config = Mock()
    request = handler(config, peer="172.30.33.4")
    request.headers["X-Forwarded-For"] = "172.30.32.2"
    request.do_GET()
    request.send_response.assert_called_with(403)
    config.read.assert_not_called()


def test_editor_http_load_save_and_no_cache(supervisor):
    client, stored = supervisor
    request = handler(client)
    request.do_GET()
    request.send_response.assert_called_with(200)
    request.send_header.assert_any_call("Cache-Control", "no-store")
    data = json.loads(request.wfile.getvalue())
    assert data["options"]["gauges"][0]["sensor_id"] == "existing_pressure"
    assert "synthetic-token" not in request.wfile.getvalue().decode()
    post = handler(client, body=data)
    post.do_POST()
    post.send_response.assert_called_with(200)
    assert "Restart" in json.loads(post.wfile.getvalue())["message"]
    assert "camera_host" not in stored


@pytest.mark.parametrize("path", ASSETS)
def test_static_assets_are_served_and_packaged(path):
    request = handler(Mock(), path=path)
    request.do_GET()
    request.send_response.assert_called_with(200)
    assert request.wfile.getvalue()
    dockerfile = Path("paine/Dockerfile").read_text()
    allowlist = Path("paine/.dockerignore").read_text()
    asset = ASSETS[path][0]
    assert asset in dockerfile and f"!{asset}" in allowlist


def test_editor_csrf_and_body_limits():
    config = Mock()
    post = handler(config, body={}, headers=False)
    post.do_POST()
    post.send_response.assert_called_with(403)
    post = handler(config, body={})
    post.headers.replace_header("Content-Length", str(MAX_BODY + 1))
    post.do_POST()
    post.send_response.assert_called_with(400)
    config.save.assert_not_called()


def test_handler_suppresses_unexpected_backend_exception_text():
    config = Mock()
    config.save.side_effect = RuntimeError("secret-password")
    post = handler(config, body={"options": {}, "revision": "revision"})
    post.do_POST()
    assert "secret-password" not in post.wfile.getvalue().decode()
    post.send_response.assert_called_with(400)


def test_main_runs_migration_and_keeps_editor_alive_without_valid_cameras(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "synthetic-token")
    monkeypatch.setattr("sys.argv", ["paine"])
    monkeypatch.setattr(run.signal, "signal", Mock())
    stop = Mock()
    monkeypatch.setattr(run, "Event", Mock(return_value=stop))
    loop = Mock(return_value=1)
    monkeypatch.setattr(run, "run_loop", loop)
    client = Mock()
    monkeypatch.setattr("paine.supervisor_config.SupervisorConfig", Mock(return_value=client))
    server = Mock()
    start = Mock(return_value=server)
    monkeypatch.setattr("paine.config_ui.start_editor", start)
    assert run.main() == 1
    client.migrate.assert_called_once_with(run.log)
    start.assert_called_once_with(client)
    stop.wait.assert_called_once_with()
    server.shutdown.assert_called_once()
    server.server_close.assert_called_once()


def test_ingress_metadata_and_version_match():
    metadata = yaml.safe_load(Path("paine/config.yaml").read_text())
    assert metadata["ingress"] and metadata["ingress_port"] == 8099
    assert metadata["panel_admin"]
    assert not metadata.get("ports")
    assert not metadata.get("hassio_api")  # Only self-options endpoints needed.
    assert f'io.hass.version="{metadata["version"]}"' in Path("paine/Dockerfile").read_text()


def test_normalizing_options_is_idempotent(old_options):
    original = deepcopy(old_options)
    migrated, changed = migrate_options(old_options)
    assert changed
    assert old_options == original
    again, changed = migrate_options(migrated)
    assert not changed
    assert again == migrated
