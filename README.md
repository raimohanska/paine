# Paine

Python application for reading a mechanical pressure gauge from camera images.
Captures a camera frame, measures the gauge's black needle, and optionally
publishes estimated pressure to MQTT with Home Assistant discovery.

## Development Container

1. Start Docker Desktop (or another Docker daemon).
2. Open this directory in VS Code with the Dev Containers extension installed.
3. Run **Dev Containers: Reopen in Container**.
4. Open a container terminal and run `opencode`. Use `/connect` to authenticate
   with your model provider.

The container includes:

- Debian **13 (Trixie)** slim, the current stable Debian release.
- Python **3.10.11**, built from checksum-verified official sources in a separate
  build stage, matching the intended Raspberry Pi runtime.
- Headless OpenCV and NumPy for frame capture and gauge-needle image processing.
- Paho MQTT for publishing pressure and Home Assistant discovery.
- FFmpeg / ffprobe for inspecting and testing camera streams.
- The latest OpenCode CLI (V2), downloaded from the official installer at build time.
- Git, ripgrep, an SSH client, pytest, and Ruff.
- A non-root `vscode` user and a writable Python environment at `/opt/venv`.

OpenCode configuration and user data, including provider credentials, are stored
in Docker volumes outside the repository and survive container rebuilds. These
volumes contain secrets: do not share them. Host OpenCode credentials are not
automatically imported.

To fetch a newer OpenCode release in an existing container:

```bash
curl -fsSL https://opencode.ai/v2/install -o /tmp/install-opencode.sh
bash /tmp/install-opencode.sh --no-modify-path
rm /tmp/install-opencode.sh
opencode --version
```

Alternatively, use **Dev Containers: Rebuild Container Without Cache** to fetch the
latest release during a fresh build. Python dependencies are installed into the
image and synchronized from `requirements-dev.txt` when the container is created.
The first build also compiles Python; subsequent builds reuse that cached stage.

## Camera Access

RTSP is typical for a network/IP camera, not a directly attached USB webcam. A
network camera must expose an RTSP stream; its URL and authentication depend on
the camera model. The container can connect outbound to a camera's LAN IP without
port forwarding. The host must have a route to the camera.

OpenCV's FFmpeg backend supports RTSP. The devcontainer defaults RTSP transport to
TCP through `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`, avoiding inbound
UDP media ports through Docker's NAT. Remove or change this setting if your camera
requires another transport.

Use `ffprobe -rtsp_transport tcp 'rtsp://CAMERA_IP:554/STREAM_PATH'` to inspect a
stream. Do not commit real camera URLs containing passwords or place them in
shared logs. Keep credentials in local environment variables or an ignored
`.env` file, which `capture_camera.py` loads automatically.

## Capture and MQTT publishing

Install dependencies with `python -m pip install -r requirements.txt`, then run:

```bash
python capture_camera.py
```

Each run captures once, saves `snapshot.jpg` and `snapshot_gauge.jpg`, and prints
estimated pressure. The crop assumes the camera position stays fixed. Calibration
uses the 2- and 3-bar ticks; override with `--two-bar-angle` and `--three-bar-angle`
(clockwise degrees from 12 o'clock). Image-based pressure is an estimate.

Create your local, ignored `.env` with these settings (replace placeholders):

```dotenv
CAMERA_HOST=camera-host
CAMERA_USER=camera-user
CAMERA_PASSWORD=camera-password
MQTT_HOST=broker-host
MQTT_PORT=1883
MQTT_USER=mqtt-user
MQTT_PASSWORD=mqtt-password
MQTT_TLS=false
MQTT_SENSOR_NAME=Heating pressure
MQTT_SENSOR_ID=paine_pressure
MQTT_STATE_TOPIC=paine/pressure/state
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_EXPIRE_AFTER=300
MQTT_DEVICE_ID=paine_pressure_gauge
MQTT_DEVICE_NAME=Paine pressure gauge
```

Publishing is enabled when `MQTT_HOST` is set. Without it, capture still works.
Discovery also registers a device named **Paine pressure gauge**, which you can
assign to an Area in Home Assistant under **Settings → Devices & services →
Devices**. Optionally set `MQTT_SUGGESTED_AREA=Garage` to suggest its initial room.
Keep `MQTT_DEVICE_ID` stable; it is independent of the sensor's unique ID.
Other MQTT settings have the defaults shown; username/password are optional for
brokers allowing anonymous access. With `MQTT_TLS=true`, the default port is 8883
if `MQTT_PORT` is omitted; certificates and hostnames are verified using system
trust roots. Do not send credentials over untrusted networks without TLS.

Enable Home Assistant's MQTT integration against the same broker and discovery
prefix. The script publishes retained discovery configuration to
`homeassistant/sensor/paine_pressure/config`, then a **retained** reading to
the state topic. Both use QoS 1 and wait for broker acknowledgements before
disconnecting. Failures return a nonzero exit status; capture/measurement failures
do not publish a pressure reading. Discovery never includes broker credentials.

Keep the sensor ID stable to avoid duplicate entities. Schedule the command
externally, and set `MQTT_EXPIRE_AFTER` longer than the capture interval (e.g.
300 seconds for captures every 60 seconds). Home Assistant marks the sensor
unavailable when readings expire. Retained readings are replayed to new
subscribers, including after a Home Assistant restart; replaying an old reading
can restart the expiry timer, so keep captures scheduled regularly. If changing IDs/topics, remove the old retained
discovery configuration separately.

For a USB webcam on a Linux host, explicit `/dev/video0` device passthrough and
video-group permissions will be needed. Docker Desktop on macOS does not directly
expose the Mac's webcam as a Linux video device. No privileged mode or USB mapping
is enabled by default.

This setup is headless: save images to files and view them in the editor rather
than using `cv2.imshow()`. Do not install `opencv-python` alongside
`opencv-python-headless`; both provide the same `cv2` module.

## Raspberry Pi

Use **64-bit Raspberry Pi OS (aarch64)** for the pinned pip dependencies. The
container uses the host's native architecture, supporting both ARM64 (including
Apple Silicon) and x86-64 without forcing emulation. On a Pi with Python 3.10.11
already available:

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"
```

Python 3.10.11 is intentionally pinned, but it is an old patch release, and Python
3.10 reached end-of-life in October 2026. The container builds it on the official
`debian:trixie-slim` image; matching Python does not make the container OS identical
to the Pi OS. Plan a coordinated Python/security upgrade later. On 32-bit Pi OS,
these wheel pins are not supported by this setup;
use a separately validated system OpenCV installation or switch to 64-bit OS.
