"""One isolated water capture/decision/publication cycle for Supervisor."""

import json
from collections import Counter
from datetime import date
from pathlib import Path
import time

import cv2
import numpy as np

from .capture_camera import open_camera_stream
from .mqtt_pressure import daily_water_config, publish_water
from .water_model import corroborate_fours, prepare_strip, recognize
from .water_examples import collect
from .water_state import decide, convert_layout
from .water_store import atomic, journal, load_state, save_publication, save_sample
from .water_vision import WARMUP_SECONDS, select_digits
from .water_locator import locate, load_reference, load_hint, warp


def observe(settings, root):
    from ai_edge_litert.interpreter import Interpreter

    here = Path(__file__).parent
    model = Interpreter(model_path=str(here / "water.tflite"), num_threads=1)
    model.allocate_tensors()
    observations = []
    cv2.setNumThreads(2)  # the isolated worker also runs on small HA hosts
    reference = load_reference()
    mirror = settings.mirror_image
    cache = root / "register-location.json"
    hint = None
    with open_camera_stream(settings.config, 1, settings.timeout) as camera:
        first = None
        target = WARMUP_SECONDS
        for i in range(9):
            while True:
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("Timed out waiting for a camera frame")
                t = time.monotonic()
                if first is None:
                    first = t
                if t - first >= target:
                    break
            target = t - first + 0.25
            img = cv2.flip(frame, 1) if mirror else frame
            if i == 0:
                hint = load_hint(cache, img.shape, mirror)
            hit = locate(img, reference, hint)
            if hit:
                q, n, kind, search = hit
                hint = q
                observations.append(
                    (
                        i + 1,
                        q,
                        n,
                        kind,
                        search,
                        warp(img, q, 174 if kind == "reference" else n * 24),
                    )
                )
    # Never combine two banks or layouts into a single reading or cached hint.
    if observations:
        layout = Counter((o[2], o[3]) for o in observations).most_common(1)[0][0]
        observations = [o for o in observations if (o[2], o[3]) == layout]
        median = np.median([o[1] for o in observations], axis=0)
        height = np.linalg.norm(median[0] - median[3])
        observations = [
            o
            for o in observations
            if np.linalg.norm(o[1] - median, axis=1).max() < height * 0.22
        ]
    overview = cv2.resize(img, (450, max(1, round(450 * img.shape[0] / img.shape[1]))))
    if len(observations) < 5:
        return (
            [],
            [],
            cv2.resize(overview, (225, 250)),
            {"frames": [], "located": len(observations)},
            [],
            overview,
        )
    indices, quads, counts, kinds, searches, raw = zip(*observations)
    q = quads[-1]
    cv2.polylines(
        overview,
        [
            np.int32(
                q * [overview.shape[1] / img.shape[1], overview.shape[0] / img.shape[0]]
            )
        ],
        True,
        (0, 255, 0),
        2,
    )
    try:
        atomic(
            cache,
            json.dumps(
                dict(frame=list(img.shape[:2]), mirror=mirror, corners=q.tolist())
            ).encode(),
        )
    except OSError:
        pass  # a location hint is disposable
    crops = [prepare_strip(c) for c in raw]
    calibrated = kinds[0] == "reference"
    audits = [{} for _ in crops]
    positions = [
        recognize(model, c, audit, calibrated) for c, audit in zip(crops, audits)
    ]
    if calibrated:
        corroborate_fours(model, raw, positions, audits)
    selected, winners = select_digits(crops, positions)
    composite_audit = {}
    composite = recognize(model, selected, composite_audit, calibrated)
    corrections = {str(i): a for i, a in zip(indices, audits) if a}
    if composite_audit:
        corrections["selected"] = composite_audit
    details = dict(
        frames=indices,
        sources=[indices[w] for w in winners],
        wheel_count=counts[0],
        locator=kinds[0],
        search=searches,
    )
    if corrections:
        details["four_matches"] = corrections
    return positions, composite, np.vstack([selected, *crops]), details, raw, overview


def water_once(settings, root, log, safe_failure):
    stage = "checkpoint"
    event = {
        "time": int(time.time()),
        "reader": "class100-0182/v7-register",
        "warmup_s": WARMUP_SECONDS,
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        checkpoint = load_state(root)
        decimals = int(settings.config.get("WATER_DECIMALS", 3))
        omit = int(settings.config.get("WATER_OMIT", 0))
        state = convert_layout(checkpoint, decimals, omit)
        stage = "capture/inference"
        rows, selected, image, details, raw, overview = observe(settings, root)
        now = int(time.time())
        count = details.get("wheel_count", 7)
        if rows and "last" not in state and state.get("wheels", count) != count:
            state = {}  # a provisional layout must commission again from scratch
        mismatch = bool(rows and state.get("wheels", count) != count)
        vote_rows = [] if mismatch else [r[: count - omit] for r in rows]
        new, result = decide(
            state,
            vote_rows,
            selected[: count - omit],
            now,
            settings.interval,
            settings.water_flow_lpm,
            count - omit,
            decimals,
        )
        if state or new:
            new.update(decimals=decimals, omit=omit, wheels=state.get("wheels", count))
        event.update(decimal_places=decimals, ignore_rightmost_wheels=omit)
        if len(rows) < 5 or (len(rows) < 6 and result["decision"] == "weak_burst"):
            result["decision"] = "insufficient_alignment"
        if mismatch:
            result["decision"] = "wheel_count_changed"
        event.update(time=now, **details, positions=rows, selected=selected, **result)
        stage = "save checkpoint"
        if new != checkpoint:
            atomic(root / "state.json", json.dumps(new).encode())
        ok = result["decision"] in ("confirmed", "baseline_corrected")
        category = (
            ("steady" if state.get("last") == new.get("last") else "change")
            if ok
            else "reject"
        )
        try:
            previews = [("overview.jpg", overview)]
            if rows:
                previews.extend(
                    [("register.png", raw[-1]), ("reading.png", image[:32])]
                )
            else:
                for name in ("register.png", "reading.png"):
                    (root / name).unlink(missing_ok=True)
            for name, preview in previews:
                encoded, buf = cv2.imencode(Path(name).suffix, preview)
                if encoded:
                    atomic(root / name, buf.tobytes())
        except (cv2.error, OSError):
            event["preview_error"] = "write failed"
        try:
            event["sample"] = save_sample(root, now, category, image)
        except OSError:
            event["sample_error"] = "write failed"
        try:
            example = collect(root, raw, event)
            if example:
                event["review_example"] = example
        except Exception as error:
            # Diagnostic storage failures must not suppress the meter reading.
            event["example_error"] = type(error).__name__
        if ok:
            stage = "mqtt"
            publish_water(settings.config, new["last"], settings.timeout)
            event["published_m3"] = f"{new['last'] / 10**decimals:.{decimals}f}"
            try:
                save_publication(root, int(time.time()), event['published_m3'] + ' m³')
            except OSError:
                event['publication_record_error'] = 'write failed'
        else:
            # Also replace older retained discovery (which specified expiry)
            # when upgrading during an extended run of rejected observations.
            stage = "mqtt discovery"
            publish_water(settings.config, None, settings.timeout)
            event["mqtt"] = "discovery_only"
        # Discovery can move HA to a new state topic during a run of rejects.
        # Seed it from today's confirmed checkpoint, never from an OCR guess.
        daily = None
        if ("day_start" in new and new['at'] <= now
                and date.fromtimestamp(new['at']) == date.fromtimestamp(now)):
            daily = new['last'] - new['day_start']
        publish_water(daily_water_config(settings.config), daily, settings.timeout)
        if not ok and daily is not None:
            event['mqtt'] = 'daily_checkpoint'
        return 0
    except Exception as error:
        event.update(
            error_stage=stage,
            error_type=type(error).__name__,
            error=safe_failure(error),
        )
        return 1
    finally:
        try:
            journal(root, event, log)
        except OSError:
            log("[water] Could not persist diagnostic journal")
