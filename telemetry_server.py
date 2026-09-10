#!/usr/bin/env python3
"""
Secure Password-Protected HTTP Telemetry & AI Monitoring Server
Provides an authenticated dark-mode dashboard, JSON API, sanitized log viewer,
and an AI-optimized Markdown summary endpoint (/api/ai-summary).
"""

import os
import sys
import json
import csv
import hmac
import hashlib
import secrets
import subprocess
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
import time
import urllib.request
import struct
import base64
import select
import logging
from decimal import Decimal

logger = logging.getLogger("telemetry")

# Import compute_indicators from backtest
try:
    from backtest import compute_indicators
except ImportError:
    compute_indicators = None

try:
    from pybit.unified_trading import HTTP as BybitHTTP
except ImportError:
    BybitHTTP = None

# Import research & backtest replay engines
try:
    from research.replay_engine import ReplayEngine, CHAMPION_PROFILES
    from research.monte_carlo import MonteCarloEngine
    from research.optimizer import StrategyOptimizer
    from research.dashboard_components import get_research_css, get_research_html, get_research_js
    from research.test_store import save_test_run, get_test_run, list_test_runs, delete_test_run
except ImportError:
    ReplayEngine = None
    CHAMPION_PROFILES = {}
    MonteCarloEngine = None
    StrategyOptimizer = None
    get_research_css = lambda: ""
    get_research_html = lambda: ""
    get_research_js = lambda: ""
    save_test_run = lambda *args, **kwargs: {"test_id": "err", "test_url": "#"}
    get_test_run = lambda test_id: None
    list_test_runs = lambda *args, **kwargs: []
    delete_test_run = lambda test_id: False


# In-memory candle cache: { (symbol, interval, limit): (timestamp, data) }
CANDLE_CACHE = {}
CANDLE_CACHE_TTL = 8.0  # seconds

BYBIT_ACC_CACHE = {}
BYBIT_ACC_CACHE_TTL = 10.0  # seconds


def fetch_bybit_account_and_trades() -> dict:
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
                open_fee = float(t.get("openFee", 0) or 0)
                close_fee = float(t.get("closeFee", 0) or 0)
                side_raw = t.get("side", "")
                qty_val = float(t.get("closedSize", 0) or t.get("qty", 0) or 0)
                entry_px = float(t.get("avgEntryPrice", 0) or 0)
                exit_px = float(t.get("avgExitPrice", 0) or 0)

                # Accounting: Gross - OpenFee - CloseFee - FundingFee = ClosedPnL
                if side_raw.lower() == "sell":
                    gross_pnl = (exit_px - entry_px) * qty_val
                    trade_type = "Close Long"
                else:
                    gross_pnl = (entry_px - exit_px) * qty_val
                    trade_type = "Close Short"
                funding_fee = gross_pnl - open_fee - close_fee - pnl

                formatted.append({
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts_ms else "",
                    "symbol": sym,
                    "side": side_raw,
                    "trade_type": trade_type,
                    "qty": qty_val,
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "closed_pnl": pnl,
                    "open_fee": open_fee,
                    "close_fee": close_fee,
                    "funding_fee": funding_fee,
                    "exec_fee": open_fee + close_fee,
                })
            res_data["total_realized_pnl"] = round(total_pnl, 2)
            res_data["by_symbol"] = {k: round(v, 2) for k, v in by_sym.items()}
            res_data["trades_count"] = len(trades)
            res_data["recent_trades"] = formatted

        BYBIT_ACC_CACHE["acc"] = (now, res_data)
    except Exception:
        pass

    return res_data


TICKER_CACHE = {}
TICKER_CACHE_TTL = 10.0


def fetch_24h_tickers() -> dict:
    """Fetch 24h price percentage changes for linear perpetual markets."""
    now = time.time()
    if "tickers" in TICKER_CACHE:
        cached_ts, cached_data = TICKER_CACHE["tickers"]
        if now - cached_ts < TICKER_CACHE_TTL:
            return cached_data

    is_testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
    domain = "api-testnet.bybit.com" if is_testnet else "api.bybit.com"
    url = f"https://{domain}/v5/market/tickers?category=linear"
    tickers = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BybitHedgeBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            for t in data.get("result", {}).get("list", []):
                sym = t.get("symbol")
                pct = float(t.get("price24hPcnt", 0) or 0) * 100
                tickers[sym] = round(pct, 2)
        TICKER_CACHE["tickers"] = (now, tickers)
    except Exception:
        pass
    return tickers


def fetch_candles_with_indicators(symbol: str, interval: str = "60", limit: int = 80) -> dict:
    """Fetch recent klines from Bybit linear API and compute EMA9, EMA21, ADX, and % change."""
    cache_key = (symbol, str(interval), limit)
    now = time.time()
    if cache_key in CANDLE_CACHE:
        cached_ts, cached_data = CANDLE_CACHE[cache_key]
        if now - cached_ts < CANDLE_CACHE_TTL:
            return cached_data

    is_testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
    domain = "api-testnet.bybit.com" if is_testnet else "api.bybit.com"
    api_interval = str(interval)
    url = f"https://{domain}/v5/market/kline?category=linear&symbol={symbol}&interval={api_interval}&limit={min(limit, 100)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BybitHedgeBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return {"symbol": symbol, "interval": interval, "error": str(e), "candles": []}

    raw_list = data.get("result", {}).get("list", [])
    if not raw_list:
        return {"symbol": symbol, "interval": interval, "error": "No kline data", "candles": []}

    raw_list.reverse()
    parsed = []
    for k in raw_list:
        try:
            parsed.append({
                "timestamp": int(k[0]),
                "datetime": datetime.fromtimestamp(int(k[0]) / 1000),
                "open": Decimal(str(k[1])),
                "high": Decimal(str(k[2])),
                "low": Decimal(str(k[3])),
                "close": Decimal(str(k[4])),
                "volume": Decimal(str(k[5])),
            })
        except Exception:
            continue

    if compute_indicators and len(parsed) >= 5:
        try:
            compute_indicators(parsed, fast_periods=[9, 21], adx_period=14)
        except Exception:
            pass

    candles_out = []
    for c in parsed:
        dt = c["datetime"]
        time_str = dt.strftime("%H:%M") if interval not in ["D", "W"] else dt.strftime("%m-%d")
        f_ema = round(float(c.get("ema_9")), 2) if c.get("ema_9") is not None else None
        s_ema = round(float(c.get("ema_21")), 2) if c.get("ema_21") is not None else None
        adx_val = round(float(c.get("adx")), 1) if c.get("adx") is not None else None

        candles_out.append({
            "t": int(c["timestamp"] / 1000),
            "ts": time_str,
            "o": float(c["open"]),
            "h": float(c["high"]),
            "l": float(c["low"]),
            "c": float(c["close"]),
            "v": float(c["volume"]),
            "ema9": f_ema,
            "ema21": s_ema,
            "adx": adx_val,
        })

    first_open = candles_out[0]["o"] if candles_out else 0.0
    latest_close = candles_out[-1]["c"] if candles_out else 0.0
    change_pct = ((latest_close - first_open) / first_open * 100) if first_open > 0 else 0.0
    change_usd = latest_close - first_open

    res_data = {
        "symbol": symbol,
        "interval": interval,
        "first_open": first_open,
        "latest_close": latest_close,
        "change_pct": round(change_pct, 2),
        "change_usd": round(change_usd, 2),
        "count": len(candles_out),
        "candles": candles_out,
    }
    CANDLE_CACHE[cache_key] = (now, res_data)
    return res_data


# Load environment variables from .env
ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".env"))
if os.path.exists(ENV_PATH):
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Configuration
PORT = int(os.environ.get("TELEMETRY_PORT", 8080))
PASSWORD = os.environ.get("TELEMETRY_PASSWORD", "").strip()

# If no password specified, generate a random one and save in memory
if not PASSWORD:
    PASSWORD = secrets.token_hex(16)
    print(f"\n[SECURITY] No TELEMETRY_PASSWORD set in .env! Generated random password:")
    print(f"[SECURITY] >>> {PASSWORD} <<<\n")

# Active authenticated browser sessions (in-memory)
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
    """Scrub sensitive credentials and secrets from log output."""
    res = text
    # Explicitly scrub known env keys if present
    k = os.environ.get("BYBIT_API_KEY", "")
    s = os.environ.get("BYBIT_API_SECRET", "")
    if k and len(k) > 4:
        res = res.replace(k, "[REDACTED_API_KEY]")
    if s and len(s) > 4:
        res = res.replace(s, "[REDACTED_API_SECRET]")
    if PASSWORD and len(PASSWORD) > 4:
        res = res.replace(PASSWORD, "[REDACTED_PASSWORD]")
    for pat in SCRUB_PATTERNS:
        res = pat.sub(r"\1[REDACTED]", res)
    return res


def get_systemd_logs(lines: int = 100, errors_only: bool = False) -> str:
    """Fetch logs from journalctl on Linux or fallback on other systems."""
    if sys.platform != "win32":
        try:
            cmd = ["journalctl", "-u", "bybit-bot", "-n", str(lines), "--no-pager"]
            if errors_only:
                cmd.extend(["-p", "err..emerg"])
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return sanitize_logs(res.stdout)
        except Exception as e:
            return f"[Systemd Journal Query Error: {e}]"

    # Fallback / Local testing: check for any local log files
    for fname in ["bybit_bot.log", "bot.log"]:
        if os.path.exists(fname):
            try:
                with open(fname, "r", encoding="utf-8", errors="replace") as f:
                    content = f.readlines()[-lines:]
                    return sanitize_logs("".join(content))
            except Exception:
                pass
    return "No systemd journal available (Running locally or service not started)."


def get_service_status() -> dict:
    """Check status of bybit-bot systemd service."""
    status_info = {"active": False, "status": "unknown", "pid": None, "uptime": "unknown"}
    if sys.platform != "win32":
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "bybit-bot"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3
            )
            status_str = res.stdout.strip()
            status_info["active"] = (status_str == "active")
            status_info["status"] = status_str

            # Details
            stat_res = subprocess.run(
                ["systemctl", "show", "bybit-bot", "--property=ActiveEnterTimestamp,MainPID"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3
            )
            for line in stat_res.stdout.splitlines():
                if line.startswith("MainPID="):
                    status_info["pid"] = line.split("=")[1].strip()
                elif line.startswith("ActiveEnterTimestamp="):
                    status_info["uptime"] = line.split("=")[1].strip()
        except Exception:
            pass
    return status_info


def read_bot_state() -> dict:
    """Read the atomically dumped bot_state.json."""
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bot_state.json"))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and "session_start_iso" in data:
                    try:
                        start_dt = datetime.fromisoformat(data["session_start_iso"])
                        data["uptime_seconds"] = max(0, int((datetime.now() - start_dt).total_seconds()))
                    except Exception:
                        pass
                return data
        except Exception:
            pass
    return {}


def read_trade_history(limit: int = 1000) -> list:
    """Read trade audit log from bybit_trades.csv, safely handling both 12-column and legacy schemas."""
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bybit_trades.csv"))
    trades = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                r = csv.reader(f)
                for row in r:
                    if not row or not row[0] or row[0].strip().lower() == "timestamp":
                        continue
                    if len(row) >= 12:
                        trades.append({
                            "timestamp": row[0].strip(),
                            "symbol": row[1].strip(),
                            "cycle": row[2].strip(),
                            "leg": row[3].strip(),
                            "event": row[4].strip(),
                            "price": row[5].strip(),
                            "size": row[6].strip(),
                            "extreme_price": row[7].strip(),
                            "trailing_sl": row[8].strip(),
                            "tp_target": row[9].strip(),
                            "leg_pnl_usd": row[10].strip(),
                            "pair_cumulative_pnl": row[11].strip(),
                        })
                    elif len(row) >= 10:
                        trades.append({
                            "timestamp": row[0].strip(),
                            "symbol": "BTCUSDT",
                            "cycle": "1",
                            "leg": row[1].strip(),
                            "event": row[2].strip(),
                            "price": row[3].strip(),
                            "size": "",
                            "extreme_price": row[4].strip(),
                            "trailing_sl": row[5].strip(),
                            "tp_target": row[6].strip(),
                            "leg_pnl_usd": row[8].strip(),
                            "pair_cumulative_pnl": row[9].strip(),
                        })
            trades.reverse()  # Newest first
            return trades[:limit]
        except Exception:
            pass
    return trades


def format_bybit_fee(fee_val: float) -> str:
    """Format Bybit fee e.g. 0.0121 USDT, 0.33018567 USDT matching exchange Closed PnL UI."""
    if abs(fee_val) < 1e-8:
        return "0.0000 USDT"
    s = f"{fee_val:.8f}".rstrip("0").rstrip(".")
    parts = s.split(".")
    if len(parts) == 2 and len(parts[1]) < 4:
        s = parts[0] + "." + parts[1].ljust(4, "0")
    elif len(parts) == 1:
        s = parts[0] + ".0000"
    return f"{s} USDT"


def get_coin_icon(symbol: str, size: int = 20) -> str:
    """Return crisp inline SVG coin icon for BTC, ETH, SOL, XAU or fallback."""
    s = symbol.upper()
    if "BTC" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#f7931a"/><path d="M15.5 10.5c.3-1.5-.9-2.3-2.5-2.8l.5-2-1.2-.3-.5 2c-.3-.1-.7-.2-1-.2l.5-2-1.2-.3-.5 2-2.5-.6-.4 1.4s.9.2.9.2c.5.1.6.5.6.7l-.6 2.4c0 0 .1 0 .1 0l-.1 0-.8 3.3c-.1.2-.2.4-.6.3 0 0-.9-.2-.9-.2l-.6 1.5 2.4.6c.4.1.7.2 1.1.2l-.5 2.1 1.2.3.5-2c.3.1.7.2 1 .2l-.5 2 1.2.3.5-2.1c2.1.4 3.7.2 4.4-1.7.5-1.5 0-2.4-1.1-3 .8-.2 1.4-.7 1.6-1.8zm-2.8 4c-.4 1.5-3 .7-3.8.5l.7-2.8c.8.2 3.5.6 3.1 2.3zm.4-4c-.3 1.4-2.5.7-3.2.5l.6-2.5c.7.2 2.9.5 2.6 2z" fill="#fff"/></svg>'
    elif "ETH" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#627eea"/><path d="M12 4v6.6l5.6 2.5L12 4z" fill="#fff" fill-opacity=".6"/><path d="M12 4L6.4 13.1l5.6-2.5V4z" fill="#fff"/><path d="M12 17.5v4.5l5.6-7.8L12 17.5z" fill="#fff" fill-opacity=".6"/><path d="M12 22v-4.5L6.4 14.2L12 22z" fill="#fff"/><path d="M12 16.5l5.6-3.3L12 10.6v5.9z" fill="#fff" fill-opacity=".2"/><path d="M6.4 13.2l5.6 3.3v-5.9l-5.6 2.6z" fill="#fff" fill-opacity=".6"/></svg>'
    elif "SOL" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#0f172a" stroke="#14F195" stroke-width="1.5"/><path d="M7 16h8.5l1.5-1.5H8.5L7 16zm0-7h8.5l1.5-1.5H8.5L7 9zm10 3.5H8.5L7 14h8.5l1.5-1.5z" fill="#14F195"/></svg>'
    elif "XAU" in s or "PAXG" in s or "GOLD" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#eab308"/><path d="M7 15l2-6h6l2 6H7zm2.5-1.5h5l-.8-3h-3.4l-.8 3z" fill="#fff"/></svg>'
    else:
        return f'<span style="display:inline-block; width:{size}px; height:{size}px; border-radius:50%; background:#334155; color:#cbd5e1; font-size:10px; line-height:{size}px; text-align:center; flex-shrink:0; vertical-align:middle;">●</span>'


def render_coin_badge(symbol: str, size: int = 20) -> str:
    """Render coin icon and bold symbol badge matching Bybit table Contracts column."""
    icon = get_coin_icon(symbol, size=size)
    return f'<div style="display:inline-flex; align-items:center; gap:8px;">{icon}<span style="font-weight:700; color:#f8fafc;">{symbol}</span></div>'


def _json_serialize_fallback(obj):
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    try:
        from decimal import Decimal
        if isinstance(obj, Decimal):
            return float(obj)
    except ImportError:
        pass
    try:
        import numpy as np
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj)


class TelemetryHandler(BaseHTTPRequestHandler):
    server_version = "BybitHedgeTelemetry/1.0"

    def _is_authenticated(self) -> bool:
        """Check authentication via Cookie, Header, or Query Parameter."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # 1. Query parameter (?password=... or ?token=...)
        q_pass = qs.get("password", [None])[0] or qs.get("token", [None])[0]
        if q_pass and hmac.compare_digest(q_pass, PASSWORD):
            return True

        # 2. Authorization Header (Bearer or Basic)
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
            if hmac.compare_digest(token, PASSWORD):
                return True

        # 3. Session Cookie
        cookie_hdr = self.headers.get("Cookie")
        if cookie_hdr:
            cookies = SimpleCookie(cookie_hdr)
            if "telemetry_session" in cookies:
                sid = cookies["telemetry_session"].value
                if sid in ACTIVE_SESSIONS:
                    return True

        return False

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2, default=_json_serialize_fallback).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


    def _send_markdown(self, text: str, status: int = 200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200, cookie: str = None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str = None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = parse_qs(body)
            entered_pw = data.get("password", [""])[0]

            if hmac.compare_digest(entered_pw, PASSWORD):
                sid = secrets.token_hex(24)
                ACTIVE_SESSIONS.add(sid)
                cookie = f"telemetry_session={sid}; Path=/; HttpOnly; SameSite=Lax"
                self._redirect("/dashboard", cookie=cookie)
            else:
                self._send_html(self._render_login(error="Invalid password. Please try again."), status=401)
            return

        # Protected Research API Endpoints
        if parsed.path.startswith("/api/research/"):
            if not self._is_authenticated():
                self._send_json({"error": "Unauthorized", "message": "Authentication required"}, 401)
                return

            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                payload = json.loads(raw_body) if raw_body else {}
            except Exception as e:
                self._send_json({"error": f"Invalid JSON body: {e}"}, 400)
                return

            if parsed.path == "/api/research/backtest":
                self._handle_api_research_backtest(payload)
                return
            elif parsed.path == "/api/research/monte-carlo":
                self._handle_api_research_monte_carlo(payload)
                return
            elif parsed.path == "/api/research/optimize":
                self._handle_api_research_optimize(payload)
                return
            elif parsed.path == "/api/research/download-bars":
                self._handle_api_research_download_bars(payload)
                return
            elif parsed.path == "/api/research/test/delete":
                tid = payload.get("id") or payload.get("test_id")
                if not tid:
                    self._send_json({"error": "Missing test id"}, 400)
                    return
                ok = delete_test_run(tid)
                self._send_json({"status": "ok", "deleted": ok, "test_id": tid})
                return

        self.send_error(404, "Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # Public Login Page
        if parsed.path == "/login":
            if self._is_authenticated():
                self._redirect("/dashboard")
            else:
                self._send_html(self._render_login())
            return

        # Logout
        if parsed.path == "/logout":
            cookie_hdr = self.headers.get("Cookie")
            if cookie_hdr:
                cookies = SimpleCookie(cookie_hdr)
                if "telemetry_session" in cookies:
                    ACTIVE_SESSIONS.discard(cookies["telemetry_session"].value)
            self._redirect("/login", cookie="telemetry_session=; Path=/; Max-Age=0")
            return

        # Auto-login via query parameter if in browser
        q_pass = qs.get("password", [None])[0] or qs.get("token", [None])[0]
        set_cookie = None
        if q_pass and hmac.compare_digest(q_pass, PASSWORD):
            sid = secrets.token_hex(24)
            ACTIVE_SESSIONS.add(sid)
            set_cookie = f"telemetry_session={sid}; Path=/; HttpOnly; SameSite=Lax"

        # Check authentication for protected routes
        if not self._is_authenticated():
            if parsed.path.startswith("/api/") or parsed.path == "/ws":
                self._send_json({"error": "Unauthorized", "message": "Valid password required via ?password=... or Bearer header."}, 401)
            else:
                self._redirect("/login")
            return

        # Routes
        if parsed.path in ["/", "/dashboard", "/research"]:
            self._send_html(self._render_dashboard(), cookie=set_cookie)
        elif parsed.path == "/ws":
            self._handle_ws()
        elif parsed.path == "/api/live-status":
            self._send_json(self._get_live_telemetry_payload())
        elif parsed.path == "/api/status":
            self._handle_api_status()
        elif parsed.path == "/api/ai-summary":
            self._handle_api_ai_summary()
        elif parsed.path == "/api/logs":
            self._handle_api_logs(qs)
        elif parsed.path == "/api/trades":
            self._handle_api_trades(qs)
        elif parsed.path == "/api/clear-trades":
            self._handle_api_clear_trades(qs)
        elif parsed.path == "/api/candles":
            self._handle_api_candles(qs)
        elif parsed.path == "/api/tickers":
            self._handle_api_tickers()
        elif parsed.path == "/api/research/config":
            self._send_json({
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PAXGUSDT"],
                "profiles": CHAMPION_PROFILES if CHAMPION_PROFILES else {},
            })
        elif parsed.path == "/api/research/test":
            tid = qs.get("id", [None])[0] or qs.get("test_id", [None])[0]
            if not tid:
                self._send_json({"error": "Missing test id parameter (?id=...)"}, 400)
                return
            run_data = get_test_run(tid)
            if not run_data:
                self._send_json({"error": f"Test run '{tid}' not found"}, 404)
                return
            self._send_json({"status": "ok", "test": run_data})
        elif parsed.path == "/api/research/tests":
            limit = int(qs.get("limit", [50])[0])
            ttype = qs.get("type", [None])[0]
            self._send_json({
                "status": "ok",
                "tests": list_test_runs(limit=limit, test_type=ttype),
            })
        elif parsed.path == "/api/research/download-bars":
            symbols_q = qs.get("symbols", ["BTCUSDT,ETHUSDT,SOLUSDT,PAXGUSDT"])[0]
            bars_q = int(qs.get("bars", [8000])[0])
            interval_q = qs.get("interval", ["60"])[0]
            testnet_q = qs.get("testnet", ["0"])[0].lower() in ["1", "true", "yes"]
            payload = {"symbols": symbols_q.split(","), "bars": bars_q, "interval": interval_q, "testnet": testnet_q}
            self._handle_api_research_download_bars(payload)
        else:
            self.send_error(404, "Not Found")

    # ==========================================================================
    # API HANDLERS
    # ==========================================================================

    def _handle_api_status(self):
        state = read_bot_state()
        svc = get_service_status()
        acc = fetch_bybit_account_and_trades()

        equity = state.get("account_equity") or acc.get("equity", 0.0)
        avail = state.get("available_balance") or acc.get("available_balance", 0.0)
        realized_pnl = state.get("exchange_realized_pnl") or acc.get("total_realized_pnl", 0.0)
        sym_pnl = state.get("exchange_pnl_by_symbol") or acc.get("by_symbol", {})
        trades_cnt = state.get("total_trades_count") or acc.get("trades_count", 0)
        recent_trades = state.get("exchange_recent_trades") or acc.get("recent_trades", [])

        resp = {
            "server_time": datetime.now().isoformat(),
            "service": svc,
            "account": {
                "equity": equity,
                "available_balance": avail,
                "realized_pnl": realized_pnl,
                "by_symbol": sym_pnl,
                "total_trades": trades_cnt,
            },
            "recent_closed_trades": recent_trades[:25],
            "bot_state": state,
        }
        self._send_json(resp)

    def _handle_api_ai_summary(self):
        """Generate high-signal, compact Markdown summary for AI Agents."""
        state = read_bot_state()
        svc = get_service_status()
        acc = fetch_bybit_account_and_trades()
        trades = read_trade_history(limit=10)
        logs = get_systemd_logs(lines=15)

        uptime_sec = state.get("uptime_seconds", 0)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s" if uptime_sec else "N/A"

        # Calculate trade statistics
        total_pnl = state.get("exchange_realized_pnl") or acc.get("total_realized_pnl", 0.0)
        open_pnl = state.get("open_pnl", 0.0)
        equity = state.get("account_equity") or acc.get("equity", 0.0)
        avail = state.get("available_balance") or acc.get("available_balance", 0.0)
        sym_pnl = state.get("exchange_pnl_by_symbol") or acc.get("by_symbol", {})
        trades_cnt = state.get("total_trades_count") or acc.get("trades_count", 0)
        recent_exchange_trades = state.get("exchange_recent_trades") or acc.get("recent_trades", [])

        active_count = state.get("active_pairs_count", 0)
        max_pairs = state.get("max_concurrent_pairs", 3)
        network = state.get("network", "TESTNET")
        leverage = state.get("leverage", 4)

        md = []
        md.append(f"# Bybit Multi-Pair Bot: Live Telemetry Summary")
        md.append(f"**Timestamp**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}`  |  **Service**: `{svc.get('status', 'active').upper()}` (PID: {svc.get('pid', 'N/A')})")
        md.append(f"- **Network**: `{network}` | **Leverage**: `{leverage}x` | **Uptime**: `{uptime_str}` | **Scans**: `{state.get('scan_count', 0)}`")
        if equity:
            md.append(f"- **Account Equity**: `${equity:,.2f} USDT` | **Available Margin**: `${avail:,.2f} USDT`")
        md.append(f"- **Realized PnL (Trade History)**: `${total_pnl:+.2f} USDT` across `{trades_cnt}` closed Bybit trades")
        if sym_pnl:
            sym_str = " | ".join([f"{k}: `${v:+.2f}`" for k, v in sym_pnl.items()])
            md.append(f"- **Realized PnL by Symbol**: {sym_str}")
        md.append(f"- **Portfolio Status**: `{active_count}/{max_pairs}` Pairs Active | **Open Floating PnL**: `${open_pnl:+.2f}`")
        md.append("")

        md.append("## Active Markets & Positions")
        pairs = state.get("pairs", {})
        if not pairs:
            md.append("*No market state cached yet (Bot starting up or idle).*")
        else:
            for sym, p in pairs.items():
                status = p.get("status", "SCANNING")
                px = p.get("latest_price")
                px_str = f"${px:,.2f}" if px else "N/A"
                ema_fast = p.get("fast_ema")
                ema_slow = p.get("slow_ema")
                adx = p.get("adx")
                ind_str = f"EMA({ema_fast:.1f}/{ema_slow:.1f}) ADX={adx:.1f}" if (ema_fast and ema_slow and adx) else "Computing..."

                md.append(f"### {sym} (`{status}` @ {px_str})")
                md.append(f"- **Indicators**: {ind_str} | **Status Msg**: {p.get('status_msg', '')}")

                long_leg = p.get("long_leg")
                short_leg = p.get("short_leg")
                if long_leg:
                    l_sl = float(long_leg.get("trailing_sl", 0.0) or 0.0)
                    l_tp = float(long_leg.get("tp_target", 0.0) or 0.0)
                    l_sl_buf = (px - l_sl) if (px and l_sl) else 0.0
                    l_tp_buf = (l_tp - px) if (px and l_tp) else 0.0
                    md.append(
                        f"  * **Long Leg**: {long_leg['size']} @ ${long_leg['entry_price']:.2f} | "
                        f"SL: ${l_sl:.2f} (-${abs(l_sl_buf):.2f}) | TP: ${l_tp:.2f} (+${abs(l_tp_buf):.2f}) | "
                        f"Unrealized PnL: ${long_leg['unrealized_pnl']:+.2f} ({long_leg['pnl_pct']:+.2f}%)"
                    )
                if short_leg:
                    s_sl = float(short_leg.get("trailing_sl", 0.0) or 0.0)
                    s_tp = float(short_leg.get("tp_target", 0.0) or 0.0)
                    s_sl_buf = (s_sl - px) if (px and s_sl) else 0.0
                    s_tp_buf = (px - s_tp) if (px and s_tp) else 0.0
                    md.append(
                        f"  * **Short Leg**: {short_leg['size']} @ ${short_leg['entry_price']:.2f} | "
                        f"SL: ${s_sl:.2f} (+${abs(s_sl_buf):.2f}) | TP: ${s_tp:.2f} (-${abs(s_tp_buf):.2f}) | "
                        f"Unrealized PnL: ${short_leg['unrealized_pnl']:+.2f} ({short_leg['pnl_pct']:+.2f}%)"
                    )
                if not long_leg and not short_leg:
                    md.append("  * *No active legs (Scanning for EMA cross + ADX gating)*")
                md.append("")

        if trades:
            md.append("## Bot State Machine Audit (`bybit_trades.csv`)")
            md.append("| Timestamp | Symbol | Leg | Event | Price | Size | Trailing SL | Apex TP | Leg PnL |")
            md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
            for t in trades[:6]:
                try:
                    px_val = float(t.get("price") or 0)
                except (ValueError, TypeError):
                    px_val = 0.0
                try:
                    sl_val = float(t.get("trailing_sl") or 0)
                except (ValueError, TypeError):
                    sl_val = 0.0
                try:
                    tp_val = float(t.get("tp_target") or 0)
                except (ValueError, TypeError):
                    tp_val = 0.0
                try:
                    pnl_val = float(t.get("leg_pnl_usd") or 0)
                except (ValueError, TypeError):
                    pnl_val = 0.0
                sl_str = f"${sl_val:.2f}" if sl_val > 0 else "---"
                tp_str = f"${tp_val:.2f}" if tp_val > 0 else "---"
                md.append(f"| {t.get('timestamp','')} | **{t.get('symbol','')}** | {t.get('leg','')} | `{t.get('event','')}` | ${px_val:.2f} | {t.get('size','')} | {sl_str} | {tp_str} | **${pnl_val:+.2f}** |")
            md.append("")

        if recent_exchange_trades:
            md.append("## Bybit Exchange Closed PnL Ledger (UTA V5)")
            md.append("| Contracts | Qty | Entry Price | Exit Price | Trade Type | Closed P&L | Result | Open Trade Volume | Closed Trade Volume | Opening Fee | Closing Fee | Funding Fee | Trade Time |")
            md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
            for t in recent_exchange_trades[:12]:
                ts_str = t.get("timestamp", "")
                if not ts_str and t.get("updated_time"):
                    try:
                        ts_str = datetime.fromtimestamp(int(t.get("updated_time")) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        ts_str = ""
                trade_type = t.get("trade_type", "Close Long" if t.get("side", "").lower() == "sell" else "Close Short")
                is_close_long = "long" in trade_type.lower()
                open_fee = float(t.get("open_fee", 0) or t.get("openFee", 0) or 0)
                close_fee = float(t.get("close_fee", 0) or t.get("closeFee", 0) or 0)
                pnl_val = float(t.get("closed_pnl", 0) or 0)
                entry_px = float(t.get("entry_price", 0) or t.get("avgEntryPrice", 0) or 0)
                exit_px = float(t.get("exit_price", 0) or t.get("avgExitPrice", 0) or 0)
                qty_val = float(t.get("qty", 0) or 0)
                abs_q = abs(qty_val)
                if abs_q == int(abs_q):
                    q_num = str(int(abs_q))
                else:
                    q_num = f"{abs_q:.6f}".rstrip("0").rstrip(".")
                qty_str = f"-{q_num}" if is_close_long else q_num

                if t.get("funding_fee") is not None:
                    funding_fee = float(t.get("funding_fee"))
                else:
                    gross = (exit_px - entry_px) * abs_q if is_close_long else (entry_px - exit_px) * abs_q
                    funding_fee = gross - open_fee - close_fee - pnl_val
                fund_str = f"{funding_fee:.4f}" if abs(funding_fee) >= 0.00005 else "0.0000"
                res_badge = "**Win**" if pnl_val >= 0 else "*Loss*"
                open_vol = entry_px * abs_q
                close_vol = exit_px * abs_q
                open_fee_str = format_bybit_fee(open_fee)
                close_fee_str = format_bybit_fee(close_fee)
                md.append(f"| **{t.get('symbol','')}** | {qty_str} | {entry_px:,.2f} | {exit_px:,.2f} | `{trade_type}` | **{pnl_val:+.4f}** | {res_badge} | {open_vol:,.2f} | {close_vol:,.2f} | {open_fee_str} | {close_fee_str} | {fund_str} | {ts_str} |")
            md.append("")
        elif not trades:
            md.append("*No closed trades recorded yet.*")
            md.append("")

        md.append("## Recent System & Crash Logs (Last 15 Lines)")
        md.append("```text")
        md.append(logs.strip())
        md.append("```")

        self._send_markdown("\n".join(md))

    def _handle_api_logs(self, qs: dict):
        lines = int(qs.get("lines", [100])[0])
        err_only = qs.get("errors", ["0"])[0] in ["1", "true", "yes"]
        raw_logs = get_systemd_logs(lines=min(lines, 2500), errors_only=err_only)
        self._send_json({"lines": lines, "errors_only": err_only, "logs": raw_logs.splitlines()})

    def _handle_api_trades(self, qs: dict):
        limit = int(qs.get("limit", [50])[0])
        trades = read_trade_history(limit=min(limit, 200))
        self._send_json({"trades": trades})

    def _handle_api_clear_trades(self, qs: dict):
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bybit_trades.csv"))
        cleared = False
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "timestamp", "symbol", "cycle", "leg", "event",
                    "price", "size", "extreme_price", "trailing_sl", "tp_target",
                    "leg_pnl_usd", "pair_cumulative_pnl",
                ])
            cleared = True
        except Exception as e:
            logger.warning(f"Failed to clear trades ledger: {e}")

        # If redirect requested, send back to dashboard
        if qs.get("redirect", ["0"])[0] in ["1", "true", "yes"]:
            self._redirect("/dashboard")
        else:
            self._send_json({"success": cleared, "message": "Trade audit ledger cleared."})

    def _handle_api_candles(self, qs: dict):
        sym = qs.get("symbol", ["BTCUSDT"])[0].upper()
        interval = qs.get("interval", ["60"])[0]
        limit = int(qs.get("limit", [80])[0])
        data = fetch_candles_with_indicators(sym, interval=interval, limit=limit)
        self._send_json(data)

    def _handle_api_tickers(self):
        tickers = fetch_24h_tickers()
        self._send_json({"tickers": tickers})

    def _handle_api_research_backtest(self, payload: dict):
        if not ReplayEngine:
            self._send_json({"error": "ReplayEngine module not loaded"}, 500)
            return

        symbol = str(payload.get("symbol", "BTCUSDT")).upper()
        limit = int(payload.get("bars") or payload.get("limit") or 2000)
        custom_params = {}
        for k in ["d_pct", "confirm_mult", "b1_tp_mult", "b2_tp_mult", "leverage", "use_dynamic_atr", "atr_mult", "adx_min", "hedge_ratio"]:
            if k in payload:
                if k == "use_dynamic_atr":
                    custom_params[k] = bool(payload[k])
                else:
                    try:
                        custom_params[k] = float(payload[k])
                    except (ValueError, TypeError):
                        pass

        try:
            candles = ReplayEngine.load_candles(symbol, limit=limit)
            engine = ReplayEngine(initial_capital=float(payload.get("initial_capital", 1000.0)))
            res = engine.run_backtest(symbol, candles, custom_params=custom_params if custom_params else None)

            # Benchmark buy and hold curve
            p0 = float(candles[0]["close"]) if candles else 1.0
            benchmark_curve = []
            stride = max(1, len(candles) // 100)
            for c in candles[::stride]:
                benchmark_curve.append({
                    "time": str(c.get("datetime", "")),
                    "price": float(c["close"]),
                    "return_pct": round(((float(c["close"]) - p0) / p0) * 100.0, 2),
                })

            import numpy as np
            from research.replay_engine import calc_ema
            c_arr = np.array([float(c["close"]) for c in candles], dtype=np.float64)
            ema9 = calc_ema(c_arr, 9)
            ema21 = calc_ema(c_arr, 21)

            price_candles = [
                {
                    "idx": i,
                    "t": c.get("timestamp", 0),
                    "time": str(c.get("datetime", "")),
                    "o": float(c["open"]),
                    "h": float(c["high"]),
                    "l": float(c["low"]),
                    "c": float(c["close"]),
                    "ema9": round(float(ema9[i]), 2) if not np.isnan(ema9[i]) else None,
                    "ema21": round(float(ema21[i]), 2) if not np.isnan(ema21[i]) else None,
                }
                for i, c in enumerate(candles)
            ]

            trades_data = [
                {
                    "cycle_id": t.cycle_id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "scenario": t.scenario,
                    "gross_pnl": t.gross_pnl,
                    "fees": t.fees,
                    "net_pnl": t.net_pnl,
                    "return_pct": t.return_pct,
                    "bars_held": t.bars_held,
                    "minutes_held": t.minutes_held,
                    "exhaustion_guard_triggered": t.exhaustion_guard_triggered,
                    "entry_bar_idx": t.entry_bar_idx,
                    "exit_bar_idx": t.exit_bar_idx,
                    "primary_exit": t.primary_exit,
                    "counter_exit": t.counter_exit,
                    "primary_pnl": t.primary_pnl,
                    "counter_pnl": t.counter_pnl,
                }
                for t in res.trades
            ]

            resp = {
                "symbol": res.symbol,
                "total_trades": res.total_trades,
                "winning_trades": res.winning_trades,
                "breakeven_trades": res.breakeven_trades,
                "losing_trades": res.losing_trades,
                "win_rate": res.win_rate,
                "shield_rate": res.shield_rate,
                "gross_profit": res.gross_profit,
                "gross_loss": res.gross_loss,
                "total_fees": res.total_fees,
                "net_profit": res.net_profit,
                "profit_factor": res.profit_factor,
                "max_drawdown": res.max_drawdown,
                "max_drawdown_pct": res.max_drawdown_pct,
                "sharpe_ratio": res.sharpe_ratio,
                "scenario_counts": res.scenario_counts,
                "equity_curve": res.equity_curve,
                "equity_points": [d["equity"] for d in res.equity_curve],
                "drawdown_curve": res.drawdown_curve,
                "drawdown_series": [d["drawdown_pct"] for d in res.drawdown_curve],
                "benchmark_curve": benchmark_curve,
                "buy_hold_curve": [round(b["price"] * (1000.0 / benchmark_curve[0]["price"]), 2) for b in benchmark_curve] if benchmark_curve else [],
                "trades": trades_data,
                # Jesse-Grade Extended Performance Metrics
                "long_trades": res.long_trades,
                "long_win_rate": res.long_win_rate,
                "long_profit": res.long_profit,
                "short_trades": res.short_trades,
                "short_win_rate": res.short_win_rate,
                "short_profit": res.short_profit,
                "avg_win": res.avg_win,
                "avg_loss": res.avg_loss,
                "win_loss_ratio": res.win_loss_ratio,
                "largest_win": res.largest_win,
                "largest_loss": res.largest_loss,
                "expectancy_usd": res.expectancy_usd,
                "cagr_pct": res.cagr_pct,
                "sortino_ratio": res.sortino_ratio,
                "calmar_ratio": res.calmar_ratio,
                "max_consecutive_wins": res.max_consecutive_wins,
                "max_consecutive_losses": res.max_consecutive_losses,
                "avg_bars_held": res.avg_bars_held,
                "max_bars_held": res.max_bars_held,
                "min_bars_held": res.min_bars_held,
                "candles_count": res.candles_count,
                "price_candles": price_candles,
            }

            # Persist test run to generate permanent link (Permlink)
            try:
                summary = {
                    "symbol": res.symbol,
                    "total_trades": res.total_trades,
                    "win_rate": res.win_rate,
                    "net_profit": res.net_profit,
                    "profit_factor": res.profit_factor,
                    "max_drawdown_pct": res.max_drawdown_pct,
                    "expectancy_usd": res.expectancy_usd,
                    "cagr_pct": res.cagr_pct,
                    "sortino_ratio": res.sortino_ratio,
                    "calmar_ratio": res.calmar_ratio,
                    "shield_rate": res.shield_rate,
                }
                saved = save_test_run(
                    test_type="backtest",
                    symbol=res.symbol,
                    title=f"{res.symbol} 1-Min Replay Backtest",
                    summary=summary,
                    data=resp,
                    params=payload,
                )
                resp["test_id"] = saved["test_id"]
                resp["test_url"] = saved["test_url"]
            except Exception as se:
                logger.warning(f"Could not persist backtest test run: {se}")

            self._send_json(resp)
        except Exception as e:
            logger.error(f"Research backtest API error: {e}", exc_info=True)
            self._send_json({"error": str(e)}, 500)

    def _handle_api_research_monte_carlo(self, payload: dict):
        if not MonteCarloEngine:
            self._send_json({"error": "MonteCarloEngine module not loaded"}, 500)
            return

        trade_pnls = payload.get("trade_pnls", [])
        if not trade_pnls:
            symbol = payload.get("symbol", "BTCUSDT").upper()
            bars = int(payload.get("bars", 2000))
            if ReplayEngine:
                try:
                    candles = ReplayEngine.load_candles(symbol, limit=bars)
                    engine = ReplayEngine(initial_capital=float(payload.get("initial_capital", 1000.0)))
                    res = engine.run_backtest(symbol, candles)
                    trade_pnls = [t.net_pnl for t in res.trades]
                except Exception as e:
                    logger.error(f"Failed to auto-generate trades for MC: {e}")

        if not trade_pnls:
            self._send_json({"error": "No trade PnLs available for Monte Carlo simulation"}, 400)
            return

        iterations = int(payload.get("iterations", 5000))
        permutations = int(payload.get("permutations", 1000))

        try:
            mc_engine = MonteCarloEngine(initial_capital=float(payload.get("initial_capital", 1000.0)))
            mc = mc_engine.run_monte_carlo(trade_pnls, iterations=iterations)
            rst = mc_engine.run_rst_permutation(trade_pnls, permutations=permutations)

            resp = {
                "monte_carlo": {
                    "iterations": mc.iterations,
                    "num_trades": mc.num_trades,
                    "median_profit": mc.median_profit,
                    "mean_profit": mc.mean_profit,
                    "conf_interval_90": mc.conf_interval_90,
                    "conf_interval_95": mc.conf_interval_95,
                    "percentile_5": mc.percentile_5,
                    "percentile_25": mc.percentile_25,
                    "percentile_50": mc.percentile_50,
                    "percentile_75": mc.percentile_75,
                    "percentile_95": mc.percentile_95,
                    "prob_profit": mc.prob_profit,
                    "risk_of_ruin": mc.risk_of_ruin,
                    "median_max_dd": mc.median_max_dd,
                    "percentile_95_max_dd": mc.percentile_95_max_dd,
                    "fan_chart": mc.fan_chart,
                },
                "rst": {
                    "permutations": rst.permutations,
                    "strategy_net_profit": rst.strategy_net_profit,
                    "null_mean_profit": rst.null_mean_profit,
                    "null_std_profit": rst.null_std_profit,
                    "z_score": rst.z_score,
                    "p_value": rst.p_value,
                    "is_significant": rst.is_significant,
                    "confidence_level_pct": rst.confidence_level_pct,
                },
            }

            # Persist test run to generate permanent link (Permlink)
            try:
                sym = payload.get("symbol", "PORTFOLIO").upper()
                summary = {
                    "symbol": sym,
                    "iterations": mc.iterations,
                    "num_trades": mc.num_trades,
                    "median_profit": mc.median_profit,
                    "mean_profit": mc.mean_profit,
                    "prob_profit": mc.prob_profit,
                    "risk_of_ruin": mc.risk_of_ruin,
                    "strategy_net_profit": rst.strategy_net_profit,
                    "p_value": rst.p_value,
                    "z_score": rst.z_score,
                    "is_significant": rst.is_significant,
                }
                saved = save_test_run(
                    test_type="monte_carlo",
                    symbol=sym,
                    title=f"{sym} Monte Carlo (5k) & RST",
                    summary=summary,
                    data=resp,
                    params=payload,
                )
                resp["test_id"] = saved["test_id"]
                resp["test_url"] = saved["test_url"]
            except Exception as se:
                logger.warning(f"Could not persist Monte Carlo test run: {se}")

            self._send_json(resp)
        except Exception as e:
            logger.error(f"Research Monte Carlo API error: {e}", exc_info=True)
            self._send_json({"error": str(e)}, 500)

    def _handle_api_research_optimize(self, payload: dict):
        """Handle POST /api/research/optimize to run walk-forward hyperparameter optimization."""
        if not StrategyOptimizer or not ReplayEngine:
            self._send_json({"error": "Optimizer engine not available"}, 500)
            return

        symbol = payload.get("symbol", "BTCUSDT").upper()
        bars = int(payload.get("bars", 2000))
        trials = int(payload.get("trials", 25))
        objective = payload.get("objective", "sharpe")
        train_ratio = float(payload.get("train_ratio", 0.70))

        try:
            candles = ReplayEngine.load_candles(symbol, limit=bars)
            optimizer = StrategyOptimizer(initial_capital=1000.0)
            res = optimizer.run_optimization(
                symbol=symbol,
                candles=candles,
                num_trials=trials,
                train_ratio=train_ratio,
                objective=objective
            )

            resp = {
                "symbol": res.symbol,
                "trials_evaluated": res.trials_evaluated,
                "objective": res.objective,
                "train_test_split": res.train_test_split,
                "best_params": res.best_params,
                "best_fitness": res.best_fitness,
                "best_training_metrics": res.best_training_metrics,
                "best_testing_metrics": res.best_testing_metrics,
                "leaderboard": [
                    {
                        "trial": t.trial_number,
                        "params": t.params,
                        "training_profit": t.training_profit,
                        "training_sharpe": t.training_sharpe,
                        "training_win_rate": t.training_win_rate,
                        "testing_profit": t.testing_profit,
                        "testing_sharpe": t.testing_sharpe,
                        "testing_win_rate": t.testing_win_rate,
                        "testing_max_dd": t.testing_max_dd,
                        "fitness_score": t.fitness_score,
                        "is_overfit": t.is_overfit,
                    }
                    for t in res.leaderboard[:10]
                ],
            }

            # Persist test run to generate permanent link (Permlink)
            try:
                summary = {
                    "symbol": res.symbol,
                    "trials": res.trials_evaluated,
                    "objective": res.objective,
                    "best_fitness": res.best_fitness,
                    "best_params": res.best_params,
                    "testing_profit": res.best_testing_metrics.get("profit", 0.0),
                    "testing_win_rate": res.best_testing_metrics.get("win_rate", 0.0),
                    "testing_max_dd": res.best_testing_metrics.get("max_drawdown_pct", 0.0),
                }
                saved = save_test_run(
                    test_type="optimizer",
                    symbol=res.symbol,
                    title=f"{res.symbol} Walk-Forward Optimization",
                    summary=summary,
                    data=resp,
                    params=payload,
                )
                resp["test_id"] = saved["test_id"]
                resp["test_url"] = saved["test_url"]
            except Exception as se:
                logger.warning(f"Could not persist Optimizer test run: {se}")

            self._send_json(resp)
        except Exception as e:
            logger.error(f"Research Optimize API error: {e}", exc_info=True)
            self._send_json({"error": str(e)}, 500)

    def _handle_api_research_download_bars(self, payload: dict):
        """Handle POST or GET /api/research/download-bars to fetch latest continuous Kline data from Bybit."""
        symbols_raw = payload.get("symbols", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PAXGUSDT"])
        if isinstance(symbols_raw, str):
            symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
        else:
            symbols = [str(s).strip().upper() for s in symbols_raw if str(s).strip()]

        bars = int(payload.get("bars", 8000))
        interval = str(payload.get("interval", "60"))
        testnet = bool(payload.get("testnet", False))

        try:
            from scripts.download_latest_candles import download_candles_for_symbols
            results = download_candles_for_symbols(
                symbols=symbols,
                bars=bars,
                interval=interval,
                out_dir="scratch",
                testnet=testnet,
            )
            self._send_json({
                "status": "ok",
                "message": f"Successfully downloaded Kline data for {len(symbols)} symbols",
                "bars_requested": bars,
                "interval": interval,
                "results": results,
            })
        except Exception as e:
            logger.error(f"Download bars API error: {e}", exc_info=True)
            self._send_json({"error": str(e)}, 500)



    def _build_ws_frame(self, payload_bytes: bytes) -> bytes:
        """Construct an unmasked RFC 6455 WebSocket text frame (server -> client)."""
        length = len(payload_bytes)
        if length < 126:
            header = struct.pack("!BB", 0x81, length)
        elif length <= 0xFFFF:
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
                            if opcode == 0x8:  # Close frame
                                sock.sendall(bytes([0x88, 0x00]))
                                return
                            elif opcode == 0x9:  # Ping frame
                                sock.sendall(bytes([0x8A, 0x00]))
                        except (BlockingIOError, InterruptedError):
                            continue
                        except Exception:
                            return
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            logger.debug(f"WS session ended: {e}")

    def _get_live_telemetry_payload(self) -> dict:
        """Collect all live telemetry and render dynamic HTML snippets for real-time DOM injection."""
        state = read_bot_state()
        svc = get_service_status()
        acc = fetch_bybit_account_and_trades()
        trades = read_trade_history(limit=1000)
        logs = get_systemd_logs(lines=80)
        tickers_24h = fetch_24h_tickers()

        is_active = svc.get("active", True)
        status_color = "#10b981" if is_active else "#ef4444"
        status_text = "ACTIVE" if is_active else "STOPPED"

        brand_status_html = f"""
        <div class="brand-title-wrap">
          <div class="pulse-dot" style="background:{status_color}; box-shadow:0 0 10px {status_color};"></div>
          <h1>Bybit <span class="hide-mobile">Multi-Pair </span>Hedge Bot</h1>
        </div>
        <span class="status-pill" style="border-color:{status_color}; color:{status_color}">{status_text}</span>
        """

        uptime_sec = state.get("uptime_seconds", 0)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s" if uptime_sec else "Running"

        equity = state.get("account_equity") or acc.get("equity", 0.0)
        avail_bal = state.get("available_balance") or acc.get("available_balance", 0.0)
        realized_pnl = state.get("exchange_realized_pnl") or acc.get("total_realized_pnl", 0.0)
        open_pnl = state.get("open_pnl", 0.0)
        total_pnl = realized_pnl + open_pnl
        sym_pnl = state.get("exchange_pnl_by_symbol") or acc.get("by_symbol", {})
        trades_count = state.get("total_trades_count") or acc.get("trades_count", 0)
        recent_exchange_trades = state.get("exchange_recent_trades") or acc.get("recent_trades", [])

        rpnl_color = "#10b981" if realized_pnl >= 0 else "#ef4444"
        opnl_color = "#10b981" if open_pnl >= 0 else "#ef4444"
        tpnl_color = "#10b981" if total_pnl >= 0 else "#ef4444"

        stats_grid_html = f"""
        <div class="card">
          <div class="stat-title">Total Account Balance</div>
          <div class="stat-val" style="color:#38bdf8;">${equity:,.2f}</div>
          <div class="stat-sub">Available: ${avail_bal:,.2f} USDT</div>
        </div>
        <div class="card">
          <div class="stat-title">Realized Net Profit</div>
          <div class="stat-val" style="color:{rpnl_color}">${realized_pnl:+.2f}</div>
          <div class="stat-sub">Across {trades_count} closed Bybit trades</div>
        </div>
        <div class="card">
          <div class="stat-title">Open Unrealized PnL</div>
          <div class="stat-val" style="color:{opnl_color}">${open_pnl:+.2f}</div>
          <div class="stat-sub">Active market floating</div>
        </div>
        <div class="card">
          <div class="stat-title">Total Performance Impact</div>
          <div class="stat-val" style="color:{tpnl_color}">${total_pnl:+.2f}</div>
          <div class="stat-sub">Realized + Floating</div>
        </div>
        <div class="card">
          <div class="stat-title">System Uptime</div>
          <div class="stat-val" style="font-size: 18px; margin-top:4px;">{uptime_str}</div>
          <div class="stat-sub">Scans: {state.get('scan_count', 0)} | Leverage: {state.get('leverage', 4)}x</div>
        </div>
        """

        # Markets rows & Active Positions
        market_cards = []
        active_positions_rows = []
        pairs = state.get("pairs", {})
        for sym, p in pairs.items():
            px = p.get("latest_price")
            px_str = f"${px:,.2f}" if px else "---"
            px_val_str = f"${px:,.2f}" if px is not None else "---"
            p_status = p.get("status", "SCANNING")
            p_phase = p.get("phase", "SCANNING")
            p_color = "#10b981" if p_status == "ACTIVE" else "#94a3b8"
            fast_e = p.get("fast_ema")
            slow_e = p.get("slow_ema")
            adx = p.get("adx")
            ind = f"EMA: {fast_e:.1f} / {slow_e:.1f} | ADX: {adx:.1f}" if (fast_e and slow_e and adx) else "Scanning..."

            pct_val = tickers_24h.get(sym)
            if pct_val is not None:
                pct_cls = "up" if pct_val >= 0 else "down"
                pct_str = f"{pct_val:+.2f}%"
            else:
                pct_cls = ""
                pct_str = "--%"

            long_leg = p.get("long_leg")
            short_leg = p.get("short_leg")

            long_html = '<div class="leg-box empty">Long: Idle (Scanning)</div>'
            if long_leg:
                lpnl = float(long_leg.get("unrealized_pnl", 0.0) or 0.0)
                lcol = "#10b981" if lpnl >= 0 else "#ef4444"
                l_role = long_leg.get("role", "PRIMARY")
                l_entry = float(long_leg.get("entry_price", 0.0) or 0.0)
                l_sl = float(long_leg.get("trailing_sl", 0.0) or 0.0)
                l_tp = float(long_leg.get("tp_target", 0.0) or 0.0)
                l_size = float(long_leg.get("size", 0.0) or 0.0)
                l_peak = float(long_leg.get("peak_price", l_entry) or l_entry)
                l_pnl_pct = float(long_leg.get("pnl_pct", 0.0) or 0.0)

                l_sl_buf = (px - l_sl) if (px and l_sl) else 0.0
                l_sl_buf_pct = ((px - l_sl) / px * 100) if (px and l_sl and px > 0) else 0.0
                l_tp_buf = (l_tp - px) if (px and l_tp) else 0.0
                l_tp_buf_pct = ((l_tp - px) / px * 100) if (px and l_tp and px > 0) else 0.0

                if p_phase == "INCUBATION":
                    l_sl_tag = '<div class="target-tag event" title="Stop Loss arms at Zero-Loss upon ±0.80D breakout"><span>🛡️ SL</span><span style="font-size:9px; opacity:0.85;">Arms @ ±0.80D</span></div>'
                    l_tp_tag = f'<div class="target-tag event" title="Apex Take Profit target"><span>🎯 TP</span><span style="font-size:9px; opacity:0.85;">Target ${l_tp:,.2f}</span></div>' if l_tp > 0 else '<div></div>'
                    table_sl = '<span class="badge event" style="font-size:11px;">Pending ±0.80D</span><div style="font-size:10px; color:#94a3b8; margin-top:2px;">No stops in incubation</div>'
                    table_tp = f'<span class="target-tag tp" style="opacity:0.85;">🎯 Target ${l_tp:,.2f}</span><div style="font-size:10px; color:#94a3b8; margin-top:2px;">Arms on confirmation</div>' if l_tp > 0 else '---'
                else:
                    l_sl_tag = f'<div class="target-tag sl" title="Trailing Stop Loss"><span>🛡️ SL: ${l_sl:,.2f}</span><span style="font-size:9px; opacity:0.85;">(-${abs(l_sl_buf):,.1f})</span></div>' if l_sl > 0 else '<div class="target-tag event"><span>SL: ---</span></div>'
                    l_tp_tag = f'<div class="target-tag tp" title="Apex Take Profit"><span>🎯 TP: ${l_tp:,.2f}</span><span style="font-size:9px; opacity:0.85;">(+${abs(l_tp_buf):,.1f})</span></div>' if l_tp > 0 else '<div class="target-tag event"><span>TP: ---</span></div>'
                    table_sl = f'<span class="target-tag sl">🛡️ ${l_sl:,.2f}</span><div style="font-size:10px; color:#ef4444; margin-top:2px;">Buffer: ${abs(l_sl_buf):,.2f} ({abs(l_sl_buf_pct):.2f}%)</div>' if (l_sl > 0 and px) else (f'<span class="target-tag sl">🛡️ ${l_sl:,.2f}</span>' if l_sl > 0 else '---')
                    table_tp = f'<span class="target-tag tp">🎯 ${l_tp:,.2f}</span><div style="font-size:10px; color:#10b981; margin-top:2px;">Target: ${abs(l_tp_buf):,.2f} ({abs(l_tp_buf_pct):.2f}%)</div>' if (l_tp > 0 and px) else (f'<span class="target-tag tp">🎯 ${l_tp:,.2f}</span>' if l_tp > 0 else '---')

                long_html = f"""<div class="leg-box long" style="border-left: 3px solid #10b981;">
                  <div class="leg-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; gap:4px;">
                    <div style="display:flex; align-items:center; gap:4px; min-width:0;">
                      <span class="badge long" style="white-space:nowrap;">LONG {l_size}</span>
                      <span class="badge {'primary' if l_role=='PRIMARY' else 'counter'}" style="font-size:9px; padding:1px 5px; white-space:nowrap;">{l_role}</span>
                    </div>
                    <b style="color:{lcol}; font-size:11px; white-space:nowrap; margin-left:auto;">${lpnl:+.2f} ({l_pnl_pct:+.2f}%)</b>
                  </div>
                  <div class="leg-body">
                    <div style="display:flex; justify-content:space-between; font-size:10px; margin-bottom:4px; color:#94a3b8;">
                      <span>Entry: <b style="color:#e2e8f0;">${l_entry:,.2f}</b></span>
                      <span>Peak: <b style="color:#e2e8f0;">${l_peak:,.2f}</b></span>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:4px;">
                      {l_sl_tag}
                      {l_tp_tag}
                    </div>
                  </div>
                </div>"""

                l_notional_str = f"(${l_size * px:,.1f})" if (px and l_size) else ""

                active_positions_rows.append(f"""<tr>
                  <td>{render_coin_badge(sym)}</td>
                  <td><span class="badge long">🟢 LONG</span></td>
                  <td><span class="badge {'primary' if l_role=='PRIMARY' else 'counter'}">{l_role}</span> <span class="badge event">{p_phase}</span></td>
                  <td style="font-family:'JetBrains Mono';">{l_size} <span style="color:#64748b; font-size:11px;">{l_notional_str}</span></td>
                  <td style="font-family:'JetBrains Mono';">${l_entry:,.2f}</td>
                  <td style="font-family:'JetBrains Mono'; font-weight:700;">{px_val_str}</td>
                  <td>{table_sl}</td>
                  <td>{table_tp}</td>
                  <td style="color:{lcol}; font-weight:700; font-family:'JetBrains Mono';">${lpnl:+.2f} <span style="font-size:11px;">({l_pnl_pct:+.2f}%)</span></td>
                </tr>""")

            short_html = '<div class="leg-box empty">Short: Idle (Scanning)</div>'
            if short_leg:
                spnl = float(short_leg.get("unrealized_pnl", 0.0) or 0.0)
                scol = "#10b981" if spnl >= 0 else "#ef4444"
                s_role = short_leg.get("role", "PRIMARY")
                s_entry = float(short_leg.get("entry_price", 0.0) or 0.0)
                s_sl = float(short_leg.get("trailing_sl", 0.0) or 0.0)
                s_tp = float(short_leg.get("tp_target", 0.0) or 0.0)
                s_size = float(short_leg.get("size", 0.0) or 0.0)
                s_trough = float(short_leg.get("trough_price", s_entry) or s_entry)
                s_pnl_pct = float(short_leg.get("pnl_pct", 0.0) or 0.0)

                s_sl_buf = (s_sl - px) if (px and s_sl) else 0.0
                s_sl_buf_pct = ((s_sl - px) / px * 100) if (px and s_sl and px > 0) else 0.0
                s_tp_buf = (px - s_tp) if (px and s_tp) else 0.0
                s_tp_buf_pct = ((px - s_tp) / px * 100) if (px and s_tp and px > 0) else 0.0

                if p_phase == "INCUBATION":
                    s_sl_tag = '<div class="target-tag event" title="Stop Loss arms upon ±0.80D breakout"><span>🛡️ SL</span><span style="font-size:9px; opacity:0.85;">Arms @ ±0.80D</span></div>'
                    s_tp_tag = f'<div class="target-tag event" title="Apex Take Profit target"><span>🎯 TP</span><span style="font-size:9px; opacity:0.85;">Target ${s_tp:,.2f}</span></div>' if s_tp > 0 else '<div></div>'
                    table_sl = '<span class="badge event" style="font-size:11px;">Pending ±0.80D</span><div style="font-size:10px; color:#94a3b8; margin-top:2px;">No stops in incubation</div>'
                    table_tp = f'<span class="target-tag tp" style="opacity:0.85;">🎯 Target ${s_tp:,.2f}</span><div style="font-size:10px; color:#94a3b8; margin-top:2px;">Arms on confirmation</div>' if s_tp > 0 else '---'
                else:
                    s_sl_tag = f'<div class="target-tag sl" title="Trailing Stop Loss"><span>🛡️ SL: ${s_sl:,.2f}</span><span style="font-size:9px; opacity:0.85;">(+${abs(s_sl_buf):,.1f})</span></div>' if s_sl > 0 else '<div class="target-tag event"><span>SL: ---</span></div>'
                    s_tp_tag = f'<div class="target-tag tp" title="Apex Take Profit"><span>🎯 TP: ${s_tp:,.2f}</span><span style="font-size:9px; opacity:0.85;">(-${abs(s_tp_buf):,.1f})</span></div>' if s_tp > 0 else '<div class="target-tag event"><span>TP: ---</span></div>'
                    table_sl = f'<span class="target-tag sl">🛡️ ${s_sl:,.2f}</span><div style="font-size:10px; color:#ef4444; margin-top:2px;">Buffer: ${abs(s_sl_buf):,.2f} ({abs(s_sl_buf_pct):.2f}%)</div>' if (s_sl > 0 and px) else (f'<span class="target-tag sl">🛡️ ${s_sl:,.2f}</span>' if s_sl > 0 else '---')
                    table_tp = f'<span class="target-tag tp">🎯 ${s_tp:,.2f}</span><div style="font-size:10px; color:#10b981; margin-top:2px;">Target: ${abs(s_tp_buf):,.2f} ({abs(s_tp_buf_pct):.2f}%)</div>' if (s_tp > 0 and px) else (f'<span class="target-tag tp">🎯 ${s_tp:,.2f}</span>' if s_tp > 0 else '---')

                short_html = f"""<div class="leg-box short" style="border-left: 3px solid #ef4444;">
                  <div class="leg-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; gap:4px;">
                    <div style="display:flex; align-items:center; gap:4px; min-width:0;">
                      <span class="badge short">SHORT {s_size}</span>
                      <span class="badge {'primary' if s_role=='PRIMARY' else 'counter'}" style="font-size:9px; padding:1px 5px; white-space:nowrap;">{s_role}</span>
                    </div>
                    <b style="color:{scol}; font-size:11px; white-space:nowrap; margin-left:auto;">${spnl:+.2f} ({s_pnl_pct:+.2f}%)</b>
                  </div>
                  <div class="leg-body">
                    <div style="display:flex; justify-content:space-between; font-size:10px; margin-bottom:4px; color:#94a3b8;">
                      <span>Entry: <b style="color:#e2e8f0;">${s_entry:,.2f}</b></span>
                      <span>Trough: <b style="color:#e2e8f0;">${s_trough:,.2f}</b></span>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:4px;">
                      {s_sl_tag}
                      {s_tp_tag}
                    </div>
                  </div>
                </div>"""

                # Add to active positions table
                s_notional_str = f"(${s_size * px:,.1f})" if (px and s_size) else ""

                active_positions_rows.append(f"""<tr>
                  <td>{render_coin_badge(sym)}</td>
                  <td><span class="badge short">🔴 SHORT</span></td>
                  <td><span class="badge {'primary' if s_role=='PRIMARY' else 'counter'}">{s_role}</span> <span class="badge event">{p_phase}</span></td>
                  <td style="font-family:'JetBrains Mono';">{s_size} <span style="color:#64748b; font-size:11px;">{s_notional_str}</span></td>
                  <td style="font-family:'JetBrains Mono';">${s_entry:,.2f}</td>
                  <td style="font-family:'JetBrains Mono'; font-weight:700;">{px_val_str}</td>
                  <td>{table_sl}</td>
                  <td>{table_tp}</td>
                  <td style="color:{scol}; font-weight:700; font-family:'JetBrains Mono';">${spnl:+.2f} <span style="font-size:11px;">({s_pnl_pct:+.2f}%)</span></td>
                </tr>""")

            market_cards.append(f"""
            <div class="card market-card" data-symbol="{sym}">
              <div class="market-header">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="sym-badge">{get_coin_icon(sym, size=20)} {sym}</span>
                  <span class="status-pill" style="border-color:{p_color}; color:{p_color}">{p_status}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="pct-badge {pct_cls}" id="pct-{sym}">{pct_str}</span>
                  <div class="sym-price">{px_str}</div>
                </div>
              </div>

              <div class="market-ind">{ind}</div>
              <div class="legs-grid">
                {long_html}
                {short_html}
              </div>
            </div>""")

        markets_html = "\n".join(market_cards) if market_cards else '<div class="card" style="text-align:center; color:#64748b;">Engine initializing markets...</div>'

        if active_positions_rows:
            active_positions_html = f"""
            <div class="table-container" style="margin-bottom:24px;">
              <table>
                <thead>
                  <tr>
                    <th>Market</th>
                    <th>Position Leg</th>
                    <th>Role &amp; State</th>
                    <th>Size / Notional</th>
                    <th>Entry Price</th>
                    <th>Mark Price</th>
                    <th>Trailing Stop Loss (SL)</th>
                    <th>Apex Take Profit (TP)</th>
                    <th>Unrealized PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(active_positions_rows)}
                </tbody>
              </table>
            </div>"""
        else:
            active_positions_html = """
            <div class="card" style="padding:16px 20px; text-align:center; color:#94a3b8; background:#0a101d; border:1px dashed #1e293b; margin-bottom:24px;">
              <div style="font-size:18px; margin-bottom:4px;">🛡️</div>
              <div style="font-weight:600; color:#f1f5f9; margin-bottom:4px;">No Open Positions Currently Active</div>
              <div style="font-size:12px; color:#64748b;">The multi-pair state machine is scanning 1h candles on BTCUSDT, ETHUSDT, SOLUSDT, and XAUUSDT for EMA(9/21) crosses with ADX gating. When a dual-leg entry triggers, active SL and TP protection levels will update here in real time.</div>
            </div>"""

        # Tab 1: Trade rows from CSV
        trade_rows = []
        for t in trades:
            leg_val = (t.get("leg") or t.get("leg_side") or "").upper()
            evt_val = t.get("event") or t.get("event_type") or ""
            try:
                px_val = float(t.get("price") or t.get("fill_price", 0) or 0)
            except (ValueError, TypeError):
                px_val = 0.0
            try:
                sl_val = float(t.get("trailing_sl", 0) or 0)
            except (ValueError, TypeError):
                sl_val = 0.0
            try:
                tp_val = float(t.get("tp_target", 0) or 0)
            except (ValueError, TypeError):
                tp_val = 0.0
            try:
                pnl_val = float(t.get("leg_pnl_usd") or t.get("pnl_usd", 0) or 0)
            except (ValueError, TypeError):
                pnl_val = 0.0
            try:
                cum_val = float(t.get("pair_cumulative_pnl", 0) or 0)
            except (ValueError, TypeError):
                cum_val = 0.0
            size_val = t.get("size", "")
            cycle_val = t.get("cycle", "1")

            pnl_col = "#10b981" if pnl_val > 0 else ("#ef4444" if pnl_val < 0 else "#94a3b8")
            cum_col = "#10b981" if cum_val >= 0 else "#ef4444"
            leg_badge = "long" if "LONG" in leg_val or "BUY" in leg_val else ("short" if "SHORT" in leg_val or "SELL" in leg_val else "event")

            evt_class = "event"
            if "TP" in evt_val:
                evt_class = "tp"
            elif "SL" in evt_val or "COLLAPSE" in evt_val:
                evt_class = "sl"
            elif "RATCHET" in evt_val:
                evt_class = "ratchet"
            elif "FLIP" in evt_val or "SIZE" in evt_val:
                evt_class = "flip"
            elif "ENTRY" in evt_val:
                evt_class = "primary"

            sl_str = f"${sl_val:,.2f}" if sl_val > 0 else "---"
            tp_str = f"${tp_val:,.2f}" if tp_val > 0 else "---"

            trade_rows.append(f"""<tr>
              <td>{t.get('timestamp','')}</td>
              <td>{render_coin_badge(t.get('symbol',''))}</td>
              <td style="text-align:center;"><span style="font-family:'JetBrains Mono'; font-size:11px; color:#94a3b8;">#{cycle_val}</span></td>
              <td><span class="badge {leg_badge}">{leg_val}</span></td>
              <td><span class="badge {evt_class}">{evt_val}</span></td>
              <td style="font-family:'JetBrains Mono';">${px_val:,.2f}</td>
              <td style="font-family:'JetBrains Mono';">{size_val}</td>
              <td style="font-family:'JetBrains Mono'; color:#f87171;">{sl_str}</td>
              <td style="font-family:'JetBrains Mono'; color:#34d399;">{tp_str}</td>
              <td style="color:{pnl_col}; font-weight:700; font-family:'JetBrains Mono';">${pnl_val:+.2f}</td>
              <td style="color:{cum_col}; font-weight:600; font-family:'JetBrains Mono';">${cum_val:+.2f}</td>
            </tr>""")

        # Tab 2: Exchange trades from Bybit API (Exact Bybit Closed P&L Layout: 13 columns)
        exchange_trade_rows = []
        for t in recent_exchange_trades:
            pnl_val = float(t.get("closed_pnl", 0) or 0)
            pnl_col = "#10b981" if pnl_val >= 0 else "#ef4444"
            side_raw = t.get("side", "")
            trade_type = t.get("trade_type", "Close Long" if side_raw.lower() == "sell" else "Close Short")
            is_close_long = "long" in trade_type.lower()
            type_col = "#10b981" if is_close_long else "#ef4444"

            ts_display = t.get("timestamp", "")
            if not ts_display and t.get("updated_time"):
                try:
                    ts_display = datetime.fromtimestamp(int(t.get("updated_time")) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts_display = ""

            open_fee = float(t.get("open_fee", 0) or t.get("openFee", 0) or 0)
            close_fee = float(t.get("close_fee", 0) or t.get("closeFee", 0) or 0)
            entry_px = float(t.get("entry_price", 0) or t.get("avgEntryPrice", 0) or 0)
            exit_px = float(t.get("exit_price", 0) or t.get("avgExitPrice", 0) or 0)
            qty_val = float(t.get("qty", 0) or 0)
            abs_q = abs(qty_val)

            if t.get("funding_fee") is not None:
                funding_fee = float(t.get("funding_fee"))
            else:
                gross = (exit_px - entry_px) * abs_q if is_close_long else (entry_px - exit_px) * abs_q
                funding_fee = gross - open_fee - close_fee - pnl_val

            fund_col = "#94a3b8" if abs(funding_fee) < 0.00005 else ("#34d399" if funding_fee > 0 else "#f87171")
            fund_str = f"{funding_fee:.4f}" if abs(funding_fee) >= 0.00005 else "0.0000"

            if abs_q == int(abs_q):
                q_num_str = str(int(abs_q))
            else:
                q_num_str = f"{abs_q:.6f}".rstrip("0").rstrip(".")
            qty_str = f"-{q_num_str}" if is_close_long else q_num_str

            open_vol = entry_px * abs_q
            close_vol = exit_px * abs_q

            open_fee_str = format_bybit_fee(open_fee)
            close_fee_str = format_bybit_fee(close_fee)

            result_badge = '<span class="badge-result win">Win</span>' if pnl_val >= 0 else '<span class="badge-result loss">Loss</span>'
            sym_badge = render_coin_badge(t.get("symbol", ""))

            exchange_trade_rows.append(f"""<tr>
              <td>{sym_badge}</td>
              <td style="font-family:'JetBrains Mono'; font-weight:600;">{qty_str}</td>
              <td style="font-family:'JetBrains Mono';">{entry_px:,.2f}</td>
              <td style="font-family:'JetBrains Mono';">{exit_px:,.2f}</td>
              <td><span style="color:{type_col}; font-weight:600;">{trade_type}</span></td>
              <td style="color:{pnl_col}; font-weight:700; font-family:'JetBrains Mono';">{pnl_val:+.4f}</td>
              <td>{result_badge}</td>
              <td style="font-family:'JetBrains Mono';">{open_vol:,.2f}</td>
              <td style="font-family:'JetBrains Mono';">{close_vol:,.2f}</td>
              <td style="font-family:'JetBrains Mono'; color:#cbd5e1;">{open_fee_str}</td>
              <td style="font-family:'JetBrains Mono'; color:#cbd5e1;">{close_fee_str}</td>
              <td style="font-family:'JetBrains Mono'; color:{fund_col};">{fund_str}</td>
              <td style="font-size:12px; color:#94a3b8; font-family:'JetBrains Mono';">{ts_display}</td>
            </tr>""")

        csv_trades_html = "\n".join(trade_rows) if trade_rows else '<tr><td colspan="11" style="text-align:center; padding:24px; color:#64748b;">No internal bot events recorded in bybit_trades.csv yet for the current session.<br><small style="color:#475569;">Switch to the <b>Bybit Exchange Closed P&amp;L</b> tab to see official Bybit closed position executions.</small></td></tr>'
        exchange_trades_html = "\n".join(exchange_trade_rows) if exchange_trade_rows else '<tr><td colspan="13" style="text-align:center; padding:24px; color:#64748b;">No closed position fills retrieved from Bybit UTA API yet.</td></tr>'

        # Symbol breakdown badges
        badges = []
        for s, val in sym_pnl.items():
            b_col = "#10b981" if val >= 0 else "#ef4444"
            badges.append(f'<span style="display:inline-flex; align-items:center; gap:5px; vertical-align:middle;">{get_coin_icon(s, size=16)} <b>{s}:</b> <span style="color:{b_col};">${val:+.2f}</span></span>')
        pnl_by_symbol_badges = " &bull; ".join(badges) if badges else '<span style="color:#64748b;">Awaiting trade events...</span>'

        return {
            "status_color": status_color,
            "status_text": status_text,
            "brand_status_html": brand_status_html,
            "stats_grid_html": stats_grid_html,
            "pnl_by_symbol_html": pnl_by_symbol_badges,
            "markets_html": markets_html,
            "active_positions_html": active_positions_html,
            "csv_trades_html": csv_trades_html,
            "csv_count": len(trade_rows),
            "exchange_trades_html": exchange_trades_html,
            "exchange_count": len(recent_exchange_trades),
            "logs": logs,
        }

    # ==========================================================================
    # HTML UI RENDERING
    # ==========================================================================

    def _render_login(self, error: str = "") -> str:
        err_html = f'<div class="error-msg">{error}</div>' if error else ""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bybit Bot Telemetry - Authenticate</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', sans-serif;
      background: #090d16;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 16px;
    }}
    .card {{
      background: #131b2e;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 36px 32px;
      width: 100%;
      max-width: 420px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.6);
      text-align: center;
    }}
    .logo {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.5px;
      color: #38bdf8;
      margin-bottom: 8px;
    }}
    .subtitle {{
      color: #94a3b8;
      font-size: 14px;
      margin-bottom: 28px;
    }}
    .form-group {{
      margin-bottom: 20px;
      text-align: left;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: #cbd5e1;
      margin-bottom: 8px;
    }}
    input[type="password"] {{
      width: 100%;
      padding: 12px 16px;
      background: #090d16;
      border: 1px solid #334155;
      border-radius: 8px;
      color: #fff;
      font-size: 15px;
      outline: none;
      transition: border-color 0.2s;
    }}
    input[type="password"]:focus {{
      border-color: #38bdf8;
    }}
    button {{
      width: 100%;
      padding: 13px;
      background: #0284c7;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }}
    button:hover {{
      background: #0369a1;
    }}
    .error-msg {{
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid #ef4444;
      color: #fca5a5;
      padding: 10px;
      border-radius: 6px;
      font-size: 13px;
      margin-bottom: 20px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">⚡ Bybit Hedge Bot</div>
    <div class="subtitle">Enter telemetry password to access live metrics & logs</div>
    {err_html}
    <form method="POST" action="/login">
      <div class="form-group">
        <label for="password">Password / Access Token</label>
        <input type="password" id="password" name="password" required autofocus placeholder="••••••••••••••••">
      </div>
      <button type="submit">Unlock Dashboard</button>
    </form>
  </div>
</body>
</html>"""

    def _render_dashboard(self) -> str:
        d = self._get_live_telemetry_payload()
        status_color = d["status_color"]
        research_css = get_research_css()
        research_html = get_research_html()
        research_js = get_research_js()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bybit Multi-Pair Hedge Bot - Telemetry</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #080c14;
      color: #e2e8f0;
      padding: 24px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid #1e293b;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-shrink: 0;
    }}
    .brand-title-wrap {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .brand h1 {{
      font-size: 21px;
      font-weight: 700;
      letter-spacing: -0.5px;
      white-space: nowrap;
    }}
    .pulse-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: {status_color};
      box-shadow: 0 0 10px {status_color};
      flex-shrink: 0;
    }}
    .status-pill {{
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      border: 1px solid;
      padding: 2px 8px;
      border-radius: 9999px;
      white-space: nowrap;
      flex-shrink: 0;
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      border: 1px solid #334155;
      background: #1e293b;
      color: #e2e8f0;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      white-space: nowrap;
      user-select: none;
    }}
    .btn:hover {{
      background: #334155;
      border-color: #475569;
      transform: translateY(-1px);
    }}
    .btn:active {{
      transform: translateY(0);
    }}
    .btn-primary {{
      background: #0284c7;
      border-color: #0284c7;
      color: #fff;
    }}
    .btn-primary:hover {{
      background: #0369a1;
      border-color: #0369a1;
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2), 0 2px 4px -2px rgba(0, 0, 0, 0.2);
      min-width: 0;
      overflow: hidden;
    }}
    .stat-title {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #94a3b8;
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .stat-val {{
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.5px;
      font-family: 'JetBrains Mono', monospace;
    }}
    .stat-sub {{
      font-size: 12px;
      color: #64748b;
      margin-top: 4px;
    }}
    .section-title {{
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .section-title span:first-child {{
      color: #f1f5f9;
      font-weight: 600;
    }}
    .section-title span:last-child {{
      font-size: 12px;
      color: #64748b;
    }}
    .markets-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .market-card {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-width: 0;
      overflow: hidden;
    }}
    .market-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .sym-badge {{
      font-size: 16px;
      font-weight: 700;
      color: #38bdf8;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .sym-price {{
      font-size: 17px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }}
    .pct-badge {{
      font-size: 12px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
      padding: 2px 7px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      letter-spacing: -0.2px;
    }}
    .pct-badge.up {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .pct-badge.down {{
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
    }}
    .market-ind {{
      font-size: 12px;
      color: #94a3b8;
      font-family: 'JetBrains Mono', monospace;
      background: #090e1a;
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid #1e293b;
    }}
    .legs-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      min-width: 0;
    }}
    .leg-box {{
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 11px;
      background: #151f32;
      border: 1px solid #243248;
      min-width: 0;
      overflow: hidden;
      box-sizing: border-box;
    }}
    .leg-box.empty {{
      color: #64748b;
      text-align: center;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 52px;
    }}
    .leg-header {{
      display: flex;
      justify-content: space-between;
      margin-bottom: 4px;
      font-weight: 600;
    }}
    .leg-body {{
      color: #94a3b8;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      line-height: 1.4;
    }}
    .table-container {{
      width: 100%;
      border-radius: 12px;
      border: 1px solid #1e293b;
      background: #0f172a;
      margin-bottom: 24px;
      overflow: hidden;
    }}
    .table-scroll {{
      width: 100%;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }}
    table {{
      width: 100%;
      min-width: 600px;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid #1e293b;
    }}
    th {{
      color: #94a3b8;
      font-weight: 600;
      background: #131d31;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge {{
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      display: inline-block;
    }}
    .badge.buy {{ background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge.sell {{ background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
    .badge.long {{ background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge.short {{ background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
    .badge.primary {{ background: rgba(56, 189, 248, 0.18); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
    .badge.counter {{ background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }}
    .badge.event {{ background: rgba(148, 163, 184, 0.12); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.2); font-weight: 600; }}
    .badge.tp {{ background: rgba(16, 185, 129, 0.25); color: #10b981; border: 1px solid #10b981; }}
    .badge.sl {{ background: rgba(239, 68, 68, 0.25); color: #ef4444; border: 1px solid #ef4444; }}
    .badge.ratchet {{ background: rgba(168, 85, 247, 0.18); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }}
    .badge.flip {{ background: rgba(249, 115, 22, 0.2); color: #fb923c; border: 1px solid rgba(249, 115, 22, 0.35); }}
    .badge-result {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      text-align: center;
      letter-spacing: 0.2px;
    }}
    .badge-result.win {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.3);
    }}
    .badge-result.loss {{
      background: rgba(148, 163, 184, 0.12);
      color: #94a3b8;
      border: 1px solid rgba(148, 163, 184, 0.2);
    }}
    .target-tag {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 4px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 4px;
      box-sizing: border-box;
      width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .target-tag.sl {{
      background: rgba(239, 68, 68, 0.12);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.25);
    }}
    .target-tag.tp {{
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }}
    .target-tag.event {{
      background: rgba(148, 163, 184, 0.12);
      color: #94a3b8;
      border: 1px solid rgba(148, 163, 184, 0.25);
    }}
    .trade-tabs {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
      border-bottom: 1px solid #1e293b;
      padding-bottom: 8px;
      flex-wrap: wrap;
    }}
    .trade-tab-btn {{
      background: #0d1526;
      border: 1px solid #1e293b;
      color: #94a3b8;
      font-size: 13px;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .trade-tab-btn:hover {{
      color: #f1f5f9;
      background: rgba(255, 255, 255, 0.05);
    }}
    .trade-tab-btn.active {{
      background: #1e293b;
      border-color: #38bdf8;
      color: #38bdf8;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    }}
    .tab-count {{
      font-size: 11px;
      background: rgba(255, 255, 255, 0.1);
      padding: 1px 7px;
      border-radius: 999px;
      font-family: 'JetBrains Mono', monospace;
    }}
    .pagination-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 18px;
      background: #090e1a;
      border-top: 1px solid #1e293b;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .pagination-info {{
      font-size: 12px;
      color: #94a3b8;
      font-family: 'JetBrains Mono', monospace;
      font-weight: 500;
      white-space: nowrap;
    }}
    .pagination-actions {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .btn-page {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 5px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid #334155;
      background: #1e293b;
      color: #e2e8f0;
      transition: all 0.15s ease;
      user-select: none;
    }}
    .btn-page:hover:not(:disabled) {{
      background: #334155;
      color: #fff;
      border-color: #475569;
    }}
    .btn-page:disabled {{
      opacity: 0.35;
      cursor: not-allowed;
      border-color: #1e293b;
    }}
    .page-pills {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }}
    .page-pill {{
      min-width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0 6px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      background: #131d31;
      border: 1px solid #1e293b;
      color: #94a3b8;
      transition: all 0.15s ease;
      user-select: none;
    }}
    .page-pill:hover:not(.active) {{
      background: #1e293b;
      color: #fff;
      border-color: #334155;
    }}
    .page-pill.active {{
      background: #0284c7;
      border-color: #38bdf8;
      color: #ffffff;
      font-weight: 700;
      box-shadow: 0 0 8px rgba(56, 189, 248, 0.4);
    }}
    .btn-show-more {{
      display: inline-flex;
      align-items: center;
      padding: 5px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.3);
      color: #38bdf8;
      transition: all 0.15s ease;
      user-select: none;
      margin-left: 4px;
    }}
    .btn-show-more:hover {{
      background: rgba(56, 189, 248, 0.2);
      color: #7dd3fc;
      border-color: #38bdf8;
    }}
    .pagination-size {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: #94a3b8;
    }}
    .pagination-size select {{
      background: #090e1a;
      border: 1px solid #334155;
      color: #f1f5f9;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 8px;
      border-radius: 6px;
      outline: none;
      cursor: pointer;
      transition: border-color 0.15s ease;
    }}
    .pagination-size select:focus {{
      border-color: #38bdf8;
    }}
    .terminal {{
      background: #04060a;
      border: 1px solid #1e293b;
      border-radius: 10px;
      padding: 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: #94a3b8;
      max-height: 360px;
      overflow-y: auto;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.45;
    }}
    .hide-mobile {{
      display: inline;
    }}

    /* Mobile Responsive Optimizations */
    @media (max-width: 768px) {{
      body {{
        padding: 14px 10px;
      }}
      header {{
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
        margin-bottom: 16px;
        padding-bottom: 14px;
      }}
      .brand {{
        width: 100%;
        justify-content: space-between;
      }}
      .brand h1 {{
        font-size: 17px;
        white-space: normal;
      }}
      .hide-mobile {{
        display: none;
      }}
      .header-actions {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 6px;
        width: 100%;
      }}
      .header-actions .btn {{
        padding: 8px 4px;
        font-size: 11px;
        text-align: center;
        justify-content: center;
      }}
      .stats-grid {{
        grid-template-columns: repeat(2, 1fr);
        gap: 8px;
        margin-bottom: 18px;
      }}
      .card {{
        padding: 12px;
      }}
      .stat-title {{
        font-size: 10px;
      }}
      .stat-val {{
        font-size: 18px;
      }}
      .stat-sub {{
        font-size: 10px;
      }}
      .markets-grid {{
        grid-template-columns: 1fr;
        gap: 10px;
      }}
      .legs-grid {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}
    }}
    @media (max-width: 380px) {{
      .header-actions {{
        grid-template-columns: repeat(2, 1fr);
      }}
      .brand h1 {{
        font-size: 15px;
      }}
    }}
    {research_css}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand" id="brand-status">
        {d['brand_status_html']}
      </div>
      <div class="header-actions">
        <a href="/api/ai-summary" class="btn" target="_blank" title="AI Summary Markdown">🤖 AI View</a>
        <a href="/api/status" class="btn" target="_blank" title="JSON Status API">⚡ JSON</a>
        <button onclick="refreshLiveTelemetry()" class="btn btn-primary" title="Refresh Telemetry">🔄 Refresh</button>
        <a href="/logout" class="btn" title="Logout">🚪 Exit</a>
      </div>
    </header>

    <!-- Main Top Navigation Tabs -->
    <div class="view-nav-tabs">
      <button id="btn-tab-live" class="view-tab-btn active" onclick="switchMainView('live')">
        <span class="tab-indicator live-ind"></span>
        <span style="font-weight:700;">Live Operations &amp; Positions</span>
        <span class="tab-badge live-badge">BOT ACTIVE</span>
      </button>
      <button id="btn-tab-research" class="view-tab-btn" onclick="switchMainView('research')">
        <span class="tab-indicator research-ind"></span>
        <span style="font-weight:700;">Research &amp; Backtesting Suite</span>
        <span class="tab-badge research-badge">1-MIN REPLAY &bull; MC &bull; OPTIMIZER</span>
      </button>
    </div>

    <div id="view-live">
    <div class="stats-grid" id="stats-grid">
      {d['stats_grid_html']}
    </div>

    <div class="card" style="margin-bottom:24px; padding:12px 18px; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; background:#0b1329;">
      <div style="font-size:12px; font-weight:700; text-transform:uppercase; color:#94a3b8; letter-spacing:0.5px;">Bybit Realized P&L by Symbol:</div>
      <div id="pnl-by-symbol" style="display:flex; flex-wrap:wrap; gap:14px; font-family:'JetBrains Mono', monospace; font-size:13px;">
        {d['pnl_by_symbol_html']}
      </div>
    </div>

    <div class="section-title">
      <span>Market Watch & Active Hedge Legs</span>
    </div>
    <div class="markets-grid" id="markets-grid">
      {d['markets_html']}
    </div>

    <div class="section-title">
      <span>Active Live Positions & Order Protection</span>
      <span style="font-size:12px; color:#64748b;">Live Trailing SL & Apex TP Targets</span>
    </div>
    <div id="active-positions-wrap">
      {d['active_positions_html']}
    </div>

    <div class="section-title">
      <span>Trade Execution & Audit Ledgers</span>
      <span style="font-size:12px; color:#64748b;">Bot State Machine vs. Bybit Exchange Fills</span>
    </div>

    <div class="trade-tabs">
      <button id="trade-tab-csv" class="trade-tab-btn active" onclick="switchTradeTab('csv')">
        📜 Bot State Machine Audit <code>(bybit_trades.csv)</code>
        <span id="tab-count-csv" class="tab-count">{d['csv_count']}</span>
      </button>
      <button id="trade-tab-exchange" class="trade-tab-btn" onclick="switchTradeTab('exchange')">
        🏛️ Bybit Exchange Closed P&amp;L Ledger <code>(UTA V5)</code>
        <span id="tab-count-exchange" class="tab-count">{d['exchange_count']}</span>
      </button>
      <div id="clear-csv-wrap" style="margin-left:auto; display:flex; align-items:center; gap:8px;">
        <a href="/api/clear-trades?redirect=1" class="btn" style="font-size:11px; padding:4px 10px; border-color:#475569;" onclick="clearCsvHistory(event)" title="Clear local CSV history only">🧹 Clear CSV History</a>
      </div>
    </div>

    <!-- Tab 1: bybit_trades.csv -->
    <div id="trade-table-csv" class="table-container">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Symbol</th>
              <th>Cycle</th>
              <th>Leg</th>
              <th>State Event</th>
              <th>Fill Price</th>
              <th>Size</th>
              <th>Trailing SL</th>
              <th>Apex TP</th>
              <th>Leg PnL</th>
              <th>Cumul PnL</th>
            </tr>
          </thead>
          <tbody id="csv-trades-body">
            {d['csv_trades_html']}
          </tbody>
        </table>
      </div>
      <div class="pagination-bar" id="csv-pagination">
        <div class="pagination-info" id="csv-page-info">Showing 1–50 of {d['csv_count']} trades</div>
        <div class="pagination-actions">
          <button type="button" class="btn-page" id="csv-prev-btn" onclick="changePage('csv', -1)" disabled title="Previous Page">◀ Prev</button>
          <div class="page-pills" id="csv-page-pills"></div>
          <button type="button" class="btn-page" id="csv-next-btn" onclick="changePage('csv', 1)" title="Next Page">Next ▶</button>
          <button type="button" class="btn-show-more" id="csv-more-btn" onclick="showMoreRows('csv')">⬇ Show More (+50)</button>
        </div>
        <div class="pagination-size">
          <label for="csv-page-size">Per page:</label>
          <select id="csv-page-size" onchange="changePageSize('csv', this.value)">
            <option value="25">25</option>
            <option value="50" selected>50</option>
            <option value="100">100</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Tab 2: Bybit Exchange Closed PnL -->
    <div id="trade-table-exchange" class="table-container" style="display:none;">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Contracts</th>
              <th>Qty</th>
              <th>Entry Price</th>
              <th>Exit Price</th>
              <th>Trade Type</th>
              <th>Closed P&amp;L</th>
              <th>Result</th>
              <th>Open Trade Volume</th>
              <th>Closed Trade Volume</th>
              <th>Opening Fee</th>
              <th>Closing Fee</th>
              <th>Funding Fee</th>
              <th>Trade Time</th>
            </tr>
          </thead>
          <tbody id="exchange-trades-body">
            {d['exchange_trades_html']}
          </tbody>
        </table>
      </div>
      <div class="pagination-bar" id="exchange-pagination">
        <div class="pagination-info" id="exchange-page-info">Showing 1–50 of {d['exchange_count']} trades</div>
        <div class="pagination-actions">
          <button type="button" class="btn-page" id="exchange-prev-btn" onclick="changePage('exchange', -1)" disabled title="Previous Page">◀ Prev</button>
          <div class="page-pills" id="exchange-page-pills"></div>
          <button type="button" class="btn-page" id="exchange-next-btn" onclick="changePage('exchange', 1)" title="Next Page">Next ▶</button>
          <button type="button" class="btn-show-more" id="exchange-more-btn" onclick="showMoreRows('exchange')">⬇ Show More (+50)</button>
        </div>
        <div class="pagination-size">
          <label for="exchange-page-size">Per page:</label>
          <select id="exchange-page-size" onchange="changePageSize('exchange', this.value)">
            <option value="25">25</option>
            <option value="50" selected>50</option>
            <option value="100">100</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>
    </div>

    <div class="section-title">
      <span>Live System Logs & Crash Diagnostics</span>
      <span style="font-size:12px; color:#64748b;">journalctl -u bybit-bot -n 80</span>
    </div>
    <div class="terminal" id="term">{d['logs']}</div>
    </div> <!-- end view-live -->

    {research_html}
  </div> <!-- end container -->

  <script>
    // Live Real-Time WebSocket Telemetry (Zero page refresh)
    let ws = null;
    let wsPollFallback = null;

    function connectTelemetryWS() {{
      const protocol = (location.protocol === 'https:') ? 'wss://' : 'ws://';
      const wsUrl = protocol + location.host + '/ws' + location.search;

      try {{
        ws = new WebSocket(wsUrl);
      }} catch (err) {{
        startPollingFallback();
        return;
      }}

      ws.onopen = () => {{
        console.log('[WS] Connected to live Bybit telemetry stream');
        if (wsPollFallback) {{
          clearInterval(wsPollFallback);
          wsPollFallback = null;
        }}
      }};

      ws.onmessage = (event) => {{
        try {{
          const d = JSON.parse(event.data);
          applyLiveUpdate(d);
        }} catch (e) {{
          console.error('[WS] Error parsing payload:', e);
        }}
      }};

      ws.onerror = () => {{
        try {{ ws.close(); }} catch(e) {{}}
      }};

      ws.onclose = () => {{
        startPollingFallback();
        setTimeout(connectTelemetryWS, 2500);
      }};
    }}

    function startPollingFallback() {{
      if (wsPollFallback) return;
      wsPollFallback = setInterval(async () => {{
        try {{
          const res = await fetch('/api/live-status' + location.search);
          if (res.ok) {{
            const d = await res.json();
            applyLiveUpdate(d);
          }}
        }} catch (e) {{}}
      }}, 3000);
    }}

    // Client-side pagination state (default 50 trades per page)
    const paginationState = {{
      csv: {{ page: 1, pageSize: 50, allRows: [] }},
      exchange: {{ page: 1, pageSize: 50, allRows: [] }}
    }};

    function extractTrRows(html) {{
      if (!html) return [];
      const temp = document.createElement('tbody');
      temp.innerHTML = html.trim();
      const trs = Array.from(temp.querySelectorAll('tr')).filter(function(tr) {{
        return !tr.querySelector('td[colspan]');
      }});
      return trs.map(function(tr) {{ return tr.outerHTML; }});
    }}

    function renderPaginatedTable(tab) {{
      const st = paginationState[tab];
      const tbody = document.getElementById(tab === 'csv' ? 'csv-trades-body' : 'exchange-trades-body');
      const infoEl = document.getElementById(tab + '-page-info');
      const pillsEl = document.getElementById(tab + '-page-pills');
      const prevBtn = document.getElementById(tab + '-prev-btn');
      const nextBtn = document.getElementById(tab + '-next-btn');
      const moreBtn = document.getElementById(tab + '-more-btn');
      const countPill = document.getElementById(tab === 'csv' ? 'tab-count-csv' : 'tab-count-exchange');
      if (!tbody) return;

      const total = st.allRows.length;
      if (countPill) countPill.textContent = total;

      if (total === 0) {{
        if (tab === 'csv') {{
          tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:24px; color:#64748b;">No internal bot events recorded in bybit_trades.csv yet for the current session.<br><small style="color:#475569;">Switch to the <b>Bybit Exchange Closed P&amp;L</b> tab to see official Bybit closed position executions.</small></td></tr>';
        }} else {{
          tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:24px; color:#64748b;">No closed position fills retrieved from Bybit UTA API yet.</td></tr>';
        }}
        if (infoEl) infoEl.textContent = 'Showing 0 trades';
        if (pillsEl) pillsEl.innerHTML = '';
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
        if (moreBtn) moreBtn.style.display = 'none';
        return;
      }}

      const effPageSize = (st.pageSize === 'all') ? total : parseInt(st.pageSize, 10);
      const totalPages = Math.max(1, Math.ceil(total / effPageSize));
      if (st.page > totalPages) st.page = totalPages;
      if (st.page < 1) st.page = 1;

      const startIdx = (st.page - 1) * effPageSize;
      const endIdx = Math.min(startIdx + effPageSize, total);
      const visible = st.allRows.slice(startIdx, endIdx);

      tbody.innerHTML = visible.join('\\n');

      if (infoEl) {{
        infoEl.textContent = 'Showing ' + (startIdx + 1) + '–' + endIdx + ' of ' + total + ' trades';
      }}

      if (prevBtn) prevBtn.disabled = (st.page <= 1);
      if (nextBtn) nextBtn.disabled = (st.page >= totalPages);

      if (moreBtn) {{
        if (endIdx < total && st.pageSize !== 'all') {{
          const remaining = total - endIdx;
          const step = Math.min(50, remaining);
          moreBtn.style.display = 'inline-flex';
          moreBtn.textContent = '⬇ Show More (+' + step + ')';
        }} else {{
          moreBtn.style.display = 'none';
        }}
      }}

      if (pillsEl) {{
        if (totalPages <= 1) {{
          pillsEl.innerHTML = '<span class="page-pill active">1</span>';
        }} else {{
          let html = '';
          const maxPills = 5;
          let startP = Math.max(1, st.page - 2);
          let endP = Math.min(totalPages, startP + maxPills - 1);
          if (endP - startP < maxPills - 1) {{
            startP = Math.max(1, endP - maxPills + 1);
          }}

          if (startP > 1) {{
            html += '<button type="button" class="page-pill" onclick="gotoPage(\\'' + tab + '\\', 1)">1</button>';
            if (startP > 2) html += '<span style="color:#64748b; padding:0 2px;">…</span>';
          }}

          for (let p = startP; p <= endP; p++) {{
            const cls = (p === st.page) ? 'active' : '';
            html += '<button type="button" class="page-pill ' + cls + '" onclick="gotoPage(\\'' + tab + '\\', ' + p + ')">' + p + '</button>';
          }}

          if (endP < totalPages) {{
            if (endP < totalPages - 1) html += '<span style="color:#64748b; padding:0 2px;">…</span>';
            html += '<button type="button" class="page-pill" onclick="gotoPage(\\'' + tab + '\\', ' + totalPages + ')">' + totalPages + '</button>';
          }}
          pillsEl.innerHTML = html;
        }}
      }}
    }}

    function gotoPage(tab, p) {{
      paginationState[tab].page = p;
      renderPaginatedTable(tab);
    }}

    function changePage(tab, delta) {{
      paginationState[tab].page += delta;
      renderPaginatedTable(tab);
    }}

    function changePageSize(tab, size) {{
      paginationState[tab].pageSize = size;
      paginationState[tab].page = 1;
      renderPaginatedTable(tab);
    }}

    function showMoreRows(tab) {{
      const st = paginationState[tab];
      if (st.pageSize === 'all') return;
      const curSize = parseInt(st.pageSize, 10);
      st.pageSize = curSize + 50;
      const sel = document.getElementById(tab + '-page-size');
      if (sel) {{
        let opt = Array.from(sel.options).find(function(o) {{ return o.value == st.pageSize; }});
        if (!opt) {{
          opt = document.createElement('option');
          opt.value = st.pageSize;
          opt.textContent = st.pageSize;
          sel.insertBefore(opt, sel.lastElementChild);
        }}
        sel.value = st.pageSize;
      }}
      renderPaginatedTable(tab);
    }}

    function applyLiveUpdate(d) {{
      if (!d) return;
      if (d.brand_status_html) {{
        const el = document.getElementById('brand-status');
        if (el) el.innerHTML = d.brand_status_html;
      }}
      if (d.stats_grid_html) {{
        const el = document.getElementById('stats-grid');
        if (el) el.innerHTML = d.stats_grid_html;
      }}
      if (d.pnl_by_symbol_html) {{
        const el = document.getElementById('pnl-by-symbol');
        if (el) el.innerHTML = d.pnl_by_symbol_html;
      }}
      if (d.markets_html) {{
        const el = document.getElementById('markets-grid');
        if (el) el.innerHTML = d.markets_html;
      }}
      if (d.active_positions_html) {{
        const el = document.getElementById('active-positions-wrap');
        if (el) el.innerHTML = d.active_positions_html;
      }}
      if (d.csv_trades_html) {{
        paginationState.csv.allRows = extractTrRows(d.csv_trades_html);
        renderPaginatedTable('csv');
      }}
      if (d.csv_count !== undefined) {{
        const el = document.getElementById('tab-count-csv');
        if (el) el.textContent = d.csv_count;
      }}
      if (d.exchange_trades_html) {{
        paginationState.exchange.allRows = extractTrRows(d.exchange_trades_html);
        renderPaginatedTable('exchange');
      }}
      if (d.exchange_count !== undefined) {{
        const el = document.getElementById('tab-count-exchange');
        if (el) el.textContent = d.exchange_count;
      }}
      if (d.logs) {{
        const term = document.getElementById('term');
        if (term) {{
          const atBottom = (term.scrollHeight - term.clientHeight) <= (term.scrollTop + 60);
          term.textContent = d.logs;
          if (atBottom) term.scrollTop = term.scrollHeight;
        }}
      }}
    }}

    async function refreshLiveTelemetry() {{
      try {{
        const res = await fetch('/api/live-status' + location.search);
        if (res.ok) {{
          const d = await res.json();
          applyLiveUpdate(d);
        }}
      }} catch (e) {{}}
    }}

    async function clearCsvHistory(e) {{
      if (e) e.preventDefault();
      if (!confirm('Clear local bybit_trades.csv audit ledger? (Does not affect exchange fills)')) return false;
      try {{
        const res = await fetch('/api/clear-trades');
        const data = await res.json();
        if (data && data.success) {{
          paginationState.csv.allRows = [];
          paginationState.csv.page = 1;
          renderPaginatedTable('csv');
          const c = document.getElementById('tab-count-csv');
          if (c) c.textContent = '0';
        }}
      }} catch (err) {{
        location.href = '/api/clear-trades?redirect=1';
      }}
      return false;
    }}

    function switchTradeTab(tab) {{
      const csvBtn = document.getElementById('trade-tab-csv');
      const exchBtn = document.getElementById('trade-tab-exchange');
      const csvTable = document.getElementById('trade-table-csv');
      const exchTable = document.getElementById('trade-table-exchange');
      const clearWrap = document.getElementById('clear-csv-wrap');
      if (!csvBtn || !exchBtn || !csvTable || !exchTable) return;

      if (tab === 'exchange') {{
        csvBtn.classList.remove('active');
        exchBtn.classList.add('active');
        csvTable.style.display = 'none';
        exchTable.style.display = 'block';
        if (clearWrap) clearWrap.style.display = 'none';
      }} else {{
        exchBtn.classList.remove('active');
        csvBtn.classList.add('active');
        exchTable.style.display = 'none';
        csvTable.style.display = 'block';
        if (clearWrap) clearWrap.style.display = 'flex';
      }}
      localStorage.setItem('active_trade_tab', tab);
    }}

    // Initial pagination setup from server-rendered rows
    const initCsvTbody = document.getElementById('csv-trades-body');
    if (initCsvTbody) {{
      paginationState.csv.allRows = extractTrRows(initCsvTbody.innerHTML);
      renderPaginatedTable('csv');
    }}
    const initExchTbody = document.getElementById('exchange-trades-body');
    if (initExchTbody) {{
      paginationState.exchange.allRows = extractTrRows(initExchTbody.innerHTML);
      renderPaginatedTable('exchange');
    }}

    // Auto-restore trade tab preference or auto-switch to exchange if CSV is empty
    const savedTradeTab = localStorage.getItem('active_trade_tab');
    if (savedTradeTab === 'exchange' || (!{1 if d['csv_count'] else 0} && {1 if d['exchange_count'] else 0})) {{
      switchTradeTab('exchange');
    }} else {{
      switchTradeTab('csv');
    }}

    // Connect WebSocket
    connectTelemetryWS();

    // Initial scroll terminal to bottom
    const term = document.getElementById('term');
    if (term) term.scrollTop = term.scrollHeight;

    {research_js}
  </script>
</body>
</html>"""


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, TelemetryHandler)
    print(f"=================================================================")
    print(f"⚡ Bybit Telemetry Server active on port {PORT}")
    print(f"🔑 Protected by password/token authentication")
    print(f"📊 Dashboard URL: http://localhost:{PORT}/dashboard?password={PASSWORD}")
    print(f"🤖 AI Endpoint:  http://localhost:{PORT}/api/ai-summary?password={PASSWORD}")
    print(f"=================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nTelemetry server stopped.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
