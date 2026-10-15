# Paine pressure gauge

> **Status: only calibrated for my garage.** This is an experimental app with
> hardcoded camera/gauge geometry. Installing it does not make it work with other
> camera views or gauges. It is not a certified or safety-critical measurement.

Reads the black needle on the fixed-position network camera gauge and publishes
estimated pressure using MQTT discovery. Supports color and infrared night mode.

For building, installing, and updating the app, see [BUILD.md](BUILD.md).

## Configuration

Set these options in the app's **Configuration** tab, save, and start the app:

- `camera_host`: camera IP/hostname, optionally followed by `:port` (default 554).
  For port forwarding, use the reachable forwarding address and external port,
  for example `192.168.1.10:8554`. Do not include `rtsp://` or credentials.
- `camera_user`, `camera_password`: the camera account configured in the Tapo app.
- `mqtt_host`: `core-mosquitto` for Home Assistant's Mosquitto app, or your broker's hostname/IP.
- `mqtt_port`: usually 1883, or 8883 with TLS.
- `mqtt_user`, `mqtt_password`: a broker account allowed to publish discovery/state.
- `mqtt_tls`: enable only when the broker supports TLS with a trusted certificate.
- `capture_interval`: seconds between captures; default 60.
- `expire_after`: Home Assistant's reading expiry, default 300; must exceed the capture interval.
- `sensor_id`, `device_id`: keep the **same IDs as your existing sensor/device** to avoid duplicates.
- `sensor_name`, `device_name`: display names; spaces are supported.
- `suggested_area`: optional room/Area name for the initial device assignment.

The defaults for the sensor/device IDs are `paine_pressure` and
`paine_pressure_gauge`. If you previously changed your sensor ID (for example to
`paine_pressure1`), use that value here instead. Stop any old external capture
schedule so two instances do not publish competing readings.

No `.env` file is needed or read by the app. Configuration lives in Supervisor's
private `/data/options.json`; treat app backups as sensitive because they include
credentials. Password fields are masked in the UI, not encrypted by this app.

RTSP uses TCP, so only that forwarded TCP port is needed for capture (plus MQTT
access separately). Prefer private routing or a VPN over exposing RTSP publicly:
the RTSP connection is not encrypted, and port forwarding itself adds no encryption.

## Operation

Enable **Start on boot**. The app captures immediately and then periodically,
retrying on the next interval after camera or broker failures. Captures never
overlap. Shutdown terminates an in-flight worker. No privileged mode, host
networking, Docker access, or incoming ports are required.

The most recent photo and crop overwrite `/data/snapshot.jpg` and
`/data/snapshot_gauge.jpg` inside the app's private storage. Older images do not
accumulate. Native camera logs are suppressed to prevent accidental credential
exposure. Logs show camera/measurement and MQTT stages, pressure readings, and
sanitized failure hints. Each line is prefixed with a UTC timestamp, for example
`[2026-10-15 12:34:56 UTC] Capturing camera stream 1 and measuring gauge`.
Worker timestamps reflect event time, not when the scheduler forwards the logs.
For example, a camera failure means no MQTT publication
was attempted, while an MQTT failure follows a successfully measured pressure.

Home Assistant's MQTT integration must use the same broker and discovery prefix.
Discovery and pressure are retained, using QoS 1 with delivery acknowledgements.
Old retained pressure can look fresh after a restart; keep regular captures
running and do not treat these estimates as a safety alarm.

Find **Paine pressure gauge** under Devices and assign it to an Area. If an
existing entity was device-less before this app, restarting Home Assistant may
be needed to attach it to the newly advertised device.

## Calibration

The crop assumes the same 1920×1080 reference camera view, scaled automatically
for stream 2. The calibration defaults to the 2-bar tick at 356° and the 3-bar
tick at 61°, clockwise from 12 o'clock. Adjust `two_bar_angle` and
`three_bar_angle` if needed. The red limit indicator is ignored in color mode;
IR detection uses local contrast. Image-based readings remain approximate.

Fixed assumptions in `capture_camera.py` include the crop rectangle
`x=950:1070, y=725:845` in the reference frame, pivot `(58, 58)` in the 120×120
crop, radius 50 pixels, a 0–4 bar dial, and roughly linear angle-to-pressure
mapping. Moving/zooming the camera or using a different gauge requires changing
and validating these values. Stream-resolution scaling does not correct a
different field of view, perspective, or lens distortion. The IR heuristic has
only been exercised on this garage's images and may mistake other dark pointers
or markings for the needle.

## Troubleshooting

- **App fails to start:** inspect Logs; fill camera/broker settings and ensure
  expiry is longer than the capture interval.
- **Capture/publish fails:** verify camera account, LAN connectivity, broker
  permissions, TLS settings, and unchanged camera position.
- **Unavailable entity:** check capture logs and schedule/expiry settings.
- **Missing entity:** inspect all Entities, including Ungrouped, or use Developer
  Tools → States. MQTT Explorer should show retained discovery and state.
- **Build fails:** this app supports 64-bit ARM (`aarch64`, including Green) and
  x86-64 (`amd64`), not 32-bit Raspberry Pi installations.
