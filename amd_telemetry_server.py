#!/usr/bin/env python3
"""
Dedicated Real-Time Web Dashboard & AI Telemetry Server for AMD + FVG Bot.
Runs on Port 8081 (isolated from the existing port 8080 telemetry server).
Features full RFC 6455 WebSocket live streaming (zero page reload), JSON API, and /api/ai-summary.
"""

import os
import sys
import json
import csv
import re
import time
import struct
import base64
import hashlib
import select
import secrets
import subprocess
import urllib.request
import logging
from datetime import datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List, Optional, Tuple

try:
    from pybit.unified_trading import HTTP as BybitHTTP
except ImportError:
    BybitHTTP = None

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

# Configuration & Isolated Paths
PORT = int(os.environ.get("AMD_TELEMETRY_PORT", 8081))
PASSWORD = os.environ.get("AMD_TELEMETRY_PASSWORD", os.environ.get("TELEMETRY_PASSWORD", "")).strip()

if not PASSWORD:
    PASSWORD = secrets.token_hex(16)
    print(f"\n[SECURITY] No AMD_TELEMETRY_PASSWORD set! Generated random password:")
    print(f"[SECURITY] >>> {PASSWORD} <<<\n")

STATE_FILE = os.path.join(BASE_DIR, "amd_bot_state.json")
TRADES_FILE = os.path.join(BASE_DIR, "amd_trades.csv")
LOG_FILE = os.path.join(BASE_DIR, "amd_bot.log")

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


def fmt_price(val: Any) -> str:
    if val is None:
        return "---"
    try:
        v = float(val)
    except (ValueError, TypeError):
        return str(val)
    if v == 0.0:
        return "0.00"
    abs_v = abs(v)
    if abs_v < 0.001:
        return f"{v:,.6f}"
    elif abs_v < 0.1:
        return f"{v:,.5f}"
    elif abs_v < 1.0:
        return f"{v:,.4f}"
    elif abs_v < 10.0:
        return f"{v:,.3f}"
    else:
        return f"{v:,.2f}"


# Caching for Bybit Live Account Data
BYBIT_ACC_CACHE = {}
BYBIT_ACC_CACHE_TTL = 8.0  # seconds

TICKERS_CACHE = {}
TICKERS_CACHE_TTL = 10.0  # seconds


def fetch_bybit_account_and_trades() -> Dict[str, Any]:
    """Fetch live Unified Account balance, equity, and closed position PnL history from Bybit."""
    now = time.time()
    if "acc" in BYBIT_ACC_CACHE:
        cached_ts, cached_data = BYBIT_ACC_CACHE["acc"]
        if now - cached_ts < BYBIT_ACC_CACHE_TTL:
            return cached_data

    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")
    testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]

    res_data = {
        "equity": 0.0,
        "wallet_balance": 0.0,
        "available_balance": 0.0,
        "total_realized_pnl": 0.0,
        "by_symbol": {},
        "trades_count": 0,
        "recent_trades": [],
    }

    if not BybitHTTP or not api_key or not api_secret:
        return res_data

    try:
        session = BybitHTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
        wb = session.get_wallet_balance(accountType="UNIFIED")
        if wb.get("retCode") == 0:
            acc = wb.get("result", {}).get("list", [{}])[0]
            res_data["equity"] = float(acc.get("totalEquity", 0) or 0)
            res_data["wallet_balance"] = float(acc.get("totalWalletBalance", 0) or 0)
            res_data["available_balance"] = float(acc.get("totalAvailableBalance", 0) or 0)

        cpnl = session.get_closed_pnl(category="linear", limit=100)
        if cpnl.get("retCode") == 0:
            trades = cpnl.get("result", {}).get("list", [])
            total_pnl = sum(float(t.get("closedPnl", 0) or 0) for t in trades)
            by_sym = {}
            formatted = []
            for t in trades:
                sym = t.get("symbol", "")
                pnl = float(t.get("closedPnl", 0) or 0)
                by_sym[sym] = by_sym.get(sym, 0.0) + pnl
                ts_ms = int(t.get("updatedTime", 0) or 0)
                side_raw = t.get("side", "")
                qty_val = float(t.get("closedSize", 0) or t.get("qty", 0) or 0)
                entry_px = float(t.get("avgEntryPrice", 0) or 0)
                exit_px = float(t.get("avgExitPrice", 0) or 0)

                formatted.append({
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts_ms else "",
                    "symbol": sym,
                    "side": side_raw,
                    "qty": qty_val,
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "closed_pnl": pnl,
                })
            res_data["total_realized_pnl"] = round(total_pnl, 2)
            res_data["by_symbol"] = {k: round(v, 2) for k, v in by_sym.items()}
            res_data["trades_count"] = len(trades)
            res_data["recent_trades"] = formatted

        BYBIT_ACC_CACHE["acc"] = (now, res_data)
    except Exception:
        pass

    return res_data


def fetch_24h_tickers() -> Dict[str, float]:
    """Fetch 24h price change percentages for linear symbols."""
    now = time.time()
    if "tickers" in TICKERS_CACHE:
        cached_ts, cached_data = TICKERS_CACHE["tickers"]
        if now - cached_ts < TICKERS_CACHE_TTL:
            return cached_data

    testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
    domain = "api-testnet.bybit.com" if testnet else "api.bybit.com"
    url = f"https://{domain}/v5/market/tickers?category=linear"
    res = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BybitAMDBot/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            d = json.loads(resp.read().decode())
            for item in d.get("result", {}).get("list", []):
                sym = item.get("symbol")
                pct = item.get("price24hPcnt")
                if sym and pct is not None:
                    try:
                        res[sym] = round(float(pct) * 100.0, 2)
                    except Exception:
                        pass
        TICKERS_CACHE["tickers"] = (now, res)
    except Exception:
        pass
    return res


def read_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "session_start_iso" in data:
                    try:
                        start_dt = datetime.fromisoformat(data["session_start_iso"])
                        data["uptime_seconds"] = max(0, int((datetime.now() - start_dt).total_seconds()))
                    except Exception:
                        pass
                return data
        except Exception:
            pass
    return {
        "timestamp": datetime.now().isoformat(),
        "account": {"equity": 0.0, "wallet_balance": 0.0, "total_realized_pnl": 0.0, "closed_trades_count": 0},
        "symbols": {},
        "recent_trades": [],
    }


def read_trades(limit: int = 100) -> List[Dict[str, Any]]:
    trades = []
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) >= 10:
                        trades.append({
                            "timestamp": row[0],
                            "symbol": row[1],
                            "side": row[2],
                            "entry_price": float(row[3]),
                            "exit_price": float(row[4]),
                            "qty": float(row[5]),
                            "gross_pnl": float(row[6]),
                            "fees": float(row[7]),
                            "net_pnl": float(row[8]),
                            "exit_reason": row[9],
                            "duration_minutes": float(row[10]) if len(row) > 10 else 0.0,
                            "macro_bias": row[11] if len(row) > 11 else "",
                        })
        except Exception:
            pass
    return trades[-limit:]


def get_coin_icon(symbol: str, size: int = 20) -> str:
    s = symbol.upper()
    if "BTC" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#f7931a"/><path d="M15.5 10.5c.3-1.5-.9-2.3-2.5-2.8l.5-2-1.2-.3-.5 2c-.3-.1-.7-.2-1-.2l.5-2-1.2-.3-.5 2-2.5-.6-.4 1.4s.9.2.9.2c.5.1.6.5.6.7l-.6 2.4c0 0 .1 0 .1 0l-.1 0-.8 3.3c-.1.2-.2.4-.6.3 0 0-.9-.2-.9-.2l-.6 1.5 2.4.6c.4.1.7.2 1.1.2l-.5 2.1 1.2.3.5-2c.3.1.7.2 1 .2l-.5 2 1.2.3.5-2.1c2.1.4 3.7.2 4.4-1.7.5-1.5 0-2.4-1.1-3 .8-.2 1.4-.7 1.6-1.8zm-2.8 4c-.4 1.5-3 .7-3.8.5l.7-2.8c.8.2 3.5.6 3.1 2.3zm.4-4c-.3 1.4-2.5.7-3.2.5l.6-2.5c.7.2 2.9.5 2.6 2z" fill="#fff"/></svg>'
    elif "ETH" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#627eea"/><path d="M12 4v6.6l5.6 2.5L12 4z" fill="#fff" fill-opacity=".6"/><path d="M12 4L6.4 13.1l5.6-2.5V4z" fill="#fff"/><path d="M12 17.5v4.5l5.6-7.8L12 17.5z" fill="#fff" fill-opacity=".6"/><path d="M12 22v-4.5L6.4 14.2L12 22z" fill="#fff"/><path d="M12 16.5l5.6-3.3L12 10.6v5.9z" fill="#fff" fill-opacity=".2"/><path d="M6.4 13.2l5.6 3.3v-5.9l-5.6 2.6z" fill="#fff" fill-opacity=".6"/></svg>'
    elif "SOL" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#0f172a" stroke="#14F195" stroke-width="1.5"/><path d="M7 16h8.5l1.5-1.5H8.5L7 16zm0-7h8.5l1.5-1.5H8.5L7 9zm10 3.5H8.5L7 14h8.5l1.5-1.5z" fill="#14F195"/></svg>'
    elif "DOGE" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#c2a633"/><path d="M8 7h4c2.8 0 5 2.2 5 5s-2.2 5-5 5H8V7zm2.5 7.5h1.5c1.4 0 2.5-1.1 2.5-2.5s-1.1-2.5-2.5-2.5h-1.5v5z" fill="#fff"/><path d="M7 11.5h10v1H7z" fill="#fff"/></svg>'
    return f'<span style="display:inline-block; width:{size}px; height:{size}px; border-radius:50%; background:#334155; color:#cbd5e1; font-size:10px; line-height:{size}px; text-align:center;">●</span>'


class AMDTelemetryHandler(BaseHTTPRequestHandler):
    def _is_authenticated(self) -> bool:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "password" in qs and qs["password"][0] == PASSWORD:
            return True
        cookie_header = self.headers.get("Cookie", "")
        if f"amd_token={PASSWORD}" in cookie_header:
            return True
        return False

    def _send_unauthorized(self):
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = f"""<!DOCTYPE html>
        <html><head><title>AMD Telemetry Login</title>
        <style>body {{ background:#090d16; color:#f8fafc; font-family:sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
        .box {{ background:#111827; padding:30px; border-radius:12px; border:1px solid #1f2937; text-align:center; max-width:350px; width:100%; }}
        input {{ width:90%; padding:10px; margin:15px 0; border-radius:6px; border:1px solid #374151; background:#1f2937; color:#fff; }}
        button {{ width:98%; padding:10px; border-radius:6px; border:none; background:#3b82f6; color:#fff; font-weight:bold; cursor:pointer; }}
        </style></head><body>
        <div class="box">
          <h2>⚡ AMD + FVG Bot</h2>
          <p style="color:#94a3b8; font-size:14px;">Enter password to access dashboard</p>
          <form method="GET" action="/dashboard">
            <input type="password" name="password" placeholder="Password" required autofocus />
            <button type="submit">Access Dashboard</button>
          </form>
        </div></body></html>"""
        self.wfile.write(html.encode("utf-8"))

    # ==========================================================================
    # WEBSOCKET STREAMING (RFC 6455)
    # ==========================================================================

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
        """Handle RFC 6455 WebSocket upgrade and stream live telemetry updates every 2s."""
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
        sock.settimeout(2.0)

        try:
            while True:
                payload = self._get_live_telemetry_payload()
                data_bytes = json.dumps(payload).encode("utf-8")
                frame = self._build_ws_frame(data_bytes)
                sock.sendall(frame)

                start_wait = time.time()
                while time.time() - start_wait < 2.0:
                    r, _, _ = select.select([sock], [], [], 0.4)
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

    # ==========================================================================
    # DYNAMIC PAYLOAD BUILDER FOR SOCKET INJECTION
    # ==========================================================================

    def _get_live_telemetry_payload(self) -> Dict[str, Any]:
        state = read_state()
        bybit_acc = fetch_bybit_account_and_trades()
        tickers_24h = fetch_24h_tickers()

        symbols = state.get("symbols", {})
        bot_trades = read_trades(limit=50)
        exch_trades = bybit_acc.get("recent_trades", [])[:50]

        up_sec = state.get("uptime_seconds", 0)
        up_h = up_sec // 3600
        up_m = (up_sec % 3600) // 60
        uptime_str = f"{up_h}h {up_m}m"

        equity = bybit_acc.get("equity") or state.get("account", {}).get("equity", 0.0)
        wallet = bybit_acc.get("wallet_balance") or state.get("account", {}).get("wallet_balance", 0.0)
        avail = bybit_acc.get("available_balance") or state.get("account", {}).get("available_balance", 0.0)
        tot_realized_pnl = bybit_acc.get("total_realized_pnl") or state.get("account", {}).get("total_realized_pnl", 0.0)

        # Stats Bar HTML
        stats_html = f"""
        <div class="stat-card">
          <div class="stat-label">Total Equity</div>
          <div class="stat-val">${equity:,.2f}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Wallet Balance</div>
          <div class="stat-val">${wallet:,.2f}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Available Margin</div>
          <div class="stat-val">${avail:,.2f}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Realized PnL</div>
          <div class="stat-val" style="color:{'#10b981' if tot_realized_pnl>=0 else '#ef4444'};">
            ${tot_realized_pnl:+,.2f}
          </div>
        </div>
        """

        # Pair Cards HTML
        cards_html = []
        for sym, d in symbols.items():
            icon = get_coin_icon(sym, size=24)
            phase = d.get("phase", "ACCUMULATING")
            bias = d.get("macro_bias", "NEUTRAL")

            bias_color = "#10b981" if bias == "BULLISH" else ("#ef4444" if bias == "BEARISH" else "#94a3b8")
            phase_bg = "#1e293b"
            phase_color = "#94a3b8"
            if phase == "SWEEP_DETECTED":
                phase_bg = "#854d0e"
                phase_color = "#fef08a"
            elif phase == "FVG_PENDING":
                phase_bg = "#581c87"
                phase_color = "#e9d5ff"
            elif phase == "IN_POSITION":
                phase_bg = "#064e3b"
                phase_color = "#6ee7b7"

            mp_str = fmt_price(d.get("mark_price"))
            bsl_str = fmt_price(d.get("bsl"))
            ssl_str = fmt_price(d.get("ssl"))
            ema_str = fmt_price(d.get("macro_200_ema"))

            t24 = tickers_24h.get(sym, 0.0)
            t24_col = "#10b981" if t24 >= 0 else "#ef4444"
            t24_badge = f'<span style="font-size:11px; font-weight:700; color:{t24_col};">{t24:+,.2f}% (24h)</span>'

            pos_info_html = ""
            if phase == "IN_POSITION":
                pnl = d.get("floating_pnl", 0.0)
                pnl_pct = d.get("floating_pnl_pct", 0.0)
                pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
                pos_info_html = f"""
                <div style="margin-top:12px; padding:10px; background:#0f172a; border-radius:8px; border:1px solid #1e293b;">
                  <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="font-weight:700; color:{'#10b981' if d.get('position_side')=='LONG' else '#ef4444'}">{d.get('position_side')} {d.get('position_size')}</span>
                    <span style="font-weight:700; color:{pnl_color};">${pnl:+,.2f} ({pnl_pct:+,.2f}%)</span>
                  </div>
                  <div style="font-size:12px; color:#94a3b8;">
                    Entry: ${fmt_price(d.get('entry_price'))} | SL: ${fmt_price(d.get('stop_loss'))} | TP: ${fmt_price(d.get('take_profit'))}
                  </div>
                </div>"""
            elif phase == "FVG_PENDING":
                pos_info_html = f"""
                <div style="margin-top:12px; padding:10px; background:#2e1065; border-radius:8px; border:1px solid #581c87;">
                  <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="font-weight:700; color:#e9d5ff;">LIMIT {d.get('pending_side')} ARMED</span>
                    <span style="font-weight:700; color:#cbd5e1;">@ ${fmt_price(d.get('pending_ep'))}</span>
                  </div>
                  <div style="font-size:12px; color:#c084fc;">
                    SL: ${fmt_price(d.get('pending_sl'))} | TP: ${fmt_price(d.get('pending_tp'))} (Expires 60m)
                  </div>
                </div>"""

            card = f"""
            <div class="card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <div style="display:flex; align-items:center; gap:10px;">
                  {icon}
                  <div>
                    <span style="font-size:16px; font-weight:800; color:#f8fafc;">{sym}</span>
                    <div style="font-size:12px; color:#94a3b8;">${mp_str} &nbsp; {t24_badge}</div>
                  </div>
                </div>
                <div style="text-align:right;">
                  <span class="badge" style="background:{phase_bg}; color:{phase_color};">{phase}</span>
                  <div style="margin-top:4px;"><span class="badge" style="background:#0f172a; border:1px solid {bias_color}; color:{bias_color}; font-size:10px;">4H {bias}</span></div>
                </div>
              </div>

              <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:12px; background:#0b0f19; padding:8px; border-radius:6px;">
                <div><span style="color:#64748b;">4H 200-EMA:</span> <span style="color:#cbd5e1; font-weight:600;">${ema_str}</span></div>
                <div><span style="color:#64748b;">15m ATR:</span> <span style="color:#cbd5e1; font-weight:600;">${fmt_price(d.get('current_atr'))}</span></div>
                <div><span style="color:#64748b;">BSL (High):</span> <span style="color:#cbd5e1; font-weight:600;">${bsl_str}</span></div>
                <div><span style="color:#64748b;">SSL (Low):</span> <span style="color:#cbd5e1; font-weight:600;">${ssl_str}</span></div>
              </div>
              {pos_info_html}
            </div>"""
            cards_html.append(card)

        # Internal Bot Trades Rows
        bot_rows = []
        for t in reversed(bot_trades):
            net = t.get("net_pnl", 0.0)
            net_col = "#10b981" if net >= 0 else "#ef4444"
            side_col = "#10b981" if t.get("side") == "LONG" else "#ef4444"
            row = f"""
            <tr>
              <td>{t.get('timestamp')}</td>
              <td><strong>{t.get('symbol')}</strong></td>
              <td style="color:{side_col}; font-weight:700;">{t.get('side')}</td>
              <td>${fmt_price(t.get('entry_price'))}</td>
              <td>${fmt_price(t.get('exit_price'))}</td>
              <td>{t.get('qty')}</td>
              <td style="color:{net_col}; font-weight:700;">${net:+,.2f}</td>
              <td><span class="badge" style="background:#1e293b; color:#cbd5e1;">{t.get('exit_reason')}</span></td>
              <td>{t.get('duration_minutes', 0):.1f}m</td>
            </tr>"""
            bot_rows.append(row)
        bot_tbody = "".join(bot_rows) if bot_rows else '<tr><td colspan="9" style="text-align:center; color:#64748b; padding:20px;">No internal AMD trades recorded yet.</td></tr>'

        # Exchange Trades Rows
        exch_rows = []
        for t in exch_trades:
            pnl = t.get("closed_pnl", 0.0)
            pnl_col = "#10b981" if pnl >= 0 else "#ef4444"
            side_col = "#10b981" if "buy" in t.get("side", "").lower() else "#ef4444"
            row = f"""
            <tr>
              <td>{t.get('timestamp')}</td>
              <td><strong>{t.get('symbol')}</strong></td>
              <td style="color:{side_col}; font-weight:700;">{t.get('side')}</td>
              <td>${fmt_price(t.get('entry_price'))}</td>
              <td>${fmt_price(t.get('exit_price'))}</td>
              <td>{t.get('qty')}</td>
              <td style="color:{pnl_col}; font-weight:700;">${pnl:+,.2f}</td>
              <td><span class="badge" style="background:#1e293b; color:#cbd5e1;">Exchange Fill</span></td>
              <td>---</td>
            </tr>"""
            exch_rows.append(row)
        exch_tbody = "".join(exch_rows) if exch_rows else '<tr><td colspan="9" style="text-align:center; color:#64748b; padding:20px;">No exchange trades found.</td></tr>'

        # Logs
        log_lines = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    log_lines = [line.rstrip("\r\n") for line in f][-35:]
            except Exception:
                pass
        sanitized_logs = "\n".join(sanitize_logs(l) for l in log_lines)

        return {
            "uptime": uptime_str,
            "stats_html": stats_html,
            "markets_html": "".join(cards_html),
            "bot_trades_html": bot_tbody,
            "exch_trades_html": exch_tbody,
            "logs": sanitized_logs,
        }

    # ==========================================================================
    # HTTP ROUTING
    # ==========================================================================

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if not self._is_authenticated():
            self._send_unauthorized()
            return

        if path == "/ws":
            self._handle_ws()
        elif path == "/api/live-status":
            self._handle_api_live_status()
        elif path == "/api/status":
            self._handle_api_status()
        elif path == "/api/ai-summary":
            self._handle_api_ai_summary()
        elif path == "/api/logs":
            self._handle_api_logs(parsed.query)
        elif path == "/api/trades":
            self._handle_api_trades()
        elif path in ["", "/", "/dashboard"]:
            self._handle_dashboard()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_api_live_status(self):
        payload = self._get_live_telemetry_payload()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _handle_api_status(self):
        state = read_state()
        bybit_acc = fetch_bybit_account_and_trades()
        tickers = fetch_24h_tickers()

        state["bybit_account"] = bybit_acc
        state["tickers_24h"] = tickers

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(state, indent=2).encode("utf-8"))

    def _handle_api_trades(self):
        trades = read_trades(limit=200)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(trades, indent=2).encode("utf-8"))

    def _handle_api_logs(self, query_str: str):
        qs = parse_qs(query_str)
        n_lines = int(qs.get("lines", [50])[0])
        lines = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = [line.rstrip("\r\n") for line in f][-n_lines:]
            except Exception:
                pass
        sanitized = [sanitize_logs(l) for l in lines]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("\n".join(sanitized).encode("utf-8"))

    def _handle_api_ai_summary(self):
        state = read_state()
        bybit_acc = fetch_bybit_account_and_trades()
        symbols_data = state.get("symbols", {})

        up_sec = state.get("uptime_seconds", 0)
        up_h = up_sec // 3600
        up_m = (up_sec % 3600) // 60

        testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
        env_label = "Bybit Testnet" if testnet else "Bybit Mainnet"

        equity = bybit_acc.get("equity") or state.get("account", {}).get("equity", 0.0)
        wallet = bybit_acc.get("wallet_balance") or state.get("account", {}).get("wallet_balance", 0.0)
        realized_pnl = bybit_acc.get("total_realized_pnl") or state.get("account", {}).get("total_realized_pnl", 0.0)

        md = []
        md.append("# Bybit AMD + FVG Bot: AI Operational Summary")
        md.append(f"**Strategy**: Macro-Filtered AMD + FVG (15m Execution + 4H 200-EMA Bias)")
        md.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')} | **Uptime**: {up_h}h {up_m}m | **Environment**: {env_label}\n")
        md.append("## 1. Unified Account & Capital Health")
        md.append(f"- **Equity**: ${equity:,.2f} USDT | **Wallet Balance**: ${wallet:,.2f} USDT")
        md.append(f"- **Realized PnL**: ${realized_pnl:+,.2f} USDT | **Closed Trades Count**: {bybit_acc.get('trades_count', 0)}\n")

        md.append("## 2. Active Market Engines (15m AMD + 4H Bias)")
        for sym, d in symbols_data.items():
            md.append(f"### {sym}")
            md.append(f"- **Phase**: `{d.get('phase', 'ACCUMULATING')}` | **Mark Price**: ${fmt_price(d.get('mark_price'))}")
            md.append(f"- **4H Macro Bias**: `{d.get('macro_bias')}` (200-EMA: ${fmt_price(d.get('macro_200_ema'))})")
            md.append(f"- **15m Range**: BSL: ${fmt_price(d.get('bsl'))} | SSL: ${fmt_price(d.get('ssl'))} (Width: {d.get('range_width_pct', 0)}%)")
            if d.get("phase") == "FVG_PENDING":
                md.append(f"- **Pending FVG Limit**: {d.get('pending_side')} @ ${fmt_price(d.get('pending_ep'))} | SL: ${fmt_price(d.get('pending_sl'))} | TP: ${fmt_price(d.get('pending_tp'))}")
            elif d.get("phase") == "IN_POSITION":
                md.append(f"- **Active Position**: {d.get('position_side')} {d.get('position_size')} @ ${fmt_price(d.get('entry_price'))}")
                md.append(f"- **Floating PnL**: ${d.get('floating_pnl', 0.0):+,.2f} ({d.get('floating_pnl_pct', 0.0):+,.2f}%)")
                md.append(f"- **Structural SL**: ${fmt_price(d.get('stop_loss'))} | **Target TP**: ${fmt_price(d.get('take_profit'))}")
            md.append("")

        md.append("## 3. Recent System Logs (Sanitized)")
        lines = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = [line.rstrip("\r\n") for line in f][-10:]
            except Exception:
                pass
        for l in lines:
            md.append(f"`{sanitize_logs(l)}`")

        output = "\n".join(md)
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.end_headers()
        self.wfile.write(output.encode("utf-8"))

    # ==========================================================================
    # DASHBOARD HTML (WITH LIVE WEBSOCKET LISTENER)
    # ==========================================================================

    def _handle_dashboard(self):
        payload = self._get_live_telemetry_payload()

        testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
        env_badge = "TESTNET ACTIVE" if testnet else "MAINNET (LIVE CAPITAL)"
        env_bg = "#064e3b" if testnet else "#b91c1c"
        env_color = "#6ee7b7" if testnet else "#fecaca"

        host_only = self.headers.get('Host', 'localhost').split(':')[0]

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bybit AMD + FVG Bot Telemetry</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #070b13;
      --card-bg: #0e1626;
      --card-border: #1a2638;
      --accent-blue: #3b82f6;
      --accent-green: #10b981;
      --accent-purple: #8b5cf6;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
    }}
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:var(--bg); color:var(--text-main); font-family:'Inter', sans-serif; padding:20px; line-height:1.5; }}
    .container {{ max-width:1280px; margin:0 auto; }}
    .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:15px; border-bottom:1px solid var(--card-border); padding-bottom:15px; }}
    .title-group {{ display:flex; align-items:center; gap:12px; }}
    .title {{ font-size:22px; font-weight:800; color:#fff; display:flex; align-items:center; gap:8px; }}
    .pulse-dot {{ width:10px; height:10px; border-radius:50%; background:#10b981; box-shadow:0 0 10px #10b981; animation:pulse 2s infinite; }}
    @keyframes pulse {{ 0%{{opacity:1;}} 50%{{opacity:0.4;}} 100%{{opacity:1;}} }}
    .badge {{ font-size:11px; font-weight:700; padding:3px 8px; border-radius:4px; text-transform:uppercase; letter-spacing:0.5px; display:inline-block; }}
    .stats-bar {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:24px; }}
    .stat-card {{ background:var(--card-bg); border:1px solid var(--card-border); border-radius:10px; padding:14px; }}
    .stat-label {{ font-size:12px; color:var(--text-muted); font-weight:600; text-transform:uppercase; }}
    .stat-val {{ font-size:20px; font-weight:800; color:#fff; margin-top:4px; }}
    .grid-cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom:24px; }}
    .card {{ background:var(--card-bg); border:1px solid var(--card-border); border-radius:12px; padding:16px; transition:border-color 0.15s ease; }}
    .card:hover {{ border-color:#3b82f6; }}
    .section-title {{ font-size:16px; font-weight:700; color:#cbd5e1; margin-bottom:12px; display:flex; align-items:center; justify-content:space-between; }}
    .tab-btn {{ background:#1e293b; color:#94a3b8; border:1px solid #334155; padding:6px 14px; border-radius:6px; cursor:pointer; font-weight:600; font-size:12px; transition:all 0.15s ease; }}
    .tab-btn.active {{ background:#3b82f6; color:#fff; border-color:#3b82f6; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; text-align:left; }}
    th {{ background:#0b111e; color:var(--text-muted); padding:10px 12px; font-weight:600; border-bottom:1px solid var(--card-border); }}
    td {{ padding:10px 12px; border-bottom:1px solid #141f32; color:#cbd5e1; }}
    .terminal {{ background:#05080f; border:1px solid var(--card-border); border-radius:10px; padding:14px; font-family:'JetBrains Mono', monospace; font-size:12px; height:240px; overflow-y:auto; color:#a5b4fc; white-space:pre-wrap; line-height:1.6; }}
    .ws-indicator {{ display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:700; color:#10b981; padding:2px 8px; border-radius:12px; background:#064e3b; border:1px solid #059669; }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="title-group">
        <span class="pulse-dot"></span>
        <div class="title">[AMD] Bybit Macro AMD + FVG Bot Telemetry</div>
        <span class="badge" style="background:{env_bg}; color:{env_color}; border:1px solid {env_color};">{env_badge}</span>
        <span class="badge" style="background:#1e1b4b; color:#c7d2fe; border:1px solid #4338ca;">PORT 8081</span>
        <span class="ws-indicator" id="ws-badge">⚡ LIVE SOCKET</span>
      </div>
      <div style="font-size:13px; color:#94a3b8;">
        Uptime: <strong style="color:#f8fafc;" id="uptime-val">{payload['uptime']}</strong> | 
        <a href="http://{host_only}:8080/dashboard?password={PASSWORD}" target="_blank" style="color:#a78bfa; text-decoration:none; margin-left:8px; font-weight:600;">📊 Trend Bot (Port 8080)</a> | 
        <a href="/api/ai-summary?password={PASSWORD}" target="_blank" style="color:#60a5fa; text-decoration:none; margin-left:8px; font-weight:600;">🤖 AI Summary</a>
      </div>
    </div>

    <!-- Stats Bar (Updated live via Socket) -->
    <div class="stats-bar" id="stats-container">
      {payload['stats_html']}
    </div>

    <!-- Active AMD Strategy Pair Cards (Updated live via Socket) -->
    <div class="section-title">
      <span>Market Engines (15m AMD + 4H 200-EMA Bias)</span>
      <span style="font-size:12px; color:#64748b;">BTC, DOGE, SOL, ETH</span>
    </div>
    <div class="grid-cards" id="markets-container">
      {payload['markets_html']}
    </div>

    <!-- Closed Trades Section with Tabs -->
    <div style="background:var(--card-bg); border:1px solid var(--card-border); border-radius:12px; padding:16px; margin-bottom:24px;">
      <div class="section-title">
        <span>Trade History & Audit Ledger</span>
        <div style="display:flex; gap:8px;">
          <button class="tab-btn active" id="btn-bot-trades" onclick="switchTab('bot')">AMD Bot Log (amd_trades.csv)</button>
          <button class="tab-btn" id="btn-exch-trades" onclick="switchTab('exch')">Bybit Realized PnL</button>
        </div>
      </div>

      <!-- Bot Trades Table -->
      <div id="tab-bot" style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Qty</th>
              <th>Net PnL</th>
              <th>Reason</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody id="bot-trades-body">
            {payload['bot_trades_html']}
          </tbody>
        </table>
      </div>

      <!-- Exchange Trades Table -->
      <div id="tab-exch" style="overflow-x:auto; display:none;">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Qty</th>
              <th>Closed PnL</th>
              <th>Type</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody id="exch-trades-body">
            {payload['exch_trades_html']}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Live Sanitized Terminal Logs (Updated live via Socket) -->
    <div class="section-title">Live Engine Logs (amd_bot.log)</div>
    <div class="terminal" id="term">{payload['logs']}</div>
  </div>

  <script>
    function switchTab(tab) {{
      const bBot = document.getElementById('btn-bot-trades');
      const bExch = document.getElementById('btn-exch-trades');
      const tBot = document.getElementById('tab-bot');
      const tExch = document.getElementById('tab-exch');
      if (tab === 'bot') {{
        bBot.classList.add('active');
        bExch.classList.remove('active');
        tBot.style.display = 'block';
        tExch.style.display = 'none';
      }} else {{
        bExch.classList.add('active');
        bBot.classList.remove('active');
        tExch.style.display = 'block';
        tBot.style.display = 'none';
      }}
    }}

    // Real-Time Socket Stream Handler (NO FULL PAGE RELOAD)
    let ws = null;
    let fallbackTimer = null;

    function applyLiveUpdate(d) {{
      if (!d) return;
      if (d.uptime) {{
        const upEl = document.getElementById('uptime-val');
        if (upEl) upEl.textContent = d.uptime;
      }}
      if (d.stats_html) {{
        const sEl = document.getElementById('stats-container');
        if (sEl) sEl.innerHTML = d.stats_html;
      }}
      if (d.markets_html) {{
        const mEl = document.getElementById('markets-container');
        if (mEl) mEl.innerHTML = d.markets_html;
      }}
      if (d.bot_trades_html) {{
        const btEl = document.getElementById('bot-trades-body');
        if (btEl) btEl.innerHTML = d.bot_trades_html;
      }}
      if (d.exch_trades_html) {{
        const etEl = document.getElementById('exch-trades-body');
        if (etEl) etEl.innerHTML = d.exch_trades_html;
      }}
      if (d.logs) {{
        const term = document.getElementById('term');
        if (term) {{
          const isAtBottom = (term.scrollHeight - term.clientHeight) <= (term.scrollTop + 60);
          term.textContent = d.logs;
          if (isAtBottom) term.scrollTop = term.scrollHeight;
        }}
      }}
    }}

    function connectTelemetryWS() {{
      const protocol = (location.protocol === 'https:') ? 'wss://' : 'ws://';
      const wsUrl = protocol + location.host + '/ws' + location.search;

      try {{
        ws = new WebSocket(wsUrl);
      }} catch (e) {{
        startPollingFallback();
        return;
      }}

      ws.onopen = () => {{
        console.log('[WS] Connected to live AMD telemetry stream');
        const badge = document.getElementById('ws-badge');
        if (badge) {{
          badge.textContent = '⚡ LIVE SOCKET';
          badge.style.background = '#064e3b';
          badge.style.color = '#6ee7b7';
          badge.style.borderColor = '#059669';
        }}
        if (fallbackTimer) {{
          clearInterval(fallbackTimer);
          fallbackTimer = null;
        }}
      }};

      ws.onmessage = (evt) => {{
        try {{
          const data = JSON.parse(evt.data);
          applyLiveUpdate(data);
        }} catch (err) {{
          console.error('[WS] Parse error:', err);
        }}
      }};

      ws.onerror = () => {{
        try {{ ws.close(); }} catch(e) {{}}
      }};

      ws.onclose = () => {{
        console.log('[WS] Disconnected. Reconnecting...');
        const badge = document.getElementById('ws-badge');
        if (badge) {{
          badge.textContent = '🔄 RECONNECTING';
          badge.style.background = '#854d0e';
          badge.style.color = '#fef08a';
          badge.style.borderColor = '#ca8a04';
        }}
        startPollingFallback();
        setTimeout(connectTelemetryWS, 2500);
      }};
    }}

    function startPollingFallback() {{
      if (fallbackTimer) return;
      fallbackTimer = setInterval(async () => {{
        try {{
          const res = await fetch('/api/live-status' + location.search);
          if (res.ok) {{
            const data = await res.json();
            applyLiveUpdate(data);
          }}
        }} catch (e) {{}}
      }}, 3000);
    }}

    // Initial terminal scroll & socket connection
    const term = document.getElementById('term');
    if (term) term.scrollTop = term.scrollHeight;

    connectTelemetryWS();
  </script>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", f"amd_token={PASSWORD}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, AMDTelemetryHandler)
    print("=" * 65)
    print(f"[AMD] Bybit AMD + FVG Bot Telemetry Server active on port {PORT}")
    print(f"[AMD] Dashboard URL: http://localhost:{PORT}/dashboard?password={PASSWORD}")
    print(f"[AMD] AI Summary:   http://localhost:{PORT}/api/ai-summary?password={PASSWORD}")
    print("=" * 65)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nAMD Telemetry server stopped.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
