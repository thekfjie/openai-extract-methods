#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/bin"

requested=("$@")
if [ "${#requested[@]}" -eq 0 ]; then
  requested=(fingerprint-api control-api extract-api)
fi

declare -A packages=(
  [fingerprint-api]='./cmd/fingerprint-api ./internal/fingerprintconfig ./internal/fingerprintmodel ./internal/fingerprintpolicy ./internal/fingerprintsdk ./internal/roxyopenapi'
  [control-api]='./cmd/control-api ./internal/taskqueue ./internal/controlapi'
  [extract-api]='./cmd/extract-api ./internal/extractapi ./internal/extractmethods'
)
declare -A binaries=(
  [fingerprint-api]='automyai-fingerprint-api'
  [control-api]='automyai-control-api'
  [extract-api]='automyai-extract-api'
)

test_packages=()
build_commands=()
for target in "${requested[@]}"; do
  if [ -z "${packages[$target]:-}" ]; then
    echo "Unknown Go service '$target'; choose fingerprint-api, control-api or extract-api" >&2
    exit 2
  fi
  # shellcheck disable=SC2206
  target_packages=(${packages[$target]})
  test_packages+=("${target_packages[@]}")
  binary="${binaries[$target]}"
  build_commands+=("CGO_ENABLED=0 /usr/local/go/bin/go build -buildvcs=false -trimpath -ldflags='-s -w' -o bin/${binary}.new ./cmd/${target}")
done

printf -v test_command ' %q' "${test_packages[@]}"
build_command=""
for command in "${build_commands[@]}"; do
  if [ -n "$build_command" ]; then
    build_command+=" && "
  fi
  build_command+="$command"
done

if [ "$(id -u)" -eq 0 ]; then
  SYSTEM_DOCKER=(docker --host unix:///var/run/docker.sock)
else
  SYSTEM_DOCKER=(sudo -n docker --host unix:///var/run/docker.sock)
fi

"${SYSTEM_DOCKER[@]}" run --rm \
  -e GOCACHE=/tmp/go-build \
  -v "$ROOT_DIR:/src" -w /src \
  golang:1.26-bookworm \
  sh -lc "/usr/local/go/bin/go test${test_command} && ${build_command}"

for target in "${requested[@]}"; do
  binary="${binaries[$target]}"
  if [ "$(id -u)" -eq 0 ]; then
    chmod 755 "$ROOT_DIR/bin/$binary.new"
    mv -f "$ROOT_DIR/bin/$binary.new" "$ROOT_DIR/bin/$binary"
  else
    sudo -n chmod 755 "$ROOT_DIR/bin/$binary.new"
    sudo -n mv -f "$ROOT_DIR/bin/$binary.new" "$ROOT_DIR/bin/$binary"
  fi
done
