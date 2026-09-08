#!/usr/bin/env bash
# ==============================================================================
# Bybit Hedge Bot - Zero-Downtime Safe VPS Update Script
# ==============================================================================
set -euo pipefail

APP_DIR="/home/trader/hyper_hedge_research"
cd "$APP_DIR"

echo "=== [1/4] Pulling latest changes from git ==="
git fetch origin
git pull origin main

echo "=== [2/4] Updating Python dependencies ==="
source venv/bin/activate
pip install -r requirements.txt --upgrade

echo "=== [3/4] Running automated test suite ==="
python scratch/test_multi_bot_suite.py

echo "=== [4/4] Restarting Bybit Bot Service ==="
sudo systemctl restart bybit-bot

echo "=== Update complete! Current status: ==="
sudo systemctl status bybit-bot --no-pager
