# Changelog

## 1.0.7

- Reject legacy fixed crops when the last-known-good gauge location is elsewhere.
- Always require a verified gauge rim before measuring a fixed fallback; unrelated
  dark lines can no longer bypass the dial check after startup.
- Reject shot batches without a strict majority agreeing within a 0.15-bar range,
  including the observed `3.236, 0.679, 3.647` failure. Keep the median for valid
  batches with an isolated outlier; do not impose a fixed pressure ceiling.
- Restrict IR hub highlights to the central area so bright logo pixels above the
  hub cannot intermittently displace the needle pivot.

## 1.0.6

- Document visual, geometric, scale, IR, sampling, caching, and garage-fallback
  assumptions explicitly; this is not a generic rotary-gauge reader yet.
- Record Tapo C100 testing and include requested daytime/nighttime gauge crops
  in the repository README.
- Publish the median of configurable shot batches (`shot_count`, default 3),
  with individual readings and spread logged; `capture_interval` defaults to 60s.
- Retain last-good detected geometry in scheduler memory across capture workers;
  verify its small crop first and skip full-frame searches while it remains valid.
  Search again only when rim/needle verification fails. Cache resets on app restart.
- Do not replace cached geometry after incomplete or widely disagreeing batches.
- Fix intermittent IR rejection by evaluating coarse confidence on the visible
  inner shaft rather than the washed-out tip; retain existing confidence limits.
- Fall back to the original garage crop when automatic gauge localization fails.
- Log search counts, automatic/fixed crop selection, frame/crop dimensions, and
  detected center/diameter or fallback rectangle, without connection details.
- Save the fallback crop before measuring so failed measurements remain debuggable.
- The fallback still requires a confident needle measurement and the original
  camera view; it never publishes a fabricated pressure.

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
