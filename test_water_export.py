import io
import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import cv2
import numpy as np
import pytest

from paine.water_examples import collect
from paine.water_export import handler


def test_review_sample_survives_export_without_secret_or_duplicate(tmp_path):
    raw = [np.random.default_rng(1).integers(0, 256, (40, 150, 3), dtype=np.uint8)]
    event = dict(time=100, positions=[[20,10,0,80,10,30]], frames=[7], reader='v4',
                 candidate_ticks=210813, previous_ticks=210836, votes={210813:7}, decision='decrease')
    sample = collect(tmp_path, raw, event)
    assert sample and collect(tmp_path, raw, event) is None
    (tmp_path/'options.json').write_text('private')
    (tmp_path/'events.3').symlink_to(tmp_path/'options.json')
    key = 'a'*32
    with HTTPServer(('127.0.0.1', 0), handler(tmp_path, key)) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/water/export.zip'
            with pytest.raises(HTTPError) as denied:
                urlopen(url, timeout=5)
            assert denied.value.code == 401
            with urlopen(Request(url, headers={'Authorization':'Bearer '+key}), timeout=5) as response:
                with ZipFile(io.BytesIO(response.read())) as archive:
                    assert archive.namelist() == [sample]
                    with ZipFile(io.BytesIO(archive.read(sample))) as example:
                        assert json.loads(example.read('review.json'))['verified_ticks'] is None
                        image = cv2.imdecode(np.frombuffer(example.read('register.png'), np.uint8), 1)
                        assert np.array_equal(image, raw[0])
        finally:
            server.shutdown()
            worker.join()
