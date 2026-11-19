"""Bounded water diagnostics: 4 MB JSONL, 72 hourly JPEG slots, tiny checkpoint."""

import json
import math
from pathlib import Path


def load_state(root):
    path = root / 'state.json'
    if not path.exists():
        return {}
    if path.stat().st_size > 2048:
        raise ValueError('water checkpoint')
    state = json.loads(path.read_bytes())
    alt_keys = {'alt', 'alt_at', 'alt_hits'}
    layout = {'decimals','omit','wheels'}
    if (not isinstance(state, dict) or set(state) - ({'last','at','pending','pending_at','count','day_start'} | alt_keys | layout)
            or (layout.intersection(state) and not layout <= state.keys())
            or ('day_start' in state and 'last' not in state)
            or (alt_keys.intersection(state) and not (alt_keys | {'last'}) <= state.keys())
            or ('last' in state) != ('at' in state)
            or not (('pending' in state) == ('pending_at' in state) == ('count' in state))):
        raise ValueError('water checkpoint')
    wheels, omit = state.get('wheels',6), state.get('omit',0)
    if type(wheels) is not int or wheels not in range(4,10) or type(omit) is not int or omit not in (0,1):
        raise ValueError('water checkpoint')
    bounds = {'count':3, 'alt_hits':5, 'decimals':4, 'omit':2, 'wheels':10}
    for k, v in state.items():
        if k in ('at', 'pending_at', 'alt_at'):
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError('water checkpoint')
        elif type(v) is not int or not 0 <= v < bounds.get(k,10**(wheels-omit)):
            raise ValueError('water checkpoint')
    return state


def atomic(path, data):
    # One writer (the scheduler isolates and serializes water workers). A kill
    # mid-write can leave at most one temp file across *all* slots/checkpoints.
    tmp = path.with_name('.water.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def save_publication(root, timestamp, value, previews):
    import base64
    # Replace value and images together so a refresh never pairs different bursts.
    images = {name: base64.b64encode(buf).decode('ascii') for name, buf in previews.items()}
    atomic(root/'published.json', json.dumps(dict(time=timestamp, value=value, images=images)).encode())


def journal(root, event, log):
    line = json.dumps(event, separators=(',', ':'), allow_nan=False)
    log('[water] ' + line)
    data = (line+'\n').encode()
    if len(data) > 8192:
        raise ValueError('oversize diagnostic event')
    path = root / 'events.jsonl'
    if path.exists() and path.stat().st_size+len(data) > 1_000_000:
        for old, new in [('events.2','events.3'), ('events.1','events.2'), ('events.jsonl','events.1')]:
            if (root/old).exists():
                (root/old).replace(root/new)
    with path.open('ab') as output:
        output.write(data)


def save_sample(root, timestamp, category, image):
    import cv2
    if category not in ('steady', 'change', 'reject'):
        raise ValueError('bad diagnostic category')
    # Latest representative for each UTC hour/category; skip new steady shots
    # within the same hour. Bounded even before replacement (incl. temp file).
    path = Path(root) / f'{int(timestamp//3600)%24:02}-{category}.jpg'
    model_strip = image.shape[1] in range(80,181,20) and image.shape[0] % 32 == 0
    current = path.with_suffix('.png') if model_strip else path
    if category == 'steady' and current.exists() and int(current.stat().st_mtime//3600) == int(timestamp//3600):
        return None
    # Model strips are 120px wide. Preserve their exact pixel values on disk.
    if model_strip:
        path.unlink(missing_ok=True)  # retire the previous JPEG for this slot
        path = path.with_suffix('.png')
        ok, data = cv2.imencode('.png', image)
        if ok and data.nbytes > 60_000:
            ok, data = cv2.imencode('.png', image[:32])  # selected model inputs first
    else:
        path.with_suffix('.png').unlink(missing_ok=True)
        ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok or data.nbytes > 60_000:
        return None
    atomic(path, data.tobytes())
    return path.name
