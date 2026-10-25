#!/usr/bin/env bash
# Stop node_exporter and Prometheus started by start.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
for name in prometheus node_exporter; do
  pidfile="$HERE/run/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")" && echo "[stop] $name stopped"
  else
    echo "[stop] $name not running"
  fi
  rm -f "$pidfile"
done
