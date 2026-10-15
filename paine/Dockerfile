FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt \
    && python -c "import cv2, numpy, paho.mqtt.client; assert cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG)"

COPY __init__.py capture_camera.py mqtt_pressure.py run.py /app/paine/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp" \
    OPENCV_LOG_LEVEL=OFF

LABEL io.hass.name="Paine pressure gauge" \
    io.hass.description="Read a camera pressure gauge and publish to MQTT" \
    io.hass.version="1.0.2" \
    io.hass.type="addon" \
    io.hass.arch="aarch64|amd64"

CMD ["python", "-m", "paine.run"]
