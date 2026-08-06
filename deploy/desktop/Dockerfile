FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fluxbox \
        novnc \
        websockify \
        xterm \
        x11-xserver-utils \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

WORKDIR /app
COPY scripts/start_desktop.sh /app/scripts/start_desktop.sh
COPY deploy/desktop/fluxbox-init /app/deploy/desktop/fluxbox-init
RUN chmod +x /app/scripts/start_desktop.sh

CMD ["/app/scripts/start_desktop.sh"]
