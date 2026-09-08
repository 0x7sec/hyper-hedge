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

echo "=== [3/3] Restarting Services (Bot + Telemetry) ==="
if [ "$(id -u)" -eq 0 ]; then
  systemctl restart bybit-bot bybit-telemetry
  systemctl status bybit-bot bybit-telemetry --no-pager
else
  sudo systemctl restart bybit-bot bybit-telemetry
  sudo systemctl status bybit-bot bybit-telemetry --no-pager
fi

echo "=== Update complete! Both services active ==="
