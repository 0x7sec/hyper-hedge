#!/usr/bin/env bash
# ==============================================================================
# Bybit Hedge Bot - Zero-Downtime Safe VPS Update Script
# ==============================================================================
set -euo pipefail

APP_DIR="${HOME}/hyper_hedge_research"
cd "$APP_DIR"

echo "=== [1/3] Pulling latest changes from git ==="
git fetch origin
git reset --hard origin/main

echo "=== [2/3] Updating Python dependencies ==="
./venv/bin/pip install -r requirements.txt --upgrade -q

echo "=== [3/3] Restarting Services (Stopping Trend Bot, Restarting Telemetry + AMD Bot) ==="
if [ "$(id -u)" -eq 0 ]; then
  systemctl stop bybit-bot || true
  systemctl disable bybit-bot || true
  systemctl restart bybit-telemetry amd-bot amd-telemetry bybit-discount || true
  systemctl status bybit-telemetry amd-bot amd-telemetry bybit-discount --no-pager
else
  sudo systemctl stop bybit-bot || true
  sudo systemctl disable bybit-bot || true
  sudo systemctl restart bybit-telemetry amd-bot amd-telemetry bybit-discount || true
  sudo systemctl status bybit-telemetry amd-bot amd-telemetry bybit-discount --no-pager
fi

echo "=== Update complete! Trend bot stopped, AMD bot & Telemetry active ==="
