# Paine pressure gauge

> **Status: only calibrated for my garage.** This is an experimental app with
> gauge-specific scale and visual assumptions. Installing it does not make it work with other
> camera views or gauges. It is not a certified or safety-critical measurement.

Reads the black needle on the network camera gauge and publishes
estimated pressure using MQTT discovery. Supports color and infrared night mode.
The tested camera is a **TP-Link Tapo C100**, main 1920×1080 RTSP/TCP stream.
Testing covers this garage's daytime and infrared night-mode images, not arbitrary
camera/gauge combinations. The repository README includes one crop of each mode.

For building, installing, and updating the app, see [BUILD.md](BUILD.md).
For the full support boundary, see [Assumptions and scope](ASSUMPTIONS.md).

### Core assumptions

- Light/white circular face, a single straight dark needle, and a near-front-on
  view with adequate image detail; no perspective or lens-distortion correction.
- Clockwise, approximately linear **0–4 bar** scale. The 2- and 3-bar anchors are
  configured manually; 0/1-bar positions and units are not read from the image.
- Red is a limit indicator in color mode. IR behavior depends on how the actual
  needle and indicator reflect infrared; color alone cannot separate them there.
- Position/diameter may change, but camera roll/gauge rotation requires angle
  recalibration. Ambiguous needles/reflections/other circles can confuse heuristics.
- Cached crops are re-verified. The garage-specific fixed crop is disabled in
  app profiles; it remains only as guarded recovery in the standalone CLI.
- The median helps with isolated errors, not consistently wrong readings or
  calibration bias. This remains a prototype, not a safety-critical instrument.

## Configuration

The app supports two or more camera/gauge profiles. Your existing flat settings
are parsed automatically after upgrading; no manual conversion or rewriting of
HA settings is required. See [Camera/gauge profiles](CONFIGURATION.md) for a
the two-camera form instructions, shared defaults, and advanced YAML options.

Use the app's normal **Configuration** form. **Camera 1** uses the existing fields;
fill in **Camera 2**'s address, account, sensor ID/name and separate calibration to
enable the second camera. Leave its address empty for one camera. Both use the
shared broker and capture timing. No separate Web UI or YAML editing is needed.
Save and restart after changes. The important settings are:

- `camera_host`: camera IP/hostname, optionally followed by `:port` (default 554).
  For port forwarding, use the reachable forwarding address and external port,
  for example `192.168.1.10:8554`. Do not include `rtsp://` or credentials.
- `camera_user`, `camera_password`: the camera account configured in the Tapo app.
- `mqtt_host`: `core-mosquitto` for Home Assistant's Mosquitto app, or your broker's hostname/IP.
- `mqtt_port`: usually 1883, or 8883 with TLS.
- `mqtt_user`, `mqtt_password`: a broker account allowed to publish discovery/state.
- `mqtt_tls`: enable only when the broker supports TLS with a trusted certificate.
- `capture_interval`: seconds between reading batches; default 60. Slow batches
  can extend the effective interval; they never overlap.
- `shot_count`: captures per reading, 1–9, default **3**. Publish the **median**,
  not the arithmetic mean. Odd counts are recommended; for an even count, the
  standard median averages the two middle values. Every capture must succeed.
- `sensor_id`, `device_id`: keep the **same IDs as your existing sensor/device** to avoid duplicates.
- `sensor_name`, `device_name`: display names; spaces are supported.
- `two_bar_angle`, `three_bar_angle`: required, camera-specific tick angles.
- `second_camera_host`, `second_camera_user`, `second_camera_password`: optional
  second camera; a blank address disables it.
- `second_sensor_id`, `second_sensor_name`: second camera's independent sensor.
- `second_two_bar_angle`, `second_three_bar_angle`: calibrate for Camera 2's view.

Camera credentials, identities, and calibration are per profile; broker and timing
settings can be shared or overridden. Keep existing sensor/device IDs and topics
when explicitly moving old settings into the list. Otherwise the old camera is
included automatically when a second camera is added. Stop external capture jobs
so two instances do not publish competing readings.

Stream **1** and discovery prefix **`homeassistant`** are constants. Expiry is
automatic per sensor: **max(300 seconds, 3 × capture_interval)**. Assign Areas in
the HA device UI. The old app fields `stream`, `suggested_area`, `discovery_prefix`,
and `expire_after` are ignored and no longer shown as configurable app settings.

No `.env` file is needed or read by the app. Configuration lives in Supervisor's
private `/data/options.json`; treat app backups as sensitive because they include
credentials. Password fields are masked in the UI, not encrypted by this app.

RTSP uses TCP, so only that forwarded TCP port is needed for capture (plus MQTT
access separately). Prefer private routing or a VPN over exposing RTSP publicly:
the RTSP connection is not encrypted, and port forwarding itself adds no encryption.

## Operation

Enable **Start on boot**. The app schedules each gauge immediately and then at
its own interval, retrying after failures. Workers run sequentially on Green;
slow cameras can delay others, but a failed camera does not stop the scheduler.
Captures never overlap. Shutdown terminates an in-flight worker. No privileged mode, host
networking, Docker access, or incoming ports are required.

Within each batch the app opens independent camera captures one second apart,
logs every pressure and the spread, then publishes just the median. For example,
`0.65, 0.03, 0.66` publishes `0.65`. A strict majority must agree within a
0.15-bar-wide range: `3.236, 0.679, 3.647` is rejected without publishing.
A median of three tolerates one outlier, not two consistently wrong readings.
A spread above 0.15 bar generates a warning; the median is published **only if
that majority exists**, and tentative geometry is not saved as last-known-good.
This is not a hard pressure ceiling or a comparison with historical pressure.
Any failed capture/measurement aborts the batch without
publishing a partial median.

Each gauge writes to its own `/data/gauges/<sensor_id>/` directory. The final
shot's photo/crop overwrite `snapshot.jpg` and `snapshot_gauge.jpg` there;
preceding shots use `snapshot_shot1.jpg`,
`snapshot_shot1_gauge.jpg`, etc., in private storage. These bounded filenames are
overwritten each cycle rather than accumulating history. Native camera logs are
suppressed to prevent accidental credential
exposure. Logs show camera/measurement and MQTT stages, pressure readings, and
sanitized failure hints. Each line is prefixed with a local timestamp including
timezone/offset, for example `[2026-10-16 15:34:56 EEST+0300] Capturing shot 1/3`.
The app validates the IANA timezone supplied by Supervisor via `TZ`, logs its name
at startup, and handles daylight-saving transitions. Invalid timezone settings
fail startup rather than silently displaying the wrong local time. Outside Home
Assistant, configure the container's `TZ` yourself; without
a local timezone setting the container may still default to UTC.
Worker timestamps reflect event time, not when the scheduler forwards the logs.
Each captured shot also logs `mode=day/color-like` or `mode=night/IR-like`, inferred
from image color. This does not query the camera's actual day/night setting;
colorless daytime scenes or tinted IR images can be misclassified. It is purely
diagnostic and does not change the needle algorithm.
Gauge diagnostics include circular/rim/validated match counts, frame dimensions,
and automatic/cached center/diameter. Logs include the gauge's sensor ID. After a
successful, consistent batch, its last detected center/diameter is kept in
**scheduler memory**, including between subprocess captures. No `.env` value or
calibration file is written; memory resets on app restart. Compatible resolution
changes scale the cache; changed aspect ratios invalidate it.

On every shot, first verify that **last-known-good crop** with a lightweight
rim/brightness check and a confident needle measurement on just 120×120 pixels.
If it passes, **skip the full-frame search**. If it fails or no cache exists,
run automatic localization again. If that fails too, the app rejects the shot:
profiles never read the original garage's hardcoded crop. Full frames and any
available cached crop are saved for diagnosis. The standalone CLI retains the
guarded legacy fallback described in [Assumptions](ASSUMPTIONS.md).
The log explicitly says
`full search skipped` or why cached verification failed, helping diagnose CPU
usage as well as crop errors. For a failure on an earlier
shot, inspect that shot's `_shot1`/`_shot2` files instead of the final-shot files.
For example, a camera failure means no MQTT publication
was attempted, while an MQTT failure follows a successfully measured pressure.

Home Assistant's MQTT integration must use the same broker and the `homeassistant` discovery prefix.
Discovery and pressure are retained, using QoS 1 with delivery acknowledgements.
Old retained pressure can look fresh after a restart; keep regular captures
running and do not treat these estimates as a safety alarm.

Find **Paine pressure gauge** under Devices and assign it to an Area. If an
existing entity was device-less before this app, restarting Home Assistant may
be needed to attach it to the newly advertised device.

## Calibration

The detector searches the entire frame for a near-circular light dial, estimates
its center/diameter, and saves a normalized 120×120 crop. Stream 1 is strongly
recommended for dial detail; reduced streams can make IR needle measurements
unreliable. Automatic detection allows the gauge to move within the frame. It rejects ambiguous
matches rather than choosing an arbitrary object, then uses the logged fallback
order described above if available. The calibration defaults to
the 2-bar tick at 356° and the 3-bar
tick at 61°, clockwise from 12 o'clock. Adjust `two_bar_angle` and
`three_bar_angle` if needed. The red limit indicator is ignored in color mode;
IR detection uses local contrast. Image-based readings remain approximate.
The needle search locates the colored hub or central IR highlight when possible.
IR highlights are restricted to a smaller center area to exclude the logo above
the hub. If no qualifying highlight is visible, it uses the reference pivot,
then a weighted line fit follows
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
# Water meter (optional)

Set **Water meter — address**, username and password in Configuration, save
and restart. Keep the wheel bank upright, in focus, and evenly lit. The add-on downloads a checksum-pinned
AI-on-the-edge-device `dig-class100-0182-s2_q` during build; captures and
inference stay local. The reader searches for the digit row, not the meter face.
For the tested meter it matches numeric landmarks from the wheel bank. For other
meters it looks for a four-sided window enclosing 4–9 regularly divided wheels.
It estimates the corners anew to straighten the register. A verified burst saves
a location hint; if the local search fails it searches the full frame in that cycle.
Other meter brands, partially hidden wheels and steep camera angles have not been field validated. The default
interval is 60 seconds. Set maximum plausible flow in L/min for your supply
(default 40). Pressure and water share broker settings and run sequentially.

**Upgrading:** Back up the app including its data and note the physical meter
and current HA total. Update in place and leave the new settings at their defaults
for the existing mirrored installation. Do not delete the checkpoint or MQTT entities.
The reader builds its location cache on its own. Open Web UI → water after a
cycle: check the outline, the complete strip, wheel count and accepted total
against the physical register. If the strip is reversed, toggle **mirror image**.
If it is clipped or unreadable, improve framing or visible lighting before changing decimals.

| Camera/register setup | Mirror image | Rightmost wheels excluded | Decimal places |
|---|---|---:|---:|
| Existing installation, report hundredths | on | 1 | 2 |
| Same meter, report litres (default) | on | 0 | 3 |
| Direct view, two fractional wheels, all reported | off | 0 | 2 |

All wheels are read. For `2108276` the defaults publish `2108.276 m³`.
If an upgrade retains the old settings (2 decimal places, 1 wheel excluded),
set them to 3 and 0 respectively for litre reporting. Decimal places apply
after any wheel exclusion. Verify the final wheel across several positions.
Changing precision on the same scale converts the saved
total and daily baseline and discards unfinished votes; HA entity IDs stay
the same. Reducing precision truncates. A scale change that would reinterpret
history is refused; restore the previous settings. A different detected count
after commissioning is rejected (`wheel_count_changed`). For rollback restore
the pre-update backup, since older versions cannot read the new checkpoint.

The worker drains 20 seconds, then captures nine frames spaced at least 250 ms
apart. It reads all aligned wheels in individual frames and the best-per-digit composite.
Each model position is
stored as 0–99 (e.g. 79 means wheel position 7.9). Mid-transition positions
abstain. Once a baseline exists, five of the nine attempted shots agreeing on
the whole total suffice if it is nondecreasing and within the elapsed-time flow
limit. A changed total still requires a second qualifying burst. Otherwise,
at least six original frames must agree on the *whole* total; unless
eight agree, the composite must also support it. This is repeatability, not a
probability: a consistently misread wheel is still possible. Verify the first
published value against the meter. Three concordant bursts establish a
baseline; two confirm a changed total; one confirms an unchanged total. During
flow the second total may be higher if at least seven frames and the composite
agree and the increase since the pending burst also fits the configured limit.
Decreases and increases exceeding elapsed time × max flow (rounded up to a
configured tick) are initially rejected. To recover from a bad baseline, five
successive conflicting bursts must each have at least eight original frames
and the composite agreeing. The alternative readings must themselves be
nondecreasing and within the flow bound between bursts. A weak or compatible
burst cancels the challenge. A successful `baseline_corrected` event logs both
totals and publishes the corrected physical reading. Check HA long-term
statistics after a correction: a downward jump can be interpreted as a meter
reset and an upward jump as consumption. A continuously moving meter may delay confirmation
until it settles. No prior reading is reissued to hide a rejected cycle.

Enable MQTT discovery in HA. Look for **Water meter total** during commissioning;
it has no new numeric state until the first confirmed reading
(unique ID `paine_water_total`; retained QoS 1 topic `paine/water_meter/state`,
`m³`, `water`, `total_increasing`). The last confirmed total does not expire;
check `age_s` in the logs for its age when a burst is rejected. The reader
cannot distinguish a bad baseline from a physical meter reset.

**Daily water consumption** (`paine_water_daily`, `paine/water_meter/daily/state`)
reports m³ since the first confirmed reading of the local day. That reading
starts the sensor at zero, including when upgrading partway through the day.
The starting amount survives restarts. If the meter total is corrected below
that amount, the corrected reading becomes the new starting amount.

**After a day:** filter the app Logs for `[water]`. Each cycle reports UTC epoch,
aligned frame IDs, model wheel positions, the source frame for each selected
wheel, votes, candidate and previous ticks (10^decimal_places ticks = 1 m³), elapsed seconds,
allowed increase, decision, confirmations, and MQTT result. Look first for
`weak_burst`, `insufficient_alignment`, `decrease` and `flow_limit`. Compare the
rejected wheel positions with earlier confirmed bursts. Capture or broker errors
include a stage and a sanitized hint. Rate is a rejection bound, not a flow sensor.

The same records are in the app's persistent `/data/water-meter/events.jsonl`
(older: `events.1` through `.3`, newest first). Secondary evidence is an hourly
sample per category: confirmed steady, confirmed change and rejected, reused
on a 24-slot ring. Sheets show the selected crop first, then aligned shots in
logged frame order. Each row packs the exact 20×32 model inputs into a
lossless PNG. Oversize sheets keep just the selected row. If alignment entirely
fails the sample shows the meter area as a JPEG instead.
Use an app-data backup to inspect these files off-device. Combined water-owned
debug files and state stay below 10 MB: 4 × 1,000,000-byte journals, 72 ×
60,000-byte image slots, 48 × 22,000-byte review slots, temporary replacements,
and a tiny checkpoint.

**Training examples:** In the app-data backup, open `water-meter/examples/`.
There are separate pools for accepted bursts, disputed bursts, and apparent
wheel transitions. At most one real aligned register per burst is saved. Shape
similarity suppresses duplicates; when full, a pool replaces redundant views
only for a more distinct one. An example can outlive the hourly debug images.
Each ZIP has `register.png` (all wheels from one frame, lossless, before
digit cropping/normalization) and `review.json` (timestamp, source frame,
model predictions and decision). Even accepted values are **not** training
labels. Export examples and label copies off-device from verified readings;
keep uncertain wheels unlabelled and split evaluation by capture time/burst.
Slot names are reused, so use the timestamp when merging exports. No extra
water use or full-frame camera archive is needed.

**Download without a backup:** The optional HTTPS endpoint needs a certificate
and its matching key in HA's SSL directory (`fullchain.pem`, `privkey.pem`).
Use the hostname on the certificate. Generate a secret with `openssl rand -hex 32`,
paste it into **Water diagnostics — HTTPS export key**, and map **8099/tcp** to
a free host port in the app's Network settings. Save and restart. From a trusted
computer (example host/port), enter the same key at the prompt:

```bash
read -rs -p 'Export key: ' KEY; echo
printf 'Authorization: Bearer %s\n' "$KEY" | curl --fail --show-error \
  --header @- https://ha.example.org:8099/water/export.zip -o paine-water.zip
unset KEY
```

For a private CA, add `--cacert your-ca.pem`. The ZIP contains water diagnostics
only, including the review examples, and is assembled in memory. Leave the key
blank and port unmapped to disable. Change the key and restart to rotate it.
Supervisor controls its own stdout log retention. `/data/water-meter/state.json`
survives restarts: only remove it deliberately with the app stopped to commission
a replaced meter. Review HA long-term statistics separately after a physical reset.

## Diagnostics viewer

Choose **Open Web UI** on the app page and select a meter. Pressure tabs show the latest shot and dial crop; the pressure summary is the batch median. The water tab shows the mirror-corrected meter area, aligned register and selected digit strip from the burst. Its candidate reading is only accepted when the decision is confirmed or baseline corrected. Use **Refresh** to see the latest attempt.

**Download all enabled meter diagnostics** saves one ZIP of the stored pressure shots, water previews, samples, review examples, state and journals. The archive excludes app options and credentials. No port mapping or export key is needed for the Home Assistant UI.
