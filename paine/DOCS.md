# Paine pressure gauge

> **Status: only calibrated for my garage.** This is an experimental app with
> gauge-specific scale and visual assumptions. Installing it does not make it work with other
> camera views or gauges. It is not a certified or safety-critical measurement.

Reads the black needle on the network camera gauge and publishes
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

The detector searches the entire frame for a near-circular light dial, estimates
its center/diameter, and saves a normalized 120×120 crop. It works with either
camera stream and allows the gauge to move within the frame. It rejects ambiguous
matches rather than choosing an arbitrary object. The calibration defaults to
the 2-bar tick at 356° and the 3-bar
tick at 61°, clockwise from 12 o'clock. Adjust `two_bar_angle` and
`three_bar_angle` if needed. The red limit indicator is ignored in color mode;
IR detection uses local contrast. Image-based readings remain approximate.
The needle search locates the colored hub or central IR highlight when possible,
falling back to a reference pivot if the hub is not resolvable. A weighted line fit follows
subpixel centers across the inner needle shaft. A small position offset is fitted
separately from rotation, reducing day/IR alignment bias without a night-mode
pressure correction. Fits with excessive shift, curvature/noise, or too little
contrast are rejected. Better repeatability does not establish absolute accuracy;
the tick positions still need calibration against the physical dial.

There are no user-configured center/diameter values. Remaining assumptions include
a single near-front-on light-faced gauge with a black needle, a 0–4 bar scale,
and roughly linear angle-to-pressure mapping. For the current camera, candidate
dial radii range from about 30 to 160 pixels at 1920 pixels image width (scaled
for other resolutions). The full dial and a small surrounding margin must be
visible. Strongly elliptical contours are rejected; mild ellipses are treated as
circles, **not perspective-corrected**. Circle detection cannot determine camera
roll or identify the numbered scale ticks, so retain `two_bar_angle` and
`three_bar_angle` and recalibrate after rotation or major viewing-angle changes.
The normalized dial uses radius 50 pixels; this is a coordinate convention, not
a fixed diameter in the camera image. The IR heuristic has only been exercised
on this garage's images and synthetic tests, not arbitrary gauges.

## Troubleshooting

- **App fails to start:** inspect Logs; fill camera/broker settings and ensure
  expiry is longer than the capture interval.
- **Capture/publish fails:** verify camera account, LAN connectivity, broker
  permissions, TLS settings, clear/full gauge visibility, and near-front-on view.
- **Unavailable entity:** check capture logs and schedule/expiry settings.
- **Missing entity:** inspect all Entities, including Ungrouped, or use Developer
  Tools → States. MQTT Explorer should show retained discovery and state.
- **Build fails:** this app supports 64-bit ARM (`aarch64`, including Green) and
  x86-64 (`amd64`), not 32-bit Raspberry Pi installations.
