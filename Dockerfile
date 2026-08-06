# Pin Debian Bookworm so a rebuild cannot silently jump to a much larger
# Chromium/system layer and exhaust the host disk.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-dejavu-core \
        fonts-noto-cjk \
        nodejs \
        procps \
    && rm -rf \
        /var/lib/apt/lists/* \
        /var/cache/apt/* \
        /tmp/* \
        /usr/share/doc/* \
        /usr/share/info/* \
        /usr/share/man/* \
        /usr/share/locale/*

RUN pip install --no-cache-dir --no-compile \
        selenium \
        undetected-chromedriver \
        curl_cffi \
        "DrissionPage==4.1.1.4" \
        psutil \
        "requests>=2.31,<3"

WORKDIR /app
COPY . /app

RUN chmod +x /app/scripts/start_server.sh

EXPOSE 13030

CMD ["/app/scripts/start_server.sh"]
