# Camera/gauge profiles

## Normal HA Configuration form: one or two cameras

Use Paine's **Configuration** tab in Home Assistant. No separate Web UI or YAML
editing is required:

1. Fill in **Camera 1**'s address, username/password, sensor ID/name and calibration.
   Existing installations already have these settings; leave their identity fields
   unchanged to preserve the sensor/device in HA.
2. For the second gauge, fill in **Camera 2 — address**, username/password and
   sensor ID/name. **Leave its address empty to disable the second camera.**
3. Set Camera 2's **2-bar** and **3-bar tick angles** for that view independently.
   The displayed defaults are only starting values, not automatic calibration.
4. Both cameras use the shared MQTT broker/account, interval, timeout and shot count.
5. Save and restart Paine.

The second camera defaults to sensor ID `paine_pressure_2`, device ID
`paine_pressure_2_device`, and state topic `paine/paine_pressure_2/state`. Changing
its sensor ID also changes those generated defaults. Its ID must differ from the
first camera's. Optional `second_device_id`, `second_device_name`, and
`second_state_topic` override the generated identity/topic if needed.

The corresponding saved fields are `second_camera_host`, `second_camera_user`,
`second_camera_password`, `second_sensor_id`, `second_sensor_name`,
`second_two_bar_angle`, and `second_three_bar_angle`. Disabling Camera 2 leaves
its entered credentials and calibration saved for later use.

## Upgrading an existing installation

**Your old flat configuration continues to work without manual conversion.** The
app parses it as one camera profile, preserving the camera account, 2/3-bar
calibration, sensor/device IDs, names, and state topic. It does not rewrite your
HA settings or request extra Supervisor permissions.

To add a second camera, just fill in the **Camera 2** fields. There is no format
migration and no rewriting of stored settings.

Existing `gauges` list configurations also remain supported. Explicit list
profiles take precedence over form slots with the same sensor ID; do not mix
different views under the same ID. For advanced list-only setups, leave both
form camera addresses blank to avoid adding unwanted cameras.

## Advanced YAML example: two gauges

Only if you prefer list-based profiles, open Configuration in YAML mode. Replace the camera/broker
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
