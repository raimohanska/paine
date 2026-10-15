# Changelog

## 1.0.5

- Find the light-faced gauge across the full image instead of using a fixed crop.
- Estimate center and diameter from near-circular contours and the dial rim,
  normalize to 120×120, and locate the colored/IR needle hub when visible.
- Reject missing/ambiguous gauges rather than publish a guessed pressure.
- Verify automatic localization with translated/scaled synthetic day/night tests.
- Assumes a near-front-on view; camera roll and large perspective changes still
  require calibration. The detector remains specific to this garage's gauge.

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
