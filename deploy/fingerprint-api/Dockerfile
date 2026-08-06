FROM golang:1.26-bookworm AS builder

WORKDIR /src
COPY go.mod go.sum ./
COPY cmd/fingerprint-api ./cmd/fingerprint-api
COPY internal/fingerprintconfig ./internal/fingerprintconfig
COPY internal/fingerprintmodel ./internal/fingerprintmodel
COPY internal/fingerprintpolicy ./internal/fingerprintpolicy
COPY internal/fingerprintsdk ./internal/fingerprintsdk
COPY internal/roxyopenapi ./internal/roxyopenapi
RUN CGO_ENABLED=0 go test ./cmd/fingerprint-api ./internal/fingerprintconfig \
        ./internal/fingerprintmodel ./internal/fingerprintpolicy ./internal/fingerprintsdk \
        ./internal/roxyopenapi \
    && CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags="-s -w" \
        -o /out/automyai-fingerprint-api ./cmd/fingerprint-api

FROM node:24-bookworm-slim

WORKDIR /app
COPY --from=builder /out/automyai-fingerprint-api /usr/local/bin/automyai-fingerprint-api
COPY fingerprint/sdk /app/fingerprint/sdk

CMD ["/usr/local/bin/automyai-fingerprint-api"]
