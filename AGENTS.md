# AGENTS.md: AI Assistant & Engineering Guidelines

Welcome to the **Bybit Multi-Pair Concurrent Dual-Leg Hedging System & Research Suite** (`hyper_hedge_research`). This document serves as the master instruction manual, system architecture guide, and behavioral constraint specification for AI agents (e.g., Antigravity, Claude, ChatGPT) and quantitative developers collaborating on this codebase.

---

## 1. System Overview & Core Philosophy

This repository contains an institutional-grade algorithmic trading system for **Bybit Linear Perpetual contracts** on the **Unified Trading Account (UTA) V5 API**, supporting concurrent multi-pair execution across:
* **Bitcoin (`BTCUSDT`)**
* **Ethereum (`ETHUSDT`)**
* **Solana (`SOLUSDT`)**
* **Gold (`PAXGUSDT` / `XAUUSDT`)**

### Core Strategy: Path B Asymmetric Size-Flip Trap Hunter & Zero-Loss Pullback Strategy
Unlike naive single-directional momentum strategies that suffer catastrophic drawdowns in choppy markets, or symmetric hedges that eat double commissions, this system operates on **asymmetric coexisting dual legs**:
1. **BothSides / Hedge Mode**:
   - Holds simultaneous Long (`positionIdx=1`, side: `Buy`) and Short (`positionIdx=2`, side: `Sell`) positions on the same trading pair without mutual netting.
2. **Indicator-Signaled Entry**:
   - Double entry triggers on 60m candle close when Fast EMA(9) crosses Slow EMA(21) **AND** ADX exceeds the asset threshold.
3. **Asymmetric Sizing**:
   - **Primary Trend Leg** (signal side): Sized at **100%** base size (`eff_size`).
   - **Counter-Hedge Leg**: Sized at **30%** base size (`eff_size * 0.30`).
4. **Tri-Modal Execution State Machine**:
   - **Branch 1: Signal Confirmed (+1.0D Expansion)**:
     - Collapse 30% counter leg at market.
     - Arm 100% Primary runner with Zero-Loss SL ($P_{\text{SL}} = P_0 \pm 0.48D$), Ratchet SL at $+1.40D$ to $+1.48D$, Full TP at $+2.00D$.
   - **Branch 2: Signal Trapped (-1.0D Expansion / Method A Size-Flip)**:
     - Collapse 100% trapped leg at market.
     - Upsize counter leg by adding $+70\%$ notional to make it a 100% runner.
     - Initial SL at $P_0$, Full TP dynamically configured per asset (**$+3.50D$ for BTC & SOL**, **$+3.00D$ for ETH**) with True Breakeven lock at $-2.00D$ and profit ratchet at $-2.80D$.
   - **Branch 3: Consolidation Timeout (50-Candle Window)**:
     - If neither $+1.0D$ nor $-1.0D$ is reached within 50 bars, liquidates both legs at market to recycle margin.

---

## 2. Repository Layout & Architecture

```text
hyper_hedge_research/
├── run_bybit_bot.py                # Main CLI entry point for the live trading daemon
├── telemetry_server.py             # Authenticated HTTP telemetry, web dashboard & AI API
├── backtest.py                     # Historical kline/tick backtester & indicator math
├── requirements.txt                # Production dependencies (pybit, python-dotenv, rich, numpy)
│
├── bybit_bot/                      # Core trading bot package
│   ├── __init__.py
│   ├── config.py                   # Environment loader, CLI args & champion pair profiles
│   ├── client.py                   # Bybit V5 SDK wrapper, instrument specs, REST reconciliation
│   ├── engine.py                   # Concurrent multi-pair state machine & WebSocket listener
│   └── leg.py                      # PositionLeg dataclass, trailing stop math & PnL tracking
│
├── deploy/                         # Production Debian 13 / Ubuntu VPS assets
│   ├── deploy_vps.sh               # Automated deployment script with firewall & cron setup
│   ├── update.sh                   # Zero-downtime maintenance and update script
│   ├── bybit-bot.service           # Systemd unit file for trading engine (Restart=always)
│   ├── bybit-telemetry.service     # Systemd unit file for telemetry server (Port 8080)
│   ├── logrotate.conf              # Trade audit CSV weekly rotation configuration
│   ├── Dockerfile                  # Optional containerized build definition
│   └── docker-compose.yml          # Docker compose specification
│
├── .github/workflows/
│   └── deploy.yml                  # 1-click Continuous Deployment workflow via SSH
│
├── hype_bot/                       # Legacy Rust Hyperliquid bot (ISOLATED & GIT-IGNORED)
├── bot_state.json                  # Runtime atomic state cache (GIT-IGNORED)
├── bybit_trades.csv                # Persistent audit ledger of all closed trades (GIT-IGNORED)
└── .env                            # Active API credentials and passwords (NEVER COMMITTED)
```

---

## 3. Strict Rules & Constraints for AI Agents

### Rule 1: Security & Secrets Protection (Zero Tolerance)
* **NEVER commit or log credentials**: `.env`, `creds.txt`, API keys, API secrets, and passwords must never be staged or committed to Git.
* **Keep `.gitignore` intact**: Ensure `*.log`, `*.csv`, `*.pkl`, `.env`, and `bot_state.json*` remain ignored.
* **Log Sanitization**: Any new logging or HTTP endpoint must route output through `telemetry_server.sanitize_logs()` to prevent credential leakage.

### Rule 2: Exchange & Position Safety
* **Preserve Hedge Mode (`BothSides`)**: Never place one-way mode orders. All Long orders must use `positionIdx=1`, and Short orders must use `positionIdx=2`.
* **Crash-Restart Reconciliation**: When modifying `engine.py`, never bypass or remove `_reconcile_open_positions()`. The bot must always reconnect and adopt live exchange positions on boot.
* **Taker Fee Awareness**: Every trade transition incurs VIP0 taker fees (0.055%). Never reduce SL/TP ratios below the breakeven threshold.

### Rule 3: Debian 13 VPS Environment Compatibility
* **Non-Interactive Execution**: Always use `DEBIAN_FRONTEND=noninteractive` when installing packages.
* **Root vs. Sudo**: The production VPS runs Debian 13. When executing commands, use `sudo` only if not already running as `root`.
* **Time Synchronization**: Always verify `chrony` is active. Clock drift $>1000\text{ms}$ results in Bybit `Error 10002`.

### Rule 4: Do Not Push to Git Without Explicit User Consent
* When the user specifies "do not push" or does not ask for deployment, keep modifications strictly local.

---

## 4. How AI Agents Can Inspect Bot Telemetry

The repository provides a dedicated, token-authenticated AI monitoring endpoint running on the VPS:

### AI Markdown Endpoint:
```http
GET http://<VPS_IP>:8080/api/ai-summary?password=<TELEMETRY_PASSWORD>
```
* **Payload**: Clean, high-signal Markdown document (~500 tokens).
* **Contents**: Live process state, uptime, mark prices, EMA/ADX indicator values, open Long & Short legs with current trailing stops and floating PnL, and the last 15 sanitized system logs.

### JSON State Endpoint:
```http
GET http://<VPS_IP>:8080/api/status?password=<TELEMETRY_PASSWORD>
```

### Sanitized Crash & Error Logs:
```http
GET http://<VPS_IP>:8080/api/logs?password=<TELEMETRY_PASSWORD>&lines=100&errors=1
```

---

## 5. Developer & Agent Cheatsheet

### Running the Bot Locally:
```bash
# Dry-run simulation (live WebSocket data, simulated order fills)
python run_bybit_bot.py --dry-run

# Test immediate dual-leg entry on BTCUSDT
python run_bybit_bot.py --dry-run --test-entry

# Run specific symbols with custom interval
python run_bybit_bot.py --symbols BTCUSDT,ETHUSDT --dry-run
```

### Running the Telemetry Server:
```bash
python telemetry_server.py
# Access dashboard at: http://localhost:8080/dashboard?password=<TELEMETRY_PASSWORD>
```

### Inspecting Service Status on Debian 13 VPS:
```bash
# Service status
systemctl status bybit-bot bybit-telemetry

# Follow live trading logs
journalctl -u bybit-bot -f

# Filter for crashes or exceptions
journalctl -u bybit-bot -p err..emerg -n 50 --no-pager
journalctl -u bybit-bot --since "24 hours ago" | grep -A 20 "Traceback"

# Restart both services
systemctl restart bybit-bot bybit-telemetry
```
