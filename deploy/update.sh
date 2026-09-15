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

echo "=== [3/3] Restarting Services (Stopping Trend Bot & AMD Bot, Restarting Telemetry + Discount Suite + Options Harvester) ==="
if [ "$(id -u)" -eq 0 ]; then
  systemctl stop bybit-bot amd-bot || true
  systemctl disable bybit-bot amd-bot || true
  systemctl enable bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry || true
  systemctl restart bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry || true
  systemctl status bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry --no-pager
else
  sudo systemctl stop bybit-bot amd-bot || true
  sudo systemctl disable bybit-bot amd-bot || true
  sudo systemctl enable bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry || true
  sudo systemctl restart bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry || true
  sudo systemctl status bybit-telemetry amd-telemetry bybit-discount discount-telemetry bybit-options-harvester options-telemetry --no-pager
fi

echo "=== Update complete! Trend bot and AMD bot stopped, Discount Suite & Options Harvester active ==="
