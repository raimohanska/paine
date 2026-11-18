import io
import json
from zipfile import ZipFile

from paine.app_config import load_gauges
from paine.diagnostics_ui import make_zip, page
from test_two_camera_form import form_options


def test_viewer_and_export_scope(tmp_path):
    options = form_options(tmp_path, second_camera_host='', water_meter_camera_host='water',
                           water_meter_camera_user='camera', water_meter_camera_password='secret')
    gauges = load_gauges(options)
    pressure = tmp_path/'gauges'/gauges[0].storage_key
    pressure.mkdir(parents=True)
    (pressure/'snapshot_shot1.jpg').write_bytes(b'full')
    (pressure/'snapshot_shot1_gauge.jpg').write_bytes(b'crop')
    (pressure/'snapshot.jpg').symlink_to(options)
    water = tmp_path/'water-meter'
    water.mkdir()
    (water/'events.jsonl').write_text(json.dumps(dict(time=100, candidate_ticks=12345, decision='flow_limit'))+'\n')
    (water/'overview.jpg').write_bytes(b'overview')
    (water/'events.1').symlink_to(options)
    first = page(tmp_path, gauges, 0).decode()
    second = page(tmp_path, gauges, 1).decode()
    assert '?g=1' in first and '?g=2' not in first and 'Second pressure' not in first
    assert first.count('<img ') == 2 and 'Water meter total' in first
    assert '123.45 m³ · Not accepted (flow limit)' in second and second.count('<img ') == 1
    output = io.BytesIO()
    make_zip(output, tmp_path, gauges)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            'water-meter/events.jsonl', 'water-meter/overview.jpg',
            str((pressure/'snapshot_shot1.jpg').relative_to(tmp_path)),
            str((pressure/'snapshot_shot1_gauge.jpg').relative_to(tmp_path)),
        }
