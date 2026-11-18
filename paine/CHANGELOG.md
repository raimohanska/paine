# Changelog

## 1.4.3

- Publish a small, plausible water increase on the first burst when at least six frames agree, none vote otherwise, and the combined reading matches.

## 1.4.2

- Include the water register reference in the Docker build context so the add-on builds successfully.

## 1.4.1

- Default to counting every wheel and publishing water totals at one-litre resolution (0.001 m³). Explicit layout settings are preserved.

## 1.4.0

- Locate the wheel register in the live frame, rectify its perspective, and
  recheck a saved location before doing a full search. The water overview shows the detected row.
- Configure mirroring, decimal places and whether the last wheel contributes
  to the total. Defaults preserve existing cumulative and daily readings.
- Detect 4–9 wheels on upright, divided mechanical registers. This is experimental on other meter models.

## 1.3.2

- Read the seventh (litre) wheel in register previews, OCR events and review examples.
  Published water totals and checkpoints retain their 0.01 m³ resolution.

## 1.3.1

- Recheck ambiguous tenths-wheel ones at two crop offsets when an independent
  frame reads a centered four. Record attempted template scores and offset phases.

## 1.3.0

- Add a read-only Home Assistant diagnostics UI with per-meter capture previews, reading status and a ZIP download.

## 1.2.9

- Optional key-authenticated HTTPS export of water data and a calibrated template
  for the tenths wheel's verified 4/1 confusion; corrections logged per source frame.

- Collect a small, visually diverse set of lossless water-register crops for
  manual review and training during normal operation, within the 10 MB budget.

## 1.2.8

- Publish daily water consumption using a persisted local-day baseline.

## 1.2.7

- Log one timestamped summary per HA pressure reading instead of per-shot camera diagnostics.

## 1.2.6

- Confirm an advancing meter during sustained flow when the second burst has
  strong support and both increases are within the configured flow limit.

## 1.2.5

- Exclude two columns of separator interference from the tenths wheel; keep
  the hundredths wheel width calibrated for its narrower numeral strokes.

## 1.2.4

- Count a five-frame majority toward confirmation when it fits the confirmed
  water total and elapsed-time flow limit, even if the composite disagrees.

## 1.2.3

- Increase stream warm-up from 10 to 20 seconds before the nine water shots.

- Crop and normalize wheel tiles immediately after alignment. Inference, crop
  selection, capture previews and lossless diagnostic PNGs use the same pixels.

## 1.2.2

- Tighten the hundredths-wheel crop and prefer visually similar, majority-supported
  wheel images when choosing the sharpest composite for water inference.

## 1.2.1

- Keep the last confirmed cumulative water total visible during uncertain readings.
  Refresh MQTT discovery on rejected bursts without publishing a new measurement.

## 1.2.0

- Optional night-light water meter: local rolling-wheel inference, persistent
  confirmation filter, cumulative MQTT water sensor and bounded diagnostics.

## 1.1.3

- Add standalone CLI selection of a second camera using `SECOND_CAMERA_*`
  settings, with independent `SECOND_MQTT_*` settings and no garage fixed fallback.
- Add `--capture-only` for saving a test frame without gauge detection or MQTT,
  and `--no-mqtt` for measurement-only trials.
- Document second-camera testing and the current oblique-view limitations.

## 1.1.2

- Use HA's normal Configuration form for Camera 1 and an optional Camera 2,
  with separate accounts, sensor identities and 2/3-bar calibration.
- Leave Camera 2's address empty to disable it. Both cameras share broker/timing
  defaults while retaining separate storage, crop caches and sequential workers.
- Remove the separate Web UI, ingress server and automatic settings-write path.
  Existing flat settings keep working without conversion; advanced `gauges`
  list configurations remain supported.

## 1.1.0

- Read multiple camera/gauge profiles with independent calibration, credentials,
  MQTT identities/topics, intervals, snapshots, and in-memory crop caches.
- Preserve old flat configurations through backward-compatible parsing, without
  rewriting settings. A second profile can be added without losing the old camera.
- Share broker/timing defaults or override per camera; run workers sequentially
  so one unavailable camera does not stop the rest. Reject duplicate identities.
- Remove app settings for stream, suggested Area, discovery prefix, and expiry.
  Use stream 1, `homeassistant`, HA Area assignment, and automatic expiry.
- Require explicit 2/3-bar calibration per new camera. Do not use the original
  garage fixed crop in app profiles; retain guarded legacy recovery in the CLI.

## 1.0.8

- Log each captured frame as day/color-like or night/IR-like, explicitly inferred
  from image color rather than claiming to read the camera's actual mode.
- Use local log timestamps with timezone and UTC offset, honoring Supervisor's
  `TZ` environment; include timezone data for daylight-saving transitions.
- Validate and announce the selected timezone at startup; reject invalid IANA
  zones and warn if no `TZ` was supplied instead of silently claiming local time.

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
