FROM golang:1.26-bookworm AS builder

WORKDIR /src
COPY go.mod go.sum ./
COPY cmd/extract-api ./cmd/extract-api
COPY internal ./internal
RUN CGO_ENABLED=0 go test ./cmd/extract-api ./internal/extractapi ./internal/extractmethods \
    && CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags="-s -w" \
        -o /out/automyai-extract-api ./cmd/extract-api

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN pip install --no-cache-dir --no-compile curl_cffi

WORKDIR /app
COPY --from=builder /out/automyai-extract-api /usr/local/bin/automyai-extract-api
COPY integrations/__init__.py /app/integrations/__init__.py
COPY integrations/kakao_curl_transport.py /app/integrations/kakao_curl_transport.py
COPY integrations/oai_fingerprint.py /app/integrations/oai_fingerprint.py
COPY integrations/browser_fingerprint.py /app/integrations/browser_fingerprint.py

CMD ["/usr/local/bin/automyai-extract-api"]
