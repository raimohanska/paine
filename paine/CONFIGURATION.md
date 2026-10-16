# Camera/gauge profiles

## Visual editor (recommended)

Start Paine and select **Open Web UI** on its Info page. This opens an authenticated
Home Assistant ingress page, with one card per camera:

1. Enter the shared MQTT broker/account and capture defaults.
2. Select **Add camera**, then enter its address, camera account, unique sensor ID,
   and the 2/3-bar tick angles measured for that camera's view.
3. Expand **Advanced** to customize its device ID, state topic, interval, or broker.
   Leave overrides empty to inherit the shared settings.
4. Select **Save settings**, then **Restart** on Paine's Info page to apply them.

Passwords are masked. Existing `!secret` references stay as references when saved.
Reload the editor if someone changed the configuration elsewhere; stale saves are
rejected rather than overwriting newer settings. Removing a camera stops its
captures after restart; it does not delete retained MQTT entities automatically.

The standard HA **Configuration** form does not support editing lists of objects.
Use **Open Web UI** for visual camera management, not that form's legacy single-camera
fields. The editor is available even before any cameras are configured or when
capture configuration is invalid. It exposes no host port and accepts requests
only from Supervisor's ingress proxy. No manager/admin Supervisor API role is needed.

## Upgrading an existing installation

**Your old flat configuration continues to work without manual conversion.** The
app converts it to one camera profile, preserving the camera account, 2/3-bar
calibration, sensor/device IDs, names, and state topic. On startup it saves that
conversion through Supervisor's self-options API, updating HA's stored configuration
(including the YAML view). It never writes Supervisor's managed options file directly.
The migration reads raw stored options, not resolved runtime credentials, preserving
`!secret` references. It is idempotent: later starts do not duplicate cameras.

If persistence fails, a warning is logged and backward-compatible parsing keeps
the existing camera working. Migration is retried on the next start. An incomplete
old camera with no address is left untouched rather than discarding its credentials.

To add a second camera, you can keep the old fields and add the new camera to
`gauges`. The old camera is included automatically. If an explicit profile uses
the old `sensor_id`, that explicit entry takes precedence and is not duplicated.

For a clean new setup, put both cameras in `gauges` and omit the old flat camera
fields. Those old fields remain optional in the schema only for compatibility.

## Example: two gauges

Alternatively, open the app's Configuration page in YAML mode. Replace the camera/broker
placeholders and **calibrate the angles separately for each view**:

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_user: mqtt-user
mqtt_password: mqtt-password
mqtt_tls: false
capture_interval: 60
capture_timeout: 10
shot_count: 3
gauges:
  - sensor_id: paine_pressure
    sensor_name: Heating pressure
    device_id: paine_pressure_gauge
    device_name: Garage pressure gauge
    camera_host: "192.168.1.20:554"
    camera_user: camera-user
    camera_password: camera-password
    two_bar_angle: 356
    three_bar_angle: 61
  - sensor_id: boiler_pressure
    sensor_name: Boiler pressure
    device_id: boiler_pressure_gauge
    device_name: Boiler pressure gauge
    camera_host: "192.168.1.21:8554"
    camera_user: other-camera-user
    camera_password: other-camera-password
    two_bar_angle: 350
    three_bar_angle: 55
    capture_interval: 90
```

When moving an existing camera into the list, use its **actual current IDs and
state topic**, not blindly the example values. For example, retain
`sensor_id: paine_pressure1` and `state_topic: paine/pressure/state` if that is your
existing setup. Save and restart the app after changing profiles.

## Shared defaults and per-camera settings

- Each list entry requires `camera_host`, `camera_user`, `camera_password`,
  `sensor_id`, `two_bar_angle`, and `three_bar_angle`. `camera_host` supports
  `host:port`; the default RTSP port is 554.
- `capture_interval`, `capture_timeout`, and `shot_count` inherit the top-level
  defaults unless overridden in an entry.
- `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password`, and `mqtt_tls` can also
  be overridden per entry. HA must be connected to the broker carrying that
  sensor's discovery/state in order to receive it.
- `sensor_name`, `device_id`, `device_name`, and `state_topic` are optional.
  Without overrides, names derive from `sensor_id`, the device ID is
  `<sensor_id>_device`, and the state topic is `paine/<sensor_id>/state`.
- Duplicate sensor/device IDs and state topics on the same broker are rejected.
  Use letters, digits, underscores, or hyphens for IDs.
- The camera-specific calibration fields never inherit from a different camera.

The app always uses **stream 1** and discovery prefix **`homeassistant`**. Expiry
is an internal policy: **max(300 seconds, 3 × reading interval)** per gauge.
Assign rooms/Areas on the device page in HA. The old app settings `stream`,
`suggested_area`, `discovery_prefix`, and `expire_after` are no longer used.
The standalone CLI's existing flags and `.env` options are separate.

## Scheduling, caching, and files

Camera workers run **sequentially** to conserve CPU on Green. Each gauge has its
own target interval; a slow or unreachable camera can delay others. Failed
workers do not stop scheduling the other cameras. Choose intervals that allow
all your cameras to finish their shot batches.

Each sensor has its own in-memory crop geometry and private snapshots under
`/data/gauges/<sensor_id>/`. Caches are never shared across cameras. Logs include
the sensor ID. Updating from the old version leaves old root-level snapshots in
place, but new shots go into the per-gauge directories.

Profiles use full-frame detection and verified cached crops. They do **not** use
the original garage's hardcoded crop. That recovery path remains only in the
standalone CLI, where it still requires rim/location checks.

This feature means **one target gauge per camera view**, not selecting different
dials in the same frame. All gauges still have to satisfy the documented
light-face, single-dark-needle, near-front-on, linear 0–4 bar assumptions. Water
meter digit recognition is not implemented by this pressure-gauge profile system.
