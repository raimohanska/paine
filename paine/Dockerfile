FROM python:3.13-slim-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates libglib2.0-0 libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt \
    && python -c "import cv2, numpy, paho.mqtt.client; assert cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG)"

COPY __init__.py app_config.py capture_camera.py mqtt_pressure.py gauge_detection.py run.py diagnostics_ui.py /app/paine/
COPY water_*.py water_landmarks.npz /app/paine/
# Verified upstream model; no network access for inference at runtime.
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/jomjol/AI-on-the-edge-device/04fad5d3efc518c4c2ad8cd14904f1d966ac3605/sd-card/config/dig-class100-0182-s2_q.tflite','/app/paine/water.tflite')" \
    && echo '78f8ee04f8e195ef9e8e1314c77690d530ef151e24c1cf7ab3c66055d0c2b950  /app/paine/water.tflite' | sha256sum -c - \
    && python -c "from ai_edge_litert.interpreter import Interpreter; Interpreter(model_path='/app/paine/water.tflite').allocate_tensors()"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp" \
    OPENCV_LOG_LEVEL=OFF

LABEL io.hass.name="Paine pressure gauge" \
    io.hass.description="Read a camera pressure gauge and publish to MQTT" \
    io.hass.version="1.3.0" \
    io.hass.type="addon" \
    io.hass.arch="aarch64|amd64"

CMD ["python", "-m", "paine.run"]
