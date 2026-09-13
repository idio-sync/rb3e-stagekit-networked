# Headless RB3Enhanced dashboard server.
#
# Build from the repository root:
#   docker build -t rb3e-dashboard-server .
#
# The image carries the server only. The desktop application stays on the
# machine a person is sitting at: Discord Rich Presence talks to a local IPC
# pipe, VLC needs a screen, and neither belongs on a NAS.

FROM python:3.11-slim

# Unraid's appdata share is owned by nobody:users (99:100), and a container
# user that cannot write its own data directory is a confusing first run.
# Override at build time for other hosts.
ARG PUID=99
ARG PGID=100

# Home Assistant over MQTT is optional and off by default. The webhook needs
# no dependency, so the plain image installs nothing extra.
ARG INSTALL_MQTT=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RB3E_DATA_DIR=/data

WORKDIR /app

# Dependencies first, so editing the application does not reinstall them.
COPY Dashboard/requirements-server.txt ./requirements-server.txt
COPY Dashboard/requirements-mqtt.txt ./requirements-mqtt.txt
RUN pip install --no-cache-dir -r requirements-server.txt \
 && if [ "$INSTALL_MQTT" = "true" ]; then \
      pip install --no-cache-dir -r requirements-mqtt.txt; \
    fi

COPY Dashboard/rb3server.py ./rb3server.py
COPY Dashboard/rb3dashboard ./rb3dashboard
COPY Dashboard/tools/healthcheck.py ./healthcheck.py

RUN groupadd --gid "$PGID" --non-unique rb3 \
 && useradd --uid "$PUID" --gid "$PGID" --non-unique --no-create-home rb3 \
 && mkdir -p /data \
 && chown -R "$PUID:$PGID" /data /app

# No VOLUME declaration on purpose. Compose and the Unraid template both bind
# a host directory to /data, and an anonymous volume would quietly absorb the
# history of anyone who forgot -v. The startup preflight warns when /data is
# not a mount point instead.
USER rb3

# Declared for documentation. With host networking these are the host's ports:
# UDP 21070 receives console events, UDP 21071 carries Pico discovery and
# telemetry, and TCP 8321 serves the dashboard and its API.
EXPOSE 8321/tcp
EXPOSE 21070/udp
EXPOSE 21071/udp

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]

ENTRYPOINT ["python", "/app/rb3server.py"]
