import cv2
import numpy as np
import pytest
from unittest.mock import Mock

from capture_camera import measure_pressure
from paine import capture_camera as camera


def gauge_image(angle, size=120):
    image = np.full((120, 120, 3), 255, dtype=np.uint8)
    pivot = (58, 58)
    # A red limit indicator must not win over the black measurement needle.
    cv2.line(image, pivot, (30, 30), (0, 0, 200), 4)
    radians = np.deg2rad(angle)
    tip = (round(58 + 40 * np.sin(radians)), round(58 - 40 * np.cos(radians)))
    cv2.line(image, pivot, tip, (20, 20, 20), 3)
    cv2.circle(image, pivot, 8, (0, 0, 200), -1)
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("angle, pressure", [(226, 0), (270, 2 - 86 / 65), (291, 1), (356, 2), (61, 3)])
def test_pressure_calibration(angle, pressure):
    detected, measured = measure_pressure(gauge_image(angle))
    assert abs((detected - angle + 180) % 360 - 180) <= 3
    assert measured == pytest.approx(pressure, abs=0.05)


def test_reduced_resolution():
    angle, pressure = measure_pressure(gauge_image(270, size=40))
    assert angle == pytest.approx(270, abs=3)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.05)


def test_no_needle():
    with pytest.raises(RuntimeError, match="confidently detect"):
        measure_pressure(np.full((120, 120, 3), 255, dtype=np.uint8))


def test_night_mode_gray_needle():
    image = np.full((120, 120, 3), 185, dtype=np.uint8)
    # IR renders the red indicator bright and the black needle gray.
    cv2.line(image, (58, 58), (30, 30), (220, 220, 220), 4)
    cv2.line(image, (58, 58), (18, 58), (150, 150, 150), 3)
    cv2.circle(image, (58, 58), 8, (200, 200, 200), -1)
    angle, pressure = measure_pressure(image)
    assert angle == pytest.approx(270, abs=3)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.05)


def test_red_indicator_without_black_needle():
    image = np.full((120, 120, 3), 255, dtype=np.uint8)
    cv2.line(image, (58, 58), (30, 30), (0, 0, 200), 4)
    with pytest.raises(RuntimeError, match="confidently detect"):
        measure_pressure(image)


def test_custom_tick_calibration():
    _, pressure = measure_pressure(gauge_image(270), two_bar_angle=0, three_bar_angle=60)
    assert pressure == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize("angle", [240, 255, 270, 285, 350, 30, 61])
@pytest.mark.parametrize("shift", [(-1, 2), (2, -1)])
def test_day_night_alignment_with_shifted_low_contrast_needle(angle, shift):
    color = gauge_image(angle)
    red = (color[:, :, 2] > 100) & (color[:, :, 0] < 100)
    black = color.max(axis=2) < 100
    infrared = np.full((120, 120), 185, dtype=np.uint8)
    infrared[red] = 220
    infrared[black] = 150
    infrared = cv2.warpAffine(
        infrared, np.float32([[1, 0, shift[0]], [0, 1, shift[1]]]),
        (120, 120), borderValue=185,
    )
    # Small camera-mode translation must not be interpreted as needle rotation.
    day_angle, day_pressure = measure_pressure(color)
    night_angle, night_pressure = measure_pressure(cv2.cvtColor(infrared, cv2.COLOR_GRAY2BGR))
    assert abs((night_angle - day_angle + 180) % 360 - 180) < 1.5
    assert night_pressure == pytest.approx(day_pressure, abs=0.025)


def test_refined_angle_is_not_rounded_to_whole_degrees():
    angle, _ = measure_pressure(gauge_image(291))
    assert abs(angle - round(angle)) > 0.1


def test_median_batch_rejects_single_low_outlier(tmp_path, monkeypatch, capsys):
    capture = Mock(side_effect=[(270, 0.65), (228, 0.03), (271, 0.66)])
    monkeypatch.setattr(camera, "capture_picture", capture)
    monkeypatch.setattr(camera.time, "sleep", Mock())
    cache = {"circle": [300, 200, 40], "frame": [640, 480]}
    angle, pressure = camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10, gauge_cache=cache)
    assert pressure == 0.65
    assert angle == pytest.approx((356 + (0.65 - 2) * 65) % 360)
    assert capture.call_count == 3
    assert [call.args[1].name for call in capture.call_args_list] == [
        "snapshot_shot1.jpg", "snapshot_shot2.jpg", "snapshot.jpg",
    ]
    output = capsys.readouterr().out
    assert "0.650, 0.030, 0.660" in output
    assert "Median pressure from 3 shots: 0.650" in output
    assert "WARNING" in output


@pytest.mark.parametrize("shot_count,expected", [(1, 0.6), (2, 0.65), (3, 0.7), (5, 0.7)])
def test_configurable_batch_count(tmp_path, monkeypatch, shot_count, expected):
    capture = Mock(side_effect=[(270, value) for value in [0.6, 0.7, 0.8, 0.7, 0.6]])
    monkeypatch.setattr(camera, "capture_picture", capture)
    monkeypatch.setattr(camera.time, "sleep", Mock())
    _, pressure = camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10, shot_count=shot_count)
    assert pressure == pytest.approx(expected)
    assert capture.call_count == shot_count


def test_batch_failure_does_not_use_partial_readings_or_new_geometry(tmp_path, monkeypatch):
    cache = {"circle": [300, 200, 40], "frame": [640, 480]}
    original = dict(cache)
    calls = []

    def capture(*args, **kwargs):
        calls.append(True)
        kwargs["gauge_cache"].update(circle=[400, 200, 40])
        if len(calls) == 2:
            raise RuntimeError("Could not confidently detect the black gauge needle")
        return 270, 0.65

    monkeypatch.setattr(camera, "capture_picture", capture)
    monkeypatch.setattr(camera.time, "sleep", Mock())
    with pytest.raises(RuntimeError):
        camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10, gauge_cache=cache)
    assert cache == original


def test_outlier_does_not_replace_last_good_geometry(tmp_path, monkeypatch):
    cache = {"circle": [300, 200, 40], "frame": [640, 480]}
    original = dict(cache)
    values = iter([0.65, 0.66, 0.03])

    def capture(*args, **kwargs):
        kwargs["gauge_cache"].update(circle=[400, 200, 40])
        return 270, next(values)

    monkeypatch.setattr(camera, "capture_picture", capture)
    monkeypatch.setattr(camera.time, "sleep", Mock())
    _, pressure = camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10, gauge_cache=cache)
    assert pressure == 0.65
    assert cache == original


@pytest.mark.parametrize("values", [[3.236, 0.679, 3.647], [0.03, 0.68, 3.2], [0.6, 1.0]])
def test_inconsistent_batch_cannot_publish_arbitrary_median(tmp_path, monkeypatch, values):
    capture = Mock(side_effect=[(270, value) for value in values])
    monkeypatch.setattr(camera, "capture_picture", capture)
    monkeypatch.setattr(camera.time, "sleep", Mock())
    cache = {"circle": [991.8, 918.1, 52.55], "frame": [1920, 1080]}
    original = dict(cache)
    with pytest.raises(RuntimeError, match="no consistent majority"):
        camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10,
                                      shot_count=len(values), gauge_cache=cache)
    assert cache == original


def test_low_contrast_short_ir_shaft():
    image = np.full((120, 120, 3), 185, np.uint8)
    cv2.line(image, (58, 58), (28, 58), (166, 166, 166), 2)
    cv2.circle(image, (58, 58), 8, (205, 205, 205), -1)
    angle, _ = measure_pressure(image)
    assert angle == pytest.approx(270, abs=2)


def test_ir_logo_above_hub_cannot_be_selected_as_pivot():
    image = np.full((120, 120, 3), 185, np.uint8)
    cv2.line(image, (58, 58), (18, 58), (150, 150, 150), 3)
    cv2.circle(image, (58, 58), 7, (195, 195, 195), -1)
    # Brighter than the true hub, inside the old 12-pixel search but not the
    # physical hub area. The old IR detector jumped to this logo highlight.
    cv2.rectangle(image, (55, 48), (63, 50), (245, 245, 245), -1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, y = camera._needle_pivot(gray, np.zeros((120, 120), dtype=bool))
    assert y > 54
    angle, pressure = measure_pressure(image)
    assert angle == pytest.approx(270, abs=2)
    assert pressure == pytest.approx(2 - 86 / 65, abs=0.04)


def test_frame_mode_classifies_monochrome_ir_without_using_brightness():
    for brightness in (20, 185, 240):
        frame = np.full((120, 120, 3), brightness, np.uint8)
        assert camera.frame_lighting_mode(frame) == "night/IR-like"


def test_frame_mode_classifies_color_and_tolerates_small_chroma_noise():
    assert camera.frame_lighting_mode(gauge_image(270)) == "day/color-like"
    frame = np.full((1080, 1920, 3), 185, np.uint8)
    frame[:, :, 0] = 182
    assert camera.frame_lighting_mode(frame) == "night/IR-like"
    frame[100:400, 100:400] = (20, 40, 180)
    assert camera.frame_lighting_mode(frame) == "day/color-like"


@pytest.mark.parametrize("night", [False, True])
def test_capture_logs_frame_mode_even_if_measurement_later_fails(tmp_path, monkeypatch, night):
    frame = gauge_image(270)
    if night:
        frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    video = Mock()
    video.open.return_value = True
    video.read.return_value = (True, frame)
    monkeypatch.setattr(cv2, "VideoCapture", Mock(return_value=video))
    monkeypatch.setattr(camera, "save_gauge_crop", Mock(side_effect=RuntimeError("measurement failed")))
    messages = []
    with pytest.raises(RuntimeError):
        camera.capture_picture(
            {"CAMERA_HOST": "camera", "CAMERA_USER": "test", "CAMERA_PASSWORD": "test"},
            tmp_path / "frame.jpg", 1, 10, diagnostics=messages.append,
        )
    expected = "night/IR-like" if night else "day/color-like"
    assert any(f"mode={expected}" in message and "inferred" in message for message in messages)
    video.release.assert_called_once()


def test_consensus_is_not_a_hardcoded_low_pressure_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(camera, "capture_picture", Mock(side_effect=[
        (75, 3.21), (75, 3.22), (75, 3.20),
    ]))
    monkeypatch.setattr(camera.time, "sleep", Mock())
    _, pressure = camera.capture_pressure_batch({}, tmp_path / "snapshot.jpg", 1, 10)
    assert pressure == pytest.approx(3.21)
