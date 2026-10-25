#!/usr/bin/env bash
# Start node_exporter (:9100) and Prometheus (:9090) in the background.
# Logs -> monitoring/logs/, PIDs -> monitoring/run/, TSDB -> monitoring/data/
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/bin"
mkdir -p "$HERE/logs" "$HERE/run" "$HERE/data"

if [[ ! -x "$BIN/prometheus/prometheus" || ! -x "$BIN/node_exporter/node_exporter" ]]; then
  echo "Binaries missing - run: bash monitoring/setup.sh" >&2
  exit 1
fi

start() {
  local name="$1"; shift
  local pidfile="$HERE/run/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "[start] $name already running (pid $(cat "$pidfile"))"
    return
  fi
  nohup "$@" >"$HERE/logs/$name.log" 2>&1 &
  echo $! >"$pidfile"
  echo "[start] $name started (pid $!), log: monitoring/logs/$name.log"
}

start node_exporter "$BIN/node_exporter/node_exporter" \
  --web.listen-address=":9100"

start prometheus "$BIN/prometheus/prometheus" \
  --config.file="$HERE/prometheus.yml" \
  --storage.tsdb.path="$HERE/data" \
  --web.listen-address=":9090" \
  --web.enable-lifecycle

sleep 2
echo
echo "node_exporter : http://localhost:9100/metrics"
echo "Prometheus    : http://localhost:9090   (Status -> Targets should show 3 x UP)"
echo "Alerts        : http://localhost:9090/alerts"
echo "Reload config : curl -X POST http://localhost:9090/-/reload"
echo "Stop          : bash monitoring/stop.sh"
