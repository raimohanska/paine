# Changelog

## 1.0.4

- Refine the needle direction using subpixel cross-section centers and a weighted
  line fit, allowing small camera-mode shifts without interpreting them as rotation.
- Exclude the hub and faint tip/scale from refinement and reject inconsistent fits.
- Add shifted, low-contrast day/night regression tests. This improves lighting
  agreement, not the accuracy of the approximate pressure tick calibration.

## 1.0.3

- Remove the redundant camera port setting; use `camera_host=hostname:port`.
- Explain host:port support directly on the Home Assistant configuration page.

## 1.0.2

- Prefix app log lines with UTC timestamps, preserving worker event times.
- Add a camera RTSP port option for forwarded connections (default 554).

## 1.0.1

- Log camera/measurement and MQTT publication stages separately.
- Report safe failure hints for RTSP access, needle detection, DNS, TLS, and MQTT.

## 1.0.0

- Initial Home Assistant app with isolated Python/OpenCV dependencies.
- Periodic color/IR pressure measurement and retained MQTT discovery/state.
- Device registration, Area suggestion, sanitized logs, and graceful shutdown.
