import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from threading import Event
from unittest.mock import Mock

import pytest
import yaml

from paine import run


def test_log_timestamps_each_line_in_utc(monkeypatch, capsys):
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 15, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr(run, "datetime", clock)
    run.log("First line\nSecond line")
    assert capsys.readouterr().out == (
        "[2026-10-15 12:34:56 UTC] First line\n"
        "[2026-10-15 12:34:56 UTC] Second line\n"
    )
    clock.now.assert_called_once_with(timezone.utc)


def options_file(tmp_path, **overrides):
    defaults = yaml.safe_load(Path("paine/config.yaml").read_text())["options"]
    defaults.update(camera_host="camera", camera_user="test-user",
                    camera_password="test-camera-password", mqtt_user="test-mqtt-user",
                    mqtt_password="test-mqtt-password")
    defaults.update(overrides)
    path = tmp_path / "test-options.json"
    path.write_text(json.dumps(defaults))
    return path


def test_app_config_and_credentials(tmp_path):
    metadata = yaml.safe_load(Path("paine/config.yaml").read_text())
    assert "aarch64" in metadata["arch"]
    assert set(metadata["options"]) == set(metadata["schema"])
    assert metadata["schema"]["camera_password"] == "password"
    assert not metadata.get("host_network")
    assert not metadata.get("privileged")
    config, interval, stream, timeout, two, three = run.load_options(options_file(tmp_path))
    assert (interval, stream, timeout, two, three) == (60, 1, 10, 356, 61)
    assert config["MQTT_HOST"] == "core-mosquitto"
    assert config["CAMERA_HOST"] == "camera:554"
    assert config["CAMERA_PASSWORD"] == "test-camera-password"
    assert config["MQTT_DEVICE_ID"] == "paine_pressure_gauge"


@pytest.mark.parametrize("overrides", [
    {"camera_password": ""}, {"capture_interval": 0}, {"expire_after": 60},
    {"capture_timeout": float("nan")}, {"stream": 3}, {"two_bar_angle": 61},
    {"mqtt_tls": "false"},
])
def test_invalid_options(tmp_path, overrides):
    with pytest.raises(ValueError):
        run.load_options(options_file(tmp_path, **overrides))


def test_forwarded_camera_port(tmp_path):
    config, *_ = run.load_options(options_file(tmp_path, camera_host="forwarder", camera_port=8554))
    assert config["CAMERA_HOST"] == "forwarder:8554"


def test_legacy_camera_host_port_takes_precedence(tmp_path):
    config, *_ = run.load_options(options_file(tmp_path, camera_host="forwarder:8554"))
    assert config["CAMERA_HOST"] == "forwarder:8554"


@pytest.mark.parametrize("port", [0, 65536, "invalid"])
def test_invalid_camera_port(tmp_path, port):
    with pytest.raises(ValueError):
        run.load_options(options_file(tmp_path, camera_port=port))


def test_capture_and_publish_once(tmp_path, monkeypatch, capsys):
    capture = Mock(return_value=(272, 0.71))
    publish = Mock()
    monkeypatch.setattr(run, "capture_picture", capture)
    monkeypatch.setattr(run, "publish_pressure", publish)
    assert run.capture_once(options_file(tmp_path), tmp_path) == 0
    assert capture.call_args.args[1] == tmp_path / "snapshot.jpg"
    assert publish.call_args.args[1] == 0.71
    assert "0.71 bar" in capsys.readouterr().out


def test_worker_failure_does_not_leak_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "capture_picture", Mock(side_effect=RuntimeError("secret-authenticated-url")))
    assert run.capture_once(options_file(tmp_path), tmp_path) == 1
    output = capsys.readouterr().out
    assert "failed" in output
    assert "secret-authenticated-url" not in output


def test_capture_failure_identifies_stage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "capture_picture", Mock(side_effect=RuntimeError(
        "Could not confidently detect the black gauge needle")))
    publish = Mock()
    monkeypatch.setattr(run, "publish_pressure", publish)
    assert run.capture_once(options_file(tmp_path), tmp_path) == 1
    output = capsys.readouterr().out
    assert "Camera capture/measurement failed" in output
    assert "Cannot confidently detect the needle" in output
    publish.assert_not_called()


def test_mqtt_failure_identifies_stage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "capture_picture", Mock(return_value=(272, 0.71)))
    monkeypatch.setattr(run, "publish_pressure", Mock(side_effect=ConnectionRefusedError(
        "secret-authenticated-url")))
    assert run.capture_once(options_file(tmp_path), tmp_path) == 1
    output = capsys.readouterr().out
    assert "estimated pressure: 0.71" in output
    assert "MQTT publication failed: Connection refused" in output
    assert "secret-authenticated-url" not in output


def test_worker_subprocess(tmp_path, monkeypatch, capsys):
    process = Mock(returncode=0)
    worker_output = "[2026-10-15 12:34:56 UTC] Pressure: 0.71 bar\n"
    process.communicate.return_value = (worker_output, None)
    process.poll.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(run.subprocess, "Popen", popen)
    assert run.run_capture(tmp_path / "test-options.json", tmp_path, 10, Event())
    assert popen.call_args.kwargs["stderr"] == subprocess.DEVNULL
    assert capsys.readouterr().out == worker_output


def test_worker_shutdown(tmp_path, monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(run.subprocess, "Popen", Mock(return_value=process))
    stop = Event()
    stop.set()
    assert not run.run_capture(tmp_path / "test-options.json", tmp_path, 10, stop)
    process.terminate.assert_called_once()
    process.communicate.assert_called_once_with(timeout=5)


def test_scheduler_runs_immediately_then_stops(tmp_path, monkeypatch):
    stop = Event()
    capture = Mock(side_effect=lambda *_: stop.set())
    monkeypatch.setattr(run, "run_capture", capture)
    assert run.run_loop(options_file(tmp_path), tmp_path, stop) == 0
    capture.assert_called_once()


def test_scheduler_retries_failures(tmp_path, monkeypatch):
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    capture = Mock(return_value=False)
    monkeypatch.setattr(run, "run_capture", capture)
    assert run.run_loop(options_file(tmp_path), tmp_path, stop) == 0
    assert capture.call_count == 2
    assert stop.wait.call_count == 2
