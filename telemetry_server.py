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
        else:
            self.send_error(404, "Not Found")

    # ==========================================================================
    # API HANDLERS
    # ==========================================================================

    def _handle_api_status(self):
        state = read_bot_state()
        svc = get_service_status()
        resp = {
            "server_time": datetime.now().isoformat(),
            "service": svc,
            "bot_state": state,
        }
        self._send_json(resp)

    def _handle_api_ai_summary(self):
        """Generate high-signal, compact Markdown summary for AI Agents."""
        state = read_bot_state()
        svc = get_service_status()
        trades = read_trade_history(limit=10)
        logs = get_systemd_logs(lines=15)

        uptime_sec = state.get("uptime_seconds", 0)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s" if uptime_sec else "N/A"

        # Calculate trade statistics
        total_pnl = state.get("realized_pnl", 0.0)
        open_pnl = state.get("open_pnl", 0.0)
        active_count = state.get("active_pairs_count", 0)
        max_pairs = state.get("max_concurrent_pairs", 3)
        network = state.get("network", "TESTNET")
        leverage = state.get("leverage", 4)

        md = []
        md.append(f"# Bybit Multi-Pair Bot: Live Telemetry Summary")
        md.append(f"**Timestamp**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}`  |  **Service**: `{svc.get('status', 'active').upper()}` (PID: {svc.get('pid', 'N/A')})")
        md.append(f"- **Network**: `{network}` | **Leverage**: `{leverage}x` | **Uptime**: `{uptime_str}` | **Scans**: `{state.get('scan_count', 0)}`")
        md.append(f"- **Portfolio Status**: `{active_count}/{max_pairs}` Pairs Active | **Open PnL**: `${open_pnl:+.2f}` | **Realized PnL**: `${total_pnl:+.2f}`")
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

        md.append("## Recent Closed Trades")
        if not trades:
            md.append("*No closed trades recorded in audit ledger yet.*")
        else:
            md.append("| Timestamp | Symbol | Leg | Event | Price | Size | Net PnL |")
            md.append("|---|---|:---:|:---:|:---:|:---:|:---:|")
            for t in trades[:6]:
                pnl = t.get("pnl_usd", "0")
                md.append(f"| {t.get('timestamp','')} | {t.get('symbol','')} | {t.get('leg_side','')} | {t.get('event_type','')} | ${float(t.get('fill_price',0)):.2f} | {t.get('size','')} | ${float(pnl):+.2f} |")
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
                    "price", "extreme_price", "trailing_sl", "tp_target",
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
        trades = read_trade_history(limit=25)
        logs = get_systemd_logs(lines=80)

        is_active = svc.get("active", True)
        status_color = "#10b981" if is_active else "#ef4444"
        status_text = "ACTIVE" if is_active else "STOPPED"

        uptime_sec = state.get("uptime_seconds", 0)
        uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s" if uptime_sec else "Running"

        realized_pnl = state.get("realized_pnl", 0.0)
        open_pnl = state.get("open_pnl", 0.0)
        total_pnl = realized_pnl + open_pnl

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
            <div class="card market-card">
              <div class="market-header">
                <div>
                  <span class="sym-badge">{sym}</span>
                  <span class="status-pill" style="border-color:{p_color}; color:{p_color}">{p_status}</span>
                </div>
                <div class="sym-price">{px_str}</div>
              </div>
              <div class="market-ind">{ind}</div>
              <div class="legs-grid">
                {long_html}
                {short_html}
              </div>
            </div>""")

        markets_html = "\n".join(market_cards) if market_cards else '<div class="card" style="text-align:center; color:#64748b;">Engine initializing markets...</div>'

        # Trade rows
        trade_rows = []
        for t in trades:
            pnl_val = float(t.get("pnl_usd", 0) or 0)
            col = "#10b981" if pnl_val > 0 else ("#ef4444" if pnl_val < 0 else "#94a3b8")
            trade_rows.append(f"""<tr>
              <td>{t.get('timestamp','')}</td>
              <td><b>{t.get('symbol','')}</b></td>
              <td><span class="badge {t.get('leg_side','').lower()}">{t.get('leg_side','')}</span></td>
              <td>{t.get('event_type','')}</td>
              <td>${float(t.get('fill_price',0)):.2f}</td>
              <td>{t.get('size','')}</td>
              <td style="color:{col}; font-weight:600;">${pnl_val:+.2f}</td>
            </tr>""")
        trades_html = "\n".join(trade_rows) if trade_rows else '<tr><td colspan="7" style="text-align:center; color:#64748b;">No trades executed yet</td></tr>'

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
        <div class="stat-title">Realized Net Profit</div>
        <div class="stat-val" style="color:{rpnl_color}">${realized_pnl:+.2f}</div>
        <div class="stat-sub">From closed hedge legs</div>
      </div>
      <div class="card">
        <div class="stat-title">Open Unrealized PnL</div>
        <div class="stat-val" style="color:{opnl_color}">${open_pnl:+.2f}</div>
        <div class="stat-sub">Active market floating</div>
      </div>
      <div class="card">
        <div class="stat-title">Total Account Impact</div>
        <div class="stat-val" style="color:{tpnl_color}">${total_pnl:+.2f}</div>
        <div class="stat-sub">Realized + Floating</div>
      </div>
      <div class="card">
        <div class="stat-title">System Uptime</div>
        <div class="stat-val" style="font-size: 18px; margin-top:4px;">{uptime_str}</div>
        <div class="stat-sub">Scans: {state.get('scan_count', 0)} | Leverage: {state.get('leverage', 4)}x</div>
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
    // Auto-refresh every 10 seconds
    setTimeout(() => {{ location.reload(); }}, 10000);
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
