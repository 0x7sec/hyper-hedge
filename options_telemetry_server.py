#!/usr/bin/env python3
"""
options_telemetry_server.py — Dedicated Real-Time WebSocket Dashboard & AI Telemetry Server
for Bybit UTA Delta-Neutral Options Harvester ($1,000 USD Capital Enclosure).
Runs on Port 8083 with RFC 6455 WebSocket streaming (zero page reload, instant live sync).
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

PORT = int(os.environ.get("OPTIONS_TELEMETRY_PORT", 8083))
PASSWORD = os.environ.get("OPTIONS_TELEMETRY_PASSWORD", os.environ.get("TELEMETRY_PASSWORD", "hedge_bot_sec_2026")).strip()

if not PASSWORD:
    PASSWORD = secrets.token_hex(16)
    print(f"\n[SECURITY] No OPTIONS_TELEMETRY_PASSWORD set! Generated random password:")
    print(f"[SECURITY] >>> {PASSWORD} <<<\n")

STATE_FILE = os.path.join(BASE_DIR, "options_bot_state.json")
LOG_FILE = os.path.join(BASE_DIR, "options_bot.log")

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


def read_options_state() -> Dict[str, Any]:
    """Read the latest atomic state from options_bot_state.json."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "version": "1.0.0",
        "timestamp": time.time(),
        "state": "STATE_0_SCANNING",
        "cycle_id": 1,
        "allocated_capital": 1000.0,
        "current_capital": 1000.0,
        "peak_capital": 1000.0,
        "total_realized_pnl": 0.0,
        "total_theta_harvested": 0.0,
        "total_cycles_completed": 0,
        "profitable_cycles": 0,
        "loss_cycles": 0,
        "win_rate_pct": 0.0,
        "circuit_breaker_active": False,
        "last_status_message": "Awaiting options engine initialization...",
        "active_put": None,
        "active_call": None,
        "breakevens": {},
        "cone": {},
        "initial_net_premium": 0.0,
        "ddh_perp_position": 0.0,
        "ddh_rebalance_count": 0,
        "ddh_realized_pnl": 0.0,
    }


_LOGS_CACHE: Dict[int, List[str]] = {}
_LAST_LOGS_FETCH = 0.0


def get_system_logs(lines: int = 40) -> List[str]:
    """Fetch recent logs from options_bot.log or journalctl."""
    global _LOGS_CACHE, _LAST_LOGS_FETCH
    now = time.time()
    if (now - _LAST_LOGS_FETCH < 2.0) and lines in _LOGS_CACHE:
        return _LOGS_CACHE[lines]

    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
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
            cmd = ["journalctl", "-u", "bybit-options-harvester", "-n", str(lines), "--no-pager"]
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

    fallback = ["Options Harvester daemon active. Awaiting engine events..."]
    _LOGS_CACHE[lines] = fallback
    _LAST_LOGS_FETCH = now
    return fallback


_PRICE_CACHE = {}
_LAST_PRICE_FETCH = 0


def get_live_market_prices() -> Dict[str, float]:
    """Fetch live BTCUSDT mark price."""
    global _PRICE_CACHE, _LAST_PRICE_FETCH
    now = time.time()
    if now - _LAST_PRICE_FETCH < 4 and _PRICE_CACHE:
        return _PRICE_CACHE
    try:
        import requests
        testnet = os.environ.get("TESTNET", "false").lower() in ("true", "1", "yes")
        domain = "api-testnet.bybit.com" if testnet else "api.bybit.com"
        r = requests.get(f"https://{domain}/v5/market/tickers?category=linear&symbol=BTCUSDT", timeout=3).json()
        if r.get("retCode") == 0 and r.get("result", {}).get("list"):
            item = r["result"]["list"][0]
            _PRICE_CACHE = {"BTCUSDT": float(item.get("markPrice") or item.get("lastPrice") or 75500.0)}
            _LAST_PRICE_FETCH = now
            return _PRICE_CACHE
    except Exception:
        pass
    if not _PRICE_CACHE:
        _PRICE_CACHE = {"BTCUSDT": 75500.0}
    return _PRICE_CACHE


def get_uptime_info(state: Dict[str, Any]) -> Tuple[int, str]:
    """Compute uptime seconds and formatted string."""
    up_sec = 0
    start_ts = state.get("cycle_start_time", 0.0)
    if start_ts > 0:
        up_sec = max(0, int(time.time() - start_ts))

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
  <title>Bybit UTA Delta-Neutral Options Harvester — Live Dashboard (Port __PORT__)</title>
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
      --purple: #a855f7;
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
    }
    .badge-sim { background: rgba(245, 158, 11, 0.15); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-live { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }
    .badge-uptime {
      background: rgba(168, 85, 247, 0.15);
      color: #c084fc;
      border: 1px solid rgba(168, 85, 247, 0.3);
      font-weight: 600;
    }
    .badge-live { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-active { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-socket {
      background: #064e3b;
      color: #6ee7b7;
      border: 1px solid #059669;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .dot {
      width: 6px;
      height: 6px;
      background: #10b981;
      border-radius: 50%;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0% { opacity: 0.4; } 50% { opacity: 1; } 100% { opacity: 0.4; } }

    .grid-metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .card-label {
      font-size: 12px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 6px;
    }
    .card-value {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .card-sub {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 4px;
    }

    /* Range Cone Visualizer */
    .cone-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 24px;
    }
    .cone-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }
    .cone-bar-container {
      position: relative;
      background: #1e293b;
      height: 28px;
      border-radius: 6px;
      overflow: hidden;
      margin: 20px 0 10px 0;
      border: 1px solid #334155;
    }
    .safe-zone {
      position: absolute;
      top: 0;
      bottom: 0;
      background: rgba(16, 185, 129, 0.25);
      border-left: 2px solid var(--green);
      border-right: 2px solid var(--green);
    }
    .spot-marker {
      position: absolute;
      top: -4px;
      bottom: -4px;
      width: 4px;
      background: #fbbf24;
      box-shadow: 0 0 8px #fbbf24;
      z-index: 5;
    }
    .cone-labels {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--text-muted);
    }

    /* Delta Meter */
    .delta-meter {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-top: 10px;
    }
    .meter-track {
      flex: 1;
      height: 10px;
      background: #1e293b;
      border-radius: 5px;
      position: relative;
    }
    .meter-center {
      position: absolute;
      left: 50%;
      top: 0;
      bottom: 0;
      width: 2px;
      background: #475569;
    }
    .meter-pin {
      position: absolute;
      top: -3px;
      width: 6px;
      height: 16px;
      background: var(--accent);
      border-radius: 2px;
      transform: translateX(-50%);
    }

    /* Table */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th {
      text-align: left;
      padding: 10px 14px;
      background: #090e1a;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 11px;
    }
    td {
      padding: 12px 14px;
      border-bottom: 1px solid #141f36;
    }
    tr:last-child td { border-bottom: none; }

    /* Terminal Logs */
    .terminal-card {
      background: #030712;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px;
    }
    .terminal-header {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
    }
    .terminal-body {
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 12px;
      color: #94a3b8;
      max-height: 220px;
      overflow-y: auto;
      white-space: pre-wrap;
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="title-area">
        <h1>⚡ Bybit UTA Delta-Neutral Options Harvester</h1>
        <p>Short Strangle Volatility Risk Premium (VRP) & Dynamic Delta Hedging (DDH) Engine</p>
      </div>
      <div class="badges">
        <span class="badge __MODE_BADGE__" id="mode-badge">__MODE_STR__</span>
        <span class="badge badge-active" id="engine-state">__STATE__</span>
        <span class="badge badge-uptime" id="uptime-badge">⏱️ UPTIME: __UPTIME__</span>
        <span class="badge badge-socket" id="ws-badge">
          <span class="dot"></span> LIVE WS (8083)
        </span>
      </div>
    </div>

    <!-- Capital & Greeks Metrics Grid -->
    <div class="grid-metrics">
      <div class="card">
        <div class="card-label">Isolated Capital Enclosure</div>
        <div class="card-value" style="color: var(--text);" id="val-capital">__CAPITAL__</div>
        <div class="card-sub">Strict $1,000 USD isolated ceiling</div>
      </div>

      <div class="card">
        <div class="card-label">Total Realized PnL</div>
        <div class="card-value" style="color: __PNL_COLOR__;" id="val-pnl">__PNL__</div>
        <div class="card-sub" id="val-pnl-sub">__WIN_RATE__ Win Rate (__WINS__/__CYCLES__ cycles)</div>
      </div>

      <div class="card">
        <div class="card-label">Statistical Edge (VRP)</div>
        <div class="card-value" style="color: #38bdf8;" id="val-pop">~80% Prob. Win</div>
        <div class="card-sub">Selling Options (Short Strangle vs Buying)</div>
      </div>

      <div class="card">
        <div class="card-label">Net Delta (Δ) Exposure</div>
        <div class="card-value" style="color: var(--accent);" id="val-delta">__DELTA__</div>
        <div class="card-sub">Target: 0.0000 | Tolerance: ±0.10</div>
      </div>

      <div class="card">
        <div class="card-label">Daily Theta (Θ) Decay</div>
        <div class="card-value" style="color: var(--green);" id="val-theta">__THETA__</div>
        <div class="card-sub">Harvest Target: 70% collected premium</div>
      </div>
    </div>

    <!-- Strangle Range Cone & Breakeven Visualizer -->
    <div class="cone-card">
      <div class="cone-header">
        <div>
          <span style="font-weight: 700; font-size: 15px; color: var(--text);">Strangle Profit Range Cone</span>
          <span style="font-size: 12px; color: var(--text-muted); margin-left: 8px;">(BTCUSDT Mark: <span id="spot-px-text" style="color: #fbbf24; font-weight: bold;">__SPOT_PX__</span>)</span>
        </div>
        <div style="font-size: 12px; color: var(--text-muted);">
          Range Width: <span id="range-cushion-text" style="color: var(--accent); font-weight: 600;">__RANGE_CUSHION__</span>
        </div>
      </div>

      <div class="cone-bar-container">
        <!-- Safe profit zone (Lower BE to Upper BE) -->
        <div class="safe-zone" id="cone-safe-zone" style="left: 15%; width: 70%;"></div>
        <!-- Yellow marker for current spot price -->
        <div class="spot-marker" id="cone-spot-marker" style="left: 50%;"></div>
      </div>

      <div class="cone-labels">
        <div>Lower BE: <strong style="color: var(--green);" id="lbl-lower-be">__LOWER_BE__</strong> (Put: <span id="lbl-put-strike">__PUT_STRIKE__</span>)</div>
        <div>Delta Tolerance: <strong style="color: var(--accent);">±0.10 Band</strong></div>
        <div>Upper BE: <strong style="color: var(--green);" id="lbl-upper-be">__UPPER_BE__</strong> (Call: <span id="lbl-call-strike">__CALL_STRIKE__</span>)</div>
      </div>

      <div class="delta-meter">
        <span style="font-size: 11px; color: var(--text-muted);">-0.20Δ</span>
        <div class="meter-track">
          <div class="meter-center"></div>
          <div class="meter-pin" id="delta-pin" style="left: 50%;"></div>
        </div>
        <span style="font-size: 11px; color: var(--text-muted);">+0.20Δ</span>
      </div>
    </div>

    <!-- Active Strangle Legs Table -->
    <div class="card" style="margin-bottom: 24px; padding: 0; overflow: hidden;">
      <div style="padding: 16px 20px; border-bottom: 1px solid var(--card-border); font-weight: 600; font-size: 14px;">
        Active Strangle Portfolio Legs & Risk Parameters
      </div>
      <table>
        <thead>
          <tr>
            <th>Leg & Side (Action)</th>
            <th>Contract Symbol</th>
            <th>Strike</th>
            <th>Size</th>
            <th>Entry Mark</th>
            <th>Current Mark</th>
            <th>Delta (Δ)</th>
            <th>2.0x Stop Loss</th>
            <th>Decay Progress</th>
          </tr>
        </thead>
        <tbody id="legs-table-body">
          __LEGS_ROWS__
        </tbody>
      </table>
    </div>

    <!-- Terminal Logs -->
    <div class="terminal-card">
      <div class="terminal-header">
        <span>Real-Time Options Harvester Daemon Logs</span>
        <span style="color: var(--text-muted);" id="last-update-time">Streaming</span>
      </div>
      <div class="terminal-body" id="terminal">__INITIAL_LOGS__</div>
    </div>
  </div>

  <script>
    let ws;
    function connectWebSocket() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${location.host}/ws${location.search}`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        const badge = document.getElementById('ws-badge');
        if (badge) {
          badge.className = 'badge badge-socket';
          badge.innerHTML = '<span class="dot"></span> LIVE WS (8083)';
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          applyLiveUpdate(data);
        } catch (e) {
          console.error("WS Parse Error:", e);
        }
      };

      ws.onclose = () => {
        const badge = document.getElementById('ws-badge');
        if (badge) {
          badge.className = 'badge badge-sim';
          badge.textContent = 'RECONNECTING...';
        }
        setTimeout(connectWebSocket, 2000);
      };

      ws.onerror = () => {
        if (ws) ws.close();
      };
    }

    function applyLiveUpdate(d) {
      if (!d) return;

      // Update Mode Badge
      const modeEl = document.getElementById('mode-badge');
      if (modeEl) {
        const isLive = d.dry_run === false;
        modeEl.textContent = isLive ? 'LIVE BYBIT UTA' : 'SIMULATION (DRY-RUN)';
        modeEl.className = `badge ${isLive ? 'badge-live' : 'badge-sim'}`;
      }

      // Update Uptime Badge
      const upEl = document.getElementById('uptime-badge');
      if (upEl && d.uptime) {
        upEl.textContent = `⏱️ UPTIME: ${d.uptime}`;
      }

      // Update State Badge
      const stEl = document.getElementById('engine-state');
      if (stEl) {
        stEl.textContent = d.state || 'UNKNOWN';
        stEl.className = d.state === 'STATE_2_HARVESTING' ? 'badge badge-live' : 'badge badge-active';
      }

      // Capital & PnL
      const capEl = document.getElementById('val-capital');
      if (capEl) capEl.textContent = `$${(d.current_capital || 1000).toLocaleString('en-US', {minimumFractionDigits: 2})}`;

      const pnlEl = document.getElementById('val-pnl');
      if (pnlEl) {
        const pnl = d.total_realized_pnl || 0.0;
        pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
        pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
      }

      const pnlSub = document.getElementById('val-pnl-sub');
      if (pnlSub) {
        pnlSub.textContent = `${(d.win_rate_pct || 0).toFixed(1)}% Win Rate (${d.profitable_cycles || 0}/${d.total_cycles_completed || 0} cycles)`;
      }

      // Net Delta
      const greeks = d.greeks || {};
      const normDelta = greeks.normalized_net_delta || 0.0;
      const deltaEl = document.getElementById('val-delta');
      if (deltaEl) deltaEl.textContent = `${normDelta >= 0 ? '+' : ''}${normDelta.toFixed(4)}`;

      // Theta
      const thetaEl = document.getElementById('val-theta');
      if (thetaEl) thetaEl.textContent = `+$${(greeks.net_theta_usd_per_day || 0).toFixed(2)}/day`;

      // Spot Price
      const spot = d.spot_price || 0.0;
      const spotEl = document.getElementById('spot-px-text');
      if (spotEl) spotEl.textContent = `$${spot.toLocaleString('en-US', {minimumFractionDigits: 1})}`;

      // Cone Breakevens
      const be = d.breakevens || {};
      if (be.lower_breakeven) {
        document.getElementById('lbl-lower-be').textContent = `$${be.lower_breakeven.toLocaleString('en-US', {maximumFractionDigits: 0})}`;
        document.getElementById('lbl-upper-be').textContent = `$${be.upper_breakeven.toLocaleString('en-US', {maximumFractionDigits: 0})}`;
        document.getElementById('lbl-put-strike').textContent = `$${(be.put_strike || 0).toLocaleString('en-US', {maximumFractionDigits: 0})}`;
        document.getElementById('lbl-call-strike').textContent = `$${(be.call_strike || 0).toLocaleString('en-US', {maximumFractionDigits: 0})}`;
        document.getElementById('range-cushion-text').textContent = `${(be.range_width_pct || 0).toFixed(2)}% Cushion`;

        // Position spot marker within cone
        const minRange = be.lower_breakeven * 0.98;
        const maxRange = be.upper_breakeven * 1.02;
        const totalSpan = maxRange - minRange;
        if (totalSpan > 0) {
          const spotPct = Math.max(2, Math.min(98, ((spot - minRange) / totalSpan) * 100));
          const marker = document.getElementById('cone-spot-marker');
          if (marker) marker.style.left = `${spotPct}%`;

          const lowerPct = Math.max(2, Math.min(98, ((be.lower_breakeven - minRange) / totalSpan) * 100));
          const upperPct = Math.max(2, Math.min(98, ((be.upper_breakeven - minRange) / totalSpan) * 100));
          const safeZone = document.getElementById('cone-safe-zone');
          if (safeZone) {
            safeZone.style.left = `${lowerPct}%`;
            safeZone.style.width = `${upperPct - lowerPct}%`;
          }
        }
      }

      // Delta Pin (-0.20 to +0.20)
      const pinPct = Math.max(5, Math.min(95, ((normDelta + 0.20) / 0.40) * 100));
      const deltaPin = document.getElementById('delta-pin');
      if (deltaPin) deltaPin.style.left = `${pinPct}%`;

      // Statistical Edge (PoP)
      const popEl = document.getElementById('val-pop');
      if (popEl && d.probability_of_profit) {
        popEl.textContent = `${(d.probability_of_profit * 100).toFixed(1)}% Prob. Win`;
      }

      // Active Legs Table
      const p = d.active_put;
      const c = d.active_call;
      const tbody = document.getElementById('legs-table-body');
      if (tbody && p && c) {
        tbody.innerHTML = `
          <tr>
            <td><strong style="color: var(--green);">SHORT PUT (SELL)</strong></td>
            <td><code>${p.symbol}</code></td>
            <td>$${(p.strike || 0).toLocaleString()}</td>
            <td>${p.qty || 0.01}</td>
            <td>$${(p.entry_price || 0).toFixed(2)}</td>
            <td>$${(p.mark_price || 0).toFixed(2)}</td>
            <td><span style="color: var(--green);">${(p.delta || 0).toFixed(4)}</span></td>
            <td><span style="color: var(--red);">$${((p.entry_price || 0) * 2.0).toFixed(2)}</span></td>
            <td><strong>${(((p.entry_price - p.mark_price) / p.entry_price) * 100).toFixed(1)}%</strong></td>
          </tr>
          <tr>
            <td><strong style="color: var(--accent);">SHORT CALL (SELL)</strong></td>
            <td><code>${c.symbol}</code></td>
            <td>$${(c.strike || 0).toLocaleString()}</td>
            <td>${c.qty || 0.01}</td>
            <td>$${(c.entry_price || 0).toFixed(2)}</td>
            <td>$${(c.mark_price || 0).toFixed(2)}</td>
            <td><span style="color: var(--accent);">${(c.delta || 0).toFixed(4)}</span></td>
            <td><span style="color: var(--red);">$${((c.entry_price || 0) * 2.0).toFixed(2)}</span></td>
            <td><strong>${(((c.entry_price - c.mark_price) / c.entry_price) * 100).toFixed(1)}%</strong></td>
          </tr>
        `;
      } else if (tbody && (!p || !c)) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No active strangle deployed. Scanning Bybit surface for optimal ~15-delta pairs...</td></tr>`;
      }

      // Logs
      if (d.logs && Array.isArray(d.logs)) {
        const term = document.getElementById('terminal');
        if (term) {
          term.textContent = d.logs.join('\\n');
          term.scrollTop = term.scrollHeight;
        }
      }
    }

    connectWebSocket();
  </script>
</body>
</html>
"""


class OptionsTelemetryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _is_authenticated(self, qs: Dict[str, List[str]]) -> bool:
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            for c in cookie_header.split(";"):
                parts = c.strip().split("=", 1)
                if len(parts) == 2 and parts[0] in ["options_token", "options_session", "discount_token", "hedge_token"]:
                    if parts[1] == PASSWORD:
                        return True

        req_pass = qs.get("password", [""])[0].strip()
        if req_pass and req_pass == PASSWORD:
            return True
        return False

    def _build_ws_frame(self, payload_bytes: bytes) -> bytes:
        length = len(payload_bytes)
        if length <= 125:
            header = struct.pack("!BB", 0x81, length)
        elif length <= 65535:
            header = struct.pack("!BBH", 0x81, 126, length)
        else:
            header = struct.pack("!BBQ", 0x81, 127, length)
        return header + payload_bytes

    def _handle_ws(self):
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

                start_wait = time.time()
                while time.time() - start_wait < 1.5:
                    r, _, _ = select.select([sock], [], [], 0.3)
                    if r:
                        try:
                            raw = sock.recv(4096)
                            if not raw:
                                return
                            opcode = raw[0] & 0x0F
                            if opcode == 0x8:
                                sock.sendall(bytes([0x88, 0x00]))
                                return
                            elif opcode == 0x9:
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
        state = read_options_state()
        logs = get_system_logs(35)
        up_sec, up_str = get_uptime_info(state)
        prices = get_live_market_prices()
        state["logs"] = logs
        state["uptime_seconds"] = up_sec
        state["uptime"] = up_str
        state["spot_price"] = prices.get("BTCUSDT", 75500.0)
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
            self._send_json(self._get_live_payload())
        elif parsed.path == "/api/ai-summary":
            self._handle_api_ai_summary()
        elif parsed.path == "/api/logs":
            lines_count = int(qs.get("lines", [40])[0])
            self._send_json({"logs": get_system_logs(lines_count)})
        else:
            self.send_error(404, "Not Found")

    def _handle_api_ai_summary(self):
        state = read_options_state()
        p = state.get("active_put")
        c = state.get("active_call")
        be = state.get("breakevens", {})
        up_sec, up_str = get_uptime_info(state)
        pop_val = float(state.get("probability_of_profit", 0.80)) * 100.0

        md = f"""# Bybit UTA Delta-Neutral Options Harvester — AI Status Summary
**Timestamp**: {datetime.now(timezone.utc).isoformat()}
**Uptime**: {up_str} ({up_sec}s)
**Status**: {state.get('state', 'UNKNOWN')}
**Strategy Action**: SELLING OPTIONS (Short Strangle: Short Put + Short Call)
**Statistical Edge**: ~80% Probability of Profit ({pop_val:.1f}%) via Volatility Risk Premium (VRP) & Positive Theta (Θ)
**Capital Enclosure**: ${state.get('current_capital', 1000):,.2f} / ${state.get('allocated_capital', 1000):,.2f} USD strict
**Realized PnL**: ${state.get('total_realized_pnl', 0):+.2f} ({state.get('profitable_cycles', 0)}/{state.get('total_cycles_completed', 0)} wins, {state.get('win_rate_pct', 0):.1f}%)

## Strangle Portfolio Configuration
- **Active Short Put (SELL)**: {p.get('symbol', 'None') if p else 'None'} (Strike: ${p.get('strike', 0) if p else 0:,.0f}, Entry: ${p.get('entry_price', 0) if p else 0:.2f})
- **Active Short Call (SELL)**: {c.get('symbol', 'None') if c else 'None'} (Strike: ${c.get('strike', 0) if c else 0:,.0f}, Entry: ${c.get('entry_price', 0) if c else 0:.2f})
- **Profit Range Cushion**: ${be.get('lower_breakeven', 0):,.0f} to ${be.get('upper_breakeven', 0):,.0f} ({be.get('range_width_pct', 0):.2f}%)
- **Dynamic Delta Hedge**: Perp Qty: {state.get('ddh_perp_position', 0):.3f} | Rebalances: {state.get('ddh_rebalance_count', 0)} | PnL: ${state.get('ddh_realized_pnl', 0):+.2f}
- **Message**: {state.get('last_status_message', 'N/A')}
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
  <title>Options Harvester — Authentication</title>
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
    <h1>⚡ Bybit Options Harvester</h1>
    <p>Password authentication required (Port {PORT})</p>
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
        state = read_options_state()
        p = state.get("active_put")
        c = state.get("active_call")
        be = state.get("breakevens", {})
        is_live = not state.get("dry_run", True)
        mode_str = "LIVE BYBIT UTA" if is_live else "SIMULATION (DRY-RUN)"
        mode_badge = "badge-live" if is_live else "badge-sim"
        up_sec, up_str = get_uptime_info(state)
        tot_pnl = float(state.get("total_realized_pnl", 0.0))
        pnl_color = "var(--green)" if tot_pnl >= 0 else "var(--red)"
        prices = get_live_market_prices()
        spot = prices.get("BTCUSDT", 75500.0)

        html = DASHBOARD_HTML_TEMPLATE
        html = html.replace("__PORT__", str(PORT))
        html = html.replace("__MODE_STR__", mode_str)
        html = html.replace("__MODE_BADGE__", mode_badge)
        html = html.replace("__UPTIME__", up_str)
        html = html.replace("__STATE__", state.get("state", "STATE_0_SCANNING"))
        html = html.replace("__CAPITAL__", f"${state.get('current_capital', 1000):,.2f}")
        html = html.replace("__PNL__", f"{'+' if tot_pnl >= 0 else ''}${tot_pnl:.2f}")
        html = html.replace("__PNL_COLOR__", pnl_color)
        html = html.replace("__WIN_RATE__", f"{state.get('win_rate_pct', 0):.1f}%")
        html = html.replace("__WINS__", str(state.get("profitable_cycles", 0)))
        html = html.replace("__CYCLES__", str(state.get("total_cycles_completed", 0)))
        html = html.replace("__DELTA__", "+0.0000")
        html = html.replace("__THETA__", "+$4.50/day")
        html = html.replace("__SPOT_PX__", f"${spot:,.1f}")
        html = html.replace("__RANGE_CUSHION__", f"{be.get('range_width_pct', 8.5):.2f}%")
        html = html.replace("__LOWER_BE__", f"${be.get('lower_breakeven', 72000):,.0f}")
        html = html.replace("__UPPER_BE__", f"${be.get('upper_breakeven', 79000):,.0f}")
        html = html.replace("__PUT_STRIKE__", f"${be.get('put_strike', 73000):,.0f}")
        html = html.replace("__CALL_STRIKE__", f"${be.get('call_strike', 79000):,.0f}")

        if p and c:
          rows = f"""
            <tr>
              <td><strong style="color: var(--green);">SHORT PUT (SELL)</strong></td>
              <td><code>{p['symbol']}</code></td>
              <td>${p.get('strike', 0):,.0f}</td>
              <td>{p.get('qty', 0.01)}</td>
              <td>${p.get('entry_price', 0):.2f}</td>
              <td>${p.get('mark_price', 0):.2f}</td>
              <td><span style="color: var(--green);">{p.get('delta', -0.15):.4f}</span></td>
              <td><span style="color: var(--red);">${p.get('entry_price', 0)*2.0:.2f}</span></td>
              <td><strong>70% Harvest</strong></td>
            </tr>
            <tr>
              <td><strong style="color: var(--accent);">SHORT CALL (SELL)</strong></td>
              <td><code>{c['symbol']}</code></td>
              <td>${c.get('strike', 0):,.0f}</td>
              <td>{c.get('qty', 0.01)}</td>
              <td>${c.get('entry_price', 0):.2f}</td>
              <td>${c.get('mark_price', 0):.2f}</td>
              <td><span style="color: var(--accent);">{c.get('delta', 0.15):.4f}</span></td>
              <td><span style="color: var(--red);">${c.get('entry_price', 0)*2.0:.2f}</span></td>
              <td><strong>70% Harvest</strong></td>
            </tr>
          """
        else:
          rows = """<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No active strangle deployed. Scanning Bybit surface for optimal ~15-delta pairs...</td></tr>"""

        html = html.replace("__LEGS_ROWS__", rows)
        html = html.replace("__INITIAL_LOGS__", "\n".join(get_system_logs(35)))

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"options_token={PASSWORD}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, OptionsTelemetryHandler)
    print(f"==============================================================================")
    print(f"⚡ Bybit UTA Delta-Neutral Options Harvester WebSocket Telemetry Server ACTIVE")
    print(f"  • Port: {PORT}")
    print(f"  • WebSocket Stream: ws://localhost:{PORT}/ws?password={PASSWORD}")
    print(f"  • Dashboard URL: http://localhost:{PORT}/dashboard?password={PASSWORD}")
    print(f"  • AI Summary API: http://localhost:{PORT}/api/ai-summary?password={PASSWORD}")
    print(f"==============================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down options telemetry server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
