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
from decimal import Decimal

# Import compute_indicators from backtest
try:
    from backtest import compute_indicators
except ImportError:
    compute_indicators = None

try:
    from pybit.unified_trading import HTTP as BybitHTTP
except ImportError:
    BybitHTTP = None

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
                formatted.append({
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts_ms else "",
                    "symbol": sym,
                    "side": t.get("side", ""),
                    "qty": float(t.get("qty", 0) or 0),
                    "entry_price": float(t.get("avgEntryPrice", 0) or 0),
                    "exit_price": float(t.get("avgExitPrice", 0) or 0),
                    "closed_pnl": pnl,
                    "exec_fee": float(t.get("execFee", 0) or 0),
                })
            res_data["total_realized_pnl"] = round(total_pnl, 2)
            res_data["by_symbol"] = {k: round(v, 2) for k, v in by_sym.items()}
            res_data["trades_count"] = len(trades)
            res_data["recent_trades"] = formatted

        BYBIT_ACC_CACHE["acc"] = (now, res_data)
    except Exception:
        pass

    return res_data


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
                return json.load(f)
        except Exception:
            pass
    return {}


def read_trade_history(limit: int = 50) -> list:
    """Read trade audit log from bybit_trades.csv."""
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bybit_trades.csv"))
    trades = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trades.append(row)
            trades.reverse()  # Newest first
            return trades[:limit]
        except Exception:
            pass
    return trades


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
        body = json.dumps(data, indent=2).encode("utf-8")
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
            if parsed.path.startswith("/api/"):
                self._send_json({"error": "Unauthorized", "message": "Valid password required via ?password=... or Bearer header."}, 401)
            else:
                self._redirect("/login")
            return

        # Routes
        if parsed.path in ["/", "/dashboard"]:
            self._send_html(self._render_dashboard(), cookie=set_cookie)
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
                    md.append(
                        f"  * **Long Leg**: {long_leg['size']} @ ${long_leg['entry_price']:.2f} | "
                        f"SL: ${long_leg['trailing_sl']:.2f} | TP: ${long_leg['tp_target']:.2f} | "
                        f"Unrealized PnL: ${long_leg['unrealized_pnl']:+.2f} ({long_leg['pnl_pct']:+.2f}%)"
                    )
                if short_leg:
                    md.append(
                        f"  * **Short Leg**: {short_leg['size']} @ ${short_leg['entry_price']:.2f} | "
                        f"SL: ${short_leg['trailing_sl']:.2f} | TP: ${short_leg['tp_target']:.2f} | "
                        f"Unrealized PnL: ${short_leg['unrealized_pnl']:+.2f} ({short_leg['pnl_pct']:+.2f}%)"
                    )
                if not long_leg and not short_leg:
                    md.append("  * *No active legs (Scanning for EMA cross + ADX gating)*")
                md.append("")

        md.append("## Recent Closed Trades (Exchange Ledger)")
        if recent_exchange_trades:
            md.append("| Timestamp | Symbol | Side | Qty | Entry Price | Exit Price | Net Realized PnL |")
            md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
            for t in recent_exchange_trades[:8]:
                md.append(f"| {t.get('timestamp','')} | **{t.get('symbol','')}** | {t.get('side','')} | {t.get('qty','')} | ${t.get('entry_price',0):,.2f} | ${t.get('exit_price',0):,.2f} | **${t.get('closed_pnl',0):+.2f}** |")
        elif trades:
            md.append("| Timestamp | Symbol | Leg | Event | Price | Size | Net PnL |")
            md.append("|---|---|:---:|:---:|:---:|:---:|:---:|")
            for t in trades[:6]:
                leg_val = t.get("leg") or t.get("leg_side", "")
                evt_val = t.get("event") or t.get("event_type", "")
                try:
                    px_val = float(t.get("price") or t.get("fill_price", 0) or 0)
                except (ValueError, TypeError):
                    px_val = 0.0
                try:
                    pnl_val = float(t.get("leg_pnl_usd") or t.get("pnl_usd", 0) or 0)
                except (ValueError, TypeError):
                    pnl_val = 0.0
                size_val = t.get("size", "")
                md.append(f"| {t.get('timestamp','')} | {t.get('symbol','')} | {leg_val} | {evt_val} | ${px_val:.2f} | {size_val} | ${pnl_val:+.2f} |")
        else:
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
        raw_logs = get_systemd_logs(lines=min(lines, 500), errors_only=err_only)
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
        state = read_bot_state()
        svc = get_service_status()
        acc = fetch_bybit_account_and_trades()
        trades = read_trade_history(limit=25)
        logs = get_systemd_logs(lines=80)

        is_active = svc.get("active", True)
        status_color = "#10b981" if is_active else "#ef4444"
        status_text = "ACTIVE" if is_active else "STOPPED"

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

        # Markets rows
        market_cards = []
        pairs = state.get("pairs", {})
        for sym, p in pairs.items():
            px = p.get("latest_price")
            px_str = f"${px:,.2f}" if px else "---"
            p_status = p.get("status", "SCANNING")
            p_color = "#10b981" if p_status == "ACTIVE" else "#94a3b8"
            fast_e = p.get("fast_ema")
            slow_e = p.get("slow_ema")
            adx = p.get("adx")
            ind = f"EMA: {fast_e:.1f} / {slow_e:.1f} | ADX: {adx:.1f}" if (fast_e and slow_e and adx) else "Scanning..."

            long_leg = p.get("long_leg")
            short_leg = p.get("short_leg")

            long_html = '<div class="leg-box empty">Long: Idle</div>'
            if long_leg:
                lpnl = long_leg.get("unrealized_pnl", 0.0)
                lcol = "#10b981" if lpnl >= 0 else "#ef4444"
                long_html = f"""<div class="leg-box long">
                  <div class="leg-header"><span>LONG {long_leg['size']}</span> <b style="color:{lcol}">${lpnl:+.2f} ({long_leg['pnl_pct']:+.2f}%)</b></div>
                  <div class="leg-body">Entry: ${long_leg['entry_price']:.2f} | SL: ${long_leg['trailing_sl']:.2f} | TP: ${long_leg['tp_target']:.2f}</div>
                </div>"""

            short_html = '<div class="leg-box empty">Short: Idle</div>'
            if short_leg:
                spnl = short_leg.get("unrealized_pnl", 0.0)
                scol = "#10b981" if spnl >= 0 else "#ef4444"
                short_html = f"""<div class="leg-box short">
                  <div class="leg-header"><span>SHORT {short_leg['size']}</span> <b style="color:{scol}">${spnl:+.2f} ({short_leg['pnl_pct']:+.2f}%)</b></div>
                  <div class="leg-body">Entry: ${short_leg['entry_price']:.2f} | SL: ${short_leg['trailing_sl']:.2f} | TP: ${short_leg['tp_target']:.2f}</div>
                </div>"""

            market_cards.append(f"""
            <div class="card market-card" data-symbol="{sym}">
              <div class="market-header">
                <div>
                  <span class="sym-badge">{sym}</span>
                  <span class="status-pill" style="border-color:{p_color}; color:{p_color}">{p_status}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="pct-badge" id="pct-{sym}">--%</span>
                  <div class="sym-price">{px_str}</div>
                </div>
              </div>

              <!-- Interactive Mini Graph Controls -->
              <div class="chart-controls">
                <div class="tf-pills" id="tf-pills-{sym}">
                  <button class="tf-btn" data-tf="15" onclick="changeChartTf('{sym}', '15')">15m</button>
                  <button class="tf-btn active" data-tf="60" onclick="changeChartTf('{sym}', '60')">1h</button>
                  <button class="tf-btn" data-tf="240" onclick="changeChartTf('{sym}', '240')">4h</button>
                  <button class="tf-btn" data-tf="D" onclick="changeChartTf('{sym}', 'D')">1D</button>
                </div>
                <div class="zoom-pills">
                  <button class="zoom-btn" onclick="zoomChart('{sym}', -6)" title="Zoom In (+)">➕</button>
                  <button class="zoom-btn" onclick="zoomChart('{sym}', 6)" title="Zoom Out (−)">➖</button>
                  <button class="zoom-btn" onclick="resetZoom('{sym}')" title="Reset Zoom & Pan">⟲</button>
                </div>
                <div class="chart-legend">
                  <span class="legend-item"><span class="legend-dot" style="background:#38bdf8;"></span>EMA9</span>
                  <span class="legend-item"><span class="legend-dot" style="background:#f59e0b;"></span>EMA21</span>
                  <span class="legend-item"><span class="legend-dot" style="background:#a855f7;"></span>ADX</span>
                </div>
              </div>

              <!-- Interactive Canvas Chart -->
              <div class="chart-container" id="chart-wrap-{sym}">
                <div class="chart-info-bar" id="info-{sym}">Loading candles...</div>
                <canvas id="chart-{sym}" class="market-chart"></canvas>
              </div>

              <div class="market-ind">{ind}</div>
              <div class="legs-grid">
                {long_html}
                {short_html}
              </div>
            </div>""")

        markets_html = "\n".join(market_cards) if market_cards else '<div class="card" style="text-align:center; color:#64748b;">Engine initializing markets...</div>'

        # Trade rows from CSV
        trade_rows = []
        for t in trades:
            leg_val = t.get("leg") or t.get("leg_side", "")
            evt_val = t.get("event") or t.get("event_type", "")
            try:
                px_val = float(t.get("price") or t.get("fill_price", 0) or 0)
            except (ValueError, TypeError):
                px_val = 0.0
            try:
                pnl_val = float(t.get("leg_pnl_usd") or t.get("pnl_usd", 0) or 0)
            except (ValueError, TypeError):
                pnl_val = 0.0
            size_val = t.get("size", "")
            col = "#10b981" if pnl_val > 0 else ("#ef4444" if pnl_val < 0 else "#94a3b8")
            trade_rows.append(f"""<tr>
              <td>{t.get('timestamp','')}</td>
              <td><b>{t.get('symbol','')}</b></td>
              <td><span class="badge {leg_val.lower()}">{leg_val}</span></td>
              <td>{evt_val}</td>
              <td>${px_val:.2f}</td>
              <td>{size_val}</td>
              <td style="color:{col}; font-weight:600;">${pnl_val:+.2f}</td>
            </tr>""")

        # Fallback to Exchange trades if CSV is empty
        exchange_trade_rows = []
        for t in recent_exchange_trades:
            pnl_val = float(t.get("closed_pnl", 0) or 0)
            col = "#10b981" if pnl_val > 0 else ("#ef4444" if pnl_val < 0 else "#94a3b8")
            side_badge = "long" if t.get("side", "").lower() in ["buy", "long"] else "short"
            exchange_trade_rows.append(f"""<tr>
              <td>{t.get('timestamp','')}</td>
              <td><b>{t.get('symbol','')}</b></td>
              <td><span class="badge {side_badge}">{t.get('side','')}</span></td>
              <td>Closed PnL</td>
              <td>${t.get('exit_price',0):,.2f}</td>
              <td>{t.get('qty','')}</td>
              <td style="color:{col}; font-weight:600;">${pnl_val:+.2f}</td>
            </tr>""")

        display_trade_rows = trade_rows if trade_rows else exchange_trade_rows
        trades_html = "\n".join(display_trade_rows) if display_trade_rows else '<tr><td colspan="7" style="text-align:center; color:#64748b;">No trades executed yet</td></tr>'

        # Symbol breakdown badges
        badges = []
        for s, val in sym_pnl.items():
            b_col = "#10b981" if val >= 0 else "#ef4444"
            badges.append(f'<span><b>{s}:</b> <span style="color:{b_col};">${val:+.2f}</span></span>')
        pnl_by_symbol_badges = " &bull; ".join(badges) if badges else '<span style="color:#64748b;">Awaiting trade events...</span>'

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
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .market-card {{
      display: flex;
      flex-direction: column;
      gap: 12px;
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
    .chart-controls {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
      margin-top: 2px;
      margin-bottom: 6px;
      flex-wrap: wrap;
    }}
    .tf-pills {{
      display: flex;
      gap: 3px;
      background: #090e1a;
      padding: 2px;
      border-radius: 6px;
      border: 1px solid #1e293b;
    }}
    .tf-btn {{
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .tf-btn:hover {{
      color: #f1f5f9;
      background: rgba(255,255,255,0.05);
    }}
    .tf-btn.active {{
      background: #1e293b;
      color: #38bdf8;
      box-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }}
    .zoom-pills {{
      display: flex;
      gap: 2px;
      background: #090e1a;
      padding: 2px;
      border-radius: 6px;
      border: 1px solid #1e293b;
    }}
    .zoom-btn {{
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 10px;
      font-weight: 600;
      padding: 2px 5px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .zoom-btn:hover {{
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.15);
    }}
    .chart-legend {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 10px;
      font-family: 'JetBrains Mono', monospace;
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      color: #94a3b8;
    }}
    .legend-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      display: inline-block;
    }}
    .chart-container {{
      position: relative;
      width: 100%;
      height: 195px;
      background: #060a12;
      border: 1px solid #1e293b;
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 6px;
    }}
    .market-chart {{
      width: 100%;
      height: 100%;
      display: block;
      cursor: crosshair;
    }}
    .chart-info-bar {{
      position: absolute;
      top: 3px;
      left: 6px;
      right: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      color: #94a3b8;
      pointer-events: none;
      display: flex;
      justify-content: space-between;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      z-index: 2;
      background: rgba(6, 10, 18, 0.75);
      padding: 2px 5px;
      border-radius: 4px;
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
      gap: 10px;
    }}
    .leg-box {{
      padding: 10px 12px;
      border-radius: 8px;
      font-size: 12px;
      background: #151f32;
      border: 1px solid #243248;
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
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      border-radius: 12px;
      border: 1px solid #1e293b;
      background: #0f172a;
      margin-bottom: 24px;
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
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-title-wrap">
          <div class="pulse-dot"></div>
          <h1>Bybit <span class="hide-mobile">Multi-Pair </span>Hedge Bot</h1>
        </div>
        <span class="status-pill" style="border-color:{status_color}; color:{status_color}">{status_text}</span>
      </div>
      <div class="header-actions">
        <a href="/api/ai-summary" class="btn" target="_blank" title="AI Summary Markdown">🤖 AI View</a>
        <a href="/api/status" class="btn" target="_blank" title="JSON Status API">⚡ JSON</a>
        <button onclick="location.reload()" class="btn btn-primary" title="Refresh Telemetry">🔄 Refresh</button>
        <a href="/logout" class="btn" title="Logout">🚪 Exit</a>
      </div>
    </header>

    <div class="stats-grid">
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
    </div>

    <div class="card" style="margin-bottom:24px; padding:12px 18px; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; background:#0b1329;">
      <div style="font-size:12px; font-weight:700; text-transform:uppercase; color:#94a3b8; letter-spacing:0.5px;">Bybit Realized P&L by Symbol:</div>
      <div style="display:flex; flex-wrap:wrap; gap:14px; font-family:'JetBrains Mono', monospace; font-size:13px;">
        {pnl_by_symbol_badges}
      </div>
    </div>

    <div class="section-title">
      <span>Market Watch & Active Hedge Legs</span>
    </div>
    <div class="markets-grid">
      {markets_html}
    </div>

    <div class="section-title">
      <span>Recent Trade Execution Audit</span>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:#64748b;">(bybit_trades.csv)</span>
        <a href="/api/clear-trades?redirect=1" class="btn" style="font-size:11px; padding:3px 8px; border-color:#475569;" onclick="return confirm('Clear trade history audit records?');">🧹 Clear History</a>
      </div>
    </div>
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Symbol</th>
            <th>Leg</th>
            <th>Event</th>
            <th>Fill Price</th>
            <th>Size</th>
            <th>Net PnL</th>
          </tr>
        </thead>
        <tbody>
          {trades_html}
        </tbody>
      </table>
    </div>

    <div class="section-title">
      <span>Live System Logs & Crash Diagnostics</span>
      <span style="font-size:12px; color:#64748b;">journalctl -u bybit-bot -n 80</span>
    </div>
    <div class="terminal" id="term">{logs}</div>
  </div>

  <script>
    const activeTfs = {{}};
    const chartData = {{}};
    const chartZoom = {{}}; // sym -> {{ count: 35, offset: 0 }}

    function getZoomConfig(sym, total) {{
      if (!chartZoom[sym]) {{
        chartZoom[sym] = {{ count: Math.min(35, total || 35), offset: 0 }};
      }}
      return chartZoom[sym];
    }}

    function zoomChart(sym, delta) {{
      const data = chartData[sym];
      if (!data || !data.candles) return;
      const total = data.candles.length;
      const cfg = getZoomConfig(sym, total);
      cfg.count = Math.max(10, Math.min(total, cfg.count + delta));
      cfg.offset = Math.max(0, Math.min(total - cfg.count, cfg.offset));
      renderCanvasChart(sym, data);
    }}

    function resetZoom(sym) {{
      const data = chartData[sym];
      if (!data || !data.candles) return;
      const total = data.candles.length;
      chartZoom[sym] = {{ count: Math.min(35, total), offset: 0 }};
      renderCanvasChart(sym, data);
    }}

    function initCharts() {{
      const cards = document.querySelectorAll('.market-card');
      cards.forEach(card => {{
        const sym = card.getAttribute('data-symbol');
        if (!sym) return;
        const savedTf = localStorage.getItem('tf_' + sym) || '60';
        activeTfs[sym] = savedTf;
        updateTfButtons(sym, savedTf);
        setupCanvasEvents(sym);
        loadChart(sym, savedTf);
      }});
    }}

    function updateTfButtons(sym, tf) {{
      const wrap = document.getElementById('tf-pills-' + sym);
      if (!wrap) return;
      wrap.querySelectorAll('.tf-btn').forEach(btn => {{
        btn.classList.toggle('active', btn.getAttribute('data-tf') === tf);
      }});
    }}

    function changeChartTf(sym, tf) {{
      activeTfs[sym] = tf;
      localStorage.setItem('tf_' + sym, tf);
      updateTfButtons(sym, tf);
      loadChart(sym, tf);
    }}

    async function loadChart(sym, tf) {{
      const info = document.getElementById('info-' + sym);
      try {{
        const res = await fetch(`/api/candles?symbol=${{sym}}&interval=${{tf}}&limit=80`);
        if (!res.ok) throw new Error('API error');
        const data = await res.json();
        if (!data.candles || data.candles.length === 0) {{
          if (info) info.textContent = 'No candle data';
          return;
        }}
        chartData[sym] = data;

        // Update movement % badge
        const pctBadge = document.getElementById('pct-' + sym);
        if (pctBadge) {{
          const chg = data.change_pct || 0;
          pctBadge.textContent = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
          pctBadge.className = 'pct-badge ' + (chg >= 0 ? 'up' : 'down');
        }}

        renderCanvasChart(sym, data);
      }} catch (e) {{
        if (info) info.textContent = 'Chart load error';
      }}
    }}

    function renderCanvasChart(sym, data, hoverIdx = -1) {{
      const canvas = document.getElementById('chart-' + sym);
      const info = document.getElementById('info-' + sym);
      if (!canvas) return;

      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height || 195;
      const dpr = window.devicePixelRatio || 1;

      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.resetTransform();
      ctx.scale(dpr, dpr);

      const allCandles = data.candles;
      if (!allCandles || allCandles.length === 0) return;

      const total = allCandles.length;
      const cfg = getZoomConfig(sym, total);
      const count = Math.max(10, Math.min(total, cfg.count));
      const offset = Math.max(0, Math.min(total - count, cfg.offset));
      const startIdx = total - offset - count;
      const endIdx = total - offset;
      const candles = allCandles.slice(startIdx, endIdx);

      const topH = Math.floor(h * 0.70);
      const botH = h - topH;
      const rightMargin = 48;
      const plotW = w - rightMargin;
      const n = candles.length;
      const slotW = plotW / n;
      const candleW = Math.max(2, Math.min(10, slotW * 0.68));

      // Calculate price bounds (candles + EMAs)
      let minP = Infinity, maxP = -Infinity;
      candles.forEach(c => {{
        minP = Math.min(minP, c.l);
        maxP = Math.max(maxP, c.h);
        if (c.ema9) {{ minP = Math.min(minP, c.ema9); maxP = Math.max(maxP, c.ema9); }}
        if (c.ema21) {{ minP = Math.min(minP, c.ema21); maxP = Math.max(maxP, c.ema21); }}
      }});
      const pPad = (maxP - minP) * 0.08 || 1;
      minP -= pPad; maxP += pPad;

      function yP(p) {{
        return topH - ((p - minP) / (maxP - minP)) * (topH - 22) - 10;
      }}

      // ADX bounds (0 to 60)
      let maxAdx = 55;
      candles.forEach(c => {{ if (c.adx) maxAdx = Math.max(maxAdx, c.adx); }});
      maxAdx = Math.min(100, Math.ceil(maxAdx / 10) * 10);

      function yA(a) {{
        return h - ((a - 0) / maxAdx) * (botH - 12) - 4;
      }}

      // 1. Draw horizontal grid lines & price labels
      ctx.lineWidth = 1;
      ctx.font = '9px JetBrains Mono, monospace';
      ctx.fillStyle = '#64748b';
      ctx.textAlign = 'left';

      const gridSteps = 3;
      for (let i = 0; i <= gridSteps; i++) {{
        const gVal = minP + (maxP - minP) * (i / gridSteps);
        const gy = yP(gVal);
        ctx.strokeStyle = '#151f32';
        ctx.beginPath();
        ctx.moveTo(0, gy);
        ctx.lineTo(plotW, gy);
        ctx.stroke();

        const pLabel = gVal >= 1000 ? gVal.toFixed(0) : (gVal >= 10 ? gVal.toFixed(2) : gVal.toFixed(3));
        ctx.fillText(pLabel, plotW + 5, gy + 3);
      }}

      // 2. Draw Candlesticks
      candles.forEach((c, i) => {{
        const cx = i * slotW + slotW / 2;
        const isUp = c.c >= c.o;
        const col = isUp ? '#10b981' : '#ef4444';

        ctx.strokeStyle = col;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(cx, yP(c.h));
        ctx.lineTo(cx, yP(c.l));
        ctx.stroke();

        const yOpen = yP(c.o);
        const yClose = yP(c.c);
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(1.5, Math.abs(yOpen - yClose));
        ctx.fillStyle = col;
        ctx.fillRect(cx - candleW / 2, bodyTop, candleW, bodyH);
      }});

      // 3. Draw EMA 9 line (Cyan)
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      let started = false;
      candles.forEach((c, i) => {{
        if (c.ema9 != null) {{
          const cx = i * slotW + slotW / 2;
          const cy = yP(c.ema9);
          if (!started) {{ ctx.moveTo(cx, cy); started = true; }}
          else ctx.lineTo(cx, cy);
        }}
      }});
      ctx.stroke();

      // 4. Draw EMA 21 line (Orange)
      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      started = false;
      candles.forEach((c, i) => {{
        if (c.ema21 != null) {{
          const cx = i * slotW + slotW / 2;
          const cy = yP(c.ema21);
          if (!started) {{ ctx.moveTo(cx, cy); started = true; }}
          else ctx.lineTo(cx, cy);
        }}
      }});
      ctx.stroke();

      // 5. Draw ADX Sub-Panel
      ctx.strokeStyle = '#1e293b';
      ctx.beginPath();
      ctx.moveTo(0, topH);
      ctx.lineTo(w, topH);
      ctx.stroke();

      // ADX 25 threshold line
      const y25 = yA(25);
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = '#475569';
      ctx.beginPath();
      ctx.moveTo(0, y25);
      ctx.lineTo(plotW, y25);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#64748b';
      ctx.fillText('25', plotW + 5, y25 + 3);

      // ADX line (Purple)
      ctx.beginPath();
      let adxStarted = false;
      candles.forEach((c, i) => {{
        if (c.adx != null) {{
          const cx = i * slotW + slotW / 2;
          const cy = yA(c.adx);
          if (!adxStarted) {{ ctx.moveTo(cx, cy); adxStarted = true; }}
          else ctx.lineTo(cx, cy);
        }}
      }});
      ctx.strokeStyle = '#a855f7';
      ctx.lineWidth = 1.6;
      ctx.stroke();

      // 6. Crosshair & Hover Tooltip
      const selIdx = (hoverIdx >= 0 && hoverIdx < n) ? hoverIdx : (n - 1);
      const sel = candles[selIdx];
      if (sel && info) {{
        const dirCol = sel.c >= sel.o ? '#10b981' : '#ef4444';
        const f9 = sel.ema9 ? sel.ema9.toFixed(1) : '--';
        const f21 = sel.ema21 ? sel.ema21.toFixed(1) : '--';
        const ax = sel.adx ? sel.adx.toFixed(1) : '--';
        const panned = offset > 0 ? `<b style="color:#f59e0b">PAST(-${{offset}})</b> ` : '';
        info.innerHTML = `<span>${{panned}}<b>${{sel.ts}}</b> <b style="color:${{dirCol}}">C:${{sel.c}}</b> O:${{sel.o}} H:${{sel.h}} L:${{sel.l}}</span>` +
                         `<span><b style="color:#38bdf8">EMA9:${{f9}}</b> <b style="color:#f59e0b">EMA21:${{f21}}</b> <b style="color:#a855f7">ADX:${{ax}}</b> <small style="color:#64748b;">[${{n}}b ↕zoom↔pan]</small></span>`;
      }}

      if (hoverIdx >= 0 && hoverIdx < n) {{
        const hx = hoverIdx * slotW + slotW / 2;
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = '#94a3b8';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(hx, 0);
        ctx.lineTo(hx, h);
        ctx.stroke();
        ctx.setLineDash([]);
      }}
    }}

    function setupCanvasEvents(sym) {{
      const canvas = document.getElementById('chart-' + sym);
      if (!canvas) return;

      let isDragging = false;
      let dragStartX = 0;
      let dragStartOffset = 0;

      // 1. Mouse wheel zoom
      canvas.addEventListener('wheel', (e) => {{
        e.preventDefault();
        zoomChart(sym, e.deltaY > 0 ? 5 : -5);
      }}, {{ passive: false }});

      // 2. Click & drag to pan
      canvas.addEventListener('mousedown', (e) => {{
        if (e.button !== 0) return;
        isDragging = true;
        dragStartX = e.clientX;
        const total = chartData[sym]?.candles?.length || 35;
        dragStartOffset = getZoomConfig(sym, total).offset;
        canvas.style.cursor = 'grabbing';
      }});

      window.addEventListener('mouseup', () => {{
        if (isDragging) {{
          isDragging = false;
          canvas.style.cursor = 'crosshair';
        }}
      }});

      canvas.addEventListener('mousemove', (e) => {{
        const data = chartData[sym];
        if (!data || !data.candles) return;
        const total = data.candles.length;
        const cfg = getZoomConfig(sym, total);
        const rect = canvas.getBoundingClientRect();
        const plotW = rect.width - 48;
        const slotW = plotW / cfg.count;

        if (isDragging) {{
          const deltaX = e.clientX - dragStartX;
          const shift = Math.round(deltaX / Math.max(4, slotW));
          cfg.offset = Math.max(0, Math.min(total - cfg.count, dragStartOffset + shift));
          renderCanvasChart(sym, data);
          return;
        }}

        const x = e.clientX - rect.left;
        const idx = Math.floor((x / plotW) * cfg.count);
        renderCanvasChart(sym, data, Math.max(0, Math.min(cfg.count - 1, idx)));
      }});

      canvas.addEventListener('mouseleave', () => {{
        if (!isDragging) {{
          const data = chartData[sym];
          if (data) renderCanvasChart(sym, data, -1);
        }}
      }});

      // 3. Touch support (drag & pan)
      let touchStartX = 0;
      let touchStartOffset = 0;
      canvas.addEventListener('touchstart', (e) => {{
        if (e.touches.length === 1) {{
          touchStartX = e.touches[0].clientX;
          const total = chartData[sym]?.candles?.length || 35;
          touchStartOffset = getZoomConfig(sym, total).offset;
        }}
      }}, {{ passive: true }});

      canvas.addEventListener('touchmove', (e) => {{
        const data = chartData[sym];
        if (!data || !data.candles || e.touches.length !== 1) return;
        const total = data.candles.length;
        const cfg = getZoomConfig(sym, total);
        const rect = canvas.getBoundingClientRect();
        const plotW = rect.width - 48;
        const slotW = plotW / cfg.count;
        const deltaX = e.touches[0].clientX - touchStartX;
        if (Math.abs(deltaX) > 8) {{
          const shift = Math.round(deltaX / Math.max(4, slotW));
          cfg.offset = Math.max(0, Math.min(total - cfg.count, touchStartOffset + shift));
          renderCanvasChart(sym, data);
        }}
      }}, {{ passive: true }});

      canvas.addEventListener('touchend', () => {{
        const data = chartData[sym];
        if (data) renderCanvasChart(sym, data, -1);
      }});
    }}

    // Initialize charts on window load
    window.addEventListener('DOMContentLoaded', initCharts);
    window.addEventListener('resize', () => {{
      Object.keys(chartData).forEach(sym => {{
        if (chartData[sym]) renderCanvasChart(sym, chartData[sym]);
      }});
    }});

    // Refresh charts every 15s in background
    setInterval(() => {{
      Object.keys(activeTfs).forEach(sym => {{
        loadChart(sym, activeTfs[sym]);
      }});
    }}, 15000);

    // Auto-refresh full page every 30 seconds
    setTimeout(() => {{ location.reload(); }}, 30000);
    // Scroll terminal to bottom
    const term = document.getElementById('term');
    if (term) term.scrollTop = term.scrollHeight;
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
