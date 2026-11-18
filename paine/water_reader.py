"""One isolated water capture/decision/publication cycle for Supervisor."""

import json
from pathlib import Path
import time

import cv2
import numpy as np

from .capture_camera import open_camera_stream
from .mqtt_pressure import daily_water_config, publish_water
from .water_model import corroborate_fours, prepare_strip, recognize
from .water_examples import collect
from .water_state import decide
from .water_store import atomic, journal, load_state, save_sample
from .water_vision import WARMUP_SECONDS, registered_crop, select_digits


def observe(settings):
    from ai_edge_litert.interpreter import Interpreter
    here = Path(__file__).parent
    model = Interpreter(model_path=str(here / 'water.tflite'), num_threads=1)
    model.allocate_tensors()
    crops, indices, raw = [], [], []
    with np.load(here / 'water_landmarks.npz') as refs:
        landmarks = dict(refs)
    with open_camera_stream(settings.config, 1, settings.timeout) as camera:
        first = None
        target = WARMUP_SECONDS
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
                aligned_frame = frame
                indices.append(i+1)
                raw.append(crop)
                crops.append(prepare_strip(crop))
    overview = cv2.flip(aligned_frame if crops else frame, 1)[180:680, 650:1100]
    if not crops:
        # Last meter-area view is useful for diagnosing a bumped camera.
        return [], [], cv2.resize(overview, (225, 250)), {'frames': []}, [], overview
    audits = [{} for _ in crops]
    positions = [recognize(model, c, audit) for c, audit in zip(crops, audits)]
    corroborate_fours(model, raw, positions, audits)
    selected, winners = select_digits(crops, positions)
    composite_audit = {}
    composite = recognize(model, selected, composite_audit)
    corrections = {str(i): a for i, a in zip(indices, audits) if a}
    if composite_audit:
        corrections['selected'] = composite_audit
    details = {'frames': indices, 'sources': [indices[w] for w in winners]}
    if corrections:
        details['four_matches'] = corrections
    return positions, composite, np.vstack([selected, *crops]), details, raw, overview


def water_once(settings, root, log, safe_failure):
    stage = 'checkpoint'
    event = {'time': int(time.time()), 'reader': 'class100-0182/v5-offset-four', 'warmup_s': WARMUP_SECONDS}
    try:
        root.mkdir(parents=True, exist_ok=True)
        state = load_state(root)
        stage = 'capture/inference'
        rows, selected, image, details, raw, overview = observe(settings)
        now = int(time.time())
        new, result = decide(state, rows, selected, now, settings.interval, settings.water_flow_lpm)
        if len(rows) < 5 or (len(rows) < 6 and result['decision'] == 'weak_burst'):
            result['decision'] = 'insufficient_alignment'
        event.update(time=now, **details, positions=rows, selected=selected, **result)
        stage = 'save checkpoint'
        if new != state:
            atomic(root / 'state.json', json.dumps(new).encode())
        ok = result['decision'] in ('confirmed', 'baseline_corrected')
        category = ('steady' if state.get('last') == new.get('last') else 'change') if ok else 'reject'
        try:
            previews = [('overview.jpg', overview)]
            if rows:
                previews.extend([('register.png', raw[-1]), ('reading.png', image[:32])])
            else:
                for name in ('register.png', 'reading.png'):
                    (root/name).unlink(missing_ok=True)
            for name, preview in previews:
                encoded, buf = cv2.imencode(Path(name).suffix, preview)
                if encoded:
                    atomic(root/name, buf.tobytes())
        except (cv2.error, OSError):
            event['preview_error'] = 'write failed'
        try:
            event['sample'] = save_sample(root, now, category, image)
        except OSError:
            event['sample_error'] = 'write failed'
        try:
            example = collect(root, raw, event)
            if example:
                event['review_example'] = example
        except Exception as error:
            # Diagnostic storage failures must not suppress the meter reading.
            event['example_error'] = type(error).__name__
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
        daily = new['last'] - new['day_start'] if ok else None
        publish_water(daily_water_config(settings.config), daily, settings.timeout)
        return 0
    except Exception as error:
        event.update(error_stage=stage, error_type=type(error).__name__, error=safe_failure(error))
        return 1
    finally:
        try:
            journal(root, event, log)
        except OSError:
            log('[water] Could not persist diagnostic journal')
