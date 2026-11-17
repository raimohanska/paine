"""One isolated water capture/decision/publication cycle for Supervisor."""

import json
from pathlib import Path
import time

import cv2
import numpy as np

from .capture_camera import open_camera_stream
from .mqtt_pressure import publish_water
from .water_model import recognize
from .water_state import decide
from .water_store import atomic, journal, load_state, save_sample
from .water_vision import registered_crop, select_digits


def observe(settings):
    from ai_edge_litert.interpreter import Interpreter
    here = Path(__file__).parent
    model = Interpreter(model_path=str(here / 'water.tflite'), num_threads=1)
    model.allocate_tensors()
    crops, indices = [], []
    with np.load(here / 'water_landmarks.npz') as refs:
        landmarks = dict(refs)
    with open_camera_stream(settings.config, 1, settings.timeout) as camera:
        first = None
        target = 10.
        for i in range(9):
            while True:
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError('Timed out waiting for a camera frame')
                t = time.monotonic()
                if first is None:
                    first = t
                if t-first >= target:
                    break
            target = t-first+0.25
            crop = registered_crop(frame, landmarks)
            if crop is not None:
                indices.append(i+1)
                crops.append(crop)
    if not crops:
        # Last meter-area view is useful for diagnosing a bumped camera.
        overview = cv2.resize(cv2.flip(frame, 1)[180:680, 650:1100], (225, 250))
        return [], [], overview, {'frames': []}
    positions = [recognize(model, c) for c in crops]
    selected, winners = select_digits(crops, positions)
    return positions, recognize(model, selected), np.vstack([selected, *crops]), {
        'frames': indices, 'sources': [indices[w] for w in winners]}


def water_once(settings, root, log, safe_failure):
    stage = 'checkpoint'
    event = {'time': int(time.time()), 'reader': 'class100-0182/v2'}
    try:
        root.mkdir(parents=True, exist_ok=True)
        state = load_state(root)
        stage = 'capture/inference'
        rows, selected, image, details = observe(settings)
        now = int(time.time())
        new, result = decide(state, rows, selected, now, settings.interval, settings.water_flow_lpm)
        if len(rows) < 6:
            result['decision'] = 'insufficient_alignment'
        event.update(time=now, **details, positions=rows, selected=selected, **result)
        stage = 'save checkpoint'
        if new != state:
            atomic(root / 'state.json', json.dumps(new).encode())
        ok = result['decision'] in ('confirmed', 'baseline_corrected')
        category = ('steady' if state.get('last') == new.get('last') else 'change') if ok else 'reject'
        try:
            event['sample'] = save_sample(root, now, category, image)
        except OSError:
            event['sample_error'] = 'write failed'
        if ok:
            stage = 'mqtt'
            publish_water(settings.config, new['last'], settings.timeout)
            event['published_m3'] = f"{new['last']/100:.2f}"
        else:
            # Also replace older retained discovery (which specified expiry)
            # when upgrading during an extended run of rejected observations.
            stage = 'mqtt discovery'
            publish_water(settings.config, None, settings.timeout)
            event['mqtt'] = 'discovery_only'
        return 0
    except Exception as error:
        event.update(error_stage=stage, error_type=type(error).__name__, error=safe_failure(error))
        return 1
    finally:
        try:
            journal(root, event, log)
        except OSError:
            log('[water] Could not persist diagnostic journal')
