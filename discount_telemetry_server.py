#!/usr/bin/env python3
"""
discount_telemetry_server.py — Dedicated Real-Time WebSocket Dashboard & AI Telemetry Server
for Bybit UTA Autonomous Discount Buy Suite (3x $1,000 Capital Enclosure).
Runs on Port 8082 with RFC 6455 WebSocket streaming (zero page reload, instant live sync).
"""

import os
import sys
import json
import re
import time
import struct
import base64
import hashlib
import select
import secrets
import subprocess
import logging
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Load environment variables
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

PORT = int(os.environ.get("DISCOUNT_TELEMETRY_PORT", 8082))
PASSWORD = os.environ.get("DISCOUNT_TELEMETRY_PASSWORD", os.environ.get("TELEMETRY_PASSWORD", "hedge_bot_sec_2026")).strip()

if not PASSWORD:
    PASSWORD = secrets.token_hex(16)
    print(f"\n[SECURITY] No DISCOUNT_TELEMETRY_PASSWORD set! Generated random password:")
    print(f"[SECURITY] >>> {PASSWORD} <<<\n")

STATE_FILE = os.path.join(BASE_DIR, "discount_state.json")

ACTIVE_SESSIONS = set()

# Sensitive strings to scrub from logs
SCRUB_PATTERNS = [
    re.compile(r"(BYBIT_API_KEY\s*=\s*)([^\s&]+)", re.IGNORECASE),
    re.compile(r"(BYBIT_API_SECRET\s*=\s*)([^\s&]+)", re.IGNORECASE),
    re.compile(r"(api[_-]?secret\s*[:=]\s*)([^\s&\"']+)", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s&\"']+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)([^\s&\"']+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s&\"']+)", re.IGNORECASE),
]


def sanitize_logs(text: str) -> str:
    res = text
    k = os.environ.get("BYBIT_API_KEY", "")
    s = os.environ.get("BYBIT_API_SECRET", "")
    if k and len(k) > 4:
        res = res.replace(k, "[REDACTED_API_KEY]")
    if s and len(s) > 4:
        res = res.replace(s, "[REDACTED_API_SECRET]")
    if PASSWORD and len(PASSWORD) > 4:
        res = res.replace(PASSWORD, "[REDACTED_PASSWORD]")
    for pattern in SCRUB_PATTERNS:
        res = pattern.sub(r"\1[REDACTED]", res)
    return res


def read_discount_state() -> Dict[str, Any]:
    """Read the latest atomic state from discount_state.json."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "options_engine": {
            "name": "Options Cash-Secured Put",
            "allocated_capital": 1000.0,
            "current_capital": 1000.0,
            "total_realized_pnl": 0.0,
            "total_cycles": 0,
            "profitable_cycles": 0,
            "status": "INITIALIZING",
            "active_orders": [],
            "active_positions": [],
            "metrics": {},
        },
        "spot_engine": {
            "name": "Spot Maker Accumulator",
            "allocated_capital": 1000.0,
            "current_capital": 1000.0,
            "total_realized_pnl": 0.0,
            "total_cycles": 0,
            "profitable_cycles": 0,
            "status": "INITIALIZING",
            "active_orders": [],
            "active_positions": [],
            "metrics": {},
        },
        "neutral_engine": {
            "name": "Delta-Hedged Market-Neutral",
            "allocated_capital": 1000.0,
            "current_capital": 1000.0,
            "total_realized_pnl": 0.0,
            "total_cycles": 0,
            "profitable_cycles": 0,
            "status": "INITIALIZING",
            "active_orders": [],
            "active_positions": [],
            "metrics": {},
        },
    }


_LOGS_CACHE: Dict[int, List[str]] = {}
_LAST_LOGS_FETCH = 0.0


def get_systemd_logs(lines: int = 50) -> List[str]:
    """Fetch recent logs from discount_bot.log file or systemd journal with caching."""
    global _LOGS_CACHE, _LAST_LOGS_FETCH
    now = time.time()
    if (now - _LAST_LOGS_FETCH < 2.0) and lines in _LOGS_CACHE:
        return _LOGS_CACHE[lines]

    log_file = os.path.join(BASE_DIR, "discount_bot.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.readlines()
                cleaned = [sanitize_logs(l.rstrip()) for l in content if l.strip()]
                if cleaned:
                    res = cleaned[-lines:]
                    _LOGS_CACHE[lines] = res
                    _LAST_LOGS_FETCH = now
                    return res
        except Exception:
            pass

    if sys.platform != "win32":
        try:
            cmd = ["journalctl", "-u", "bybit-discount", "-n", str(lines), "--no-pager"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                raw_lines = [sanitize_logs(l.rstrip()) for l in res.stdout.strip().split("\n") if l.strip()]
                if raw_lines:
                    res = raw_lines[-lines:]
                    _LOGS_CACHE[lines] = res
                    _LAST_LOGS_FETCH = now
                    return res
        except Exception:
            pass

    fallback = ["System daemon active. Awaiting trade engine events..."]
    _LOGS_CACHE[lines] = fallback
    _LAST_LOGS_FETCH = now
    return fallback


_PRICE_CACHE = {}
_LAST_PRICE_FETCH = 0


def get_live_market_prices() -> Dict[str, float]:
    """Fetch live mark prices for BTCUSDT, ETHUSDT, SOLUSDT with caching."""
    global _PRICE_CACHE, _LAST_PRICE_FETCH
    now = time.time()
    if now - _LAST_PRICE_FETCH < 6 and _PRICE_CACHE:
        return _PRICE_CACHE
    try:
        import requests
        testnet = os.environ.get("TESTNET", "false").lower() in ("true", "1", "yes")
        domain = "api-testnet.bybit.com" if testnet else "api.bybit.com"
        r = requests.get(f"https://{domain}/v5/market/tickers?category=linear", timeout=3).json()
        if r.get("retCode") == 0 and r.get("result", {}).get("list"):
            prices = {}
            for item in r["result"]["list"]:
                sym = item.get("symbol")
                if sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                    prices[sym] = float(item.get("markPrice") or item.get("lastPrice") or 0.0)
            if prices:
                _PRICE_CACHE = prices
                _LAST_PRICE_FETCH = now
                return _PRICE_CACHE
    except Exception:
        pass
    if not _PRICE_CACHE:
        _PRICE_CACHE = {"BTCUSDT": 77420.0, "ETHUSDT": 2650.0, "SOLUSDT": 145.0}
    return _PRICE_CACHE


def get_uptime_info(state: Dict[str, Any]) -> Tuple[int, str]:
    """Compute uptime seconds and human-readable string."""
    up_sec = 0
    # 1. Check started_at in state
    started_at = state.get("started_at") or state.get("session_start_iso")
    if started_at:
        try:
            start_dt = datetime.fromisoformat(started_at)
            now_dt = datetime.now(timezone.utc) if start_dt.tzinfo else datetime.now()
            up_sec = max(0, int((now_dt - start_dt).total_seconds()))
        except Exception:
            pass

    # 2. On Linux/Debian VPS, check systemd ActiveEnterTimestamp
    if up_sec <= 0 and sys.platform != "win32":
        try:
            cmd = ["systemctl", "show", "bybit-discount", "--property=ActiveEnterTimestamp"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                val = res.stdout.strip().split("=", 1)[1].strip()
                if val:
                    cmd2 = ["date", "-d", val, "+%s"]
                    res2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
                    if res2.returncode == 0 and res2.stdout.strip():
                        start_ts = int(res2.stdout.strip())
                        up_sec = max(0, int(time.time() - start_ts))
        except Exception:
            pass

    # Format human-readable string
    days = up_sec // 86400
    hours = (up_sec % 86400) // 3600
    mins = (up_sec % 3600) // 60
    secs = up_sec % 60

    if days > 0:
        up_str = f"{days}d {hours}h {mins}m {secs}s"
    elif hours > 0:
        up_str = f"{hours}h {mins}m {secs}s"
    elif mins > 0:
        up_str = f"{mins}m {secs}s"
    else:
        up_str = f"{secs}s"

    return up_sec, up_str


DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bybit UTA Discount Buy Suite — Live Socket Dashboard (Port __PORT__)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg-dark: #070b14;
      --card-bg: #0d1527;
      --card-border: #1e293b;
      --accent: #38bdf8;
      --green: #10b981;
      --red: #ef4444;
      --amber: #f59e0b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-dark);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      padding: 24px 20px;
      line-height: 1.5;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 12px;
    }
    .title-area h1 {
      font-size: 20px;
      font-weight: 700;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .title-area p {
      font-size: 13px;
      color: var(--text-muted);
      margin-top: 2px;
    }
    .badges { display: flex; gap: 8px; align-items: center; }
    .badge {
      font-size: 11px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      transition: all 0.2s ease;
    }
    .badge-sim { background: rgba(245, 158, 11, 0.15); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-live { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-active { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-event { background: #1e293b; color: var(--text-muted); border: 1px solid #334155; }
    .badge-socket {
      background: #064e3b;
      color: #6ee7b7;
      border: 1px solid #059669;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .badge-socket::before {
      content: "";
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse-dot 1.5s infinite;
    }
    @keyframes pulse-dot {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
      100% { opacity: 1; transform: scale(1); }
    }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-box {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px 18px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
      transition: border-color 0.2s;
    }
    .stat-box:hover { border-color: rgba(56, 189, 248, 0.4); }
    .stat-label { font-size: 11.5px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
    .stat-value { font-size: 22px; font-weight: 700; margin-top: 4px; color: #fff; }
    .stat-sub { font-size: 11.5px; margin-top: 4px; color: var(--text-muted); }

    .engines-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 20px;
      margin-bottom: 24px;
    }
    .engine-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 20px;
      position: relative;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .engine-card:hover {
      border-color: rgba(56, 189, 248, 0.3);
      box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
    }
    .engine-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }
    .engine-header h3 { font-size: 15px; font-weight: 700; color: #fff; }
    .engine-desc {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 16px;
      line-height: 1.4;
      min-height: 34px;
    }
    .metric-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12.5px;
      padding: 6px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    }
    .metric-row:last-child { border-bottom: none; }
    .metric-label { color: var(--text-muted); }
    .metric-val { font-weight: 600; color: #fff; }

    .log-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 18px 20px;
      margin-bottom: 24px;
    }
    .log-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }
    .log-header h4 { font-size: 14px; font-weight: 600; color: var(--accent); }
    .terminal {
      background: #030712;
      border: 1px solid #111827;
      border-radius: 6px;
      padding: 14px;
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
      font-size: 11px;
      color: #cbd5e1;
      max-height: 240px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .pairs-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 16px 20px;
      margin-bottom: 24px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
    }
    .pairs-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
      gap: 8px;
    }
    .pairs-header h4 {
      font-size: 13.5px;
      font-weight: 700;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .pairs-subtitle {
      font-size: 11.5px;
      color: var(--text-muted);
    }
    .pairs-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 14px;
    }
    .pair-item {
      background: #030712;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 12px 16px;
      transition: all 0.2s;
    }
    .pair-item:hover {
      border-color: rgba(56, 189, 248, 0.4);
    }
    .pair-item-primary {
      border-color: rgba(16, 185, 129, 0.45);
      background: linear-gradient(180deg, rgba(16, 185, 129, 0.05) 0%, #030712 100%);
    }
    .pair-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 6px;
    }
    .pair-name {
      font-size: 14px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .pair-price {
      font-size: 15px;
      font-weight: 700;
      font-family: monospace;
      color: #38bdf8;
    }
    .pair-badge-active {
      font-size: 9.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.4);
      text-transform: uppercase;
    }
    .pair-badge-standby {
      font-size: 9.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(148, 163, 184, 0.1);
      color: #94a3b8;
      border: 1px solid #334155;
      text-transform: uppercase;
    }
    .pair-details {
      display: flex;
      flex-direction: column;
      gap: 3px;
      font-size: 11px;
      color: var(--text-muted);
    }

    .footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--text-muted);
      border-top: 1px solid var(--card-border);
      padding-top: 16px;
      flex-wrap: wrap;
      gap: 8px;
    }
    .footer a { color: var(--accent); text-decoration: none; }
    .footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="title-area">
        <h1>⚡ Bybit UTA Discount Buy Suite</h1>
        <p>Three Autonomous $1,000 Capital-Enclosed Engines &bull; Unified Trading Account V5 API</p>
      </div>
      <div class="badges">
        <span class="badge __MODE_BADGE__" id="mode-badge">__MODE_STR__</span>
        <span class="badge badge-active" style="border-color: rgba(16, 185, 129, 0.4); color: #34d399;">PAIR: <span id="hdr-pair">__PRIMARY_PAIR__</span></span>
        <span class="badge badge-active">PORT __PORT__</span>
        <span class="badge badge-event" style="color:#38bdf8; font-family:monospace; border-color:rgba(56,189,248,0.35);">⏱️ <span id="uptime-val">__UPTIME__</span></span>
        <span class="badge badge-socket" id="ws-badge">CONNECTING...</span>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-label">Total Suite Capital</div>
        <div class="stat-value" id="tot-cap">__TOT_CAP__</div>
        <div class="stat-sub">Strict Limit: $3,000.00 USD ($1k/engine)</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Total Realized PnL</div>
        <div class="stat-value" id="tot-pnl" style="color: __PNL_COLOR__;">__TOT_PNL__</div>
        <div class="stat-sub" id="pnl-pct">__PNL_PCT__ on Suite Capital</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Cycles & Win Rate</div>
        <div class="stat-value" id="win-rate">__WIN_RATE__</div>
        <div class="stat-sub" id="cycle-count">__TOT_WINS__ Wins / __TOT_CYCLES__ Closed Cycles</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">System Uptime & Guard</div>
        <div class="stat-value" style="color: var(--green); font-size: 19px; font-family: monospace;" id="uptime-box">__UPTIME__</div>
        <div class="stat-sub">Circuit Breaker: ARMED &bull; Max DD 5.0%</div>
      </div>
    </div>

    <!-- Active Trading Pairs & Market Execution Pulse -->
    <div class="pairs-card">
      <div class="pairs-header">
        <h4>🎯 Active Trading Pairs & Market Execution</h4>
        <span class="pairs-subtitle">Unified Trading Account V5 &bull; Multi-Pair Dynamic Architecture</span>
      </div>
      <div class="pairs-grid">
        <div class="pair-item pair-item-primary">
          <div class="pair-top">
            <span class="pair-name">BTCUSDT <span class="pair-badge-active">ACTIVE PRIMARY</span></span>
            <span class="pair-price" id="pair-price-btc">__BTC_PRICE__</span>
          </div>
          <div class="pair-details">
            <span><b>Engines:</b> Options (1k), Spot Accumulator (1k), Delta-Neutral (1k)</span>
            <span><b>Capital Enclosure:</b> $3,000.00 USD total ($1,000 / engine)</span>
            <span><b>Execution Mode:</b> UTA Margin (BothSides)</span>
          </div>
        </div>

        <div class="pair-item">
          <div class="pair-top">
            <span class="pair-name">ETHUSDT <span class="pair-badge-standby">STANDBY PROFILE</span></span>
            <span class="pair-price" id="pair-price-eth">__ETH_PRICE__</span>
          </div>
          <div class="pair-details">
            <span><b>Engines:</b> Options Underwriting & Spot Discount Ladder</span>
            <span><b>Allocation:</b> Ready for concurrent capital deployment</span>
            <span><b>Execution Mode:</b> UTA Margin Supported</span>
          </div>
        </div>

        <div class="pair-item">
          <div class="pair-top">
            <span class="pair-name">SOLUSDT <span class="pair-badge-standby">STANDBY PROFILE</span></span>
            <span class="pair-price" id="pair-price-sol">__SOL_PRICE__</span>
          </div>
          <div class="pair-details">
            <span><b>Engines:</b> High-Volatility Spot & Linear Accumulator</span>
            <span><b>Allocation:</b> Ready for concurrent capital deployment</span>
            <span><b>Execution Mode:</b> UTA Margin Supported</span>
          </div>
        </div>
      </div>
    </div>

    <div class="engines-grid">
      <!-- Engine 1 -->
      <div class="engine-card">
        <div>
          <div class="engine-header">
            <h3>1. Options Put Underwriter</h3>
            <span class="badge __OPT_BADGE__" id="opt-status">__OPT_STATUS__</span>
          </div>
          <div class="engine-desc">
            Sells 24h 1% OTM Put options. If unexercised, collects ~128% APR premium. If exercised, buys spot BTC at a 1.0% discount.
          </div>
          <div class="metrics-list">
            <div class="metric-row">
              <span class="metric-label">Active Pair</span>
              <span class="metric-val" style="color: var(--accent);">BTC Options (<span id="opt-pair">__PRIMARY_PAIR__</span>)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Allocated Budget</span>
              <span class="metric-val">$1,000.00 (Strict Cap)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Current Capital</span>
              <span class="metric-val" id="opt-cap" style="color: var(--accent);">__OPT_CAP__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Realized PnL</span>
              <span class="metric-val" id="opt-pnl" style="color: __OPT_PNL_COLOR__;">__OPT_PNL__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Win Rate / Cycles</span>
              <span class="metric-val" id="opt-cycles">__OPT_WINS__ / __OPT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Active Strike</span>
              <span class="metric-val" id="opt-strike" style="color: var(--amber);">__OPT_STRIKE__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Locked Premium Yield</span>
              <span class="metric-val" id="opt-premium" style="color: var(--green);">__OPT_PREMIUM__</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Engine 2 -->
      <div class="engine-card">
        <div>
          <div class="engine-header">
            <h3>2. Spot Maker Accumulator</h3>
            <span class="badge __SPOT_BADGE__" id="spot-status">__SPOT_STATUS__</span>
          </div>
          <div class="engine-desc">
            Executes a 3-tranche Post-Only Maker limit ladder at -0.8%, -1.2%, and -1.8% discounts, collecting +0.02% maker rebates.
          </div>
          <div class="metrics-list">
            <div class="metric-row">
              <span class="metric-label">Active Pair</span>
              <span class="metric-val" style="color: var(--accent);"><span id="spot-pair">__PRIMARY_PAIR__</span> (UTA Accumulator)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Allocated Budget</span>
              <span class="metric-val">$1,000.00 (Strict Cap)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Current Capital</span>
              <span class="metric-val" id="spot-cap" style="color: var(--accent);">__SPOT_CAP__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Realized PnL</span>
              <span class="metric-val" id="spot-pnl" style="color: __SPOT_PNL_COLOR__;">__SPOT_PNL__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Win Rate / Cycles</span>
              <span class="metric-val" id="spot-cycles">__SPOT_WINS__ / __SPOT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Active Resting Orders</span>
              <span class="metric-val" id="spot-orders" style="color: var(--accent);">__SPOT_ORDERS__ Tranches</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Nearest Discount Target</span>
              <span class="metric-val" id="spot-nearest" style="color: var(--amber);">__SPOT_NEAREST__</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Engine 3 -->
      <div class="engine-card">
        <div>
          <div class="engine-header">
            <h3>3. Delta-Neutral Harvester</h3>
            <span class="badge __NEUT_BADGE__" id="neut-status">__NEUT_STATUS__</span>
          </div>
          <div class="engine-desc">
            Pre-hedged Basis Spread: Short Perp @ S0, Limit Buy Spot @ S0 &bull; 0.99. Locks 1.0% spread + funding yield with Net Delta = 0.
          </div>
          <div class="metrics-list">
            <div class="metric-row">
              <span class="metric-label">Active Pair</span>
              <span class="metric-val" style="color: var(--accent);"><span id="neut-pair">__PRIMARY_PAIR__</span> (Basis Arbitrage)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Allocated Budget</span>
              <span class="metric-val">$1,000.00 (Strict Cap)</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Current Capital</span>
              <span class="metric-val" id="neut-cap" style="color: var(--accent);">__NEUT_CAP__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Realized PnL</span>
              <span class="metric-val" id="neut-pnl" style="color: __NEUT_PNL_COLOR__;">__NEUT_PNL__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Win Rate / Cycles</span>
              <span class="metric-val" id="neut-cycles">__NEUT_WINS__ / __NEUT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Locked Spread Gain</span>
              <span class="metric-val" id="neut-spread" style="color: var(--green);">__NEUT_SPREAD__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Net Directional Delta</span>
              <span class="metric-val" id="neut-delta" style="color: var(--accent);">Δ = __NEUT_DELTA__</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="log-card">
      <div class="log-header">
        <h4>System Journal & Audit Stream</h4>
        <span style="font-size: 11px; color: var(--text-muted);" id="last-update">Socket streaming</span>
      </div>
      <div class="terminal" id="terminal">__INITIAL_LOGS__</div>
    </div>

    <div class="footer">
      <div>Bybit UTA Discount Buy Suite &bull; Institutional Quantitative Architecture</div>
      <div style="display:flex; gap:16px;">
        <a href="/api/status" target="_blank">JSON Status API</a>
        <a href="/api/ai-summary" target="_blank">AI Markdown Summary</a>
        <a href="/api/logs" target="_blank">System Logs</a>
      </div>
    </div>
  </div>

  <script>
    let ws = null;
    let fallbackTimer = null;

    function applyLiveUpdate(d) {
      if (!d) return;

      const opt = d.options_engine || {};
      const spot = d.spot_engine || {};
      const neut = d.neutral_engine || {};

      const totCap = (opt.current_capital || 1000) + (spot.current_capital || 1000) + (neut.current_capital || 1000);
      const totPnl = (opt.total_realized_pnl || 0) + (spot.total_realized_pnl || 0) + (neut.total_realized_pnl || 0);
      const totCycles = (opt.total_cycles || 0) + (spot.total_cycles || 0) + (neut.total_cycles || 0);
      const totWins = (opt.profitable_cycles || 0) + (spot.profitable_cycles || 0) + (neut.profitable_cycles || 0);
      const winRate = totCycles > 0 ? ((totWins / totCycles) * 100).toFixed(1) + '%' : '0.0%';

      // Overview
      document.getElementById('tot-cap').innerText = '$' + totCap.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      const pnlEl = document.getElementById('tot-pnl');
      pnlEl.innerText = (totPnl >= 0 ? '+' : '') + '$' + totPnl.toFixed(2);
      pnlEl.style.color = totPnl >= 0 ? '#10b981' : '#ef4444';
      document.getElementById('pnl-pct').innerText = (totPnl / 3000.0 * 100).toFixed(2) + '% on Suite Capital';
      document.getElementById('win-rate').innerText = winRate;
      document.getElementById('cycle-count').innerText = `${totWins} Wins / ${totCycles} Closed Cycles`;

      if (d.uptime) {
        const upEl = document.getElementById('uptime-val');
        if (upEl) upEl.innerText = d.uptime;
        const upBox = document.getElementById('uptime-box');
        if (upBox) upBox.innerText = d.uptime;
      }

      // Engine 1
      const optStatEl = document.getElementById('opt-status');
      optStatEl.innerText = opt.status || 'IDLE';
      optStatEl.className = 'badge ' + (opt.status === 'CONTRACT_ACTIVE' ? 'badge-live' : 'badge-event');
      document.getElementById('opt-cap').innerText = '$' + (opt.current_capital || 1000).toFixed(2);
      const optPnlEl = document.getElementById('opt-pnl');
      const optPnlVal = opt.total_realized_pnl || 0;
      optPnlEl.innerText = (optPnlVal >= 0 ? '+' : '') + '$' + optPnlVal.toFixed(2);
      optPnlEl.style.color = optPnlVal >= 0 ? '#10b981' : '#ef4444';
      document.getElementById('opt-cycles').innerText = `${opt.profitable_cycles || 0} / ${opt.total_cycles || 0} wins`;
      document.getElementById('opt-strike').innerText = '$' + (opt.metrics?.current_put_strike || '---');
      document.getElementById('opt-premium').innerText = '+$' + (opt.metrics?.premium_locked_usd || 0).toFixed(2);

      // Engine 2
      const spotStatEl = document.getElementById('spot-status');
      spotStatEl.innerText = spot.status || 'IDLE';
      spotStatEl.className = 'badge ' + (String(spot.status).includes('RESTING') ? 'badge-live' : 'badge-event');
      document.getElementById('spot-cap').innerText = '$' + (spot.current_capital || 1000).toFixed(2);
      const spotPnlEl = document.getElementById('spot-pnl');
      const spotPnlVal = spot.total_realized_pnl || 0;
      spotPnlEl.innerText = (spotPnlVal >= 0 ? '+' : '') + '$' + spotPnlVal.toFixed(2);
      spotPnlEl.style.color = spotPnlVal >= 0 ? '#10b981' : '#ef4444';
      document.getElementById('spot-cycles').innerText = `${spot.profitable_cycles || 0} / ${spot.total_cycles || 0} wins`;
      document.getElementById('spot-orders').innerText = `${(spot.active_orders || []).length} Tranches`;
      document.getElementById('spot-nearest').innerText = '$' + (spot.metrics?.nearest_discount_px || '---');

      // Engine 3
      const neutStatEl = document.getElementById('neut-status');
      neutStatEl.innerText = neut.status || 'IDLE';
      neutStatEl.className = 'badge ' + (String(neut.status).includes('HEDGED') ? 'badge-active' : 'badge-event');
      document.getElementById('neut-cap').innerText = '$' + (neut.current_capital || 1000).toFixed(2);
      const neutPnlEl = document.getElementById('neut-pnl');
      const neutPnlVal = neut.total_realized_pnl || 0;
      neutPnlEl.innerText = (neutPnlVal >= 0 ? '+' : '') + '$' + neutPnlVal.toFixed(2);
      neutPnlEl.style.color = neutPnlVal >= 0 ? '#10b981' : '#ef4444';
      document.getElementById('neut-cycles').innerText = `${neut.profitable_cycles || 0} / ${neut.total_cycles || 0} wins`;
      document.getElementById('neut-spread').innerText = '+$' + (neut.metrics?.locked_spread_usd || 0).toFixed(2);
      document.getElementById('neut-delta').innerText = 'Δ = ' + (neut.metrics?.net_delta || 0.0).toFixed(4);

      // Market Prices & Pairs Live Sync
      if (d.market_prices) {
        if (d.market_prices['BTCUSDT']) {
          const el = document.getElementById('pair-price-btc');
          if (el) el.innerText = '$' + d.market_prices['BTCUSDT'].toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
        if (d.market_prices['ETHUSDT']) {
          const el = document.getElementById('pair-price-eth');
          if (el) el.innerText = '$' + d.market_prices['ETHUSDT'].toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
        if (d.market_prices['SOLUSDT']) {
          const el = document.getElementById('pair-price-sol');
          if (el) el.innerText = '$' + d.market_prices['SOLUSDT'].toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
      }
      const primPair = d.symbol || d.primary_pair || 'BTCUSDT';
      const hdrPairEl = document.getElementById('hdr-pair');
      if (hdrPairEl) hdrPairEl.innerText = primPair;
      const optPairEl = document.getElementById('opt-pair');
      if (optPairEl) optPairEl.innerText = primPair;
      const spotPairEl = document.getElementById('spot-pair');
      if (spotPairEl) spotPairEl.innerText = primPair;
      const neutPairEl = document.getElementById('neut-pair');
      if (neutPairEl) neutPairEl.innerText = primPair;

      // Logs
      if (d.logs && d.logs.length > 0) {
        const term = document.getElementById('terminal');
        if (term) {
          const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 40;
          term.innerText = Array.isArray(d.logs) ? d.logs.join(String.fromCharCode(10)) : String(d.logs);
          if (isScrolledToBottom) {
            term.scrollTop = term.scrollHeight;
          }
        }
      }

      const syncEl = document.getElementById('last-update');
      if (syncEl) syncEl.innerText = 'Synced ' + new Date().toLocaleTimeString();
    }

    function connectWebSocket() {
      const protocol = (location.protocol === 'https:') ? 'wss://' : 'ws://';
      const wsUrl = protocol + location.host + '/ws' + location.search;

      try {
        ws = new WebSocket(wsUrl);
      } catch (e) {
        console.warn('[WS] WebSocket init failed, starting polling:', e);
        startPollingFallback();
        return;
      }

      ws.onopen = () => {
        console.log('[WS] Connected to live Discount Buy telemetry stream');
        const badge = document.getElementById('ws-badge');
        if (badge) {
          badge.textContent = 'LIVE SOCKET';
          badge.className = 'badge badge-socket';
        }
        if (fallbackTimer) {
          clearInterval(fallbackTimer);
          fallbackTimer = null;
        }
      };

      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          applyLiveUpdate(data);
        } catch (err) {
          console.error('[WS] Parse error:', err);
        }
      };

      ws.onerror = (err) => {
        console.warn('[WS] Socket error event:', err);
        try { ws.close(); } catch(e) {}
      };

      ws.onclose = () => {
        console.log('[WS] Disconnected. Reconnecting in 2.5s...');
        const badge = document.getElementById('ws-badge');
        if (badge) {
          badge.textContent = 'RECONNECTING...';
          badge.className = 'badge badge-sim';
        }
        startPollingFallback();
        setTimeout(connectWebSocket, 2500);
      };
    }

    function startPollingFallback() {
      if (fallbackTimer) return;
      fallbackTimer = setInterval(async () => {
        try {
          const res = await fetch('/api/live-status' + location.search);
          if (res.ok) {
            const data = await res.json();
            applyLiveUpdate(data);
            const badge = document.getElementById('ws-badge');
            if (badge && (!ws || ws.readyState !== WebSocket.OPEN)) {
              badge.textContent = 'POLLING (3s)';
              badge.className = 'badge badge-active';
            }
          }
        } catch (e) {}
      }, 3000);
    }

    // Scroll logs to bottom and initiate live socket connection
    const initialTerm = document.getElementById('terminal');
    if (initialTerm) initialTerm.scrollTop = initialTerm.scrollHeight;

    connectWebSocket();
  </script>
</body>
</html>
"""


class DiscountTelemetryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _is_authenticated(self, qs: Dict[str, List[str]]) -> bool:
        auth_cookie = None
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            for c in cookie_header.split(";"):
                parts = c.strip().split("=", 1)
                if len(parts) == 2 and parts[0] in ["discount_token", "discount_session", "amd_token", "hedge_token"]:
                    auth_cookie = parts[1]
                    break

        if auth_cookie and auth_cookie == PASSWORD:
            return True

        req_pass = qs.get("password", [""])[0].strip()
        if req_pass and req_pass == PASSWORD:
            return True

        return False

    def _build_ws_frame(self, payload_bytes: bytes) -> bytes:
        """RFC 6455 unmasked server-to-client text frame."""
        length = len(payload_bytes)
        if length <= 125:
            header = struct.pack("!BB", 0x81, length)
        elif length <= 65535:
            header = struct.pack("!BBH", 0x81, 126, length)
        else:
            header = struct.pack("!BBQ", 0x81, 127, length)
        return header + payload_bytes

    def _handle_ws(self):
        """Handle RFC 6455 WebSocket upgrade and stream live telemetry updates every 1.5s."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400, "Bad Request: Missing Sec-WebSocket-Key")
            return

        guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept_token = base64.b64encode(hashlib.sha1((key + guid).encode("utf-8")).digest()).decode("utf-8")

        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_token}\r\n"
            "\r\n"
        )
        try:
            self.connection.sendall(handshake.encode("utf-8"))
        except Exception:
            return

        sock = self.connection
        sock.setblocking(True)
        sock.settimeout(1.5)

        try:
            while True:
                payload = self._get_live_payload()
                data_bytes = json.dumps(payload).encode("utf-8")
                frame = self._build_ws_frame(data_bytes)
                sock.sendall(frame)

                # Wait 1.5s while responding to ping/close control frames
                start_wait = time.time()
                while time.time() - start_wait < 1.5:
                    r, _, _ = select.select([sock], [], [], 0.3)
                    if r:
                        try:
                            raw = sock.recv(4096)
                            if not raw:
                                return
                            opcode = raw[0] & 0x0F
                            if opcode == 0x8:  # Close
                                sock.sendall(bytes([0x88, 0x00]))
                                return
                            elif opcode == 0x9:  # Ping -> Pong
                                sock.sendall(bytes([0x8A, 0x00]))
                        except (BlockingIOError, InterruptedError):
                            continue
                        except Exception:
                            return
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception:
            pass

    def _get_live_payload(self) -> Dict[str, Any]:
        """Generate real-time state payload for WebSocket or polling."""
        state = read_discount_state()
        logs = get_systemd_logs(35)
        up_sec, up_str = get_uptime_info(state)
        prices = get_live_market_prices()
        state["logs"] = logs
        state["uptime_seconds"] = up_sec
        state["uptime"] = up_str
        state["market_prices"] = prices
        state["primary_pair"] = state.get("symbol", "BTCUSDT")
        state["active_pairs"] = state.get("active_pairs", ["BTCUSDT"])
        return state

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json({"status": "OK", "port": PORT, "time": datetime.now(timezone.utc).isoformat()})
            return

        if parsed.path == "/ws":
            if not self._is_authenticated(qs):
                self.send_error(401, "Unauthorized")
                return
            self._handle_ws()
            return

        if not self._is_authenticated(qs):
            if parsed.path in ["/dashboard", "/"]:
                self._serve_login_page()
            else:
                self._send_json({"error": "Unauthorized. Provide ?password=<SECRET>"}, 401)
            return

        if parsed.path in ["/dashboard", "/"]:
            self._serve_dashboard()
        elif parsed.path in ["/api/status", "/api/live-status"]:
            self._handle_api_status()
        elif parsed.path == "/api/ai-summary":
            self._handle_api_ai_summary()
        elif parsed.path == "/api/logs":
            self._handle_api_logs(qs)
        else:
            self.send_error(404, "Not Found")

    def _handle_api_status(self):
        payload = self._get_live_payload()
        self._send_json(payload)

    def _handle_api_logs(self, qs: Dict[str, List[str]]):
        lines_count = int(qs.get("lines", [40])[0])
        logs = get_systemd_logs(lines_count)
        self._send_json({"logs": logs})

    def _handle_api_ai_summary(self):
        state = read_discount_state()
        opt = state.get("options_engine", {})
        spot = state.get("spot_engine", {})
        neut = state.get("neutral_engine", {})

        tot_cap = opt.get("current_capital", 1000) + spot.get("current_capital", 1000) + neut.get("current_capital", 1000)
        tot_pnl = opt.get("total_realized_pnl", 0) + spot.get("total_realized_pnl", 0) + neut.get("total_realized_pnl", 0)
        up_sec, up_str = get_uptime_info(state)

        md = f"""# Bybit UTA Discount Buy Suite — AI Status Summary
**Timestamp**: {datetime.now(timezone.utc).isoformat()}
**Uptime**: {up_str} ({up_sec}s)
**Mode**: {"SIMULATION / DRY-RUN" if state.get("dry_run", True) else "LIVE EXCHANGE EXECUTION"}
**Total Suite Capital**: ${tot_cap:,.2f} / $3,000.00 | **Realized PnL**: ${tot_pnl:+.2f}

## 1. Options Cash-Secured Put Underwriting
- **Status**: {opt.get('status', 'N/A')}
- **Budget / Balance**: ${opt.get('allocated_capital', 1000):,.2f} / ${opt.get('current_capital', 1000):,.2f}
- **Realized PnL**: ${opt.get('total_realized_pnl', 0):+.2f} ({opt.get('profitable_cycles', 0)}/{opt.get('total_cycles', 0)} wins)
- **Active Strike**: ${opt.get('metrics', {}).get('current_put_strike', '---')}
- **Premium Locked**: ${opt.get('metrics', {}).get('premium_locked_usd', 0):.2f}

## 2. Spot Maker Accumulator (3-Tranche Ladder)
- **Status**: {spot.get('status', 'N/A')}
- **Budget / Balance**: ${spot.get('allocated_capital', 1000):,.2f} / ${spot.get('current_capital', 1000):,.2f}
- **Realized PnL**: ${spot.get('total_realized_pnl', 0):+.2f} ({spot.get('profitable_cycles', 0)}/{spot.get('total_cycles', 0)} wins)
- **Active Resting Orders**: {len(spot.get('active_orders', []))} tranches
- **Nearest Target Discount**: ${spot.get('metrics', {}).get('nearest_discount_px', '---')}

## 3. Delta-Hedged Market-Neutral Harvester
- **Status**: {neut.get('status', 'N/A')}
- **Budget / Balance**: ${neut.get('allocated_capital', 1000):,.2f} / ${neut.get('current_capital', 1000):,.2f}
- **Realized PnL**: ${neut.get('total_realized_pnl', 0):+.2f} ({neut.get('profitable_cycles', 0)}/{neut.get('total_cycles', 0)} wins)
- **Locked Spread**: +${neut.get('metrics', {}).get('locked_spread_usd', 0):.2f}
- **Net Delta**: Δ = {neut.get('metrics', {}).get('net_delta', 0.0):.4f} (Pure Basis Arbitrage)
"""
        body = md.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_login_page(self):
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bybit Discount Buy Suite — Access Authentication</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{
      background: #070b14;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #0d1527;
      border: 1px solid #1e293b;
      padding: 36px 32px;
      border-radius: 12px;
      width: 100%;
      max-width: 380px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      text-align: center;
    }}
    h1 {{ font-size: 18px; margin-bottom: 6px; color: #38bdf8; font-weight: 700; }}
    p {{ font-size: 13px; color: #94a3b8; margin-bottom: 24px; }}
    input {{
      width: 100%;
      padding: 12px 14px;
      background: #070b14;
      border: 1px solid #334155;
      color: #f8fafc;
      border-radius: 6px;
      margin-bottom: 16px;
      box-sizing: border-box;
      font-size: 14px;
    }}
    button {{
      width: 100%;
      padding: 12px;
      background: #0284c7;
      border: none;
      color: #fff;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
    }}
    button:hover {{ background: #0369a1; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>⚡ Bybit UTA Discount Buy Suite</h1>
    <p>Authentication required (Port {PORT})</p>
    <form method="GET" action="/dashboard">
      <input type="password" name="password" placeholder="Enter Telemetry Password" required autofocus>
      <button type="submit">Access Dashboard</button>
    </form>
  </div>
</body>
</html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self):
        state = read_discount_state()
        opt = state.get("options_engine", {})
        spot = state.get("spot_engine", {})
        neut = state.get("neutral_engine", {})

        tot_cap = opt.get("current_capital", 1000) + spot.get("current_capital", 1000) + neut.get("current_capital", 1000)
        tot_pnl = opt.get("total_realized_pnl", 0) + spot.get("total_realized_pnl", 0) + neut.get("total_realized_pnl", 0)
        tot_cycles = opt.get("total_cycles", 0) + spot.get("total_cycles", 0) + neut.get("total_cycles", 0)
        tot_wins = opt.get("profitable_cycles", 0) + spot.get("profitable_cycles", 0) + neut.get("profitable_cycles", 0)
        win_rate = (tot_wins / tot_cycles * 100) if tot_cycles > 0 else 0.0

        pnl_color = "#10b981" if tot_pnl >= 0 else "#ef4444"
        mode_str = "SIMULATION (DRY-RUN)" if state.get("dry_run", True) else "LIVE EXCHANGE"
        mode_badge = "badge-sim" if state.get("dry_run", True) else "badge-live"

        html = DASHBOARD_HTML_TEMPLATE
        html = html.replace("__PORT__", str(PORT))
        html = html.replace("__MODE_STR__", mode_str)
        html = html.replace("__MODE_BADGE__", mode_badge)
        html = html.replace("__TOT_CAP__", f"${tot_cap:,.2f}")
        html = html.replace("__TOT_PNL__", f"${tot_pnl:+.2f}")
        html = html.replace("__PNL_COLOR__", pnl_color)
        html = html.replace("__PNL_PCT__", f"{tot_pnl / 3000.0 * 100:+.2f}%")
        html = html.replace("__WIN_RATE__", f"{win_rate:.1f}%")
        html = html.replace("__TOT_WINS__", str(tot_wins))
        html = html.replace("__TOT_CYCLES__", str(tot_cycles))

        up_sec, up_str = get_uptime_info(state)
        html = html.replace("__UPTIME__", up_str)

        # Market Prices and Active Pairs
        prices = get_live_market_prices()
        primary_pair = state.get("symbol", "BTCUSDT")
        btc_px = f"${prices.get('BTCUSDT', 77420.0):,.2f}"
        eth_px = f"${prices.get('ETHUSDT', 2650.0):,.2f}"
        sol_px = f"${prices.get('SOLUSDT', 145.0):,.2f}"

        html = html.replace("__PRIMARY_PAIR__", primary_pair)
        html = html.replace("__BTC_PRICE__", btc_px)
        html = html.replace("__ETH_PRICE__", eth_px)
        html = html.replace("__SOL_PRICE__", sol_px)

        # Engine 1
        html = html.replace("__OPT_STATUS__", opt.get("status", "IDLE"))
        html = html.replace("__OPT_BADGE__", "badge-live" if opt.get("status") == "CONTRACT_ACTIVE" else "badge-event")
        html = html.replace("__OPT_CAP__", f"${opt.get('current_capital', 1000):,.2f}")
        html = html.replace("__OPT_PNL__", f"${opt.get('total_realized_pnl', 0):+.2f}")
        html = html.replace("__OPT_PNL_COLOR__", "#10b981" if opt.get("total_realized_pnl", 0) >= 0 else "#ef4444")
        html = html.replace("__OPT_WINS__", str(opt.get("profitable_cycles", 0)))
        html = html.replace("__OPT_CYCLES__", str(opt.get("total_cycles", 0)))
        html = html.replace("__OPT_STRIKE__", f"${opt.get('metrics', {}).get('current_put_strike', '---')}")
        html = html.replace("__OPT_PREMIUM__", f"+${opt.get('metrics', {}).get('premium_locked_usd', 0):.2f}")

        # Engine 2
        html = html.replace("__SPOT_STATUS__", spot.get("status", "IDLE"))
        html = html.replace("__SPOT_BADGE__", "badge-live" if "RESTING" in str(spot.get("status")) else "badge-event")
        html = html.replace("__SPOT_CAP__", f"${spot.get('current_capital', 1000):,.2f}")
        html = html.replace("__SPOT_PNL__", f"${spot.get('total_realized_pnl', 0):+.2f}")
        html = html.replace("__SPOT_PNL_COLOR__", "#10b981" if spot.get("total_realized_pnl", 0) >= 0 else "#ef4444")
        html = html.replace("__SPOT_WINS__", str(spot.get("profitable_cycles", 0)))
        html = html.replace("__SPOT_CYCLES__", str(spot.get("total_cycles", 0)))
        html = html.replace("__SPOT_ORDERS__", str(len(spot.get("active_orders", []))))
        html = html.replace("__SPOT_NEAREST__", f"${spot.get('metrics', {}).get('nearest_discount_px', '---')}")

        # Engine 3
        html = html.replace("__NEUT_STATUS__", neut.get("status", "IDLE"))
        html = html.replace("__NEUT_BADGE__", "badge-active" if "HEDGED" in str(neut.get("status")) else "badge-event")
        html = html.replace("__NEUT_CAP__", f"${neut.get('current_capital', 1000):,.2f}")
        html = html.replace("__NEUT_PNL__", f"${neut.get('total_realized_pnl', 0):+.2f}")
        html = html.replace("__NEUT_PNL_COLOR__", "#10b981" if neut.get("total_realized_pnl", 0) >= 0 else "#ef4444")
        html = html.replace("__NEUT_WINS__", str(neut.get("profitable_cycles", 0)))
        html = html.replace("__NEUT_CYCLES__", str(neut.get("total_cycles", 0)))
        html = html.replace("__NEUT_SPREAD__", f"+${neut.get('metrics', {}).get('locked_spread_usd', 0):.2f}")
        html = html.replace("__NEUT_DELTA__", f"{neut.get('metrics', {}).get('net_delta', 0.0):.4f}")

        # Embed initial logs
        initial_logs = "\n".join(get_systemd_logs(35))
        html = html.replace("__INITIAL_LOGS__", initial_logs)

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"discount_token={PASSWORD}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, DiscountTelemetryHandler)
    print(f"==============================================================================")
    print(f"⚡ Bybit UTA Discount Buy Suite WebSocket Telemetry Server ACTIVE")
    print(f"  • Port: {PORT}")
    print(f"  • WebSocket Stream: ws://localhost:{PORT}/ws?password={PASSWORD}")
    print(f"  • Dashboard URL: http://localhost:{PORT}/dashboard?password={PASSWORD}")
    print(f"  • AI Summary API: http://localhost:{PORT}/api/ai-summary?password={PASSWORD}")
    print(f"==============================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down discount telemetry server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
