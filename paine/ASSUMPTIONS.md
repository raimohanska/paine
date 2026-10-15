# Assumptions and scope

**Paine is a reusable prototype, not yet a generic rotary-gauge reader.** It has
been exercised on one garage's pressure gauge and Tapo camera, plus synthetic
tests. The ability to locate a circle is not proof that its scale, needle, or
physical value has been interpreted correctly.

## Gauge appearance and geometry

| Assumption | What it means |
| --- | --- |
| Light/white face, dark needle | Detection uses a bright dial boundary and local dark-stripe contrast. Dark-faced gauges with light needles are not supported. Absolute brightness/contrast thresholds are tuned to the garage images. |
| Circular, near-front-on dial | Near-circular contours estimate center/diameter. Mild ellipse distortion is tolerated but not corrected; large tilt is rejected. There is no projective or lens-distortion calibration. |
| Single measurable needle | The shaft must be straight, sufficiently long, and visible outside the hub. Extra needles, logos, hands, shadows, screws, reflections, and scale markings can create false matches. |
| Red pointer is a limit marker | Red pixels are excluded in color images. In IR, color is lost: the tested red marker reflects differently from the black needle, but that is not guaranteed for other materials/cameras. |
| Hub close to dial center | Look for a central colored hub or IR highlight. If unresolved, fall back to a near-center pivot in normalized coordinates; a line fit tolerates only small offsets. |
| One dominant gauge | Full-frame detection needs a convincing light circular face, rim, and needle. Similarly strong matches at distinct positions are rejected. It does not understand an object's identity or purpose. |
| Adequate size and visibility | At 1920 pixels image width, candidate dial radii are approximately 30–160 pixels, scaled with frame width. The full face and surrounding rim must be visible. Tiny dials cannot be made accurate merely by upscaling. |
| Sufficient detail | Stream 1 is recommended. Blur, compression, defocus, low-resolution streams, IR glare, overexposure, or occlusion can prevent detection or produce an incorrect but plausible reading. |

Automatic localization removes the need to hardcode the gauge's initial image
position/diameter for the normal path. It does **not** identify the scale labels,
correct camera roll, or make arbitrary perspective changes harmless.

## Tested camera and connection

The tested camera is a **TP-Link Tapo C100**, with color/daytime and infrared
night-mode captures from its main 1920×1080 stream. Capture assumes its
authenticated RTSP endpoints `/stream1` and `/stream2`, using TCP transport.
`camera_host` accepts a reachable hostname/IP and optional forwarded port; the
account must be the camera's RTSP account, not a cloud-login assumption. Other
RTSP camera models can use different paths and may need code changes.

Example real daytime/nighttime crops are included in the repository README.
They show one installation, not evidence of general camera/gauge compatibility.

## Pressure scale and angles

- The result is currently **pressure in bar**, on a **0–4 bar** dial. The range,
  pressure device class, and units are hardcoded, not inferred from the picture.
- Pressure increases **clockwise**, linearly with needle angle. Nonlinear scales,
  counterclockwise scales, multi-turn dials, and digital displays are unsupported.
- Two manually configured anchors define the conversion: **2 bar at 356°** and
  **3 bar at 61°** by default, clockwise from 12 o'clock. This crosses the 360°
  boundary and implies 65° per bar. The 0- and 1-bar positions are extrapolated;
  they are not independently detected. The unusual zero mark is deliberately
  not used for calibration.
- Camera roll or gauge rotation changes those anchors even if the face still
  looks perfectly circular. Recheck `two_bar_angle` and `three_bar_angle` after
  rotation or a substantial viewing-angle change.
- These anchor angles were estimated from low-resolution images, not a certified
  calibration reference. Better day/night agreement does not establish absolute
  accuracy. Small endpoint excursions are clamped to 0/4 bar; this is not an
  overrange detector.

## Cached geometry and garage-specific fallback

1. After startup, search the frame for the dial. Within a successful batch,
   geometry can be reused between shots.
2. Keep last-good geometry in the scheduler's **memory**, including across worker
   processes. Each later shot must cheaply re-verify the cached rim/brightness
   and needle before skipping the full search. A changed aspect ratio invalidates
   the cached geometry; proportional resolution changes can rescale it.
3. If cached verification fails, search the full frame again. Search thresholds
   and the cache verifier are heuristics, not guarantees against false matches.
4. If localization still fails, try the original hardcoded crop:
   **x=950:1070, y=725:845 in a 1920×1080 frame**, scaled for the current frame.
   This last resort **only makes sense for the original garage camera view**.
   It still requires a confident needle measurement. Adapt/remove it before
   deploying on an unrelated view; do not mistake it for generic recovery.
5. No geometry is saved in `.env` or a calibration file. Restarting the app clears
   scheduler memory. Batches with failed measurements or large shot spread do
   not replace the last-good cache.

The normalized dial radius of 50 pixels and 120×120 crop are internal coordinate
conventions, not requirements for the gauge's original diameter in the picture.

## Sampling, outliers, and Home Assistant

- A run uses `shot_count` independent captures (default **3**), separated by one
  second, and publishes their **median**. This assumes pressure is approximately
  steady across the batch. `capture_interval` defaults to **60 seconds**.
- One isolated outlier cannot move the median of three outside the other two
  readings. Two wrong readings can still win. A median does not correct shared
  calibration bias, consistently wrong needle identification, or connectivity.
- Every shot must yield a valid measurement. A failure aborts the batch rather
  than silently using a smaller sample. Spread greater than 0.15 bar is logged;
  the median is still published, but that batch does not replace cached geometry.
- MQTT discovery advertises a pressure sensor in bar. It is not automatically
  suitable for temperature gauges, tachometers, fuel gauges, or other dial types.
- Retained MQTT state can replay an old pressure after HA restarts. Keep captures
  scheduled and choose an expiry longer than the reading interval. Expiry and
  log timestamps are not a guarantee of physical measurement freshness.
- Images and pressure estimates must not be used as the sole input to a safety
  interlock, pressure relief system, or critical alarm.

## What broader support would require

At minimum: configurable units/range/direction, a documented calibration procedure,
removal or explicit opt-in of the garage fixed fallback, validation on multiple
gauges/cameras and lighting modes, and better handling of ambiguous needles,
camera roll, and oblique views. OCR or automatic scale recognition would be a
separate feature; neither is implemented here.
