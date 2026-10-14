FROM debian:trixie-slim AS python-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libbz2-dev \
        libffi-dev \
        liblzma-dev \
        libncurses-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        uuid-dev \
        xz-utils \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/python-build
RUN curl -fsSL https://www.python.org/ftp/python/3.10.11/Python-3.10.11.tar.xz -o Python.tar.xz \
    && printf '3c3bc3048303721c904a03eb8326b631e921f11cc3be2988456a42f115daf04c  Python.tar.xz\n' | sha256sum --check - \
    && tar -xJf Python.tar.xz --strip-components=1 \
    && ./configure --prefix=/usr/local --with-ensurepip=install \
    && make -j"$(nproc)" \
    && make altinstall \
    && ln -s python3.10 /usr/local/bin/python3 \
    && ln -s python3.10 /usr/local/bin/python \
    && python -c "import bz2, ctypes, curses, lzma, readline, sqlite3, ssl, sys, uuid, zlib; assert sys.version_info[:3] == (3, 10, 11)"

FROM debian:trixie-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        libbz2-1.0 \
        libffi8 \
        libglib2.0-0t64 \
        libgomp1 \
        liblzma5 \
        libncursesw6 \
        libreadline8t64 \
        libsqlite3-0 \
        libssl3t64 \
        libuuid1 \
        openssh-client \
        ripgrep \
        sudo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-builder /usr/local/ /usr/local/

RUN useradd --create-home --shell /bin/bash --uid 1000 vscode \
    && printf 'vscode ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode \
    && python -m venv /opt/venv \
    && chown -R vscode:vscode /opt/venv

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:/home/vscode/.opencode/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER vscode
WORKDIR /home/vscode

RUN mkdir -p /home/vscode/.config /home/vscode/.local/share \
    && curl -fsSL https://opencode.ai/v2/install -o /tmp/install-opencode.sh \
    && bash /tmp/install-opencode.sh --no-modify-path \
    && rm /tmp/install-opencode.sh \
    && opencode --version

COPY requirements.txt requirements-dev.txt /tmp/python-deps/
COPY paine/requirements.txt /tmp/python-deps/paine/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/python-deps/requirements-dev.txt \
    && python -c "import sys, cv2, numpy, paho.mqtt.client; assert sys.version_info[:3] == (3, 10, 11); assert cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG)"
