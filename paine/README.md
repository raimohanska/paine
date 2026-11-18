# Paine pressure gauge

> **Garage-specific prototype:** currently only works in my garage, using fixed
> assumptions about the near-front-on camera view, dial scale, and needle.
> The gauge position/diameter are detected automatically; rotation and significant
> perspective changes still require calibration.
> Not a general-purpose gauge reader or a safety-critical pressure monitor.

Home Assistant app (formerly add-on) for reading a pressure gauge and an optional
rolling-wheel water meter from camera images. It publishes readings to MQTT
with Home Assistant discovery. Supports Home
Assistant Green (`aarch64`) and x86-64 (`amd64`).
Tested camera: **TP-Link Tapo C100**, main 1920×1080 RTSP/TCP stream, in both
daytime/color and infrared night modes. Real example crops are in the repository's
main README.

Configure camera/MQTT credentials in the app's Configuration tab. By default,
three-shot pressure batches publish median pressure every 60 seconds; configure
`shot_count` and `capture_interval` to change these. Last-good geometry stays in
memory: cheaply verify its crop and skip searching while it remains valid.
If verification fails, search again; app profiles never fall back to the original
garage coordinates. Use the normal HA **Configuration** form for Camera 1 and
optional Camera 2, with separate credentials, MQTT identities and calibration.
Leave Camera 2's address empty to disable it. No separate Web UI or YAML editing
is required. Old settings keep working; see [Configuration](CONFIGURATION.md).
See **Documentation** for setup and calibration.

To enable water readings, set **Water meter — address**, username and password,
then save and restart. The reader requires the calibrated meter face, mirror
placement, night light and 1080p stream. Verify **Water meter total** (m³)
against the physical register when the first confirmed value appears. **Daily
water consumption** (m³) counts from the first confirmed reading of the local
day. See the water meter section of `paine/DOCS.md` for details.

See [Assumptions and scope](ASSUMPTIONS.md) before using a different camera/gauge.

See [build/deploy instructions](BUILD.md) for installation from GitHub or locally.
The app image/Supervisor installation has not yet been tested on Green.
