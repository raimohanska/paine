# Paine pressure gauge

> **Garage-specific prototype:** currently only works in my garage, using fixed
> assumptions about the camera view, gauge position, pivot, scale, and needle.
> Not a general-purpose gauge reader or a safety-critical pressure monitor.

Home Assistant app (formerly add-on) for capturing a Tapo camera pressure gauge
and publishing its estimated reading through MQTT discovery. Supports Home
Assistant Green (`aarch64`) and x86-64 (`amd64`).

Configure camera/MQTT credentials in the app's Configuration tab. Captures run
every 60 seconds by default. See **Documentation** for setup and calibration.

See [build/deploy instructions](BUILD.md) for installation from GitHub or locally.
The app image/Supervisor installation has not yet been tested on Green.
