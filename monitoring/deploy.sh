#!/usr/bin/env bash
# Deploy (or update) the monitoring stack on the NMS: copies this directory to lab@NMS:/opt/monitoring and runs
# docker compose up -d, then reloads Prometheus so a rule / scrape change is picked up without a restart.
set -euo pipefail
NMS=${NMS:-lab@10.0.0.10}; DIR=$(cd "$(dirname "$0")" && pwd); SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
$SSH "$NMS" 'sudo -n true 2>/dev/null && sudo mkdir -p /opt/monitoring && sudo chown $(id -u):$(id -g) /opt/monitoring || mkdir -p /opt/monitoring'
rsync -a --delete -e "$SSH" --exclude deploy.sh "$DIR/" "$NMS:/opt/monitoring/"
$SSH "$NMS" 'cd /opt/monitoring && docker compose up -d --remove-orphans && sleep 3 && docker compose ps --format "{{.Name}} {{.Status}}"'
$SSH "$NMS" 'curl -sf -X POST http://localhost:9090/-/reload && echo "prometheus reloaded"' || echo "prometheus reload skipped (not up yet)"
