"""Small review set of real, unlabelled register crops sampled by appearance."""

import io
import json
from zipfile import ZipFile

import cv2
import numpy as np

from .water_store import atomic


def signature(crop):
    # Compare glyph shapes, not brightness, high-frequency noise or OCR text.
    from .water_model import prepare_strip
    strip = cv2.cvtColor(prepare_strip(crop), cv2.COLOR_BGR2GRAY)
    return np.stack([cv2.resize(tile, (6, 8), interpolation=cv2.INTER_AREA)
                     for tile in np.hsplit(strip, strip.shape[1]//20)])


def distance(a, b):
    # Don't let four unchanged integer wheels drown out one changing digit.
    common = min(len(a), len(b))  # six-wheel examples from previous versions
    return np.abs(a[:common].astype(float)-b[:common]).mean(axis=(1, 2)).max()


def collect(root, raw, event):
    if not raw:
        return None
    rows = event['positions']
    rolling = [i for i, row in enumerate(rows) if any(p % 10 in (4, 5, 6) for p in row)]
    if rolling:
        group, eligible = 'rolling', rolling
    else:
        group = 'accepted' if event['decision'] in ('confirmed','baseline_corrected') else 'disputed'
        eligible = range(len(raw))
    folder = root / 'examples'
    folder.mkdir(exist_ok=True)
    slots = [folder / f'{group}-{i:02}.zip' for i in range(16)]
    stored = {}
    for path in slots:
        if path.exists():
            with ZipFile(path) as z:
                meta = json.loads(z.read('review.json'))
                stored[path] = np.frombuffer(bytes.fromhex(meta['signature']), np.uint8).reshape(-1,8,6)
    def nearest(sig):
        return min((distance(sig, other) for other in stored.values()), default=256)
    # Consider all eligible real frames, but save at most one per cycle.
    candidates = [(i, signature(raw[i])) for i in eligible]
    i, sig = max(candidates, key=lambda pair: nearest(pair[1]))
    novelty = nearest(sig)
    if novelty < 14:
        return None
    path = next((p for p in slots if p not in stored), None)
    if path is None:
        # Only evict from the most redundant pair; preserve isolated examples.
        separation, path = min((distance(a,b), p) for p,a in stored.items()
                               for q,b in stored.items() if p < q)
        if novelty <= separation+2:
            return None
    ok, data = cv2.imencode('.png', raw[i])
    if not ok:
        return None
    meta = dict(time=event['time'], reader=event['reader'], frame=event['frames'][i],
                decimal_places=event.get('decimal_places',2),
                ignore_rightmost_wheels=event.get('ignore_rightmost_wheels',1),
                positions=rows[i], candidate_ticks=event['candidate_ticks'],
                previous_ticks=event['previous_ticks'], decision=event['decision'],
                votes=event['votes'], four_match=event.get('four_matches', {}).get(str(event['frames'][i])),
                verified_ticks=None, signature=sig.tobytes().hex())
    buf = io.BytesIO()
    with ZipFile(buf, 'w') as z:
        z.writestr('register.png', data.tobytes())
        z.writestr('review.json', json.dumps(meta))
    if buf.tell() > 22_000:
        return None
    atomic(path, buf.getvalue())
    return f'examples/{path.name}'
