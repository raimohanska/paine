# Build and deploy

## Status and requirements

This is a garage-specific prototype, not a generic pressure gauge reader. Its
camera/dial appearance, scale, and needle-detection assumptions are specific to
the author's installation. Gauge position/size and the visible hub are detected
automatically, but camera roll and significant perspective changes are not
corrected. Adapt and validate the calibration before using
any other camera view/gauge. Never use it as a substitute for a safety device.

Supported app hosts: Home Assistant OS on **ARM64 (`aarch64`, including Green)**
or **x86-64 (`amd64`)**. The old 32-bit Stretch Raspberry Pi is not supported.
The host must reach the camera RTSP stream and MQTT broker over the network.

Tests and ARM64 binary dependency downloads were verified in the development
environment. Docker is unavailable there, so the full image build and actual
Supervisor installation are **not yet verified**. Check build logs on first
installation and compare the measured pressure with the physical gauge.

## Recommended: install from GitHub on Green

1. Commit and push the app files to your GitHub repository. Required structure:

   ```text
   repository.yaml
   paine/
     config.yaml
     Dockerfile
     requirements.txt
     __init__.py
     app_config.py
     supervisor_config.py
     config_ui.py
     config_ui.html
     editor.js
     editor.css
     capture_camera.py
     gauge_detection.py
     mqtt_pressure.py
     run.py
     README.md
     DOCS.md
   ```

   Do not commit `.env`, photos, logs, or private app options.

2. In Home Assistant, open **Settings → Apps → App store**. Older releases call
   these **Add-ons → Add-on store**.
3. Choose **⋮ → Repositories**, add your GitHub repository URL, then refresh or
   choose **Check for updates**.
4. Find **Paine pressure gauge** in that repository and select **Install**.
   There is no prebuilt image setting: Supervisor builds the Dockerfile locally
   for Green's ARM64 architecture. The first installation needs internet access
   to download the base image, Debian packages, and Python wheels. It can take
   several minutes. No Docker CLI or Python installation on HA's host is needed.
5. Start Paine and select **Open Web UI**. Fill out the visual camera cards:
   - Camera hostname/IP and camera-account credentials. For a non-default RTSP
     port, set `camera_host` to `hostname:port` (default port is 554). With
     forwarding, use the reachable forwarding address and forwarded port.
   - MQTT broker details. For the Mosquitto app, normally use `core-mosquitto`
     and port `1883`, with a broker account allowed to publish.
   - Keep `sensor_id` and `device_id` equal to the IDs from your existing setup.
     Changing the defaults is important if you previously used `paine_pressure1`.
   - Select **Add camera** for the second gauge and calibrate it separately.
     Existing flat camera settings migrate automatically into the saved `gauges`
     list without changing IDs. See `CONFIGURATION.md` for details.
6. Save, enable **Start on boot**, and **Restart** Paine from its Info page.
7. Inspect **Logs** for `Pressure: ...; published to MQTT`. Check the entity's
   value against the physical gauge, then assign the device to an Area.
8. Stop any old cron job or external capture process to avoid competing readings.

The app takes three shots immediately and publishes their median, repeating every
60 seconds. Configure `shot_count` (1–9, default 3) and `capture_interval` (seconds,
default 60) in the app UI or per gauge. Expiry is automatically the greater of
300 seconds and three reading intervals. Stream 1 and discovery prefix
`homeassistant` are fixed. Assign Areas from the HA device page. Home Assistant's
MQTT integration must connect to the same broker with discovery enabled.

If an already existing device-less entity does not attach to the device after a
live discovery update, restart Home Assistant to reload the retained metadata.

## Alternative: install as a local app

1. Use the Samba or SSH app to access Home Assistant's local-app directory.
2. Copy **the contents of this repository's `paine/` directory** into
   `/local_apps/paine`. Older releases use `/addons/paine`; Samba may label the
   share `local_apps` or `addons`.
3. Do not copy the outer repository folder, `.env`, diagnostic logs, or images.
   The resulting directory must directly contain `config.yaml` and `Dockerfile`.
4. In the app store, choose **Check for updates** and refresh. Install under
   **Local apps/add-ons**, then configure/start as above.

## Developer builds outside Home Assistant

Run these at the repository root, with Docker installed:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
python3 -m ruff check .
docker build -t paine-app:local ./paine
```

Optional browser smoke tests for the visual editor (synthetic settings only):

```bash
python3 -m pip install playwright
python3 -m playwright install --with-deps chromium
python3 -m pytest -q test_editor_browser.py
```

These tests check Add/Remove cards, required fields, save/reload, preserved
identities and secret references, and relative URLs under an ingress-style path.
They use a mocked Supervisor endpoint; actual HA deployment still needs verification.

This builds for the machine's native architecture. To cross-build ARM64 from an
x86-64 machine using Docker Buildx (requires an ARM64-capable builder or QEMU
emulation, supplied by Docker Desktop):

```bash
docker buildx build --platform linux/arm64 --load -t paine-app:arm64 ./paine
```

The `paine/` build context is allowlisted; app options and `.env` never enter the
image. The development container at `.devcontainer/` is separate and is **not**
the app deployment image.

Optional local runtime test: create a private directory outside the repository
containing `options.json`, a JSON object with the same keys as the `options`
mapping in `paine/config.yaml`. Fill its camera/MQTT settings and protect its
permissions. Do not put real credentials in shell arguments or Git.

```bash
docker run --rm --name paine-app-test \
  --mount type=bind,src=/ABSOLUTE/PRIVATE/DIRECTORY,dst=/data \
  paine-app:local
```

Outside Home Assistant, replace `core-mosquitto` with a reachable broker
hostname/IP; HA's internal app DNS name is not generally available there. The
container overwrites bounded per-shot snapshot/crop filenames in this private directory. Stop
it with `docker stop paine-app-test` from another terminal. Run only one publisher
for a given sensor during tests.

## Updates

1. Change the app source and bump `version` in `paine/config.yaml` and
   `io.hass.version` in `paine/Dockerfile`; update `CHANGELOG.md`.
2. Run the tests/build checks, commit, and push.
3. In Home Assistant's app store, select **Check for updates**, then update the
   installed app and check its Logs. Restart it after changing configuration.
4. For a local installation, copy the updated app files to the same local
   directory before checking for updates.

Protect Home Assistant backups: Supervisor app options contain camera and broker
credentials. Do not delete/recreate sensor IDs as a normal upgrade step; stable
IDs preserve the entity's registry association and reduce duplicate entities.
