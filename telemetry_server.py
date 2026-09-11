#!/usr/bin/env python3
"""
Secure Password-Protected HTTP Telemetry & AI Monitoring Server
# Dedicated to Bybit Single-Leg Trend Runner & Multi-Pair Concurrency Engine.
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

try:
    from bybit_bot.config import DEFAULT_PROFILES
except ImportError:
    DEFAULT_PROFILES = {}

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
    except Exception as e:
        logger.debug(f"Error fetching Bybit account/trades: {e}")

    return res_data


TICKERS_CACHE = {}
TICKERS_CACHE_TTL = 15.0  # seconds


def fetch_24h_tickers() -> dict:
    """Fetch 24h price change percentages for symbols."""
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
        req = urllib.request.Request(url, headers={"User-Agent": "BybitHedgeBot/1.0"})
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


def fetch_candles(symbol: str, interval: str = "60", limit: int = 100) -> dict:
    """Fetch historical kline candles from Bybit Linear Perpetual API."""
    now = time.time()
    cache_key = (symbol, interval, limit)
    if cache_key in CANDLE_CACHE:
        cached_ts, cached_data = CANDLE_CACHE[cache_key]
        if now - cached_ts < CANDLE_CACHE_TTL:
            return cached_data

    testnet = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]
    domain = "api-testnet.bybit.com" if testnet else "api.bybit.com"

    interval_map = {"1": "1", "3": "3", "5": "5", "15": "15", "30": "30", "60": "60", "120": "120", "240": "240", "D": "D", "W": "W"}
    api_interval = interval_map.get(str(interval), "60")

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
    """Read trade audit log from bybit_trades.csv."""
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
    """Format Bybit fee e.g. 0.0121 USDT matching exchange UI."""
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
    """Return crisp inline SVG coin icons for the 8-asset universe."""
    s = symbol.upper()
    if "BTC" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#f7931a"/><path d="M15.5 10.5c.3-1.5-.9-2.3-2.5-2.8l.5-2-1.2-.3-.5 2c-.3-.1-.7-.2-1-.2l.5-2-1.2-.3-.5 2-2.5-.6-.4 1.4s.9.2.9.2c.5.1.6.5.6.7l-.6 2.4c0 0 .1 0 .1 0l-.1 0-.8 3.3c-.1.2-.2.4-.6.3 0 0-.9-.2-.9-.2l-.6 1.5 2.4.6c.4.1.7.2 1.1.2l-.5 2.1 1.2.3.5-2c.3.1.7.2 1 .2l-.5 2 1.2.3.5-2.1c2.1.4 3.7.2 4.4-1.7.5-1.5 0-2.4-1.1-3 .8-.2 1.4-.7 1.6-1.8zm-2.8 4c-.4 1.5-3 .7-3.8.5l.7-2.8c.8.2 3.5.6 3.1 2.3zm.4-4c-.3 1.4-2.5.7-3.2.5l.6-2.5c.7.2 2.9.5 2.6 2z" fill="#fff"/></svg>'
    elif "ETH" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#627eea"/><path d="M12 4v6.6l5.6 2.5L12 4z" fill="#fff" fill-opacity=".6"/><path d="M12 4L6.4 13.1l5.6-2.5V4z" fill="#fff"/><path d="M12 17.5v4.5l5.6-7.8L12 17.5z" fill="#fff" fill-opacity=".6"/><path d="M12 22v-4.5L6.4 14.2L12 22z" fill="#fff"/><path d="M12 16.5l5.6-3.3L12 10.6v5.9z" fill="#fff" fill-opacity=".2"/><path d="M6.4 13.2l5.6 3.3v-5.9l-5.6 2.6z" fill="#fff" fill-opacity=".6"/></svg>'
    elif "SOL" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#0f172a" stroke="#14F195" stroke-width="1.5"/><path d="M7 16h8.5l1.5-1.5H8.5L7 16zm0-7h8.5l1.5-1.5H8.5L7 9zm10 3.5H8.5L7 14h8.5l1.5-1.5z" fill="#14F195"/></svg>'
    elif "AVAX" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#e84142"/><path d="M15.5 16.5h3L13 6.5l-2.5 4.5 2.5 4.5h2.5zm-5-3.5L8 16.5H5.5L10 8.5l2 3.5-1.5 1z" fill="#fff"/></svg>'
    elif "LINK" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#375bd2"/><path d="M12 6.5l4.8 2.8v5.4L12 17.5l-4.8-2.8V9.3L12 6.5zm0 2.3l-2.8 1.6v3.2L12 15.2l2.8-1.6v-3.2L12 8.8z" fill="#fff"/></svg>'
    elif "HYPE" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#0b1e36" stroke="#00f5a0" stroke-width="1.5"/><path d="M8 7v10h2.5v-3.5h3V17H16V7h-2.5v4h-3V7H8z" fill="#00f5a0"/></svg>'
    elif "XMR" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#ff6600"/><path d="M12 5l5 5v7h-2.5v-5.5L12 9l-2.5 2.5V17H7v-7l5-5z" fill="#fff"/></svg>'
    elif "DOGE" in s:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0; vertical-align:middle;"><circle cx="12" cy="12" r="11" fill="#c2a633"/><path d="M8 7h4c2.8 0 5 2.2 5 5s-2.2 5-5 5H8V7zm2.5 7.5h1.5c1.4 0 2.5-1.1 2.5-2.5s-1.1-2.5-2.5-2.5h-1.5v5z" fill="#fff"/><path d="M7 11.5h10v1H7z" fill="#fff"/></svg>'
    else:
        return f'<span style="display:inline-block; width:{size}px; height:{size}px; border-radius:50%; background:#334155; color:#cbd5e1; font-size:10px; line-height:{size}px; text-align:center; flex-shrink:0; vertical-align:middle;">●</span>'


def render_coin_badge(symbol: str, size: int = 20) -> str:
    """Render coin icon and bold symbol badge."""
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
    server_version = "BybitSingleLegTelemetry/2.0"

    def _is_authenticated(self) -> bool:
        """Check authentication via Cookie, Header, or Query Parameter."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        q_pass = qs.get("password", [None])[0] or qs.get("token", [None])[0]
        if q_pass and hmac.compare_digest(q_pass, PASSWORD):
            return True

        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
            if hmac.compare_digest(token, PASSWORD):
                return True

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
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            post_data = parse_qs(body)
            pwd = post_data.get("password", [""])[0]

            if hmac.compare_digest(pwd, PASSWORD):
                sid = secrets.token_hex(24)
                ACTIVE_SESSIONS.add(sid)
                cookie = f"telemetry_session={sid}; Path=/; HttpOnly; SameSite=Lax"
                self._redirect("/dashboard", cookie=cookie)
            else:
                self._send_html(self._render_login(error="Invalid password. Please try again."), status=401)
            return

        self.send_error(404, "Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/login":
            if self._is_authenticated():
                self._redirect("/dashboard")
            else:
                self._send_html(self._render_login())
            return

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
        if parsed.path in ["/", "/dashboard"]:
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
        open_pnl = state.get("open_pnl", 0.0)
        trades_count = state.get("total_trades_count") or acc.get("trades_count", 0)

        data = {
            "status": "online" if svc.get("active", True) else "offline",
            "strategy": "Single-Leg Trend Runner & Zero-Loss Ratchet",
            "service": svc,
            "account": {
                "equity_usd": equity,
                "available_usd": avail,
                "realized_pnl_usd": realized_pnl,
                "open_pnl_usd": open_pnl,
                "trades_count": trades_count,
            },
            "concurrency": {
                "active_pairs": state.get("active_pairs_count", 0),
                "max_concurrent_pairs": state.get("max_concurrent_pairs", 4),
                "leverage": state.get("leverage", 4),
            },
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": state.get("uptime_seconds", 0),
            "pairs": state.get("pairs", {}),
        }
        self._send_json(data)

    def _handle_api_ai_summary(self):
        """Generate high-signal, compact Markdown summary for AI quantitative reasoning."""
        state = read_bot_state()
        svc = get_service_status()
        acc = fetch_bybit_account_and_trades()
        trades = read_trade_history(limit=10)
        logs = get_systemd_logs(lines=15)

        uptime_sec = state.get("uptime_seconds", 0)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s" if uptime_sec else "Running"

        total_pnl = state.get("exchange_realized_pnl") or acc.get("total_realized_pnl", 0.0)
        open_pnl = state.get("open_pnl", 0.0)
        equity = state.get("account_equity") or acc.get("equity", 0.0)
        avail = state.get("available_balance") or acc.get("available_balance", 0.0)
        sym_pnl = state.get("exchange_pnl_by_symbol") or acc.get("by_symbol", {})
        trades_cnt = state.get("total_trades_count") or acc.get("trades_count", 0)

        active_count = state.get("active_pairs_count", 0)
        max_pairs = state.get("max_concurrent_pairs", 4)
        network = state.get("network", "TESTNET")
        leverage = state.get("leverage", 4)

        md = []
        md.append(f"# Bybit Single-Leg Trend Runner: Live Telemetry Summary")
        md.append(f"**Strategy**: Single-Leg Trend Runner & Zero-Loss Ratchet Engine")
        md.append(f"**Timestamp**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}` | **Service**: `{svc.get('status', 'active').upper()}` (PID: {svc.get('pid', 'N/A')})")
        md.append(f"- **Universe**: 8 Champion Pairs (`AVAX`, `LINK`, `HYPE`, `DOGE`, `XMR`, `BTC`, `ETH`, `SOL`)")
        md.append(f"- **Concurrency**: `{active_count}/{max_pairs}` Slots Active | **Leverage**: `{leverage}x` | **Uptime**: `{uptime_str}` | **Scans**: `{state.get('scan_count', 0)}`")
        if equity:
            md.append(f"- **Account Equity**: `${equity:,.2f} USDT` | **Available Margin**: `${avail:,.2f} USDT`")
        md.append(f"- **Realized PnL**: `${total_pnl:+.2f} USDT` across `{trades_cnt}` closed Bybit trades | **Open PnL**: `${open_pnl:+.2f}`")
        target_universe = ["AVAXUSDT", "LINKUSDT", "HYPEUSDT", "DOGEUSDT", "XMRUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        sym_parts = [f"{k}: `${sym_pnl.get(k, 0.0):+.2f}`" for k in target_universe]
        md.append(f"- **Realized PnL by Symbol**: {' | '.join(sym_parts)}")
        md.append("")

        md.append("## Active Single-Leg Positions & Scanner State")
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
                ind_str = f"EMA({ema_fast:.1f}/{ema_slow:.1f}) ADX={adx:.1f}" if (ema_fast and ema_slow and adx) else "Scanning..."

                long_leg = p.get("long_leg")
                short_leg = p.get("short_leg")
                active_leg = long_leg if long_leg else short_leg

                if active_leg:
                    side_str = "LONG" if long_leg else "SHORT"
                    sl_val = float(active_leg.get("trailing_sl", 0.0) or 0.0)
                    tp_val = float(active_leg.get("tp_target", 0.0) or 0.0)
                    entry_px = float(active_leg.get("entry_price", 0.0) or 0.0)
                    unrealized = float(active_leg.get("unrealized_pnl", 0.0) or 0.0)
                    pnl_pct = float(active_leg.get("pnl_pct", 0.0) or 0.0)
                    md.append(f"### {sym} (ACTIVE RUNNER: {side_str} @ {px_str})")
                    md.append(f"- **Position**: {active_leg.get('size')} {side_str} @ Entry ${entry_px:,.2f} | Unrealized: ${unrealized:+.2f} ({pnl_pct:+.2f}%)")
                    md.append(f"- **Protection**: Stop-Loss: ${sl_val:,.2f} | Apex TP: ${tp_val:,.2f}")
                else:
                    slot_status = "ELIGIBLE FOR ENTRY" if active_count < max_pairs else "WAITING (CONCURRENCY FULL)"
                    md.append(f"### {sym} (`{status}` @ {px_str} - {slot_status})")
                    md.append(f"- **Indicators**: {ind_str} | **Bar Close Remaining**: {p.get('status_msg', '')}")
                md.append("")

        if trades:
            md.append("## Trade Execution Audit (`bybit_trades.csv`)")
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

        md.append("## Recent Process Logs")
        md.append("```text")
        md.append(logs[-800:] if len(logs) > 800 else logs)
        md.append("```")

        self._send_markdown("\n".join(md))

    def _handle_api_logs(self, qs: dict):
        lines = int(qs.get("lines", [80])[0])
        errors_only = qs.get("errors", ["0"])[0].lower() in ["1", "true"]
        logs = get_systemd_logs(lines=lines, errors_only=errors_only)
        self._send_json({"lines": lines, "errors_only": errors_only, "logs": logs})

    def _handle_api_trades(self, qs: dict):
        limit = int(qs.get("limit", [50])[0])
        trades = read_trade_history(limit=limit)
        self._send_json({"count": len(trades), "trades": trades})

    def _handle_api_clear_trades(self, qs: dict):
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bybit_trades.csv"))
        header = "timestamp,symbol,cycle,leg,event,price,size,extreme_price,trailing_sl,tp_target,leg_pnl_usd,pair_cumulative_pnl\n"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(header)
            if qs.get("redirect", [None])[0]:
                self._redirect("/dashboard")
            else:
                self._send_json({"status": "ok", "success": True, "message": "Cleared local bybit_trades.csv"})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_api_candles(self, qs: dict):
        symbol = qs.get("symbol", ["BTCUSDT"])[0].upper()
        interval = qs.get("interval", ["60"])[0]
        limit = int(qs.get("limit", [60])[0])
        data = fetch_candles(symbol, interval=interval, limit=limit)
        self._send_json(data)

    def _handle_api_tickers(self):
        tickers = fetch_24h_tickers()
        self._send_json({"tickers": tickers})

    # ==========================================================================
    # WEBSOCKET STREAMING
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
        except Exception as e:
            logger.debug(f"WS session ended: {e}")

    # ==========================================================================
    # TELEMETRY DOM PAYLOAD BUILDER
    # ==========================================================================

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
          <h1>Bybit Single-Leg Trend Runner</h1>
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

        active_count = state.get("active_pairs_count", 0)
        max_concurrent = state.get("max_concurrent_pairs", 4)
        used_margin = active_count * 250.0
        avail_margin_slots = max(0, max_concurrent - active_count)
        reserve_cash = max(0.0, 1000.0 - used_margin)

        rpnl_color = "#10b981" if realized_pnl >= 0 else "#ef4444"
        opnl_color = "#10b981" if open_pnl >= 0 else "#ef4444"
        tpnl_color = "#10b981" if total_pnl >= 0 else "#ef4444"

        stats_grid_html = f"""
        <div class="card">
          <div class="stat-title">Total Account Balance</div>
          <div class="stat-val" style="color:#38bdf8;">${equity:,.2f}</div>
          <div class="stat-sub">Available Margin: ${avail_bal:,.2f} USDT</div>
        </div>
        <div class="card">
          <div class="stat-title">Concurrency Slots</div>
          <div class="stat-val" style="color:{'#10b981' if avail_margin_slots > 0 else '#f59e0b'};">
            {active_count} / {max_concurrent} Active
          </div>
          <div class="stat-sub">Margin In Use: ${used_margin:,.0f} | Reserve: ${reserve_cash:,.0f}</div>
        </div>
        <div class="card">
          <div class="stat-title">Realized Net Profit</div>
          <div class="stat-val" style="color:{rpnl_color}">${realized_pnl:+.2f}</div>
          <div class="stat-sub">Across {trades_count} closed Bybit trades</div>
        </div>
        <div class="card">
          <div class="stat-title">Open Floating PnL</div>
          <div class="stat-val" style="color:{opnl_color}">${open_pnl:+.2f}</div>
          <div class="stat-sub">Active market floating</div>
        </div>
        <div class="card">
          <div class="stat-title">System Status</div>
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
            fast_e = p.get("fast_ema")
            slow_e = p.get("slow_ema")
            adx = p.get("adx")
            trend_str = "BULLISH" if (fast_e and slow_e and fast_e > slow_e) else "BEARISH"
            trend_col = "#10b981" if trend_str == "BULLISH" else "#ef4444"
            ind = f"EMA(9/21): {fast_e:.1f}/{slow_e:.1f} | ADX: {adx:.1f}" if (fast_e and slow_e and adx) else "Scanning..."

            pct_val = tickers_24h.get(sym)
            if pct_val is not None:
                pct_cls = "up" if pct_val >= 0 else "down"
                pct_str = f"{pct_val:+.2f}%"
            else:
                pct_cls = ""
                pct_str = "--%"

            long_leg = p.get("long_leg")
            short_leg = p.get("short_leg")
            active_leg = long_leg if long_leg else short_leg

            if active_leg:
                side_str = "LONG" if long_leg else "SHORT"
                side_badge_cls = "long" if long_leg else "short"
                side_symbol = "🟢 LONG" if long_leg else "🔴 SHORT"
                pnl_val = float(active_leg.get("unrealized_pnl", 0.0) or 0.0)
                pnl_pct = float(active_leg.get("pnl_pct", 0.0) or 0.0)
                pcol = "#10b981" if pnl_val >= 0 else "#ef4444"
                entry_px = float(active_leg.get("entry_price", 0.0) or 0.0)
                sl_px = float(active_leg.get("trailing_sl", 0.0) or 0.0)
                tp_px = float(active_leg.get("tp_target", 0.0) or 0.0)
                size_val = float(active_leg.get("size", 0.0) or 0.0)
                peak_px = float(active_leg.get("peak_price", entry_px) or entry_px)
                trough_px = float(active_leg.get("trough_price", entry_px) or entry_px)

                # Distance calculations
                if long_leg:
                    sl_dist = (px - sl_px) if (px and sl_px) else 0.0
                    sl_dist_pct = (sl_dist / px * 100) if (px and sl_px and px > 0) else 0.0
                    tp_dist = (tp_px - px) if (px and tp_px) else 0.0
                    tp_dist_pct = (tp_dist / px * 100) if (px and tp_px and px > 0) else 0.0
                else:
                    sl_dist = (sl_px - px) if (px and sl_px) else 0.0
                    sl_dist_pct = (sl_dist / px * 100) if (px and sl_px and px > 0) else 0.0
                    tp_dist = (px - tp_px) if (px and tp_px) else 0.0
                    tp_dist_pct = (tp_dist / px * 100) if (px and tp_px and px > 0) else 0.0

                # Protective SL Badge
                if p_phase == "INCUBATION":
                    sl_label = f'<span class="target-tag sl">🛡️ Hard Initial SL: ${sl_px:,.2f}</span><div style="font-size:10px; color:#f87171; margin-top:2px;">Buffer: ${abs(sl_dist):,.2f} ({abs(sl_dist_pct):.2f}%)</div>'
                    tp_label = f'<span class="target-tag tp">🎯 Apex TP: ${tp_px:,.2f}</span><div style="font-size:10px; color:#34d399; margin-top:2px;">Target: ${abs(tp_dist):,.2f} ({abs(tp_dist_pct):.2f}%)</div>' if tp_px > 0 else '---'
                    card_status_badge = '<span class="status-pill" style="border-color:#10b981; color:#10b981;">INCUBATING</span>'
                else:
                    sl_label = f'<span class="target-tag sl" style="border-color:#38bdf8;">🔒 True BE Locked: ${sl_px:,.2f}</span><div style="font-size:10px; color:#38bdf8; margin-top:2px;">Locked in profit</div>'
                    tp_label = f'<span class="target-tag tp">🎯 Apex TP: ${tp_px:,.2f}</span><div style="font-size:10px; color:#34d399; margin-top:2px;">Target: ${abs(tp_dist):,.2f} ({abs(tp_dist_pct):.2f}%)</div>'
                    card_status_badge = '<span class="status-pill" style="border-color:#38bdf8; color:#38bdf8;">RUNNER ACTIVE</span>'

                leg_card_html = f"""
                <div class="leg-box {side_badge_cls}" style="border-left: 4px solid {'#10b981' if long_leg else '#ef4444'};">
                  <div class="leg-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <div style="display:flex; align-items:center; gap:6px;">
                      <span class="badge {side_badge_cls}">{side_str} {size_val}</span>
                      <span class="badge primary">RUNNER</span>
                    </div>
                    <b style="color:{pcol}; font-size:12px; font-family:'JetBrains Mono';">${pnl_val:+.2f} ({pnl_pct:+.2f}%)</b>
                  </div>
                  <div class="leg-body">
                    <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:6px; color:#94a3b8;">
                      <span>Entry: <b style="color:#f8fafc;">${entry_px:,.2f}</b></span>
                      <span>{'Peak' if long_leg else 'Trough'}: <b style="color:#f8fafc;">${(peak_px if long_leg else trough_px):,.2f}</b></span>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:4px;">
                      <div class="target-tag sl"><span>🛡️ SL: ${sl_px:,.2f}</span><span style="font-size:9px; opacity:0.85;">(-${abs(sl_dist):,.1f})</span></div>
                      <div class="target-tag tp"><span>🎯 TP: ${tp_px:,.2f}</span><span style="font-size:9px; opacity:0.85;">(+${abs(tp_dist):,.1f})</span></div>
                    </div>
                  </div>
                </div>"""

                notional_str = f"(${size_val * px:,.1f})" if (px and size_val) else ""

                active_positions_rows.append(f"""<tr>
                  <td>{render_coin_badge(sym)}</td>
                  <td><span class="badge {side_badge_cls}">{side_symbol}</span></td>
                  <td><span class="badge primary">TREND RUNNER</span></td>
                  <td style="font-family:'JetBrains Mono';">{size_val} <span style="color:#64748b; font-size:11px;">{notional_str}</span></td>
                  <td style="font-family:'JetBrains Mono';">${entry_px:,.2f}</td>
                  <td style="font-family:'JetBrains Mono'; font-weight:700;">{px_val_str}</td>
                  <td>{sl_label}</td>
                  <td>{tp_label}</td>
                  <td style="color:{pcol}; font-weight:700; font-family:'JetBrains Mono';">${pnl_val:+.2f} <span style="font-size:11px;">({pnl_pct:+.2f}%)</span></td>
                </tr>""")

            else:
                card_status_badge = '<span class="status-pill" style="border-color:#64748b; color:#94a3b8;">SCANNING</span>'
                slot_info = '<span style="color:#10b981; font-size:11px;">⚡ Slot Available for Entry</span>' if avail_margin_slots > 0 else '<span style="color:#f59e0b; font-size:11px;">⏳ Waiting (Max 4 Active)</span>'
                leg_card_html = f"""
                <div class="leg-box empty" style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding:16px 8px; text-align:center;">
                  <div style="font-size:11px; color:#94a3b8; margin-bottom:4px;">Waiting for 60m Candle Close & ADX Breakout</div>
                  <div>{slot_info}</div>
                </div>"""

            market_cards.append(f"""
            <div class="card market-card" data-symbol="{sym}">
              <div class="market-header">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="sym-badge">{get_coin_icon(sym, size=20)} {sym}</span>
                  {card_status_badge}
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="pct-badge {pct_cls}">{pct_str}</span>
                  <div class="sym-price">{px_str}</div>
                </div>
              </div>
              <div class="market-ind" style="display:flex; justify-content:space-between;">
                <span>{ind}</span>
                <span style="color:{trend_col}; font-weight:600;">{trend_str}</span>
              </div>
              <div style="margin-top:8px;">
                {leg_card_html}
              </div>
            </div>""")

        markets_html = "\n".join(market_cards) if market_cards else '<div class="card" style="text-align:center; color:#64748b;">Loading 8-asset universe...</div>'

        if active_positions_rows:
            active_positions_html = f"""
            <div class="table-container" style="margin-bottom:24px;">
              <table>
                <thead>
                  <tr>
                    <th>Market</th>
                    <th>Position</th>
                    <th>Strategy Mode</th>
                    <th>Size / Notional</th>
                    <th>Entry Price</th>
                    <th>Mark Price</th>
                    <th>Protective Stop Loss (SL)</th>
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
            active_positions_html = f"""
            <div class="card" style="padding:18px 24px; text-align:center; color:#94a3b8; background:#0a101d; border:1px dashed #1e293b; margin-bottom:24px;">
              <div style="font-size:20px; margin-bottom:4px;">🛡️</div>
              <div style="font-weight:600; color:#f1f5f9; margin-bottom:4px;">All 4 Concurrency Slots Available</div>
              <div style="font-size:12px; color:#64748b;">The engine is actively scanning the 8-asset universe (AVAX, LINK, HYPE, DOGE, XMR, BTC, ETH, SOL) on 60m candle closes. When Macro 200-EMA and rising ADX confirm a trend, a single runner position with initial hard SL will arm here automatically.</div>
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
            elif "SL" in evt_val or "STOP" in evt_val:
                evt_class = "sl"
            elif "RATCHET" in evt_val:
                evt_class = "ratchet"
            elif "BREAKEVEN" in evt_val or "BE" in evt_val:
                evt_class = "primary"
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

        # Tab 2: Exchange trades from Bybit API
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

            open_vol = entry_px * abs_q
            close_vol = exit_px * abs_q

            res_label = "Profit" if pnl_val > 0 else ("Loss" if pnl_val < 0 else "BE")
            res_bg = "rgba(16,185,129,0.15)" if pnl_val > 0 else ("rgba(239,68,68,0.15)" if pnl_val < 0 else "rgba(148,163,184,0.15)")

            exchange_trade_rows.append(f"""<tr>
              <td>{render_coin_badge(t.get('symbol',''))}</td>
              <td style="font-family:'JetBrains Mono'; font-weight:600;">{abs_q}</td>
              <td style="font-family:'JetBrains Mono';">${entry_px:,.2f}</td>
              <td style="font-family:'JetBrains Mono'; font-weight:700;">${exit_px:,.2f}</td>
              <td><span style="color:{type_col}; font-weight:600;">{trade_type}</span></td>
              <td style="color:{pnl_col}; font-weight:700; font-family:'JetBrains Mono';">${pnl_val:+.2f}</td>
              <td><span style="background:{res_bg}; color:{pnl_col}; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:700;">{res_label}</span></td>
              <td style="font-family:'JetBrains Mono'; color:#94a3b8;">${open_vol:,.2f}</td>
              <td style="font-family:'JetBrains Mono'; color:#94a3b8;">${close_vol:,.2f}</td>
              <td style="font-family:'JetBrains Mono'; font-size:11px; color:#cbd5e1;">{format_bybit_fee(open_fee)}</td>
              <td style="font-family:'JetBrains Mono'; font-size:11px; color:#cbd5e1;">{format_bybit_fee(close_fee)}</td>
              <td style="font-family:'JetBrains Mono'; font-size:11px; color:{fund_col};">{fund_str}</td>
              <td style="color:#64748b; font-size:11px; white-space:nowrap;">{ts_display}</td>
            </tr>""")

        # PnL by symbol badges across targeted 8 assets ONLY (no legacy pairs)
        target_universe = ["AVAXUSDT", "LINKUSDT", "HYPEUSDT", "DOGEUSDT", "XMRUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        pnl_badges = []
        for s_sym in target_universe:
            if s_sym in sym_pnl:
                s_val = float(sym_pnl[s_sym])
                if s_val > 0:
                    s_col = "#10b981"
                    val_str = f"+${s_val:,.2f}"
                elif s_val < 0:
                    s_col = "#ef4444"
                    val_str = f"-${abs(s_val):,.2f}"
                else:
                    s_col = "#94a3b8"
                    val_str = "$0.00"
            else:
                s_col = "#64748b"
                val_str = "$0.00"

            pnl_badges.append(
                f'<div class="pnl-badge-item" style="display:flex; align-items:center; gap:6px; background:#070d19; padding:4px 8px; border-radius:4px; border:1px solid #1e293b;">'
                f'{get_coin_icon(s_sym, size=15)}'
                f'<span style="font-weight:500; font-size:11px;">{s_sym}:</span>'
                f'<b style="color:{s_col}; font-family:\'JetBrains Mono\', monospace; font-size:11px;">{val_str}</b>'
                f'</div>'
            )
        pnl_by_sym_html = "".join(pnl_badges) if pnl_badges else '<span style="color:#64748b;">No closed trade fills yet</span>'

        return {
            "status_color": status_color,
            "status_text": status_text,
            "brand_status_html": brand_status_html,
            "stats_grid_html": stats_grid_html,
            "pnl_by_symbol_html": pnl_by_sym_html,
            "markets_html": markets_html,
            "active_positions_html": active_positions_html,
            "csv_trades_html": "\n".join(trade_rows),
            "csv_count": len(trade_rows),
            "exchange_trades_html": "\n".join(exchange_trade_rows),
            "exchange_count": len(exchange_trade_rows),
            "logs": logs,
        }

    # ==========================================================================
    # HTML RENDERING
    # ==========================================================================

    def _render_login(self, error: str = "") -> str:
        err_html = f'<div class="login-err">{error}</div>' if error else ""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login - Bybit Single-Leg Trend Runner</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', sans-serif;
      background: #080c14;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .login-card {{
      background: #0d1526;
      border: 1px solid #1e293b;
      border-radius: 12px;
      padding: 32px;
      width: 100%;
      max-width: 400px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .login-brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }}
    .login-brand h1 {{
      font-size: 18px;
      font-weight: 700;
      color: #f8fafc;
    }}
    .login-err {{
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid #ef4444;
      color: #f87171;
      padding: 10px 14px;
      border-radius: 6px;
      font-size: 13px;
      margin-bottom: 20px;
    }}
    .input-group {{
      margin-bottom: 20px;
    }}
    .input-group label {{
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: #94a3b8;
      margin-bottom: 8px;
    }}
    .input-group input {{
      width: 100%;
      padding: 10px 14px;
      background: #080c14;
      border: 1px solid #334155;
      border-radius: 6px;
      color: #f8fafc;
      font-size: 14px;
      outline: none;
      transition: border-color 0.15s;
    }}
    .input-group input:focus {{
      border-color: #38bdf8;
    }}
    button {{
      width: 100%;
      padding: 10px;
      background: #0284c7;
      color: white;
      border: none;
      border-radius: 6px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      transition: background 0.15s;
    }}
    button:hover {{
      background: #0369a1;
    }}
  </style>
</head>
<body>
  <div class="login-card">
    <div class="login-brand">
      <div style="width:10px; height:10px; border-radius:50%; background:#10b981; box-shadow:0 0 8px #10b981;"></div>
      <h1>Bybit Single-Leg Trend Runner</h1>
    </div>
    {err_html}
    <form method="POST" action="/login">
      <div class="input-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required autofocus placeholder="Enter password...">
      </div>
      <button type="submit">Unlock Dashboard</button>
    </form>
  </div>
</body>
</html>"""

    def _render_dashboard(self) -> str:
        d = self._get_live_telemetry_payload()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bybit Single-Leg Trend Runner - Telemetry</title>
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
      max-width: 1440px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 16px;
      border-bottom: 1px solid #1e293b;
      gap: 16px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .brand-title-wrap {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .brand h1 {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #f8fafc;
    }}
    .pulse-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .btn {{
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      text-decoration: none;
      color: #e2e8f0;
      background: #0d1526;
      border: 1px solid #334155;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s;
    }}
    .btn:hover {{
      background: #1e293b;
      border-color: #475569;
    }}
    .btn-primary {{
      background: #0284c7;
      border-color: #0284c7;
      color: white;
    }}
    .btn-primary:hover {{
      background: #0369a1;
      border-color: #0369a1;
    }}
    .status-pill {{
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 10px;
      font-weight: 700;
      border: 1px solid;
      letter-spacing: 0.5px;
    }}

    /* Strategy Banner */
    .strategy-banner {{
      background: linear-gradient(90deg, #0b1e36 0%, #0d1526 100%);
      border: 1px solid #1e3a5f;
      border-radius: 8px;
      padding: 12px 18px;
      margin-bottom: 20px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .strat-badge {{
      background: #0284c7;
      color: white;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      letter-spacing: 0.5px;
      display: inline-block;
      margin-right: 8px;
    }}
    .strat-sub {{
      font-size: 12px;
      color: #94a3b8;
    }}
    .strat-limits {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .limit-pill {{
      background: #080c14;
      border: 1px solid #1e293b;
      padding: 3px 10px;
      border-radius: 4px;
      font-size: 11px;
      color: #cbd5e1;
    }}
    .limit-pill b {{
      color: #38bdf8;
    }}
    .limit-pill.safe b {{
      color: #10b981;
    }}

    /* Stats Grid */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .card {{
      background: #0d1526;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 16px;
    }}
    .stat-title {{
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #94a3b8;
      margin-bottom: 6px;
    }}
    .stat-val {{
      font-size: 22px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
      color: #f8fafc;
      letter-spacing: -0.02em;
    }}
    .stat-sub {{
      font-size: 11px;
      color: #64748b;
      margin-top: 4px;
    }}

    /* Section Header */
    .section-title {{
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #94a3b8;
      margin: 24px 0 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    /* Markets Grid (8 pairs) */
    .markets-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .market-card {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      transition: border-color 0.15s;
    }}
    .market-card:hover {{
      border-color: #334155;
    }}
    .market-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .sym-badge {{
      font-size: 14px;
      font-weight: 700;
      color: #f8fafc;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .sym-price {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      font-weight: 700;
      color: #f8fafc;
    }}
    .pct-badge {{
      font-size: 11px;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
      padding: 1px 6px;
      border-radius: 4px;
      background: #1e293b;
      color: #94a3b8;
    }}
    .pct-badge.up {{
      background: rgba(16, 185, 129, 0.15);
      color: #10b981;
    }}
    .pct-badge.down {{
      background: rgba(239, 68, 68, 0.15);
      color: #ef4444;
    }}
    .market-ind {{
      font-size: 11px;
      color: #94a3b8;
      background: #080c14;
      padding: 6px 10px;
      border-radius: 4px;
      border: 1px solid #1e293b;
      font-family: 'JetBrains Mono', monospace;
    }}
    .leg-box {{
      background: #080c14;
      border-radius: 6px;
      padding: 10px 12px;
      border: 1px solid #1e293b;
    }}
    .leg-box.empty {{
      border: 1px dashed #1e293b;
      background: #060911;
    }}

    /* Badges & Target tags */
    .badge {{
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge.long {{
      background: rgba(16, 185, 129, 0.15);
      color: #10b981;
      border: 1px solid #10b981;
    }}
    .badge.short {{
      background: rgba(239, 68, 68, 0.15);
      color: #ef4444;
      border: 1px solid #ef4444;
    }}
    .badge.primary {{
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid #0284c7;
    }}
    .badge.event {{
      background: rgba(148, 163, 184, 0.15);
      color: #cbd5e1;
      border: 1px solid #475569;
    }}
    .badge.tp {{
      background: rgba(16, 185, 129, 0.15);
      color: #10b981;
    }}
    .badge.sl {{
      background: rgba(239, 68, 68, 0.15);
      color: #ef4444;
    }}
    .badge.ratchet {{
      background: rgba(245, 158, 11, 0.15);
      color: #f59e0b;
    }}

    .target-tag {{
      display: inline-flex;
      justify-content: space-between;
      align-items: center;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      border: 1px solid;
    }}
    .target-tag.sl {{
      background: rgba(239, 68, 68, 0.1);
      border-color: rgba(239, 68, 68, 0.3);
      color: #f87171;
    }}
    .target-tag.tp {{
      background: rgba(16, 185, 129, 0.1);
      border-color: rgba(16, 185, 129, 0.3);
      color: #34d399;
    }}
    .target-tag.event {{
      background: rgba(148, 163, 184, 0.1);
      border-color: rgba(148, 163, 184, 0.2);
      color: #94a3b8;
    }}

    /* Tables */
    .table-container {{
      overflow-x: auto;
      background: #0d1526;
      border: 1px solid #1e293b;
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 12px;
    }}
    th {{
      background: #080c14;
      padding: 10px 14px;
      font-weight: 600;
      color: #94a3b8;
      border-bottom: 1px solid #1e293b;
      text-transform: uppercase;
      font-size: 10px;
      letter-spacing: 0.5px;
      white-space: nowrap;
    }}
    td {{
      padding: 10px 14px;
      border-bottom: 1px solid #1e293b;
      color: #e2e8f0;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    tr:hover td {{
      background: rgba(255, 255, 255, 0.02);
    }}

    /* Trade Tabs */
    .trade-tabs-nav {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .trade-tab-btn {{
      padding: 6px 14px;
      background: #080c14;
      border: 1px solid #1e293b;
      border-radius: 6px;
      color: #94a3b8;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .trade-tab-btn.active {{
      background: #0d1526;
      color: #f8fafc;
      border-color: #38bdf8;
    }}
    .tab-count-pill {{
      background: #1e293b;
      padding: 1px 6px;
      border-radius: 9999px;
      font-size: 10px;
      color: #cbd5e1;
    }}

    /* Pagination controls */
    .pagination-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 16px;
      background: #080c14;
      border-top: 1px solid #1e293b;
      font-size: 12px;
      color: #94a3b8;
    }}
    .page-pill {{
      padding: 4px 8px;
      background: #0d1526;
      border: 1px solid #1e293b;
      border-radius: 4px;
      color: #94a3b8;
      cursor: pointer;
      font-size: 11px;
    }}
    .page-pill.active {{
      background: #0284c7;
      color: white;
      border-color: #0284c7;
    }}

    /* Logs Terminal */
    .terminal {{
      background: #050811;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 14px 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: #94a3b8;
      max-height: 350px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.6;
    }}

    /* =========================================================================
       Mobile Responsive Styles (< 768px and < 480px)
       ========================================================================= */
    @media (max-width: 768px) {{
      body {{
        padding: 10px;
      }}
      .container {{
        width: 100%;
      }}
      header {{
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
        margin-bottom: 12px;
        padding-bottom: 12px;
      }}
      .brand {{
        justify-content: space-between;
        width: 100%;
      }}
      .brand h1 {{
        font-size: 15px;
      }}
      .header-actions {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 6px;
        width: 100%;
      }}
      .header-actions .btn {{
        justify-content: center;
        padding: 7px 2px;
        font-size: 11px;
        text-align: center;
      }}
      .strategy-banner {{
        flex-direction: column;
        align-items: stretch;
        padding: 10px 12px;
        gap: 10px;
        margin-bottom: 12px;
      }}
      .strat-sub {{
        font-size: 11.5px;
        line-height: 1.4;
      }}
      .strat-limits {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 6px;
        width: 100%;
      }}
      .limit-pill {{
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 6px 4px;
        font-size: 10.5px;
        gap: 2px;
      }}
      .stats-grid {{
        grid-template-columns: repeat(2, 1fr);
        gap: 8px;
        margin-bottom: 12px;
      }}
      .stats-grid .card:last-child {{
        grid-column: span 2;
      }}
      .card {{
        padding: 10px 12px;
      }}
      .stat-title {{
        font-size: 10px;
        margin-bottom: 4px;
      }}
      .stat-val {{
        font-size: 17px;
      }}
      .stat-sub {{
        font-size: 10px;
        margin-top: 2px;
      }}
      .pnl-strip-card {{
        flex-direction: column !important;
        align-items: stretch !important;
        gap: 8px !important;
        padding: 10px 12px !important;
        margin-bottom: 14px !important;
      }}
      #pnl-by-symbol {{
        display: grid !important;
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 6px !important;
        width: 100% !important;
      }}
      .pnl-badge-item {{
        display: flex !important;
        justify-content: space-between !important;
        padding: 5px 8px !important;
      }}
      .section-title {{
        flex-direction: column;
        align-items: flex-start;
        gap: 6px;
        margin: 18px 0 10px;
        font-size: 12px;
      }}
      .markets-grid {{
        grid-template-columns: 1fr;
        gap: 10px;
        margin-bottom: 16px;
      }}
      .market-card {{
        padding: 12px;
      }}
      .table-container {{
        border-radius: 6px;
        -webkit-overflow-scrolling: touch;
      }}
      table {{
        font-size: 11px;
      }}
      th, td {{
        padding: 8px 10px;
        white-space: nowrap;
      }}
      .trades-header-wrap {{
        flex-direction: column !important;
        align-items: stretch !important;
        gap: 8px !important;
      }}
      .trade-tabs-nav {{
        width: 100%;
        display: grid;
        grid-template-columns: 1fr 1fr auto;
        gap: 6px;
      }}
      .trade-tab-btn {{
        justify-content: center;
        padding: 7px 6px;
        font-size: 11px;
      }}
      .pagination-bar {{
        flex-direction: column;
        gap: 8px;
        align-items: center;
        padding: 8px 12px;
      }}
      .terminal {{
        font-size: 10px;
        padding: 10px;
        max-height: 250px;
      }}
    }}

    @media (max-width: 360px) {{
      .stats-grid {{
        grid-template-columns: 1fr;
      }}
      .stats-grid .card:last-child {{
        grid-column: span 1;
      }}
      #pnl-by-symbol {{
        grid-template-columns: 1fr !important;
      }}
      .strat-limits {{
        grid-template-columns: 1fr;
      }}
    }}
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

    <!-- Strategy Banner & Concurrency Overview -->
    <div class="strategy-banner">
      <div class="strat-info">
        <span class="strat-sub" style="font-weight:600; color:#cbd5e1; font-size:12.5px;">8-Asset Champion Universe &bull; Macro 200-EMA + Rising ADX &bull; True Breakeven &bull; Progressive Ratchets</span>
      </div>
      <div class="strat-limits">
        <span class="limit-pill">Base Capital: <b>$1,000 USDT</b></span>
        <span class="limit-pill">Leverage: <b>4x</b></span>
        <span class="limit-pill">Max Concurrent: <b>4 Trades</b></span>
        <span class="limit-pill safe">Alloc: <b>4 &times; $250 Margin</b></span>
      </div>
    </div>

    <!-- Stats Grid -->
    <div class="stats-grid" id="stats-grid">
      {d['stats_grid_html']}
    </div>

    <!-- PnL by Symbol Strip -->
    <div class="card pnl-strip-card" style="margin-bottom:20px; padding:10px 16px; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; background:#0b1329;">
      <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:#94a3b8; letter-spacing:0.5px;">Bybit Realized P&L by Symbol:</div>
      <div id="pnl-by-symbol" style="display:flex; flex-wrap:wrap; gap:8px; font-family:'JetBrains Mono', monospace; font-size:12px;">
        {d['pnl_by_symbol_html']}
      </div>
    </div>

    <!-- 8-Asset Universe Scanner Cards -->
    <div class="section-title">
      <span>8-Asset Champion Universe &bull; Scanner Status</span>
      <span style="font-size:11px; color:#64748b;">AVAX &bull; LINK &bull; HYPE &bull; DOGE &bull; XMR &bull; BTC &bull; ETH &bull; SOL</span>
    </div>
    <div class="markets-grid" id="markets-grid">
      {d['markets_html']}
    </div>

    <!-- Active Live Positions -->
    <div class="section-title">
      <span>Active Live Runner Positions &amp; Protective Stops</span>
      <span style="font-size:11px; color:#64748b;">Hard Initial SL &bull; True Breakeven Lock &bull; Apex TP</span>
    </div>
    <div id="active-positions-wrap">
      {d['active_positions_html']}
    </div>

    <!-- Trade Audit Ledgers -->
    <div class="section-title trades-header-wrap">
      <span>Trade Execution &amp; Audit Ledgers</span>
      <div class="trade-tabs-nav">
        <button id="trade-tab-csv" class="trade-tab-btn active" onclick="switchTradeTab('csv')">
          <span>State Machine Audit</span>
          <span class="tab-count-pill" id="tab-count-csv">{d['csv_count']}</span>
        </button>
        <button id="trade-tab-exchange" class="trade-tab-btn" onclick="switchTradeTab('exchange')">
          <span>Bybit UTA Closed P&amp;L</span>
          <span class="tab-count-pill" id="tab-count-exchange">{d['exchange_count']}</span>
        </button>
        <div id="clear-csv-wrap">
          <button onclick="clearCsvHistory(event)" class="btn" style="padding:4px 8px; font-size:11px; color:#f87171;">Clear CSV</button>
        </div>
      </div>
    </div>

    <!-- Table 1: CSV Audit Ledger -->
    <div id="trade-table-csv" class="table-container" style="margin-bottom:24px;">
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Symbol</th>
            <th style="text-align:center;">Cycle</th>
            <th>Leg Side</th>
            <th>Event</th>
            <th>Price</th>
            <th>Size</th>
            <th>Trailing SL</th>
            <th>Apex TP</th>
            <th>Leg PnL</th>
            <th>Pair Cumulative</th>
          </tr>
        </thead>
        <tbody id="csv-trades-body">
          {d['csv_trades_html']}
        </tbody>
      </table>
      <div class="pagination-bar">
        <div id="csv-page-info">Showing trades</div>
        <div id="csv-page-pills" style="display:flex; gap:4px;"></div>
        <div style="display:flex; gap:6px;">
          <button id="csv-prev-btn" class="btn" style="padding:2px 8px;" onclick="changePage('csv', -1)">Prev</button>
          <button id="csv-next-btn" class="btn" style="padding:2px 8px;" onclick="changePage('csv', 1)">Next</button>
        </div>
      </div>
    </div>

    <!-- Table 2: Bybit Exchange Closed P&L -->
    <div id="trade-table-exchange" class="table-container" style="display:none; margin-bottom:24px;">
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
            <th>Open Vol</th>
            <th>Close Vol</th>
            <th>Open Fee</th>
            <th>Close Fee</th>
            <th>Funding Fee</th>
            <th>Trade Time</th>
          </tr>
        </thead>
        <tbody id="exchange-trades-body">
          {d['exchange_trades_html']}
        </tbody>
      </table>
      <div class="pagination-bar">
        <div id="exchange-page-info">Showing trades</div>
        <div id="exchange-page-pills" style="display:flex; gap:4px;"></div>
        <div style="display:flex; gap:6px;">
          <button id="exchange-prev-btn" class="btn" style="padding:2px 8px;" onclick="changePage('exchange', -1)">Prev</button>
          <button id="exchange-next-btn" class="btn" style="padding:2px 8px;" onclick="changePage('exchange', 1)">Next</button>
        </div>
      </div>
    </div>

    <!-- Live System Logs -->
    <div class="section-title">
      <span>Live System Logs &amp; Diagnostics</span>
      <span style="font-size:11px; color:#64748b;">journalctl -u bybit-bot -n 80</span>
    </div>
    <div class="terminal" id="term">{d['logs']}</div>
  </div>

  <script>
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
      const countPill = document.getElementById(tab === 'csv' ? 'tab-count-csv' : 'tab-count-exchange');
      if (!tbody) return;

      const total = st.allRows.length;
      if (countPill) countPill.textContent = total;

      if (total === 0) {{
        if (tab === 'csv') {{
          tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:24px; color:#64748b;">No internal bot events recorded in bybit_trades.csv yet for the current session.</td></tr>';
        }} else {{
          tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:24px; color:#64748b;">No closed position fills retrieved from Bybit UTA API yet.</td></tr>';
        }}
        if (infoEl) infoEl.textContent = 'Showing 0 trades';
        if (pillsEl) pillsEl.innerHTML = '';
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
        return;
      }}

      const effPageSize = st.pageSize;
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
      if (!confirm('Clear local bybit_trades.csv audit ledger?')) return false;
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
    }}

    // Initial pagination setup
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

    connectTelemetryWS();

    const term = document.getElementById('term');
    if (term) term.scrollTop = term.scrollHeight;
  </script>
</body>
</html>"""


def run_server():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, TelemetryHandler)
    print(f"=================================================================")
    print(f"⚡ Bybit Single-Leg Telemetry Server active on port {PORT}")
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
