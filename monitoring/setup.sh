#!/usr/bin/env bash
# Download node_exporter + Prometheus into monitoring/bin (Linux, no sudo).
# Pin versions (optional):
#   PROMETHEUS_VERSION=3.15.1 NODE_EXPORTER_VERSION=1.12.1 bash monitoring/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/bin"
mkdir -p "$BIN"

DEFAULT_PROMETHEUS_VERSION="3.15.1"
DEFAULT_NODE_EXPORTER_VERSION="1.12.1"

OS=linux

case "$(uname -m)" in
  x86_64|amd64)  ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  armv7l)        ARCH=armv7 ;;
  *) echo "Unsupported CPU architecture $(uname -m)" >&2; exit 1 ;;
esac

latest_version() {
  local url
  url="$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/prometheus/$1/releases/latest" 2>/dev/null || true)"
  url="${url##*/v}"
  if [[ "$url" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$url"; else echo "$2"; fi
}

install_component() {
  local name="$1" ver="$2"
  local dir="$name-$ver.$OS-$ARCH"
  if [[ -x "$BIN/$dir/$name" ]]; then
    echo "[setup] $name $ver already present"
  else
    local tmp; tmp="$(mktemp -d)"
    local url="https://github.com/prometheus/$name/releases/download/v$ver/$dir.tar.gz"
    echo "[setup] downloading $url"
    curl -fL --progress-bar -o "$tmp/$dir.tar.gz" "$url"
    tar -xzf "$tmp/$dir.tar.gz" -C "$BIN"
    rm -rf "$tmp"
  fi
  ln -sfn "$BIN/$dir" "$BIN/$name"     # stable path: bin/<name>/<name>
}

PROM_VER="${PROMETHEUS_VERSION:-$(latest_version prometheus "$DEFAULT_PROMETHEUS_VERSION")}"
NODE_VER="${NODE_EXPORTER_VERSION:-$(latest_version node_exporter "$DEFAULT_NODE_EXPORTER_VERSION")}"

install_component node_exporter "$NODE_VER"
install_component prometheus "$PROM_VER"

echo
"$BIN/node_exporter/node_exporter" --version 2>&1 | head -1
"$BIN/prometheus/prometheus" --version 2>&1 | head -1
"$BIN/prometheus/promtool" check config "$HERE/prometheus.yml"
echo
echo "[setup] done. Start with:  bash monitoring/start.sh"
