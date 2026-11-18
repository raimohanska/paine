"""Wheel geometry, commissioning units, and optional local capture regressions."""

import json
import os
from contextlib import contextmanager
from itertools import cycle, count
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from paine.water_locator import (
    load_hint,
    load_reference,
    locate,
    other_bank,
    rectangle,
    warp,
)
from paine.water_model import prepare_strip, wheel_inputs
from paine.water_state import convert_layout, decide
from paine.water_store import load_state
from paine.app_config import load_gauges
from paine import water_reader, mqtt_pressure


def bank(n):
    image = np.full((140, 340), 130, np.uint8)
    x, y = 51, 46
    cv2.rectangle(image, (x, y), (x + n * 28, y + 44), 25, -1)
    for i in range(n):
        cv2.rectangle(
            image, (x + 28 * i + 1, y + 1), (x + 28 * i + 27, y + 43), 220, -1
        )
        cv2.putText(image, str(i), (x + 28 * i + 5, y + 31), 0, 0.8, 30, 2)
    return image


@pytest.mark.parametrize("n", range(4, 10))
def test_unseen_wheel_bank(n):
    img = bank(n)
    H = cv2.getPerspectiveTransform(
        rectangle(340, 140), np.float32([[42, 9], [365, 44], [336, 183], [14, 162]])
    )
    img = cv2.warpPerspective(img, H, (390, 200), borderValue=130)
    hit = other_bank(img)
    assert hit is not None
    q, count, kind = hit
    assert (count, kind) == (n, "dividers")
    expected = cv2.perspectiveTransform(
        np.float32([[[51, 46], [51 + n * 28, 46], [51 + n * 28, 90], [51, 90]]]), H
    )[0]
    assert np.linalg.norm(q - expected, axis=1).max() < 4
    tiles = prepare_strip(warp(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), q, 24 * n))
    assert len(list(wheel_inputs(tiles))) == n


def test_reject_serial_number_and_two_banks():
    plain = np.full((140, 340), 180, np.uint8)
    cv2.rectangle(plain, (28, 38), (272, 93), 25, 2)
    cv2.putText(plain, "1234567", (37, 78), 0, 1.5, 20, 2)
    assert other_bank(plain) is None
    assert other_bank(np.vstack([bank(5), bank(7)])) is None


def test_cache_is_only_a_verified_hint(tmp_path):
    ref = load_reference()
    img = cv2.cvtColor(bank(7), cv2.COLOR_GRAY2BGR)
    img = cv2.copyMakeBorder(img, 0, 40, 0, 600, cv2.BORDER_CONSTANT, value=(130,) * 3)
    q, n, _, s = locate(img, ref)
    assert (n, s) == (7, "full")
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(dict(mirror=True, frame=list(img.shape[:2]), corners=q.tolist()))
    )
    hint = load_hint(path, img.shape, True)
    assert locate(img, ref, hint)[3] == "local"
    moved = cv2.warpAffine(
        img, np.float32([[1, 0, 480], [0, 1, 23]]), (img.shape[1], img.shape[0])
    )
    found = locate(moved, ref, hint)
    assert found[3] == "full" and np.max(np.abs(found[0] - q - [480, 23])) < 3
    assert (
        load_hint(path, img.shape, False) is None
        and load_hint(path, (240, 100), True) is None
    )
    for text in ("{", "{}", '{"frame":[180,940],"mirror":true,"corners":[1e99]}'):
        path.write_text(text)
        assert load_hint(path, img.shape, True) is None


def test_precision_change_round_trip_and_flow_limit(tmp_path):
    old = dict(
        last=210828, day_start=210803, at=60, pending=210829, pending_at=100, count=1
    )
    new = convert_layout(old, 3, 0)
    assert new == dict(
        last=2108280, day_start=2108030, at=60, decimals=3, omit=0, wheels=7
    )
    (tmp_path / "state.json").write_text(json.dumps(new))
    assert load_state(tmp_path) == new
    row = [20, 10, 0, 80, 30, 20, 10]  # 2108.321: 41 litres in one minute
    _, event = decide(new, [row] * 9, row, 120, 60, 40, 7, 3)
    assert event["decision"] == "flow_limit" and event["max_delta_ticks"] == 40
    assert convert_layout(new, 2, 1)["day_start"] == old["day_start"]
    with pytest.raises(ValueError, match="reinterpret"):
        convert_layout(old, 2, 0)


def test_litre_update_preserves_entities_and_rejects_wrong_count(tmp_path, monkeypatch):
    config = tmp_path / "options.json"
    config.write_text(
        json.dumps(
            dict(
                water_meter_camera_host="camera",
                water_meter_camera_user="user",
                water_meter_camera_password="test",
                water_meter_mirror_image=False,
            )
        )
    )
    (settings,) = load_gauges(config)
    assert settings.mirror_image is False
    root = tmp_path / "data"
    root.mkdir()
    (root / "state.json").write_text(
        json.dumps(dict(last=210828, day_start=210820, at=60))
    )
    row = [20, 10, 0, 80, 20, 80, 0]
    preview = np.zeros((32, 140, 3), np.uint8)
    raw = np.zeros((40, 174, 3), np.uint8)

    def observe(settings, root):
        return (
            [list(row) for _ in range(9)],
            row,
            preview,
            dict(wheel_count=len(row), frames=list(range(1, 10))),
            [raw] * 9,
            preview,
        )

    monkeypatch.setattr(water_reader, "observe", observe)
    monkeypatch.setattr(water_reader, "time", SimpleNamespace(time=lambda: 120))
    sent = []
    monkeypatch.setattr(mqtt_pressure, "_publish", lambda *args: sent.append(args))

    def run():
        assert water_reader.water_once(settings, root, lambda _: None, str) == 0
        return json.loads((root / "events.jsonl").read_text().splitlines()[-1])

    assert run()["decision"] == "confirmed"
    assert [(a[2], a[4], a[3]["suggested_display_precision"]) for a in sent] == [
        ("paine/water_meter/state", "2108.280", 3),
        ("paine/water_meter/daily/state", "0.080", 3),
    ]
    assert [a[3]['unit_of_measurement'] for a in sent] == ['m³', 'm³']
    del row[-1]
    assert run()["decision"] == "wheel_count_changed"
    assert sent[-1][4] == '0.080' and sent[-2][4] is None
    assert load_state(root)["last"] == 2108280


@pytest.mark.parametrize(
    "group,visible",
    [
        ("water-meter-20261117-181751", False),
        ("water-meter-lights-on-20261117-181900", True),
        ("water-meter-night-light-20261117-182653", True),
    ],
)
def test_real_full_frame_bursts(group, visible):
    paths = sorted((Path("snapshots") / group).glob("shot-*.jpg"))
    if not paths:
        pytest.skip("Private full-frame capture set unavailable")
    ref = load_reference()
    hint = None
    for path in paths:
        img = cv2.flip(cv2.imread(str(path)), 1)
        hit = locate(img, ref, hint)
        assert (hit is not None) == visible
        if visible:
            q, n, kind, search = hit
            assert (n, kind) == (7, "reference")
            assert search == ("full" if hint is None else "local")
            expected = np.float32(
                [[763, 433], [938, 442], [938, 480], [762, 473]]
                if "lights-on" in group
                else [[784, 386], [957, 400], [955, 438], [782, 426]]
            )
            assert np.linalg.norm(q - expected, axis=1).max() < 5
            hint = q


def test_moved_camera_real_register_without_meter_face():
    path = Path("snapshots/water-meter-lights-on-20261117-181900/shot-3.jpg")
    if not path.exists():
        pytest.skip("Private full-frame capture unavailable")
    src = np.float32([[763, 433], [938, 442], [938, 480], [762, 473]])
    img = cv2.flip(cv2.imread(str(path)), 1)
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, src.astype(int), 255)
    img[mask == 0] = 100  # remove *all* surrounding face and labels
    dest = np.float32([[182, 84], [402, 106], [411, 159], [166, 146]])
    H = cv2.getPerspectiveTransform(src, dest)
    changed = cv2.warpPerspective(img, H, (640, 360), borderValue=(100,) * 3)
    q, n, _, _ = locate(changed, load_reference())
    assert n == 7 and np.max(np.linalg.norm(q - dest, axis=1)) < 5


def test_capture_worker_replay(tmp_path, monkeypatch):
    """Opt-in with the checksum-pinned model; no camera, clock waits, or MQTT."""
    model = os.environ.get("PAINE_TEST_WATER_MODEL")
    paths = sorted(
        Path("snapshots/water-meter-lights-on-20261117-181900").glob("shot-*.jpg")
    )
    if not model or not paths:
        pytest.skip("Specify PAINE_TEST_WATER_MODEL with the local capture set")
    from ai_edge_litert import interpreter
    from paine import water_reader

    real = interpreter.Interpreter
    monkeypatch.setattr(
        interpreter, "Interpreter", lambda **kw: real(model_path=model, num_threads=1)
    )
    images = [cv2.imread(str(p)) for p in paths]
    frames = cycle(images)

    @contextmanager
    def camera(*args):
        yield SimpleNamespace(read=lambda: (True, next(frames)))

    monkeypatch.setattr(water_reader, "open_camera_stream", camera)
    monkeypatch.setattr(
        water_reader, "time", SimpleNamespace(monotonic=count(step=1).__next__)
    )
    settings = SimpleNamespace(mirror_image=True, config={}, timeout=10)
    for expected in ("full", "local"):
        rows, selected, sheet, details, raw, overview = water_reader.observe(
            settings, tmp_path
        )
        assert (
            len(rows) == 9 and all(len(row) == 7 for row in rows) and len(selected) == 7
        )
        assert details["search"][0] == expected and details["wheel_count"] == 7
        assert raw[0].shape == (40, 174, 3) and sheet.shape == (320, 140, 3)
        assert overview.shape[1] == 450
        assert [(p + 5) // 10 for p in selected[:6]] == [2, 1, 0, 8, 2, 7]
