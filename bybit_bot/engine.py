import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import time
import json
import csv
import logging
from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from pybit.unified_trading import WebSocket
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from bybit_bot.config import Config, SymbolConfig
from bybit_bot.client import BybitService
from bybit_bot.leg import PositionLeg

# Import indicator computation from backtest engine (project root)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backtest import compute_indicators

logger  = logging.getLogger("BybitEngine")
console = Console()


@dataclass
class PairState:
    cfg: SymbolConfig
    status: str = "SCANNING"  # "SCANNING", "ACTIVE", "COOLDOWN"
    phase: str = "SCANNING"   # "SCANNING", "INCUBATION", "RUNNER_B1", "RUNNER_B2", "COOLDOWN"
    long_leg: Optional[PositionLeg] = None
    short_leg: Optional[PositionLeg] = None
    latest_price: Optional[Decimal] = None
    cycle_count: int = 0
    cumulative_pnl: Decimal = Decimal("0")
    last_closed_ts: int = 0
    cooldown_until: float = 0.0
    status_msg: str = "Initializing..."
    fast_ema: Optional[Decimal] = None
    slow_ema: Optional[Decimal] = None
    adx_val: Optional[Decimal] = None

    # Path B: Asymmetric Size-Flip Trap Hunter state
    signal_direction: Optional[str] = None  # "bullish" or "bearish"
    entry_price: Decimal = Decimal("0")
    entry_ts: float = 0.0
    bars_elapsed: int = 0
    b1_trailed: bool = False
    b2_trailed_to_be: bool = False
    b2_trailed_to_plus_1d: bool = False
    base_be_sl: Decimal = Decimal("0")

    def is_active(self) -> bool:
        return self.status == "ACTIVE" and (
            (self.long_leg and self.long_leg.status == "ACTIVE") or
            (self.short_leg and self.short_leg.status == "ACTIVE")
        )


class BybitTradingEngine:
    def __init__(self, service: BybitService, config: Config):
        self.service = service
        self.config  = config

        # Multi-pair state container
        self.pairs: Dict[str, PairState] = {
            s.symbol: PairState(cfg=s) for s in config.symbols
        }

        self.running = False
        self.session_start = datetime.now()
        self.total_cycles_completed = 0
        self.scan_count = 0
        self.ws: Optional[WebSocket] = None

        self._init_csv()

    # ==========================================================================
    # STARTUP
    # ==========================================================================

    def start(self) -> None:
        """Initialize all markets, reconcile positions, start WebSocket and main loop."""
        grid_rows = []
        for s in self.config.symbols:
            grid_rows.append(
                f"[bold green]{s.symbol:<8}[/bold green] | "
                f"TF: {s.candle_interval}m | "
                f"EMA({s.ema_fast}/{s.ema_slow}) ADX>{s.adx_min} | "
                f"SL: [red]{s.sl_pct}%[/red] TP: [green]{s.tp_pct}%[/green] | "
                f"BE: {'ON' if s.be_lock else 'OFF'} | "
                f"Size: {s.size}"
            )

        console.print(Panel.fit(
            f"[bold yellow]BYBIT MULTI-PAIR CONCURRENT TRADING BOT[/bold yellow]\n"
            f"[cyan]Network:[/cyan] [bold]{'TESTNET' if self.config.testnet else 'MAINNET'}[/bold]  |  "
            f"[cyan]Leverage:[/cyan] {self.config.leverage}x  |  "
            f"[cyan]Max Concurrent Pairs:[/cyan] {self.config.max_concurrent_pairs}\n"
            f"[cyan]Mode:[/cyan] [bold magenta]{'DRY-RUN (SIMULATION)' if self.config.dry_run else 'LIVE TRADING'}[/bold magenta]  |  "
            f"[cyan]Log:[/cyan] {self.config.log_csv}\n\n"
            f"[bold cyan]Configured Markets:[/bold cyan]\n" + "\n".join(grid_rows),
            border_style="cyan",
        ))

        # 1. Initialize markets & accounts
        self.service.init_market_and_account([s.symbol for s in self.config.symbols])

        # 2. Crash-restart reconciliation across all configured pairs
        self._reconcile_open_positions()

        # 3. Start unified WebSocket streaming
        self._start_websocket()

        # 4. Immediate test entry if requested via flag or env
        if self.config.test_entry or self.config.force_entry:
            target_sym = self.config.force_entry if self.config.force_entry else self.config.symbols[0].symbol
            target_sym = target_sym.upper()
            if target_sym in self.pairs:
                console.print(
                    f"\n[bold magenta]>>> TEST-ENTRY TRIGGERED: Immediately entering live dual-leg hedge on {target_sym}! <<<[/bold magenta]"
                )
                self._enter_pair_trade(self.pairs[target_sym], "bullish")
            else:
                console.print(f"[bold red]Cannot force entry: {target_sym} not in configured symbols.[/bold red]")

        self.running = True

        # 5. Run main concurrent engine loop
        try:
            self._main_loop()
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Ctrl+C received -- shutting down gracefully...[/bold yellow]")
        finally:
            self.running = False
            if self.ws:
                try:
                    self.ws.exit()
                except Exception:
                    pass
            self._print_session_summary()

    # ==========================================================================
    # WEBSOCKET STREAMING
    # ==========================================================================

    def _start_websocket(self) -> None:
        """Start a single WebSocket subscribing to ticker streams for all active symbols."""
        self.ws = WebSocket(testnet=self.config.testnet, channel_type="linear")
        for sym in self.pairs.keys():
            self.ws.ticker_stream(symbol=sym, callback=self._on_ticker_message)
        logger.info(f"Unified WebSocket streaming {len(self.pairs)} symbols...")

    def _on_ticker_message(self, msg: dict) -> None:
        """Handle live price ticks across any subscribed symbol."""
        if not self.running:
            return
        topic = msg.get("topic", "")
        # Format is tickers.BTCUSDT
        sym = topic.split(".")[-1] if "." in topic else ""
        if not sym or sym not in self.pairs:
            return

        pair = self.pairs[sym]
        data = msg.get("data", {})
        price_str = data.get("lastPrice") or data.get("markPrice")
        if not price_str:
            return

        price = Decimal(str(price_str))
        pair.latest_price = price

        # Process trailing stop & TP if pair is active
        if pair.status == "ACTIVE":
            self._process_pair_tick(pair, price)

    # ==========================================================================
    # CRASH-RESTART RECONCILIATION
    # ==========================================================================

    def _reconcile_open_positions(self) -> None:
        """Check Bybit for existing open positions across all symbols on startup."""
        if self.config.dry_run:
            return
        try:
            positions = self.service.get_open_positions()
            if not positions:
                return

            logger.warning(f"[RECONCILE] Found {len(positions)} open position(s) on exchange.")
            for pos in positions:
                sym   = pos.get("symbol", "")
                if sym not in self.pairs:
                    continue

                pair  = self.pairs[sym]
                idx   = int(pos.get("positionIdx", 0))
                size  = Decimal(str(pos.get("size", "0")))
                entry = Decimal(str(pos.get("avgPrice", "0")))

                if size == 0 or entry == 0:
                    continue

                if idx == 1:
                    pair.long_leg = PositionLeg.new_long(
                        size, entry, pair.cfg.sl_ratio, pair.cfg.tp_ratio, symbol=sym
                    )
                    pos_sl = pos.get("stopLoss")
                    if pos_sl and float(pos_sl) > 0:
                        pair.long_leg.trailing_sl = Decimal(str(pos_sl))
                    pos_tp = pos.get("takeProfit")
                    if pos_tp and float(pos_tp) > 0:
                        pair.long_leg.tp_target = Decimal(str(pos_tp))
                    pair.status = "ACTIVE"
                    logger.info(f"[{sym}] Reconnected LONG  {size} @ {entry} (SL: {pair.long_leg.trailing_sl}, TP: {pair.long_leg.tp_target})")
                elif idx == 2:
                    pair.short_leg = PositionLeg.new_short(
                        size, entry, pair.cfg.sl_ratio, pair.cfg.tp_ratio, symbol=sym
                    )
                    pos_sl = pos.get("stopLoss")
                    if pos_sl and float(pos_sl) > 0:
                        pair.short_leg.trailing_sl = Decimal(str(pos_sl))
                    pos_tp = pos.get("takeProfit")
                    if pos_tp and float(pos_tp) > 0:
                        pair.short_leg.tp_target = Decimal(str(pos_tp))
                    pair.status = "ACTIVE"
                    logger.info(f"[{sym}] Reconnected SHORT {size} @ {entry} (SL: {pair.short_leg.trailing_sl}, TP: {pair.short_leg.tp_target})")

        except Exception as e:
            logger.warning(f"Reconciliation check skipped: {e}")
        self._dump_state_json()

    # ==========================================================================
    # MAIN CONCURRENT LOOP
    # ==========================================================================

    def _main_loop(self) -> None:
        """Main loop: scans candidates, reconciles open trades, and prints live status."""
        last_scan_time = 0.0
        last_reconcile_time = 0.0

        # Run first scan immediately on start
        self._scan_all_pairs()
        last_scan_time = time.time()

        while self.running:
            now = time.time()

            # -- 1. Check max cycles limit ------------------------------------
            if self.config.max_cycles > 0 and self.total_cycles_completed >= self.config.max_cycles:
                console.print(f"\n[bold cyan]Max cycles ({self.config.max_cycles}) completed. Stopping.[/bold cyan]")
                break

            # -- 2. Scan for entry signals across idle pairs ------------------
            if now - last_scan_time >= self.config.poll_interval:
                last_scan_time = now
                self._scan_all_pairs()

            # -- 3. Periodic REST reconciliation (every 5 seconds) -----------
            if not self.config.dry_run and now - last_reconcile_time >= 5.0:
                last_reconcile_time = now
                self._reconcile_active_pairs_with_exchange()

            time.sleep(0.5)

    # ==========================================================================
    # CONCURRENT SIGNAL SCANNER
    # ==========================================================================

    def _scan_all_pairs(self) -> None:
        """Scan each idle pair, evaluate indicators, and print structured status report."""
        self.scan_count += 1
        now = time.time()
        active_count = sum(1 for p in self.pairs.values() if p.status == "ACTIVE")
        now_str = datetime.now().strftime("%H:%M:%S")

        report_lines = []

        for sym, pair in self.pairs.items():
            if not self.running:
                break

            # Handle cooldown
            if pair.status == "COOLDOWN":
                if now >= pair.cooldown_until:
                    pair.status = "SCANNING"
                    pair.status_msg = "Cooldown complete. Scanning..."
                    report_lines.append(f"  * [bold]{sym:<8}[/bold]: Cooldown complete -> Scanning")
                else:
                    rem = int(pair.cooldown_until - now)
                    pair.status_msg = f"Cooldown ({rem}s rem)"
                    report_lines.append(f"  * [bold]{sym:<8}[/bold]: COOLDOWN ({rem}s remaining)")
                continue

            # Active pairs display current legs & PnL
            if pair.status == "ACTIVE":
                px = pair.latest_price or self.service.get_market_price(sym)
                pair.latest_price = px

                # Check for candle timeout if in incubation
                if pair.phase == "INCUBATION" and pair.entry_ts > 0:
                    bar_secs = int(pair.cfg.candle_interval) * 60
                    elapsed_bars = int((now - pair.entry_ts) / bar_secs) if bar_secs > 0 else 0
                    pair.bars_elapsed = elapsed_bars
                    if elapsed_bars >= pair.cfg.timeout_bars:
                        console.print(f"\n[bold red]>>> [{sym}] TIMEOUT REACHED ({elapsed_bars}/{pair.cfg.timeout_bars} bars in deadlock) <<<[/bold red]")
                        self._timeout_pair_cycle(pair)
                        report_lines.append(f"  * [bold red]{sym:<8}[/bold red]: TIMEOUT LIQUIDATION (50 bars)")
                        continue

                l_pnl, l_pct = pair.long_leg.pnl(px) if pair.long_leg else (Decimal("0"), Decimal("0"))
                s_pnl, s_pct = pair.short_leg.pnl(px) if pair.short_leg else (Decimal("0"), Decimal("0"))
                net = l_pnl + s_pnl
                color = "bold green" if net >= 0 else "bold red"
                report_lines.append(
                    f"  * [bold yellow]{sym:<8}[/bold yellow]: [bold]{pair.phase}[/bold] @ ${px:.2f} | "
                    f"Long: ${l_pnl:+.2f} ({l_pct:+.2f}%) | Short: ${s_pnl:+.2f} ({s_pct:+.2f}%) | "
                    f"Net: [{color}]${net:+.2f}[/{color}]"
                )
                continue

            # Check concurrency limit
            if active_count >= self.config.max_concurrent_pairs:
                report_lines.append(f"  * [bold]{sym:<8}[/bold]: WAITING (Max {self.config.max_concurrent_pairs} pairs active)")
                continue

            # Poll recent candles for pair
            try:
                candles = self.service.get_recent_candles(
                    symbol=sym, interval=pair.cfg.candle_interval, limit=100
                )
                if len(candles) < 3:
                    report_lines.append(f"  * [bold]{sym:<8}[/bold]: Fetching klines ({len(candles)}/100)...")
                    continue

                # Compute EMA & ADX on candles
                compute_indicators(
                    candles,
                    fast_periods=[pair.cfg.ema_fast, pair.cfg.ema_slow],
                    adx_period=pair.cfg.adx_period,
                )

                closed_candle = candles[-2]
                closed_ts = closed_candle["timestamp"]
                px = pair.latest_price or candles[-1]["close"]
                pair.latest_price = px

                fast_v = closed_candle.get(f"ema_{pair.cfg.ema_fast}")
                slow_v = closed_candle.get(f"ema_{pair.cfg.ema_slow}")
                adx_v  = closed_candle.get("adx")

                pair.fast_ema = Decimal(str(fast_v)) if fast_v is not None else None
                pair.slow_ema = Decimal(str(slow_v)) if slow_v is not None else None
                pair.adx_val  = Decimal(str(adx_v)) if adx_v is not None else None

                # Calculate seconds until current forming bar closes
                interval_secs = int(pair.cfg.candle_interval) * 60
                forming_ts = candles[-1]["timestamp"]
                next_close = int(forming_ts / 1000) + interval_secs
                rem = max(0, next_close - int(time.time()))
                mins, secs = divmod(rem, 60)
                rem_str = f"{mins:02d}m {secs:02d}s"

                # Check for closed-candle signal crossover if on a new bar
                if closed_ts != pair.last_closed_ts:
                    pair.last_closed_ts = closed_ts
                    direction = self._check_pair_signal(pair, candles)
                    if direction:
                        console.print(
                            f"\n[bold green]>>> [{sym}] SIGNAL CONFIRMED: {direction.upper()} on "
                            f"{closed_candle['datetime'].strftime('%H:%M')} candle! <<<[/bold green]"
                        )
                        self._enter_pair_trade(pair, direction)
                        active_count += 1
                        report_lines.append(f"  * [bold green]{sym:<8}[/bold green]: TRIGGERED {direction.upper()} ENTRY!")
                        continue

                # Trend and ADX status
                trend = "Bullish (EMA9 > EMA21)" if (fast_v and slow_v and fast_v > slow_v) else "Bearish (EMA9 < EMA21)"
                adx_s = f"{float(adx_v):.1f}" if adx_v else "N/A"
                f_s = f"{float(fast_v):.2f}" if fast_v else "N/A"
                s_s = f"{float(slow_v):.2f}" if slow_v else "N/A"
                adx_ok = "OK" if (pair.cfg.adx_min == 0 or (adx_v and adx_v > pair.cfg.adx_min)) else f"need >{pair.cfg.adx_min}"

                report_lines.append(
                    f"  * [bold]{sym:<8}[/bold]: ${px} | "
                    f"EMA({pair.cfg.ema_fast}/{pair.cfg.ema_slow}): {f_s} / {s_s} ({trend}) | "
                    f"ADX: {adx_s} ({adx_ok}) | "
                    f"Bar Close in: {rem_str}"
                )

            except Exception as e:
                report_lines.append(f"  * [bold]{sym:<8}[/bold]: Scan err: {str(e)[:30]}")

        # Summary line
        cum_all = sum((p.cumulative_pnl for p in self.pairs.values()), Decimal("0"))
        tot_open = Decimal("0")
        for p in self.pairs.values():
            if p.status == "ACTIVE":
                px = p.latest_price or Decimal("0")
                l = p.long_leg.pnl(px)[0] if p.long_leg else Decimal("0")
                s = p.short_leg.pnl(px)[0] if p.short_leg else Decimal("0")
                tot_open += (l + s)

        # Print clean status report with clear visual separation
        console.print(f"\n[bold cyan]--- [SCAN #{self.scan_count} @ {now_str}] ----------------------------------------------[/bold cyan]")
        for line in report_lines:
            console.print(line)
        console.print(
            f"  [cyan]Portfolio:[/cyan] {active_count}/{self.config.max_concurrent_pairs} Active Pairs | "
            f"Open PnL: ${tot_open:+.2f} | Realized PnL: ${cum_all:+.2f}"
        )
        self._dump_state_json()

    def _dump_state_json(self) -> None:
        """Atomically dump current bot state to bot_state.json for the telemetry server."""
        try:
            cum_all = sum((p.cumulative_pnl for p in self.pairs.values()), Decimal("0"))
            tot_open = Decimal("0")
            pairs_dict = {}

            for sym, pair in self.pairs.items():
                px = pair.latest_price
                px_float = float(px) if px is not None else None
                l_pnl, l_pct = (pair.long_leg.pnl(px) if (pair.long_leg and px) else (Decimal("0"), Decimal("0")))
                s_pnl, s_pct = (pair.short_leg.pnl(px) if (pair.short_leg and px) else (Decimal("0"), Decimal("0")))

                if pair.status == "ACTIVE":
                    tot_open += (l_pnl + s_pnl)

                p_data = {
                    "symbol": sym,
                    "status": pair.status,
                    "phase": pair.phase,
                    "signal_direction": pair.signal_direction,
                    "status_msg": pair.status_msg,
                    "latest_price": px_float,
                    "entry_price": float(pair.entry_price) if pair.entry_price else None,
                    "bars_elapsed": pair.bars_elapsed,
                    "candle_interval": pair.cfg.candle_interval,
                    "d_pct": float(pair.cfg.d_pct),
                    "cycle_count": pair.cycle_count,
                    "cumulative_pnl": float(pair.cumulative_pnl),
                    "fast_ema": float(pair.fast_ema) if pair.fast_ema is not None else None,
                    "slow_ema": float(pair.slow_ema) if pair.slow_ema is not None else None,
                    "adx": float(pair.adx_val) if pair.adx_val is not None else None,
                    "long_leg": None,
                    "short_leg": None,
                }

                if pair.long_leg and pair.long_leg.status == "ACTIVE":
                    p_data["long_leg"] = {
                        "side": "Long",
                        "role": pair.long_leg.role,
                        "size": float(pair.long_leg.size),
                        "entry_price": float(pair.long_leg.entry_price),
                        "peak_price": float(pair.long_leg.extreme_price),
                        "trailing_sl": float(pair.long_leg.trailing_sl) if pair.long_leg.trailing_sl else None,
                        "tp_target": float(pair.long_leg.tp_target) if pair.long_leg.tp_target else None,
                        "unrealized_pnl": float(l_pnl),
                        "pnl_pct": float(l_pct),
                    }

                if pair.short_leg and pair.short_leg.status == "ACTIVE":
                    p_data["short_leg"] = {
                        "side": "Short",
                        "role": pair.short_leg.role,
                        "size": float(pair.short_leg.size),
                        "entry_price": float(pair.short_leg.entry_price),
                        "trough_price": float(pair.short_leg.extreme_price),
                        "trailing_sl": float(pair.short_leg.trailing_sl) if pair.short_leg.trailing_sl else None,
                        "tp_target": float(pair.short_leg.tp_target) if pair.short_leg.tp_target else None,
                        "unrealized_pnl": float(s_pnl),
                        "pnl_pct": float(s_pct),
                    }

                pairs_dict[sym] = p_data

            state = {
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": int((datetime.now() - self.session_start).total_seconds()),
                "total_cycles_completed": self.total_cycles_completed,
                "scan_count": self.scan_count,
                "network": "TESTNET" if self.config.testnet else "MAINNET",
                "leverage": self.config.leverage,
                "max_concurrent_pairs": self.config.max_concurrent_pairs,
                "active_pairs_count": sum(1 for p in self.pairs.values() if p.status == "ACTIVE"),
                "open_pnl": float(tot_open),
                "realized_pnl": float(cum_all),
                "pairs": pairs_dict,
            }

            tmp_file = "bot_state.json.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_file, "bot_state.json")
        except Exception as e:
            logger.debug(f"Failed to dump bot_state.json: {e}")

    def _check_pair_signal(self, pair: PairState, candles: List[Dict[str, Any]]) -> Optional[str]:
        """Check EMA crossover + ADX threshold on candles[-3] and candles[-2]."""
        if len(candles) < 3:
            return None

        prev = candles[-3]
        curr = candles[-2]

        pf = prev.get(f"ema_{pair.cfg.ema_fast}")
        ps = prev.get(f"ema_{pair.cfg.ema_slow}")
        cf = curr.get(f"ema_{pair.cfg.ema_fast}")
        cs = curr.get(f"ema_{pair.cfg.ema_slow}")
        adx = curr.get("adx")

        if any(v is None for v in [pf, ps, cf, cs]):
            return None

        if pair.cfg.adx_min > 0:
            if adx is None or adx <= pair.cfg.adx_min:
                return None

        crossed_up   = (pf <= ps) and (cf > cs)
        crossed_down = (pf >= ps) and (cf < cs)

        if crossed_up:
            return "bullish"
        if crossed_down:
            return "bearish"
        return None

    # ==========================================================================
    # TRADE ENTRY
    # ==========================================================================

    def _enter_pair_trade(self, pair: PairState, direction: str) -> None:
        """Execute simultaneous Long and Short market orders for this pair."""
        sym = pair.cfg.symbol
        console.print(f"\n[bold yellow]>>> [{sym}] EXECUTING DOUBLE ENTRY (LONG & SHORT HEDGE) <<<[/bold yellow]")

        sl_ratio = pair.cfg.sl_ratio
        tp_ratio = pair.cfg.tp_ratio

        if pair.cfg.asymmetric and direction in ("bullish", "bearish"):
            base = pair.cfg.size
            counter = pair.cfg.effective_counter_size
            long_size  = base if direction == "bullish" else counter
            short_size = counter if direction == "bullish" else base
            long_role  = "PRIMARY" if direction == "bullish" else "COUNTER"
            short_role = "COUNTER" if direction == "bullish" else "PRIMARY"
        else:
            long_size  = pair.cfg.size
            short_size = pair.cfg.size
            long_role  = "PRIMARY"
            short_role = "PRIMARY"

        # Long leg
        long_fill = self.service.place_market_open("Buy", long_size, position_idx=1, symbol=sym)
        time.sleep(0.3)
        pair.long_leg = PositionLeg.new_long(long_size, long_fill, sl_ratio, tp_ratio, symbol=sym, role=long_role)
        self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
        self._csv_event(pair, pair.long_leg, "ENTRY", long_fill)

        # Short leg
        short_fill = self.service.place_market_open("Sell", short_size, position_idx=2, symbol=sym)
        time.sleep(0.3)
        pair.short_leg = PositionLeg.new_short(short_size, short_fill, sl_ratio, tp_ratio, symbol=sym, role=short_role)
        self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
        self._csv_event(pair, pair.short_leg, "ENTRY", short_fill)

        pair.status = "ACTIVE"
        pair.phase = "INCUBATION" if pair.cfg.asymmetric else "ACTIVE"
        pair.signal_direction = direction
        pair.entry_price = (long_fill + short_fill) / Decimal("2")
        pair.entry_ts = time.time()
        pair.bars_elapsed = 0
        pair.b1_trailed = False
        pair.b2_trailed_to_be = False
        pair.b2_trailed_to_plus_1d = False
        pair.base_be_sl = Decimal("0")
        pair.cycle_count += 1
        pair.status_msg = f"ACTIVE ({pair.phase} #{pair.cycle_count})"

        logger.info(
            f"[{sym}] Both legs active: Long({long_role})={long_size} @ {long_fill:.2f} | "
            f"Short({short_role})={short_size} @ {short_fill:.2f} | Phase={pair.phase}"
        )
        self._dump_state_json()

    # ==========================================================================
    # DEAD-RANGE TIMEOUT LIQUIDATION (BRANCH 3)
    # ==========================================================================

    def _timeout_pair_cycle(self, pair: PairState) -> None:
        """Close both legs at market after timeout (50 bars in deadlock)."""
        sym = pair.cfg.symbol
        console.print(f"\n[bold magenta]>>> [{sym}] EXECUTING TIMEOUT LIQUIDATION (50 BARS CHOP) <<<[/bold magenta]")
        px = pair.latest_price or self.service.get_market_price(sym)
        if pair.long_leg and pair.long_leg.status == "ACTIVE":
            self.service.close_position(1, pair.long_leg.size, symbol=sym)
            pair.long_leg.status = "CLOSED_TIMEOUT"
            pair.long_leg.exit_price = px
            self._csv_event(pair, pair.long_leg, "TIMEOUT_EXIT", px)
        if pair.short_leg and pair.short_leg.status == "ACTIVE":
            self.service.close_position(2, pair.short_leg.size, symbol=sym)
            pair.short_leg.status = "CLOSED_TIMEOUT"
            pair.short_leg.exit_price = px
            self._csv_event(pair, pair.short_leg, "TIMEOUT_EXIT", px)
        self._close_pair_cycle(pair)

    # ==========================================================================
    # LIVE TICK HANDLER & TRAILING STOP
    # ==========================================================================

    def _process_pair_tick(self, pair: PairState, price: Decimal) -> None:
        """Route tick to Path B asymmetric engine or legacy symmetric trailing stop."""
        pair.latest_price = price
        if pair.cfg.asymmetric:
            self._process_path_b_tick(pair, price)
        else:
            self._process_legacy_tick(pair, price)

    def _process_path_b_tick(self, pair: PairState, price: Decimal) -> None:
        """Execute Path B Asymmetric Trap Hunter & Zero-Loss Pullback state machine."""
        sym = pair.cfg.symbol
        d_val = pair.cfg.d_ratio
        entry_px = pair.entry_price
        base_qty = pair.cfg.size
        c_qty = pair.cfg.effective_counter_size
        fee_rate = Decimal("0.00055")

        # -- PHASE 1: INCUBATION (Delta-Neutral Breakout Monitor) --------------
        if pair.phase == "INCUBATION":
            if entry_px <= Decimal("0"):
                return

            if pair.signal_direction == "bullish":
                # BRANCH 1: Signal Was Right (Market expands +1.0D upward)
                if price >= entry_px * (Decimal("1") + d_val):
                    confirm_px = price
                    # 1. Collapse 30% Counter Short Leg
                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        self.service.close_position(2, pair.short_leg.size, symbol=sym)
                        pair.short_leg.status = "CLOSED_COLLAPSE"
                        pair.short_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.short_leg, "B1_COLLAPSE_COUNTER", confirm_px)

                    # 2. Arm 100% Primary Long Runner with Zero-Loss SL
                    s_loss = (entry_px - confirm_px) * c_qty
                    s_fees = (entry_px + confirm_px) * c_qty * fee_rate
                    needed_be = abs(s_loss) + s_fees + (entry_px * base_qty * fee_rate * Decimal("2"))
                    long_sl_be = entry_px + (needed_be / base_qty)
                    long_tp = confirm_px * (Decimal("1") + Decimal("1.00") * d_val)

                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        pair.long_leg.trailing_sl = long_sl_be
                        pair.long_leg.tp_target = long_tp
                        pair.base_be_sl = long_sl_be
                        pair.b1_trailed = False
                        self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.long_leg, "B1_ARMED", confirm_px)

                    pair.phase = "RUNNER_B1"
                    pair.status_msg = f"RUNNER B1 (Long @ {price:.2f}, SL: {long_sl_be:.2f}, TP: {long_tp:.2f})"
                    console.print(
                        f"\n[bold green]>>> [{sym}] BRANCH 1 HIT (+1.0D)! Counter Short collapsed. "
                        f"Long Armed (SL: {long_sl_be:.2f}, TP: {long_tp:.2f}) <<<[/bold green]"
                    )
                    self._dump_state_json()
                    return

                # BRANCH 2: Signal Was Wrong / Trap (Market dumps -1.0D downward)
                elif price <= entry_px * (Decimal("1") - d_val):
                    confirm_px = price
                    # 1. Collapse Trapped 100% Primary Long Leg
                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        self.service.close_position(1, pair.long_leg.size, symbol=sym)
                        pair.long_leg.status = "CLOSED_COLLAPSE"
                        pair.long_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.long_leg, "B2_COLLAPSE_PRIMARY", confirm_px)

                    # 2. Size-Flip: Add +70% to Counter Short Leg to make 100% Runner
                    add_qty = base_qty - (pair.short_leg.size if pair.short_leg else c_qty)
                    add_fill = self.service.place_market_open("Sell", add_qty, position_idx=2, symbol=sym)
                    if pair.short_leg:
                        pair.short_leg.upsize(add_qty, add_fill)
                        pair.short_leg.role = "PRIMARY"

                    # 3. Compute B2 Levels
                    trapped_loss = - (entry_px - confirm_px) * base_qty
                    trapped_fees = (entry_px + confirm_px) * base_qty * fee_rate
                    upsize_fees = confirm_px * add_qty * fee_rate
                    blend_px = pair.short_leg.entry_price if pair.short_leg else confirm_px
                    total_drain = abs(trapped_loss) + trapped_fees + upsize_fees + (base_qty * blend_px * fee_rate * Decimal("2"))
                    true_be = blend_px - (total_drain / base_qty)
                    tp_level = confirm_px * (Decimal("1") - Decimal("2.50") * d_val)
                    curr_sl = entry_px  # Initial SL placed at initial entry P0

                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        pair.short_leg.trailing_sl = curr_sl
                        pair.short_leg.tp_target = tp_level
                        pair.base_be_sl = true_be
                        pair.b2_trailed_to_be = False
                        pair.b2_trailed_to_plus_1d = False
                        self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.short_leg, "B2_SIZE_FLIP", confirm_px)

                    pair.phase = "RUNNER_B2"
                    pair.status_msg = f"RUNNER B2 (Short Size-Flip @ {price:.2f}, SL: {curr_sl:.2f}, TP: {tp_level:.2f})"
                    console.print(
                        f"\n[bold yellow]>>> [{sym}] BRANCH 2 HIT (-1.0D)! Long collapsed. "
                        f"Short Size-Flipped to 100% Runner (SL: {curr_sl:.2f}, TP: {tp_level:.2f}) <<<[/bold yellow]"
                    )
                    self._dump_state_json()
                    return

            elif pair.signal_direction == "bearish":
                # BRANCH 1: Signal Was Right (Market expands -1.0D downward)
                if price <= entry_px * (Decimal("1") - d_val):
                    confirm_px = price
                    # 1. Collapse 30% Counter Long Leg
                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        self.service.close_position(1, pair.long_leg.size, symbol=sym)
                        pair.long_leg.status = "CLOSED_COLLAPSE"
                        pair.long_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.long_leg, "B1_COLLAPSE_COUNTER", confirm_px)

                    # 2. Arm 100% Primary Short Runner with Zero-Loss SL
                    l_loss = (confirm_px - entry_px) * c_qty
                    l_fees = (entry_px + confirm_px) * c_qty * fee_rate
                    needed_be = abs(l_loss) + l_fees + (entry_px * base_qty * fee_rate * Decimal("2"))
                    short_sl_be = entry_px - (needed_be / base_qty)
                    short_tp = confirm_px * (Decimal("1") - Decimal("1.00") * d_val)

                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        pair.short_leg.trailing_sl = short_sl_be
                        pair.short_leg.tp_target = short_tp
                        pair.base_be_sl = short_sl_be
                        pair.b1_trailed = False
                        self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.short_leg, "B1_ARMED", confirm_px)

                    pair.phase = "RUNNER_B1"
                    pair.status_msg = f"RUNNER B1 (Short @ {price:.2f}, SL: {short_sl_be:.2f}, TP: {short_tp:.2f})"
                    console.print(
                        f"\n[bold green]>>> [{sym}] BRANCH 1 HIT (-1.0D)! Counter Long collapsed. "
                        f"Short Armed (SL: {short_sl_be:.2f}, TP: {short_tp:.2f}) <<<[/bold green]"
                    )
                    self._dump_state_json()
                    return

                # BRANCH 2: Signal Was Wrong / Trap (Market pumps +1.0D upward)
                elif price >= entry_px * (Decimal("1") + d_val):
                    confirm_px = price
                    # 1. Collapse Trapped 100% Primary Short Leg
                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        self.service.close_position(2, pair.short_leg.size, symbol=sym)
                        pair.short_leg.status = "CLOSED_COLLAPSE"
                        pair.short_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.short_leg, "B2_COLLAPSE_PRIMARY", confirm_px)

                    # 2. Size-Flip: Add +70% to Counter Long Leg to make 100% Runner
                    add_qty = base_qty - (pair.long_leg.size if pair.long_leg else c_qty)
                    add_fill = self.service.place_market_open("Buy", add_qty, position_idx=1, symbol=sym)
                    if pair.long_leg:
                        pair.long_leg.upsize(add_qty, add_fill)
                        pair.long_leg.role = "PRIMARY"

                    # 3. Compute B2 Levels
                    trapped_loss = - (confirm_px - entry_px) * base_qty
                    trapped_fees = (entry_px + confirm_px) * base_qty * fee_rate
                    upsize_fees = confirm_px * add_qty * fee_rate
                    blend_px = pair.long_leg.entry_price if pair.long_leg else confirm_px
                    total_drain = abs(trapped_loss) + trapped_fees + upsize_fees + (base_qty * blend_px * fee_rate * Decimal("2"))
                    true_be = blend_px + (total_drain / base_qty)
                    tp_level = confirm_px * (Decimal("1") + Decimal("2.50") * d_val)
                    curr_sl = entry_px  # Initial SL placed at initial entry P0

                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        pair.long_leg.trailing_sl = curr_sl
                        pair.long_leg.tp_target = tp_level
                        pair.base_be_sl = true_be
                        pair.b2_trailed_to_be = False
                        pair.b2_trailed_to_plus_1d = False
                        self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.long_leg, "B2_SIZE_FLIP", confirm_px)

                    pair.phase = "RUNNER_B2"
                    pair.status_msg = f"RUNNER B2 (Long Size-Flip @ {price:.2f}, SL: {curr_sl:.2f}, TP: {tp_level:.2f})"
                    console.print(
                        f"\n[bold yellow]>>> [{sym}] BRANCH 2 HIT (+1.0D)! Short collapsed. "
                        f"Long Size-Flipped to 100% Runner (SL: {curr_sl:.2f}, TP: {tp_level:.2f}) <<<[/bold yellow]"
                    )
                    self._dump_state_json()
                    return

        # -- PHASE 2: RUNNER B1 (Trend Expansion & Zero-Loss Pullback) ---------
        elif pair.phase == "RUNNER_B1":
            if pair.signal_direction == "bullish" and pair.long_leg and pair.long_leg.status == "ACTIVE":
                # Ratchet Milestone: +1.40D reached -> Ratchet SL to BE + 1.0D
                trail_trig = entry_px * (Decimal("1") + Decimal("1.40") * d_val)
                if price >= trail_trig and not pair.b1_trailed:
                    new_sl = pair.base_be_sl + (entry_px * d_val)
                    pair.long_leg.trailing_sl = new_sl
                    pair.b1_trailed = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B1 RATCHET] SL raised to {new_sl:.2f} (+1.0D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B1_RATCHET", price)

                # TP Hit
                if price >= pair.long_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} LONG B1 TP HIT] @ {price:.2f} <<<[/bold green]")
                    self.service.close_position(1, pair.long_leg.size, symbol=sym)
                    pair.long_leg.status = "CLOSED_TP"
                    pair.long_leg.exit_price = price
                    self._csv_event(pair, pair.long_leg, "TP_HIT", price)
                    self._close_pair_cycle(pair)
                    return

                # SL Hit (Zero-loss pullback or lock)
                elif price <= pair.long_leg.trailing_sl:
                    console.print(f"\n[bold yellow]>>> [{sym} LONG B1 SL HIT] @ {price:.2f} (Zero-Loss / Lock Preserved) <<<[/bold yellow]")
                    self.service.close_position(1, pair.long_leg.size, symbol=sym)
                    pair.long_leg.status = "CLOSED_SL"
                    pair.long_leg.exit_price = price
                    self._csv_event(pair, pair.long_leg, "SL_HIT", price)
                    self._close_pair_cycle(pair)
                    return

            elif pair.signal_direction == "bearish" and pair.short_leg and pair.short_leg.status == "ACTIVE":
                # Ratchet Milestone: -1.40D reached -> Ratchet SL to BE - 1.0D
                trail_trig = entry_px * (Decimal("1") - Decimal("1.40") * d_val)
                if price <= trail_trig and not pair.b1_trailed:
                    new_sl = pair.base_be_sl - (entry_px * d_val)
                    pair.short_leg.trailing_sl = new_sl
                    pair.b1_trailed = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B1 RATCHET] SL lowered to {new_sl:.2f} (+1.0D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B1_RATCHET", price)

                # TP Hit
                if price <= pair.short_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} SHORT B1 TP HIT] @ {price:.2f} <<<[/bold green]")
                    self.service.close_position(2, pair.short_leg.size, symbol=sym)
                    pair.short_leg.status = "CLOSED_TP"
                    pair.short_leg.exit_price = price
                    self._csv_event(pair, pair.short_leg, "TP_HIT", price)
                    self._close_pair_cycle(pair)
                    return

                # SL Hit (Zero-loss pullback or lock)
                elif price >= pair.short_leg.trailing_sl:
                    console.print(f"\n[bold yellow]>>> [{sym} SHORT B1 SL HIT] @ {price:.2f} (Zero-Loss / Lock Preserved) <<<[/bold yellow]")
                    self.service.close_position(2, pair.short_leg.size, symbol=sym)
                    pair.short_leg.status = "CLOSED_SL"
                    pair.short_leg.exit_price = price
                    self._csv_event(pair, pair.short_leg, "SL_HIT", price)
                    self._close_pair_cycle(pair)
                    return

        # -- PHASE 3: RUNNER B2 (Size-Flip Trap Hunter) ------------------------
        elif pair.phase == "RUNNER_B2":
            if pair.signal_direction == "bullish" and pair.short_leg and pair.short_leg.status == "ACTIVE":
                # Milestone 2: Reaches -3.0D -> Ratchet SL to +1.0D Profit Lock
                ratchet_trig = pair.base_be_sl - (pair.short_leg.entry_price * Decimal("0.40") * d_val)
                sl_ratchet = pair.base_be_sl - (pair.short_leg.entry_price * d_val)
                if price <= ratchet_trig and not pair.b2_trailed_to_plus_1d:
                    pair.short_leg.trailing_sl = sl_ratchet
                    pair.b2_trailed_to_plus_1d = True
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 PROFIT RATCHET] SL lowered to {sl_ratchet:.2f} (+1.0D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B2_RATCHET_PROFIT", price)

                # Milestone 1: Reaches -2.0D -> True Breakeven Lock (Zero Loss Secured)
                elif price <= pair.base_be_sl and not pair.b2_trailed_to_be:
                    pair.short_leg.trailing_sl = pair.base_be_sl
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 TRUE BE LOCK] SL set to {pair.base_be_sl:.2f} (Zero Loss Secured) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B2_RATCHET_BE", price)

                # TP Hit (-3.5D Target)
                if price <= pair.short_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 TP HIT] @ {price:.2f} <<<[/bold green]")
                    self.service.close_position(2, pair.short_leg.size, symbol=sym)
                    pair.short_leg.status = "CLOSED_TP"
                    pair.short_leg.exit_price = price
                    self._csv_event(pair, pair.short_leg, "TP_HIT", price)
                    self._close_pair_cycle(pair)
                    return

                # SL Hit (P0 or ratcheted)
                elif price >= pair.short_leg.trailing_sl:
                    console.print(f"\n[bold yellow]>>> [{sym} SHORT B2 SL HIT] @ {price:.2f} <<<[/bold yellow]")
                    self.service.close_position(2, pair.short_leg.size, symbol=sym)
                    pair.short_leg.status = "CLOSED_SL"
                    pair.short_leg.exit_price = price
                    self._csv_event(pair, pair.short_leg, "SL_HIT", price)
                    self._close_pair_cycle(pair)
                    return

            elif pair.signal_direction == "bearish" and pair.long_leg and pair.long_leg.status == "ACTIVE":
                # Milestone 2: Reaches +3.0D -> Ratchet SL to +1.0D Profit Lock
                ratchet_trig = pair.base_be_sl + (pair.long_leg.entry_price * Decimal("0.40") * d_val)
                sl_ratchet = pair.base_be_sl + (pair.long_leg.entry_price * d_val)
                if price >= ratchet_trig and not pair.b2_trailed_to_plus_1d:
                    pair.long_leg.trailing_sl = sl_ratchet
                    pair.b2_trailed_to_plus_1d = True
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 PROFIT RATCHET] SL raised to {sl_ratchet:.2f} (+1.0D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B2_RATCHET_PROFIT", price)

                # Milestone 1: Reaches +2.0D -> True Breakeven Lock (Zero Loss Secured)
                elif price >= pair.base_be_sl and not pair.b2_trailed_to_be:
                    pair.long_leg.trailing_sl = pair.base_be_sl
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 TRUE BE LOCK] SL set to {pair.base_be_sl:.2f} (Zero Loss Secured) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B2_RATCHET_BE", price)

                # TP Hit (+3.5D Target)
                if price >= pair.long_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 TP HIT] @ {price:.2f} <<<[/bold green]")
                    self.service.close_position(1, pair.long_leg.size, symbol=sym)
                    pair.long_leg.status = "CLOSED_TP"
                    pair.long_leg.exit_price = price
                    self._csv_event(pair, pair.long_leg, "TP_HIT", price)
                    self._close_pair_cycle(pair)
                    return

                # SL Hit (P0 or ratcheted)
                elif price <= pair.long_leg.trailing_sl:
                    console.print(f"\n[bold yellow]>>> [{sym} LONG B2 SL HIT] @ {price:.2f} <<<[/bold yellow]")
                    self.service.close_position(1, pair.long_leg.size, symbol=sym)
                    pair.long_leg.status = "CLOSED_SL"
                    pair.long_leg.exit_price = price
                    self._csv_event(pair, pair.long_leg, "SL_HIT", price)
                    self._close_pair_cycle(pair)
                    return

        # Check if both legs ended
        l_done = pair.long_leg is None or pair.long_leg.status != "ACTIVE"
        s_done = pair.short_leg is None or pair.short_leg.status != "ACTIVE"
        if l_done and s_done:
            self._close_pair_cycle(pair)

    def _process_legacy_tick(self, pair: PairState, price: Decimal) -> None:
        """Process trailing ratchet, TP, and SL triggers on live price tick (legacy symmetric)."""
        sym          = pair.cfg.symbol
        sl_ratio     = pair.cfg.sl_ratio
        ratchet_step = pair.cfg.ratchet_step_ratio

        # -- Long leg ----------------------------------------------------------
        if pair.long_leg and pair.long_leg.status == "ACTIVE":
            act = pair.long_leg.on_price_tick(price, sl_ratio, ratchet_step)

            if act == "RATCHET_TRIGGER":
                logger.info(f"[{sym}] LONG RATCHET -> new SL={pair.long_leg.trailing_sl:.2f}")
                self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                self._csv_event(pair, pair.long_leg, "RATCHET", price)

            elif act == "TRIGGER_TP":
                console.print(f"\n[bold green]>>> [{sym} LONG TP HIT] @ {price:.2f} <<<[/bold green]")
                self.service.close_position(1, pair.long_leg.size, symbol=sym)
                self._csv_event(pair, pair.long_leg, "TP_HIT", price)
                # Close counter Short leg immediately to bank net gains and prevent runaway trend loss
                if pair.short_leg and pair.short_leg.status == "ACTIVE":
                    logger.info(f"[{sym}] LONG hit TP -> Closed counter SHORT immediately at market to lock net gains.")
                    self.service.close_position(2, pair.short_leg.size, symbol=sym)

            elif act == "TRIGGER_SL":
                console.print(f"\n[bold red]>>> [{sym} LONG SL HIT] @ {price:.2f} <<<[/bold red]")
                self.service.close_position(1, pair.long_leg.size, symbol=sym)
                self._csv_event(pair, pair.long_leg, "SL_HIT", price)
                self._apply_be_lock_short(pair, price)

        # -- Short leg ---------------------------------------------------------
        if pair.short_leg and pair.short_leg.status == "ACTIVE":
            act = pair.short_leg.on_price_tick(price, sl_ratio, ratchet_step)

            if act == "RATCHET_TRIGGER":
                logger.info(f"[{sym}] SHORT RATCHET -> new SL={pair.short_leg.trailing_sl:.2f}")
                self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                self._csv_event(pair, pair.short_leg, "RATCHET", price)

            elif act == "TRIGGER_TP":
                console.print(f"\n[bold green]>>> [{sym} SHORT TP HIT] @ {price:.2f} <<<[/bold green]")
                self.service.close_position(2, pair.short_leg.size, symbol=sym)
                self._csv_event(pair, pair.short_leg, "TP_HIT", price)
                # Close counter Long leg immediately to bank net gains and prevent runaway trend loss
                if pair.long_leg and pair.long_leg.status == "ACTIVE":
                    logger.info(f"[{sym}] SHORT hit TP -> Closed counter LONG immediately at market to lock net gains.")
                    self.service.close_position(1, pair.long_leg.size, symbol=sym)

            elif act == "TRIGGER_SL":
                console.print(f"\n[bold red]>>> [{sym} SHORT SL HIT] @ {price:.2f} <<<[/bold red]")
                self.service.close_position(2, pair.short_leg.size, symbol=sym)
                self._csv_event(pair, pair.short_leg, "SL_HIT", price)
                self._apply_be_lock_long(pair, price)

        # Check if both legs are now closed
        l_done = pair.long_leg is None or pair.long_leg.status != "ACTIVE"
        s_done = pair.short_leg is None or pair.short_leg.status != "ACTIVE"
        if l_done and s_done:
            self._close_pair_cycle(pair)

    # ==========================================================================
    # BREAK-EVEN LOCK
    # ==========================================================================

    def _apply_be_lock_short(self, pair: PairState, price: Decimal) -> None:
        """Lock surviving Short leg to break-even buffer."""
        if not (pair.cfg.be_lock and pair.short_leg and pair.short_leg.status == "ACTIVE"):
            return
        be_sl = pair.short_leg.entry_price * (Decimal("1") - pair.cfg.be_buffer_ratio)
        # For Short, SL must be above current price and tighter than current trailing SL
        if be_sl < pair.short_leg.trailing_sl and be_sl > price:
            pair.short_leg.trailing_sl = be_sl
            logger.info(f"[{pair.cfg.symbol}] BE LOCK: Short SL locked to {be_sl:.2f}")
            self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=pair.cfg.symbol)

    def _apply_be_lock_long(self, pair: PairState, price: Decimal) -> None:
        """Lock surviving Long leg to break-even buffer."""
        if not (pair.cfg.be_lock and pair.long_leg and pair.long_leg.status == "ACTIVE"):
            return
        be_sl = pair.long_leg.entry_price * (Decimal("1") + pair.cfg.be_buffer_ratio)
        # For Long, SL must be below current price and tighter than current trailing SL
        if be_sl > pair.long_leg.trailing_sl and be_sl < price:
            pair.long_leg.trailing_sl = be_sl
            logger.info(f"[{pair.cfg.symbol}] BE LOCK: Long SL locked to {be_sl:.2f}")
            self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=pair.cfg.symbol)

    # ==========================================================================
    # CYCLE COMPLETION
    # ==========================================================================

    def _close_pair_cycle(self, pair: PairState) -> None:
        """Record completed cycle for a pair, update cumulative PnL, set cooldown."""
        final_px = pair.latest_price or Decimal("0")
        l_pnl = pair.long_leg.pnl(final_px)[0] if pair.long_leg else Decimal("0")
        s_pnl = pair.short_leg.pnl(final_px)[0] if pair.short_leg else Decimal("0")
        net = l_pnl + s_pnl

        pair.cumulative_pnl += net
        self.total_cycles_completed += 1

        sym = pair.cfg.symbol
        console.print(
            f"\n[bold cyan]>>> [{sym}] CYCLE #{pair.cycle_count} COMPLETE | "
            f"Net: ${net:+.2f} | Pair Cumulative: ${pair.cumulative_pnl:+.2f} <<<\n[/bold cyan]"
        )

        # Reset legs and state
        pair.long_leg = None
        pair.short_leg = None
        pair.signal_direction = None
        pair.entry_price = Decimal("0")
        pair.entry_ts = 0.0
        pair.bars_elapsed = 0
        pair.b1_trailed = False
        pair.b2_trailed_to_be = False
        pair.b2_trailed_to_plus_1d = False
        pair.base_be_sl = Decimal("0")

        if self.config.cooldown_secs > 0:
            pair.status = "COOLDOWN"
            pair.phase = "COOLDOWN"
            pair.cooldown_until = time.time() + self.config.cooldown_secs
            pair.status_msg = f"Cooldown ({self.config.cooldown_secs}s)"
        else:
            pair.status = "SCANNING"
            pair.phase = "SCANNING"
            pair.status_msg = "Scanning for next signal..."
        self._dump_state_json()

    # ==========================================================================
    # REST EXCHANGE RECONCILIATION
    # ==========================================================================

    def _reconcile_active_pairs_with_exchange(self) -> None:
        """Query open positions across Bybit linear perps to detect resting trigger executions."""
        try:
            positions = self.service.get_open_positions()
            # Map: {symbol: set of active positionIdx}
            active_by_sym: Dict[str, set] = {}
            for p in positions:
                sym = p.get("symbol", "")
                idx = int(p.get("positionIdx", 0))
                active_by_sym.setdefault(sym, set()).add(idx)

            for sym, pair in self.pairs.items():
                if pair.status != "ACTIVE":
                    continue

                active_indices = active_by_sym.get(sym, set())
                px = pair.latest_price or self.service.get_market_price(sym)

                if pair.long_leg and pair.long_leg.status == "ACTIVE" and 1 not in active_indices:
                    logger.info(f"[{sym}] Exchange confirms LONG closed.")
                    record = self.service.get_last_closed_pnl(sym)
                    if record:
                        exit_px = Decimal(str(record.get("avgExitPrice") or px))
                        realized = Decimal(str(record.get("closedPnl") or "0"))
                        pair.long_leg.exit_price = exit_px
                        pair.long_leg.realized_pnl = realized
                        pair.long_leg.status = "CLOSED_TP" if realized >= Decimal("0") else "CLOSED_SL"
                        logger.info(f"[{sym}] LONG verified: {pair.long_leg.status} @ {exit_px} | PnL: ${realized:+.2f}")
                    else:
                        if px >= pair.long_leg.tp_target * Decimal("0.999"):
                            pair.long_leg.status = "CLOSED_TP"
                            pair.long_leg.exit_price = pair.long_leg.tp_target
                        else:
                            pair.long_leg.status = "CLOSED_SL"
                            pair.long_leg.exit_price = pair.long_leg.trailing_sl

                    self._csv_event(pair, pair.long_leg, pair.long_leg.status, pair.long_leg.exit_price)

                    if pair.long_leg.status == "CLOSED_TP":
                        # Long took profit! Immediately close counter Short to secure net gain and avoid 6% trend loss
                        if pair.short_leg and pair.short_leg.status == "ACTIVE":
                            logger.info(f"[{sym}] LONG hit TP -> Closed counter SHORT immediately to secure net gains.")
                            self.service.close_position(2, pair.short_leg.size, symbol=sym)
                    else:
                        self._apply_be_lock_short(pair, px)

                if pair.short_leg and pair.short_leg.status == "ACTIVE" and 2 not in active_indices:
                    logger.info(f"[{sym}] Exchange confirms SHORT closed.")
                    record = self.service.get_last_closed_pnl(sym)
                    if record:
                        exit_px = Decimal(str(record.get("avgExitPrice") or px))
                        realized = Decimal(str(record.get("closedPnl") or "0"))
                        pair.short_leg.exit_price = exit_px
                        pair.short_leg.realized_pnl = realized
                        pair.short_leg.status = "CLOSED_TP" if realized >= Decimal("0") else "CLOSED_SL"
                        logger.info(f"[{sym}] SHORT verified: {pair.short_leg.status} @ {exit_px} | PnL: ${realized:+.2f}")
                    else:
                        if px <= pair.short_leg.tp_target * Decimal("1.001"):
                            pair.short_leg.status = "CLOSED_TP"
                            pair.short_leg.exit_price = pair.short_leg.tp_target
                        else:
                            pair.short_leg.status = "CLOSED_SL"
                            pair.short_leg.exit_price = pair.short_leg.trailing_sl

                    self._csv_event(pair, pair.short_leg, pair.short_leg.status, pair.short_leg.exit_price)

                    if pair.short_leg.status == "CLOSED_TP":
                        # Short took profit! Immediately close counter Long to secure net gain and avoid 6% trend loss
                        if pair.long_leg and pair.long_leg.status == "ACTIVE":
                            logger.info(f"[{sym}] SHORT hit TP -> Closed counter LONG immediately to secure net gains.")
                            self.service.close_position(1, pair.long_leg.size, symbol=sym)
                    else:
                        self._apply_be_lock_long(pair, px)

                # Check if cycle ended
                l_done = pair.long_leg is None or pair.long_leg.status != "ACTIVE"
                s_done = pair.short_leg is None or pair.short_leg.status != "ACTIVE"
                if l_done and s_done:
                    self._close_pair_cycle(pair)

        except Exception as e:
            logger.debug(f"REST reconciliation skipped: {e}")

    # ==========================================================================


    def _print_session_summary(self) -> None:
        """Print final multi-pair session summary table on exit."""
        table = Table(title="Multi-Pair Session Summary", border_style="cyan")
        table.add_column("Asset", style="bold")
        table.add_column("Cycles Completed", justify="right")
        table.add_column("Cumulative Net PnL", justify="right")

        tot_pnl = Decimal("0")
        for sym, pair in self.pairs.items():
            col = "green" if pair.cumulative_pnl >= 0 else "red"
            table.add_row(sym, str(pair.cycle_count), f"[{col}]${pair.cumulative_pnl:+.2f}[/{col}]")
            tot_pnl += pair.cumulative_pnl

        col_tot = "bold green" if tot_pnl >= 0 else "bold red"
        table.add_row("TOTAL PORTFOLIO", str(self.total_cycles_completed), f"[{col_tot}]${tot_pnl:+.2f}[/{col_tot}]")

        console.print("\n")
        console.print(table)
        elapsed = (datetime.now() - self.session_start).total_seconds()
        console.print(f"[bold cyan]Session duration: {elapsed/3600:.2f} hours | Trade ledger: {self.config.log_csv}[/bold cyan]\n")

    # ==========================================================================
    # CSV LEDGER
    # ==========================================================================

    def _init_csv(self) -> None:
        if not os.path.exists(self.config.log_csv):
            with open(self.config.log_csv, mode="w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "timestamp", "symbol", "cycle", "leg", "event",
                    "price", "extreme_price", "trailing_sl", "tp_target",
                    "leg_pnl_usd", "pair_cumulative_pnl",
                ])

    def _csv_event(self, pair: PairState, leg: PositionLeg, event: str, price: Decimal) -> None:
        pnl_usd = leg.pnl(price)[0]
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.config.log_csv, mode="a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                ts,
                pair.cfg.symbol,
                pair.cycle_count,
                "LONG" if leg.position_idx == 1 else "SHORT",
                event,
                f"{price:.2f}",
                f"{leg.extreme_price:.2f}",
                f"{leg.trailing_sl:.2f}",
                f"{leg.tp_target:.2f}",
                f"{pnl_usd:+.2f}",
                f"{pair.cumulative_pnl:+.2f}",
            ])
