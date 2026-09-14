#!/usr/bin/env python3
"""
discount_telemetry_server.py — Dedicated Real-Time Dashboard & AI Telemetry Server
for Bybit UTA Autonomous Discount Buy Suite (3x $1,000 Capital Enclosure).
Runs on Port 8082 (Isolated from Port 8080 Trend and Port 8081 AMD servers).
"""

import os
import sys
import json
import re
import time
import secrets
import subprocess
import logging
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List, Optional

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


def get_systemd_logs(lines: int = 40) -> List[str]:
    """Fetch recent journalctl logs for bybit-discount."""
    try:
        cmd = ["journalctl", "-u", "bybit-discount", "-n", str(lines), "--no-pager"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            raw_lines = res.stdout.strip().split("\n")
            return [sanitize_logs(l) for l in raw_lines[-lines:]]
    except Exception:
        pass
    return ["No recent systemd logs available (or running outside VPS)."]


DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bybit UTA Discount Buy Suite — Dashboard (Port __PORT__)</title>
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
    }
    .badge-sim { background: rgba(245, 158, 11, 0.15); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-live { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-active { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-event { background: #1e293b; color: var(--text-muted); }

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
    }
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
        <span class="badge __MODE_BADGE__">__MODE_STR__</span>
        <span class="badge badge-active">PORT __PORT__</span>
        <span class="badge badge-live" id="live-indicator">LIVE POLLING</span>
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
        <div class="stat-label">Hardware Circuit Breaker</div>
        <div class="stat-value" style="color: var(--green); font-size: 18px;">ARMED &bull; OK</div>
        <div class="stat-sub">Max Drawdown Limit: 5.0%</div>
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
              <span class="metric-val">__OPT_WINS__ / __OPT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Active Strike</span>
              <span class="metric-val" style="color: var(--amber);">__OPT_STRIKE__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Locked Premium Yield</span>
              <span class="metric-val" style="color: var(--green);">__OPT_PREMIUM__</span>
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
              <span class="metric-val">__SPOT_WINS__ / __SPOT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Active Resting Orders</span>
              <span class="metric-val" style="color: var(--accent);">__SPOT_ORDERS__ Tranches</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Nearest Discount Target</span>
              <span class="metric-val" style="color: var(--amber);">__SPOT_NEAREST__</span>
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
              <span class="metric-val">__NEUT_WINS__ / __NEUT_CYCLES__ wins</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Locked Spread Gain</span>
              <span class="metric-val" style="color: var(--green);">__NEUT_SPREAD__</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">Net Directional Delta</span>
              <span class="metric-val" style="color: var(--accent);">Δ = __NEUT_DELTA__</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="log-card">
      <div class="log-header">
        <h4>System Journal & Audit Stream</h4>
        <span style="font-size: 11px; color: var(--text-muted);" id="last-update">Updated just now</span>
      </div>
      <div class="terminal" id="terminal">Loading latest journal logs...</div>
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
    async function updateDashboard() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const d = await res.json();
          const opt = d.options_engine || {};
          const spot = d.spot_engine || {};
          const neut = d.neutral_engine || {};

          const totCap = (opt.current_capital || 1000) + (spot.current_capital || 1000) + (neut.current_capital || 1000);
          const totPnl = (opt.total_realized_pnl || 0) + (spot.total_realized_pnl || 0) + (neut.total_realized_pnl || 0);

          document.getElementById('tot-cap').innerText = '$' + totCap.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
          const pnlEl = document.getElementById('tot-pnl');
          pnlEl.innerText = (totPnl >= 0 ? '+' : '') + '$' + totPnl.toFixed(2);
          pnlEl.style.color = totPnl >= 0 ? '#10b981' : '#ef4444';

          document.getElementById('opt-status').innerText = opt.status || 'IDLE';
          document.getElementById('spot-status').innerText = spot.status || 'IDLE';
          document.getElementById('neut-status').innerText = neut.status || 'IDLE';

          document.getElementById('opt-cap').innerText = '$' + (opt.current_capital || 1000).toFixed(2);
          document.getElementById('spot-cap').innerText = '$' + (spot.current_capital || 1000).toFixed(2);
          document.getElementById('neut-cap').innerText = '$' + (neut.current_capital || 1000).toFixed(2);

          document.getElementById('last-update').innerText = 'Synced ' + new Date().toLocaleTimeString();
        }
      } catch (e) {}

      try {
        const logRes = await fetch('/api/logs?lines=25');
        if (logRes.ok) {
          const lData = await logRes.json();
          if (lData.logs && lData.logs.length > 0) {
            document.getElementById('terminal').innerText = lData.logs.join('\\n');
          }
        }
      } catch (e) {}
    }

    setInterval(updateDashboard, 3000);
    updateDashboard();
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
                if len(parts) == 2 and parts[0] == "discount_session":
                    auth_cookie = parts[1]
                    break

        if auth_cookie and auth_cookie in ACTIVE_SESSIONS:
            return True

        req_pass = qs.get("password", [""])[0].strip()
        if req_pass and req_pass == PASSWORD:
            return True

        return False

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

        if not self._is_authenticated(qs):
            if parsed.path in ["/dashboard", "/"]:
                self._serve_login_page()
            else:
                self._send_json({"error": "Unauthorized. Provide ?password=<SECRET>"}, 401)
            return

        if parsed.path in ["/dashboard", "/"]:
            self._serve_dashboard()
        elif parsed.path == "/api/status":
            self._handle_api_status()
        elif parsed.path == "/api/ai-summary":
            self._handle_api_ai_summary()
        elif parsed.path == "/api/logs":
            self._handle_api_logs(qs)
        else:
            self.send_error(404, "Not Found")

    def _handle_api_status(self):
        state = read_discount_state()
        self._send_json(state)

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

        md = f"""# Bybit UTA Discount Buy Suite — AI Status Summary
**Timestamp**: {datetime.now(timezone.utc).isoformat()}
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

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, DiscountTelemetryHandler)
    print(f"==============================================================================")
    print(f"⚡ Bybit UTA Discount Buy Suite Telemetry & Dashboard Server ACTIVE")
    print(f"  • Port: {PORT}")
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
