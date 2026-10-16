# Paine pressure gauge

> **Garage-specific prototype:** currently only works in my garage, using fixed
> assumptions about the near-front-on camera view, dial scale, and needle.
> The gauge position/diameter are detected automatically; rotation and significant
> perspective changes still require calibration.
> Not a general-purpose gauge reader or a safety-critical pressure monitor.

Home Assistant app (formerly add-on) for capturing a Tapo camera pressure gauge
and publishing its estimated reading through MQTT discovery. Supports Home
Assistant Green (`aarch64`) and x86-64 (`amd64`).
Tested camera: **TP-Link Tapo C100**, main 1920×1080 RTSP/TCP stream, in both
daytime/color and infrared night modes. Real example crops are in the repository's
main README.

Configure camera/MQTT credentials in the app's Configuration tab. By default,
three-shot batches publish median pressure every 60 seconds; configure
`shot_count` and `capture_interval` to change these. Last-good geometry stays in
memory: cheaply verify its crop and skip searching while it remains valid.
If verification fails, search again; app profiles never fall back to the original
garage coordinates. Select **Open Web UI** to add camera cards with separate MQTT
entities and per-camera 2/3-bar calibration. Save and restart from the Info page.
Old flat settings migrate automatically into HA's saved configuration, preserving
IDs, calibration and secret references. See [Configuration](CONFIGURATION.md).
See **Documentation** for setup and calibration.
See [Assumptions and scope](ASSUMPTIONS.md) before using a different camera/gauge.

See [build/deploy instructions](BUILD.md) for installation from GitHub or locally.
The app image/Supervisor installation has not yet been tested on Green.
