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
import threading
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
    last_tick_time: float = 0.0
    cycle_count: int = 0
    cumulative_pnl: Decimal = Decimal("0")
    last_closed_ts: int = 0
    cooldown_until: float = 0.0
    status_msg: str = "Initializing..."
    fast_ema: Optional[Decimal] = None
    slow_ema: Optional[Decimal] = None
    macro_ema: Optional[Decimal] = None
    adx_val: Optional[Decimal] = None

    # Path B: Asymmetric Size-Flip Trap Hunter state
    signal_direction: Optional[str] = None  # "bullish" or "bearish"
    entry_price: Decimal = Decimal("0")
    entry_ts: float = 0.0
    bars_elapsed: int = 0
    b1_trailed: bool = False
    b1_trailed_stage2: bool = False
    b2_trailed_to_be: bool = False
    b2_trailed_to_plus_1d: bool = False
    base_be_sl: Decimal = Decimal("0")
    dynamic_d: Optional[Decimal] = None

    # Extension Guard (Exhaustion & Pullback Entry)
    pending_pullback: bool = False
    pending_direction: Optional[str] = None
    pullback_target_px: Optional[Decimal] = None
    pullback_expiry_ts: float = 0.0
    pending_candles: Optional[List[Dict[str, Any]]] = None

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
        self._lock = threading.Lock()

        # Persistent session start across process restarts & service reloads
        self.session_start = datetime.now()
        state_file = os.path.join(os.path.dirname(__file__), "..", "bot_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    old_state = json.load(f)
                    old_start_str = old_state.get("session_start_iso")
                    if old_start_str:
                        old_dt = datetime.fromisoformat(old_start_str)
                        # Retain start time if within reasonable range (e.g. within last 30 days)
                        if 0 <= (datetime.now() - old_dt).total_seconds() < 30 * 86400:
                            self.session_start = old_dt
            except Exception:
                pass

        self.total_cycles_completed = 0
        self.scan_count = 0
        self.ws: Optional[WebSocket] = None
        self.last_ws_msg_time: float = 0.0
        self.last_ws_reconnect_time: float = 0.0
        self.wallet_summary: Dict[str, Any] = {}
        self.exchange_pnl_summary: Dict[str, Any] = {}
        self.last_account_fetch_ts: float = 0.0

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
        try:
            if self.ws:
                try:
                    self.ws.exit()
                except Exception:
                    pass
            self.ws = WebSocket(testnet=self.config.testnet, channel_type="linear")
            for sym in self.pairs.keys():
                self.ws.ticker_stream(symbol=sym, callback=self._on_ticker_message)
            self.last_ws_msg_time = time.time()
            logger.info(f"Unified WebSocket streaming {len(self.pairs)} symbols...")
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")

    def _check_websocket_health(self) -> None:
        """Watchdog: if WebSocket has been silent for > 30s or disconnected, cleanly reconnect in background."""
        if not self.running or getattr(self, "_ws_reconnecting", False):
            return
        now = time.time()
        if now - self.last_ws_reconnect_time < 30.0:
            return

        is_conn = False
        try:
            is_conn = self.ws.is_connected() if self.ws else False
        except Exception:
            is_conn = False

        silent = (now - self.last_ws_msg_time > 30.0) if self.last_ws_msg_time > 0 else (now - self.session_start.timestamp() > 30.0)

        if not is_conn or silent:
            logger.warning(
                f"[WS WATCHDOG] WebSocket silent/disconnected (connected={is_conn}, "
                f"last_msg={int(now - self.last_ws_msg_time)}s ago). Reconnecting in background..."
            )
            self.last_ws_reconnect_time = now
            self._ws_reconnecting = True

            def _async_reconnect():
                try:
                    self._start_websocket()
                finally:
                    self._ws_reconnecting = False

            t = threading.Thread(target=_async_reconnect, daemon=True, name="WS-Reconnect")
            t.start()

    def _get_fresh_price(self, pair: PairState) -> Decimal:
        """Get live market price: use latest WS tick if < 3s old, else fetch fresh from REST."""
        now = time.time()
        sym = pair.cfg.symbol
        ws_is_fresh = (pair.latest_price is not None) and (now - pair.last_tick_time <= 3.0)
        if ws_is_fresh:
            return pair.latest_price

        # Fallback to REST API ticker
        try:
            px = self.service.get_market_price(sym)
            pair.latest_price = px
            pair.last_tick_time = now
            return px
        except Exception as e:
            logger.warning(f"[{sym}] REST price fetch failed: {e}")
            return pair.latest_price or Decimal("0")

    def _on_ticker_message(self, msg: dict) -> None:
        """Handle live price ticks across any subscribed symbol."""
        if not self.running:
            return
        now = time.time()
        self.last_ws_msg_time = now
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
        pair.last_tick_time = now

        # Check pending pullback if waiting
        if pair.pending_pullback:
            with self._lock:
                if pair.pending_pullback:
                    self._check_pullback_trigger(pair, price)

        # Process trailing stop & TP if pair is active (thread-safe)
        if pair.status == "ACTIVE":
            with self._lock:
                if pair.status == "ACTIVE":
                    self._process_pair_tick(pair, price)

    def _compute_atr(self, sym: str, candles: Optional[List[Dict[str, Any]]] = None) -> Decimal:
        """Retrieve recent ATR(14) for symbol from candles if available, else REST."""
        if candles and len(candles) >= 2:
            atr = candles[-2].get("atr")
            if atr and atr > Decimal("0"):
                return Decimal(str(atr))
        try:
            pair = self.pairs.get(sym)
            interval = pair.cfg.candle_interval if pair else "60"
            k = self.service.get_recent_candles(symbol=sym, interval=interval, limit=35)
            if len(k) >= 15:
                compute_indicators(k, fast_periods=[9, 21], adx_period=14)
                atr = k[-2].get("atr")
                if atr and atr > Decimal("0"):
                    return Decimal(str(atr))
        except Exception as e:
            logger.debug(f"[{sym}] Error computing ATR: {e}")
        return Decimal("0")

    def _check_pullback_trigger(self, pair: PairState, price: Decimal) -> bool:
        """Check if pending pullback entry price has been touched."""
        if not pair.pending_pullback:
            return False
        now = time.time()
        sym = pair.cfg.symbol
        if now >= pair.pullback_expiry_ts:
            logger.info(f"[{sym}] Pullback entry window expired without fill.")
            pair.pending_pullback = False
            pair.pending_direction = None
            pair.pullback_target_px = None
            pair.status = "SCANNING"
            pair.status_msg = "Pullback expired -> Scanning"
            return False

        p_dir = pair.pending_direction
        target_px = pair.pullback_target_px or Decimal("0")
        filled = False
        if p_dir == "bullish" and price <= target_px:
            filled = True
        elif p_dir == "bearish" and price >= target_px:
            filled = True

        if filled:
            console.print(
                f"\n[bold green]>>> [{sym}] PULLBACK ENTRY FILLED: {p_dir.upper()} @ {price:.2f} "
                f"(Target was {target_px:.2f})! Entering trade... <<<[/bold green]"
            )
            saved_dir = p_dir
            saved_candles = pair.pending_candles
            pair.pending_pullback = False
            pair.pending_direction = None
            pair.pullback_target_px = None
            pair.pending_candles = None
            self._enter_pair_trade(pair, saved_dir, candles=saved_candles)
            return True
        return False

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

            # Reconstruct Path B state for reconnected active pairs
            for sym, pair in self.pairs.items():
                if pair.status == "ACTIVE":
                    l_act = pair.long_leg and pair.long_leg.status == "ACTIVE"
                    s_act = pair.short_leg and pair.short_leg.status == "ACTIVE"
                    if l_act and s_act:
                        pair.phase = "INCUBATION" if pair.cfg.asymmetric else "ACTIVE"
                        pair.entry_price = (pair.long_leg.entry_price + pair.short_leg.entry_price) / Decimal("2")
                        pair.entry_ts = time.time()
                        if pair.long_leg.size > pair.short_leg.size:
                            pair.signal_direction = "bullish"
                            pair.long_leg.role = "PRIMARY"
                            pair.short_leg.role = "COUNTER"
                        elif pair.short_leg.size > pair.long_leg.size:
                            pair.signal_direction = "bearish"
                            pair.short_leg.role = "PRIMARY"
                            pair.long_leg.role = "COUNTER"
                        else:
                            pair.signal_direction = "bullish"
                            pair.long_leg.role = "PRIMARY"
                            pair.short_leg.role = "PRIMARY"
                        logger.info(
                            f"[{sym}] Path B Reconciled: Phase={pair.phase} | Entry={pair.entry_price:.2f} | "
                            f"Dir={pair.signal_direction} | Long={pair.long_leg.size} ({pair.long_leg.role}) | "
                            f"Short={pair.short_leg.size} ({pair.short_leg.role})"
                        )
                    elif l_act and not s_act:
                        pair.signal_direction = "bullish"
                        pair.entry_price = pair.long_leg.entry_price
                        pair.long_leg.role = "PRIMARY"
                        # Only enter RUNNER_B1 if stop-loss is already moved to/above entry (BE locked)
                        if pair.long_leg.trailing_sl and pair.long_leg.trailing_sl >= pair.entry_price:
                            pair.phase = "RUNNER_B1"
                        else:
                            pair.phase = "INCUBATION"
                        logger.info(
                            f"[{sym}] Single Long Reconciled: Phase={pair.phase} | Entry={pair.entry_price} | SL={pair.long_leg.trailing_sl}"
                        )
                    elif s_act and not l_act:
                        pair.signal_direction = "bearish"
                        pair.entry_price = pair.short_leg.entry_price
                        pair.short_leg.role = "PRIMARY"
                        # Only enter RUNNER_B1 if stop-loss is already moved to/below entry
                        if pair.short_leg.trailing_sl and pair.short_leg.trailing_sl <= pair.entry_price:
                            pair.phase = "RUNNER_B1"
                        else:
                            pair.phase = "INCUBATION"
                        logger.info(
                            f"[{sym}] Single Short Reconciled: Phase={pair.phase} | Entry={pair.entry_price} | SL={pair.short_leg.trailing_sl}"
                        )

                    # Dynamic ATR D calibration upon reconnection
                    pair.dynamic_d = pair.cfg.d_ratio
                    if pair.cfg.use_dynamic_atr and pair.entry_price > Decimal("0"):
                        try:
                            atr_val = self._compute_atr(sym)
                            if atr_val > Decimal("0"):
                                pair.dynamic_d = (pair.cfg.atr_mult * atr_val) / pair.entry_price
                                logger.info(f"[{sym}] Reconciled Dynamic ATR D: {pair.dynamic_d:.5f} (ATR={atr_val:.4f})")
                        except Exception as e:
                            logger.debug(f"[{sym}] ATR calculation error during reconciliation: {e}")

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
            try:
                now = time.time()

                # -- 0. Monitor WebSocket connection health -----------------------
                self._check_websocket_health()

                # -- 1. Check max cycles limit (only stop in dry-run simulation mode)
                if self.config.dry_run and self.config.max_cycles > 0 and self.total_cycles_completed >= self.config.max_cycles:
                    console.print(f"\n[bold cyan]Max cycles ({self.config.max_cycles}) completed in dry-run mode. Stopping.[/bold cyan]")
                    break

                # -- 2. Scan for entry signals across idle pairs ------------------
                if now - last_scan_time >= self.config.poll_interval:
                    last_scan_time = now
                    with self._lock:
                        self._scan_all_pairs()

                # -- 3. Periodic REST reconciliation (every 5 seconds) -----------
                if not self.config.dry_run and now - last_reconcile_time >= 5.0:
                    last_reconcile_time = now
                    self._reconcile_active_pairs_with_exchange()

            except Exception as e:
                logger.error(f"Unexpected exception in trading engine main loop: {e}", exc_info=True)

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

            # Handle pending pullback from Bar Size / Extension Guard
            if pair.pending_pullback:
                px = self._get_fresh_price(pair)
                if self._check_pullback_trigger(pair, px):
                    active_count += 1
                    report_lines.append(f"  * [bold green]{sym:<8}[/bold green]: PULLBACK FILLED -> ENTERED {pair.signal_direction.upper()}!")
                    continue
                elif pair.pending_pullback:
                    rem = max(0, int(pair.pullback_expiry_ts - now))
                    p_dir = pair.pending_direction or "TRADE"
                    target_px = pair.pullback_target_px or Decimal("0")
                    pair.status_msg = f"WAITING PULLBACK ({p_dir.upper()} @ {target_px:.2f}, {rem}s rem)"
                    report_lines.append(
                        f"  * [bold yellow]{sym:<8}[/bold yellow]: WAITING PULLBACK ({p_dir.upper()} @ {px:.2f} -> {target_px:.2f}, {rem}s rem)"
                    )
                    continue

            # Active pairs display current legs & PnL
            if pair.status == "ACTIVE":
                px = self._get_fresh_price(pair)

                # CRITICAL: Always process trailing stop / branch triggers on latest price!
                if px > Decimal("0"):
                    self._process_pair_tick(pair, px)

                # If pair cycle closed during this tick, do not continue processing as active
                if pair.status != "ACTIVE":
                    continue

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

                # Update candles and indicators for active pairs so dashboard remains live
                try:
                    active_candles = self.service.get_recent_candles(
                        symbol=sym, interval=pair.cfg.candle_interval, limit=350
                    )
                    if len(active_candles) >= 3:
                        macro_p = getattr(pair.cfg, "macro_ema_period", 200)
                        compute_indicators(
                            active_candles,
                            fast_periods=[pair.cfg.ema_fast, pair.cfg.ema_slow, macro_p],
                            adx_period=pair.cfg.adx_period,
                        )
                        c_candle = active_candles[-2]
                        fast_v = c_candle.get(f"ema_{pair.cfg.ema_fast}")
                        slow_v = c_candle.get(f"ema_{pair.cfg.ema_slow}")
                        macro_v = c_candle.get(f"ema_{macro_p}")
                        adx_v  = c_candle.get("adx")
                        if fast_v is not None:
                            pair.fast_ema = Decimal(str(fast_v))
                        if slow_v is not None:
                            pair.slow_ema = Decimal(str(slow_v))
                        if macro_v is not None:
                            pair.macro_ema = Decimal(str(macro_v))
                        if adx_v is not None:
                            pair.adx_val = Decimal(str(adx_v))
                except Exception as e:
                    logger.debug(f"[{sym}] Active candle update skipped: {e}")

                continue

            # Check concurrency limit
            if active_count >= self.config.max_concurrent_pairs:
                report_lines.append(f"  * [bold]{sym:<8}[/bold]: WAITING (Max {self.config.max_concurrent_pairs} pairs active)")
                continue

            # Poll recent candles for pair
            try:
                candles = self.service.get_recent_candles(
                    symbol=sym, interval=pair.cfg.candle_interval, limit=350
                )
                if len(candles) < 3:
                    if sym == "AVAXUSDT" and self.config.testnet:
                        report_lines.append(f"  * [bold]{sym:<8}[/bold]: OFFLINE (Testnet Contract Closed by Bybit)")
                    else:
                        report_lines.append(f"  * [bold]{sym:<8}[/bold]: Fetching klines ({len(candles)}/350)...")
                    continue

                # Compute EMA (including Macro 200) & ADX on candles
                macro_p = getattr(pair.cfg, "macro_ema_period", 200)
                compute_indicators(
                    candles,
                    fast_periods=[pair.cfg.ema_fast, pair.cfg.ema_slow, macro_p],
                    adx_period=pair.cfg.adx_period,
                )

                closed_candle = candles[-2]
                closed_ts = closed_candle["timestamp"]
                # Use fresh price: WS if fresh, else last candle close or REST
                px = self._get_fresh_price(pair)
                if px <= Decimal("0"):
                    px = candles[-1]["close"]
                    pair.latest_price = px

                fast_v = closed_candle.get(f"ema_{pair.cfg.ema_fast}")
                slow_v = closed_candle.get(f"ema_{pair.cfg.ema_slow}")
                macro_v = closed_candle.get(f"ema_{macro_p}")
                adx_v  = closed_candle.get("adx")

                pair.fast_ema = Decimal(str(fast_v)) if fast_v is not None else None
                pair.slow_ema = Decimal(str(slow_v)) if slow_v is not None else None
                pair.macro_ema = Decimal(str(macro_v)) if macro_v is not None else None
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
                        # Bar Size / Extension Guard: Check if signal candle range > 2.5 * ATR
                        candle_range = closed_candle["high"] - closed_candle["low"]
                        atr = closed_candle.get("atr")
                        guard_mult = getattr(pair.cfg, "extension_guard_mult", Decimal("2.50"))
                        is_exhaustion = False
                        if atr and atr > Decimal("0") and candle_range > (guard_mult * atr):
                            is_exhaustion = True

                        if is_exhaustion:
                            # Require a small pullback (38% of ATR) before entering rather than chasing the close
                            pullback_ratio = getattr(pair.cfg, "pullback_ratio", Decimal("0.38"))
                            pullback_offset = pullback_ratio * atr
                            pullback_px = (px - pullback_offset) if direction == "bullish" else (px + pullback_offset)
                            pair.pending_pullback = True
                            pair.pending_direction = direction
                            pair.pullback_target_px = pullback_px
                            pair.pullback_expiry_ts = now + (int(pair.cfg.candle_interval) * 60)
                            pair.pending_candles = candles
                            pair.status_msg = f"WAITING PULLBACK ({direction.upper()} target: ${pullback_px:.2f})"
                            console.print(
                                f"\n[bold yellow]>>> [{sym}] BAR SIZE / EXTENSION GUARD TRIGGERED! "
                                f"Signal candle range ({candle_range:.2f}) > {guard_mult}x ATR ({atr:.2f}). "
                                f"Holding market entry; waiting for pullback to {pullback_px:.2f} (Expiry: 60m). <<<[/bold yellow]"
                            )
                            report_lines.append(f"  * [bold yellow]{sym:<8}[/bold yellow]: EXHAUSTION GUARD -> WAITING PULLBACK to {pullback_px:.2f}")
                            continue

                        console.print(
                            f"\n[bold green]>>> [{sym}] SIGNAL CONFIRMED: {direction.upper()} on "
                            f"{closed_candle['datetime'].strftime('%H:%M')} candle! <<<[/bold green]"
                        )
                        self._enter_pair_trade(pair, direction, candles=candles)
                        active_count += 1
                        report_lines.append(f"  * [bold green]{sym:<8}[/bold green]: TRIGGERED {direction.upper()} ENTRY!")
                        continue

                # Trend and ADX status
                trend = "Bullish (EMA9 > EMA21)" if (fast_v and slow_v and fast_v > slow_v) else "Bearish (EMA9 < EMA21)"
                macro_status = f"200EMA: {float(macro_v):.1f}" if macro_v is not None else "200EMA: N/A"
                if macro_v is not None:
                    macro_align = "Bullish" if px >= Decimal(str(macro_v)) else "Bearish"
                    macro_status += f" ({macro_align})"
                adx_s = f"{float(adx_v):.1f}" if adx_v else "N/A"
                f_s = f"{float(fast_v):.2f}" if fast_v else "N/A"
                s_s = f"{float(slow_v):.2f}" if slow_v else "N/A"
                adx_ok = "OK" if (pair.cfg.adx_min == 0 or (adx_v and adx_v > pair.cfg.adx_min)) else f"need >{pair.cfg.adx_min}"

                report_lines.append(
                    f"  * [bold]{sym:<8}[/bold]: ${px} | "
                    f"EMA({pair.cfg.ema_fast}/{pair.cfg.ema_slow}): {f_s} / {s_s} ({trend}) | "
                    f"{macro_status} | "
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

        # Refresh real account balance and exchange closed PnL periodically (every 10s)
        now_ts = time.time()
        if now_ts - self.last_account_fetch_ts >= 10.0:
            try:
                self.wallet_summary = self.service.get_wallet_summary()
                self.exchange_pnl_summary = self.service.get_exchange_closed_pnl_summary(limit=100)
                self.last_account_fetch_ts = now_ts
            except Exception as e:
                logger.debug(f"Account data refresh error: {e}")

        eq = self.wallet_summary.get("total_equity", Decimal("1000.00"))
        avail = self.wallet_summary.get("available_balance", Decimal("1000.00"))
        hist_pnl = self.exchange_pnl_summary.get("total_realized_pnl", cum_all)

        # Print clean status report with clear visual separation
        console.print(f"\n[bold cyan]--- [SCAN #{self.scan_count} @ {now_str}] ----------------------------------------------[/bold cyan]")
        for line in report_lines:
            console.print(line)
        console.print(
            f"  [cyan]Portfolio:[/cyan] {active_count}/{self.config.max_concurrent_pairs} Active Pairs | "
            f"Open PnL: ${tot_open:+.2f} | Realized PnL: ${hist_pnl:+.2f} | "
            f"Equity: ${eq:,.2f} (Avail: ${avail:,.2f})"
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
                    "macro_ema": float(pair.macro_ema) if pair.macro_ema is not None else None,
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

            eq = float(self.wallet_summary.get("total_equity", Decimal("1000.00")))
            wb = float(self.wallet_summary.get("wallet_balance", Decimal("1000.00")))
            avail = float(self.wallet_summary.get("available_balance", Decimal("1000.00")))
            hist_pnl = float(self.exchange_pnl_summary.get("total_realized_pnl", cum_all))
            by_sym = {k: float(v) for k, v in self.exchange_pnl_summary.get("by_symbol", {}).items()}
            trades_cnt = self.exchange_pnl_summary.get("total_trades", 0)
            recent_trades = self.exchange_pnl_summary.get("recent_trades", [])

            state = {
                "timestamp": datetime.now().isoformat(),
                "session_start_iso": self.session_start.isoformat(),
                "uptime_seconds": int((datetime.now() - self.session_start).total_seconds()),
                "total_cycles_completed": self.total_cycles_completed,
                "scan_count": self.scan_count,
                "network": "TESTNET" if self.config.testnet else "MAINNET",
                "leverage": self.config.leverage,
                "max_concurrent_pairs": self.config.max_concurrent_pairs,
                "active_pairs_count": sum(1 for p in self.pairs.values() if p.status == "ACTIVE"),
                "open_pnl": float(tot_open),
                "realized_pnl": hist_pnl,
                "session_realized_pnl": float(cum_all),
                "account_equity": eq,
                "wallet_balance": wb,
                "available_balance": avail,
                "exchange_realized_pnl": hist_pnl,
                "exchange_pnl_by_symbol": by_sym,
                "total_trades_count": trades_cnt,
                "exchange_recent_trades": recent_trades[:25],
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

        # Stale Candle Guard: reject any entry if (now - closed_candle_timestamp) > 3 minutes (180s)
        curr_ts = curr.get("timestamp", 0)
        curr_open_sec = (curr_ts / 1000.0) if curr_ts > 1e11 else float(curr_ts)
        try:
            interval_mins = int(pair.cfg.candle_interval)
        except Exception:
            interval_mins = 60
        candle_close_sec = curr_open_sec + (interval_mins * 60)
        now_sec = time.time()
        lag_sec = now_sec - candle_close_sec

        if lag_sec > 180.0:
            logger.warning(
                f"[{pair.cfg.symbol}] STALE CANDLE GUARD: Signal rejected! "
                f"Candle closed {lag_sec:.1f}s ago (> 180s threshold; closed at {curr.get('datetime')})."
            )
            return None

        pf = prev.get(f"ema_{pair.cfg.ema_fast}")
        ps = prev.get(f"ema_{pair.cfg.ema_slow}")
        cf = curr.get(f"ema_{pair.cfg.ema_fast}")
        cs = curr.get(f"ema_{pair.cfg.ema_slow}")
        adx_curr = curr.get("adx")
        adx_prev = prev.get("adx")
        macro_p = getattr(pair.cfg, "macro_ema_period", 200)
        macro_ema = curr.get(f"ema_{macro_p}")
        curr_close = curr.get("close")

        if any(v is None for v in [pf, ps, cf, cs]):
            return None

        if pair.cfg.adx_min > 0:
            if adx_curr is None or adx_curr <= pair.cfg.adx_min:
                return None

        # Rising ADX Momentum requirement: trend strength must be expanding
        if getattr(pair.cfg, "adx_rising_required", True):
            if adx_prev is not None and adx_curr is not None and adx_curr <= adx_prev:
                logger.info(
                    f"[{pair.cfg.symbol}] ADX MOMENTUM FILTER: Signal rejected "
                    f"(ADX {float(adx_curr):.1f} <= prev {float(adx_prev):.1f}; momentum fading)."
                )
                return None

        crossed_up   = (pf <= ps) and (cf > cs)
        crossed_down = (pf >= ps) and (cf < cs)

        # Macro 200-EMA Trend Filter: Never trade counter to macro trend!
        use_macro = getattr(pair.cfg, "use_macro_trend_filter", True)
        if use_macro and macro_ema is not None and curr_close is not None:
            if crossed_up and curr_close < macro_ema:
                logger.info(
                    f"[{pair.cfg.symbol}] MACRO TREND FILTER: Bullish crossover rejected! "
                    f"Price ${float(curr_close):.2f} < 200-EMA ${float(macro_ema):.2f} (Downtrend)."
                )
                return None
            if crossed_down and curr_close > macro_ema:
                logger.info(
                    f"[{pair.cfg.symbol}] MACRO TREND FILTER: Bearish crossover rejected! "
                    f"Price ${float(curr_close):.2f} > 200-EMA ${float(macro_ema):.2f} (Uptrend)."
                )
                return None

        if crossed_up:
            return "bullish"
        if crossed_down:
            return "bearish"
        return None

    # ==========================================================================
    # TRADE ENTRY
    # ==========================================================================

    def _enter_pair_trade(self, pair: PairState, direction: str, candles: Optional[List[Dict[str, Any]]] = None) -> None:
        """Execute simultaneous Long and Short market orders for this pair."""
        sym = pair.cfg.symbol
        target_notional = getattr(pair.cfg, "target_notional", Decimal("1000.0"))
        calc_size = pair.cfg.size
        fresh_px = self._get_fresh_price(pair)

        if target_notional and target_notional > Decimal("0") and fresh_px > Decimal("0"):
            try:
                rounded_str = self.service.round_qty(target_notional / fresh_px, symbol=sym)
                calc_size = Decimal(rounded_str)
                logger.info(f"[{sym}] Dynamic Sizing: ${target_notional} notional @ ${fresh_px:.2f} -> Qty {calc_size}")
            except Exception as e:
                logger.warning(f"[{sym}] Dynamic sizing calculation fallback: {e}")
                calc_size = pair.cfg.size

        if pair.cfg.hedge_ratio == Decimal("0.0"):
            console.print(
                f"\n[bold green]>>> [{sym}] EXECUTING SINGLE-LEG TREND RUNNER ({direction.upper()}) "
                f"@ ${fresh_px:.2f} (Size: {calc_size}, Notional: ${target_notional})! <<<[/bold green]"
            )
        else:
            console.print(f"\n[bold yellow]>>> [{sym}] EXECUTING DOUBLE ENTRY (LONG & SHORT HEDGE) <<<[/bold yellow]")

        sl_ratio = pair.cfg.sl_ratio
        tp_ratio = pair.cfg.tp_ratio

        if pair.cfg.asymmetric and direction in ("bullish", "bearish"):
            base = calc_size
            counter = base * pair.cfg.hedge_ratio
            long_size  = base if direction == "bullish" else counter
            short_size = counter if direction == "bullish" else base
            long_role  = "PRIMARY" if direction == "bullish" else "COUNTER"
            short_role = "COUNTER" if direction == "bullish" else "PRIMARY"
        else:
            long_size  = calc_size
            short_size = calc_size
            long_role  = "PRIMARY"
            short_role = "PRIMARY"

        # Long leg
        long_fill = Decimal("0")
        if long_size > Decimal("0"):
            long_fill = self.service.place_market_open("Buy", long_size, position_idx=1, symbol=sym)
            time.sleep(0.3)
            pair.long_leg = PositionLeg.new_long(long_size, long_fill, sl_ratio, tp_ratio, symbol=sym, role=long_role)
            if not pair.cfg.asymmetric:
                self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
            self._csv_event(pair, pair.long_leg, "ENTRY", long_fill)
        else:
            pair.long_leg = None

        # Short leg
        short_fill = Decimal("0")
        if short_size > Decimal("0"):
            short_fill = self.service.place_market_open("Sell", short_size, position_idx=2, symbol=sym)
            time.sleep(0.3)
            pair.short_leg = PositionLeg.new_short(short_size, short_fill, sl_ratio, tp_ratio, symbol=sym, role=short_role)
            if not pair.cfg.asymmetric:
                self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
            self._csv_event(pair, pair.short_leg, "ENTRY", short_fill)
        else:
            pair.short_leg = None

        pair.status = "ACTIVE"
        pair.phase = "INCUBATION" if pair.cfg.asymmetric else "ACTIVE"
        pair.signal_direction = direction
        if long_fill > Decimal("0") and short_fill > Decimal("0"):
            pair.entry_price = (long_fill + short_fill) / Decimal("2")
        elif long_fill > Decimal("0"):
            pair.entry_price = long_fill
        else:
            pair.entry_price = short_fill

        pair.entry_ts = time.time()
        pair.bars_elapsed = 0
        pair.b1_trailed = False
        pair.b1_trailed_stage2 = False
        pair.b2_trailed_to_be = False
        pair.b2_trailed_to_plus_1d = False
        pair.base_be_sl = Decimal("0")

        # Compute Dynamic ATR D if enabled
        pair.dynamic_d = pair.cfg.d_ratio
        if pair.cfg.use_dynamic_atr and pair.entry_price > Decimal("0"):
            atr_val = self._compute_atr(sym, candles=candles)
            if atr_val > Decimal("0"):
                pair.dynamic_d = (pair.cfg.atr_mult * atr_val) / pair.entry_price
                logger.info(f"[{sym}] Dynamic ATR D calibrated: {pair.dynamic_d:.5f} (ATR={atr_val:.2f})")

        # In Single-Leg Mode (hedge_ratio == 0), arm hard initial stop-loss immediately on exchange
        if pair.cfg.hedge_ratio == Decimal("0.0"):
            b2_sl_mult = getattr(pair.cfg, "b2_confirm", Decimal("1.00"))
            if direction == "bullish" and pair.long_leg:
                init_sl = pair.entry_price * (Decimal("1") - b2_sl_mult * pair.dynamic_d)
                pair.long_leg.trailing_sl = init_sl
                self.service.set_trading_stop(1, init_sl, None, symbol=sym)
                logger.info(f"[{sym}] Single-Leg Long Initial SL armed on exchange @ {init_sl:.2f} (-{b2_sl_mult}D)")
            elif direction == "bearish" and pair.short_leg:
                init_sl = pair.entry_price * (Decimal("1") + b2_sl_mult * pair.dynamic_d)
                pair.short_leg.trailing_sl = init_sl
                self.service.set_trading_stop(2, init_sl, None, symbol=sym)
                logger.info(f"[{sym}] Single-Leg Short Initial SL armed on exchange @ {init_sl:.2f} (+{b2_sl_mult}D)")

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
        px = self._get_fresh_price(pair)
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
        if pair.status != "ACTIVE":
            return
        pair.latest_price = price
        if pair.cfg.asymmetric:
            self._process_path_b_tick(pair, price)
        else:
            self._process_legacy_tick(pair, price)

    def _process_path_b_tick(self, pair: PairState, price: Decimal) -> None:
        """Execute Path B Asymmetric Trap Hunter & Zero-Loss Pullback state machine."""
        sym = pair.cfg.symbol
        d_val = pair.dynamic_d if (pair.dynamic_d and pair.dynamic_d > Decimal("0")) else pair.cfg.d_ratio
        entry_px = pair.entry_price
        base_qty = pair.cfg.size
        c_qty = pair.cfg.effective_counter_size
        fee_rate = Decimal("0.00055")

        # -- PHASE 1: INCUBATION (Delta-Neutral Breakout Monitor) --------------
        if pair.phase == "INCUBATION":
            if entry_px <= Decimal("0"):
                return

            if pair.signal_direction == "bullish":
                b1_mult = getattr(pair.cfg, "b1_confirm", pair.cfg.confirm_mult)
                b2_mult = getattr(pair.cfg, "b2_confirm", pair.cfg.confirm_mult)

                # BRANCH 1: Signal Was Right (Market expands in signal direction by b1_mult * D)
                if price >= entry_px * (Decimal("1") + b1_mult * d_val):
                    pair.phase = "RUNNER_B1"
                    confirm_px = price
                    # 1. Collapse Counter Short Leg if active
                    if pair.short_leg and pair.short_leg.status == "ACTIVE" and pair.short_leg.size > Decimal("0"):
                        self.service.close_position(2, pair.short_leg.size, symbol=sym)
                        pair.short_leg.status = "CLOSED_COLLAPSE"
                        pair.short_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.short_leg, "B1_COLLAPSE_COUNTER", confirm_px)

                    # 2. Arm 100% Primary Long Runner with Zero-Loss SL
                    if c_qty > Decimal("0"):
                        s_loss = (entry_px - confirm_px) * c_qty
                        s_fees = (entry_px + confirm_px) * c_qty * fee_rate
                        needed_be = abs(s_loss) + s_fees + (entry_px * base_qty * fee_rate * Decimal("2"))
                        long_sl_be = entry_px + (needed_be / base_qty)
                    else:
                        long_sl_be = entry_px * (Decimal("1") + Decimal("0.0016"))
                    long_tp = entry_px * (Decimal("1") + pair.cfg.b1_tp_mult * d_val)

                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        pair.long_leg.trailing_sl = long_sl_be
                        pair.long_leg.tp_target = long_tp
                        pair.base_be_sl = long_sl_be
                        pair.b1_trailed = False
                        pair.b1_trailed_stage2 = False
                        self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.long_leg, "B1_ARMED", confirm_px)

                    pair.status_msg = f"RUNNER B1 (Long @ {price:.2f}, SL: {long_sl_be:.2f}, TP: {long_tp:.2f})"
                    console.print(
                        f"\n[bold green]>>> [{sym}] BRANCH 1 HIT (+{b1_mult}D)! "
                        f"Long Armed with Zero-Loss (SL: {long_sl_be:.2f}, TP: {long_tp:.2f}) <<<[/bold green]"
                    )
                    self._dump_state_json()
                    return

                # BRANCH 2: Signal Was Wrong / Stop Barrier Hit
                elif price <= entry_px * (Decimal("1") - b2_mult * d_val):
                    if c_qty == Decimal("0") or not pair.cfg.b2_upsize:
                        # Single-Leg Mode or Clean Cut: Stop-Loss Hit!
                        if pair.long_leg and pair.long_leg.status == "ACTIVE":
                            self.service.close_position(1, pair.long_leg.size, symbol=sym)
                            pair.long_leg.status = "CLOSED_SL"
                            pair.long_leg.exit_price = price
                            self._csv_event(pair, pair.long_leg, "SL_HIT", price)
                        if pair.short_leg and pair.short_leg.status == "ACTIVE":
                            self.service.close_position(2, pair.short_leg.size, symbol=sym)
                            pair.short_leg.status = "CLOSED_TP"
                            pair.short_leg.exit_price = price
                            self._csv_event(pair, pair.short_leg, "TP_HIT", price)
                        console.print(
                            f"\n[bold red]>>> [{sym}] STOP LOSS HIT (-{b2_mult}D) @ {price:.2f}! Position closed cleanly. <<<[/bold red]"
                        )
                        self._close_pair_cycle(pair)
                        return

                    pair.phase = "RUNNER_B2"
                    confirm_px = price
                    exhaustion_level = entry_px * (Decimal("1") - pair.cfg.exhaustion_mult * d_val)
                    tp_level = entry_px * (Decimal("1") - pair.cfg.b2_tp_mult * d_val)

                    # 1. Collapse Trapped 100% Primary Long Leg
                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        self.service.close_position(1, pair.long_leg.size, symbol=sym)
                        pair.long_leg.status = "CLOSED_COLLAPSE"
                        pair.long_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.long_leg, "B2_COLLAPSE_PRIMARY", confirm_px)

                    # EXHAUSTION GUARD: If price already plunged past exhaustion_level or TP target,
                    # do NOT size-flip by selling into an overshot wick! Harvest profit on existing 30% short and end cycle.
                    if price <= exhaustion_level or price <= tp_level:
                        if pair.short_leg and pair.short_leg.status == "ACTIVE":
                            self.service.close_position(2, pair.short_leg.size, symbol=sym)
                            pair.short_leg.status = "CLOSED_TP"
                            pair.short_leg.exit_price = price
                            self._csv_event(pair, pair.short_leg, "TP_HIT_EXHAUSTION", price)
                        console.print(
                            f"\n[bold green]>>> [{sym}] FLASH DUMP DETECTED @ {price:.2f} (<= Exhaustion {exhaustion_level:.2f})! "
                            f"Harvested 30% Short profit without size-flipping into bottom wick. <<<[/bold green]"
                        )
                        self._close_pair_cycle(pair)
                        return

                    # 2. Normal Scenario 5 Size-Flip: Add +70% to Counter Short Leg to make 100% Runner
                    prev_short_size = pair.short_leg.size if pair.short_leg else c_qty
                    add_qty = base_qty - prev_short_size
                    add_fill = self.service.place_market_open("Sell", add_qty, position_idx=2, symbol=sym)
                    actual_short_size = self.service.get_position_size(2, symbol=sym)
                    actual_added = max(Decimal("0"), actual_short_size - prev_short_size)
                    if pair.short_leg:
                        pair.short_leg.upsize(actual_added, add_fill)
                        if actual_short_size > Decimal("0"):
                            pair.short_leg.size = actual_short_size
                        pair.short_leg.role = "PRIMARY"

                    # 3. Compute B2 Levels
                    trapped_loss = - (entry_px - confirm_px) * base_qty
                    trapped_fees = (entry_px + confirm_px) * base_qty * fee_rate
                    upsize_fees = confirm_px * add_qty * fee_rate
                    blend_px = pair.short_leg.entry_price if pair.short_leg else confirm_px
                    total_drain = abs(trapped_loss) + trapped_fees + upsize_fees + (base_qty * blend_px * fee_rate * Decimal("2"))
                    true_be = blend_px - (total_drain / base_qty)
                    curr_sl = entry_px  # Initial SL placed at initial entry P0 (valid above/below market price on exchange)

                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        pair.short_leg.trailing_sl = curr_sl
                        pair.short_leg.tp_target = tp_level
                        pair.base_be_sl = true_be
                        pair.b2_trailed_to_be = False
                        pair.b2_trailed_to_plus_1d = False
                        self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.short_leg, "B2_SIZE_FLIP", confirm_px)

                    pair.status_msg = f"RUNNER B2 (Short Size-Flip @ {price:.2f}, SL: {curr_sl:.2f}, TP: {tp_level:.2f})"
                    console.print(
                        f"\n[bold yellow]>>> [{sym}] BRANCH 2 HIT (-{pair.cfg.confirm_mult}D)! Long collapsed. "
                        f"Short Size-Flipped to 100% Runner (SL: {curr_sl:.2f}, TP: {tp_level:.2f}) <<<[/bold yellow]"
                    )
                    self._dump_state_json()
                    return

            elif pair.signal_direction == "bearish":
                b1_mult = getattr(pair.cfg, "b1_confirm", pair.cfg.confirm_mult)
                b2_mult = getattr(pair.cfg, "b2_confirm", pair.cfg.confirm_mult)

                # BRANCH 1: Signal Was Right (Market expands downward by b1_mult * D)
                if price <= entry_px * (Decimal("1") - b1_mult * d_val):
                    pair.phase = "RUNNER_B1"
                    confirm_px = price
                    # 1. Collapse Counter Long Leg if active
                    if pair.long_leg and pair.long_leg.status == "ACTIVE" and pair.long_leg.size > Decimal("0"):
                        self.service.close_position(1, pair.long_leg.size, symbol=sym)
                        pair.long_leg.status = "CLOSED_COLLAPSE"
                        pair.long_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.long_leg, "B1_COLLAPSE_COUNTER", confirm_px)

                    # 2. Arm 100% Primary Short Runner with Zero-Loss SL
                    if c_qty > Decimal("0"):
                        l_loss = (confirm_px - entry_px) * c_qty
                        l_fees = (entry_px + confirm_px) * c_qty * fee_rate
                        needed_be = abs(l_loss) + l_fees + (entry_px * base_qty * fee_rate * Decimal("2"))
                        short_sl_be = entry_px - (needed_be / base_qty)
                    else:
                        short_sl_be = entry_px * (Decimal("1") - Decimal("0.0016"))
                    short_tp = entry_px * (Decimal("1") - pair.cfg.b1_tp_mult * d_val)

                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        pair.short_leg.trailing_sl = short_sl_be
                        pair.short_leg.tp_target = short_tp
                        pair.base_be_sl = short_sl_be
                        pair.b1_trailed = False
                        pair.b1_trailed_stage2 = False
                        self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.short_leg, "B1_ARMED", confirm_px)

                    pair.status_msg = f"RUNNER B1 (Short @ {price:.2f}, SL: {short_sl_be:.2f}, TP: {short_tp:.2f})"
                    console.print(
                        f"\n[bold green]>>> [{sym}] BRANCH 1 HIT (-{b1_mult}D)! "
                        f"Short Armed with Zero-Loss (SL: {short_sl_be:.2f}, TP: {short_tp:.2f}) <<<[/bold green]"
                    )
                    self._dump_state_json()
                    return

                # BRANCH 2: Signal Was Wrong / Stop Barrier Hit
                elif price >= entry_px * (Decimal("1") + b2_mult * d_val):
                    if c_qty == Decimal("0") or not pair.cfg.b2_upsize:
                        # Single-Leg Mode or Clean Cut: Stop-Loss Hit!
                        if pair.short_leg and pair.short_leg.status == "ACTIVE":
                            self.service.close_position(2, pair.short_leg.size, symbol=sym)
                            pair.short_leg.status = "CLOSED_SL"
                            pair.short_leg.exit_price = price
                            self._csv_event(pair, pair.short_leg, "SL_HIT", price)
                        if pair.long_leg and pair.long_leg.status == "ACTIVE":
                            self.service.close_position(1, pair.long_leg.size, symbol=sym)
                            pair.long_leg.status = "CLOSED_TP"
                            pair.long_leg.exit_price = price
                            self._csv_event(pair, pair.long_leg, "TP_HIT", price)
                        console.print(
                            f"\n[bold red]>>> [{sym}] STOP LOSS HIT (+{b2_mult}D) @ {price:.2f}! Position closed cleanly. <<<[/bold red]"
                        )
                        self._close_pair_cycle(pair)
                        return

                    pair.phase = "RUNNER_B2"
                    confirm_px = price
                    exhaustion_level = entry_px * (Decimal("1") + pair.cfg.exhaustion_mult * d_val)
                    tp_level = entry_px * (Decimal("1") + pair.cfg.b2_tp_mult * d_val)

                    # 1. Collapse Trapped 100% Primary Short Leg
                    if pair.short_leg and pair.short_leg.status == "ACTIVE":
                        self.service.close_position(2, pair.short_leg.size, symbol=sym)
                        pair.short_leg.status = "CLOSED_COLLAPSE"
                        pair.short_leg.exit_price = confirm_px
                        self._csv_event(pair, pair.short_leg, "B2_COLLAPSE_PRIMARY", confirm_px)

                    # EXHAUSTION GUARD: If price already pumped past exhaustion_level or TP target,
                    # do NOT size-flip by buying the top wick! Harvest profit on existing 30% long and end cycle.
                    if price >= exhaustion_level or price >= tp_level:
                        if pair.long_leg and pair.long_leg.status == "ACTIVE":
                            self.service.close_position(1, pair.long_leg.size, symbol=sym)
                            pair.long_leg.status = "CLOSED_TP"
                            pair.long_leg.exit_price = price
                            self._csv_event(pair, pair.long_leg, "TP_HIT_EXHAUSTION", price)
                        console.print(
                            f"\n[bold green]>>> [{sym}] FLASH PUMP DETECTED @ {price:.2f} (>= Exhaustion {exhaustion_level:.2f})! "
                            f"Harvested 30% Long profit without size-flipping into top wick. <<<[/bold green]"
                        )
                        self._close_pair_cycle(pair)
                        return

                    # 2. Normal Scenario 5 Size-Flip: Add +70% to Counter Long Leg to make 100% Runner
                    prev_long_size = pair.long_leg.size if pair.long_leg else c_qty
                    add_qty = base_qty - prev_long_size
                    add_fill = self.service.place_market_open("Buy", add_qty, position_idx=1, symbol=sym)
                    actual_long_size = self.service.get_position_size(1, symbol=sym)
                    actual_added = max(Decimal("0"), actual_long_size - prev_long_size)
                    if pair.long_leg:
                        pair.long_leg.upsize(actual_added, add_fill)
                        if actual_long_size > Decimal("0"):
                            pair.long_leg.size = actual_long_size
                        pair.long_leg.role = "PRIMARY"

                    # 3. Compute B2 Levels
                    trapped_loss = - (confirm_px - entry_px) * base_qty
                    trapped_fees = (entry_px + confirm_px) * base_qty * fee_rate
                    upsize_fees = confirm_px * add_qty * fee_rate
                    blend_px = pair.long_leg.entry_price if pair.long_leg else confirm_px
                    total_drain = abs(trapped_loss) + trapped_fees + upsize_fees + (base_qty * blend_px * fee_rate * Decimal("2"))
                    true_be = blend_px + (total_drain / base_qty)
                    curr_sl = entry_px  # Initial SL placed at initial entry P0 (valid above/below market price on exchange)

                    if pair.long_leg and pair.long_leg.status == "ACTIVE":
                        pair.long_leg.trailing_sl = curr_sl
                        pair.long_leg.tp_target = tp_level
                        pair.base_be_sl = true_be
                        pair.b2_trailed_to_be = False
                        pair.b2_trailed_to_plus_1d = False
                        self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                        self._csv_event(pair, pair.long_leg, "B2_SIZE_FLIP", confirm_px)

                    pair.status_msg = f"RUNNER B2 (Long Size-Flip @ {price:.2f}, SL: {curr_sl:.2f}, TP: {tp_level:.2f})"
                    console.print(
                        f"\n[bold yellow]>>> [{sym}] BRANCH 2 HIT (+{pair.cfg.confirm_mult}D)! Short collapsed. "
                        f"Long Size-Flipped to 100% Runner (SL: {curr_sl:.2f}, TP: {tp_level:.2f}) <<<[/bold yellow]"
                    )
                    self._dump_state_json()
                    return

        # -- PHASE 2: RUNNER B1 (Trend Expansion & Zero-Loss Pullback) ---------
        elif pair.phase == "RUNNER_B1":
            if pair.signal_direction == "bullish" and pair.long_leg and pair.long_leg.status == "ACTIVE":
                # Ratchet Milestone 2: +2.20D reached -> Ratchet SL to P0 + 1.70D (+1.7D profit lock)
                trail_trig2 = entry_px * (Decimal("1") + pair.cfg.b1_r2_trig * d_val)
                if price >= trail_trig2 and not pair.b1_trailed_stage2:
                    new_sl2 = entry_px * (Decimal("1") + pair.cfg.b1_r2_sl * d_val)
                    pair.long_leg.trailing_sl = new_sl2
                    pair.b1_trailed = True
                    pair.b1_trailed_stage2 = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B1 RATCHET STAGE 2] SL raised to {new_sl2:.2f} (+{pair.cfg.b1_r2_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B1_RATCHET_2", price)

                # Ratchet Milestone 1: +1.40D reached -> Ratchet SL to P0 + 1.0D (+1.0D profit lock)
                elif price >= entry_px * (Decimal("1") + pair.cfg.b1_r1_trig * d_val) and not pair.b1_trailed:
                    new_sl = entry_px * (Decimal("1") + pair.cfg.b1_r1_sl * d_val)
                    pair.long_leg.trailing_sl = new_sl
                    pair.b1_trailed = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B1 RATCHET STAGE 1] SL raised to {new_sl:.2f} (+{pair.cfg.b1_r1_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B1_RATCHET_1", price)

                # TP Hit
                if price >= pair.long_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} LONG B1 TP HIT ({pair.cfg.b1_tp_mult}D)] @ {price:.2f} <<<[/bold green]")
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
                # Ratchet Milestone 2: -2.20D reached -> Ratchet SL to P0 - 1.70D (+1.7D profit lock)
                trail_trig2 = entry_px * (Decimal("1") - pair.cfg.b1_r2_trig * d_val)
                if price <= trail_trig2 and not pair.b1_trailed_stage2:
                    new_sl2 = entry_px * (Decimal("1") - pair.cfg.b1_r2_sl * d_val)
                    pair.short_leg.trailing_sl = new_sl2
                    pair.b1_trailed = True
                    pair.b1_trailed_stage2 = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B1 RATCHET STAGE 2] SL lowered to {new_sl2:.2f} (+{pair.cfg.b1_r2_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B1_RATCHET_2", price)

                # Ratchet Milestone 1: -1.40D reached -> Ratchet SL to P0 - 1.0D (+1.0D profit lock)
                elif price <= entry_px * (Decimal("1") - pair.cfg.b1_r1_trig * d_val) and not pair.b1_trailed:
                    new_sl = entry_px * (Decimal("1") - pair.cfg.b1_r1_sl * d_val)
                    pair.short_leg.trailing_sl = new_sl
                    pair.b1_trailed = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B1 RATCHET STAGE 1] SL lowered to {new_sl:.2f} (+{pair.cfg.b1_r1_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B1_RATCHET_1", price)

                # TP Hit
                if price <= pair.short_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} SHORT B1 TP HIT ({pair.cfg.b1_tp_mult}D)] @ {price:.2f} <<<[/bold green]")
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
                # Milestone 2: Reaches 2.50D -> Ratchet SL to 2.10D (+0.65D Profit Lock above True BE)
                ratchet_trig = entry_px * (Decimal("1") - pair.cfg.b2_r2_trig * d_val)
                sl_ratchet = entry_px * (Decimal("1") - pair.cfg.b2_r2_sl * d_val)
                if price <= ratchet_trig and not pair.b2_trailed_to_plus_1d:
                    pair.short_leg.trailing_sl = sl_ratchet
                    pair.b2_trailed_to_plus_1d = True
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 PROFIT RATCHET] SL lowered to {sl_ratchet:.2f} (+{pair.cfg.b2_r2_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B2_RATCHET_PROFIT", price)

                # Milestone 1: Extends past True BE (cushion = 0.10D) -> Lock True BE (Zero Loss Secured)
                elif price <= pair.base_be_sl - (entry_px * pair.cfg.b2_be_cushion * d_val) and not pair.b2_trailed_to_be:
                    pair.short_leg.trailing_sl = pair.base_be_sl
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(2, pair.short_leg.trailing_sl, pair.short_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 TRUE BE LOCK] SL set to {pair.base_be_sl:.2f} (Zero Loss Secured) <<<[/bold green]")
                    self._csv_event(pair, pair.short_leg, "B2_RATCHET_BE", price)

                # TP Hit (B2 Target)
                if price <= pair.short_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} SHORT B2 TP HIT ({pair.cfg.b2_tp_mult}D)] @ {price:.2f} <<<[/bold green]")
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
                # Milestone 2: Reaches 2.50D -> Ratchet SL to 2.10D (+0.65D Profit Lock above True BE)
                ratchet_trig = entry_px * (Decimal("1") + pair.cfg.b2_r2_trig * d_val)
                sl_ratchet = entry_px * (Decimal("1") + pair.cfg.b2_r2_sl * d_val)
                if price >= ratchet_trig and not pair.b2_trailed_to_plus_1d:
                    pair.long_leg.trailing_sl = sl_ratchet
                    pair.b2_trailed_to_plus_1d = True
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 PROFIT RATCHET] SL raised to {sl_ratchet:.2f} (+{pair.cfg.b2_r2_sl}D Locked) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B2_RATCHET_PROFIT", price)

                # Milestone 1: Extends past True BE (cushion = 0.10D) -> Lock True BE (Zero Loss Secured)
                elif price >= pair.base_be_sl + (entry_px * pair.cfg.b2_be_cushion * d_val) and not pair.b2_trailed_to_be:
                    pair.long_leg.trailing_sl = pair.base_be_sl
                    pair.b2_trailed_to_be = True
                    self.service.set_trading_stop(1, pair.long_leg.trailing_sl, pair.long_leg.tp_target, symbol=sym)
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 TRUE BE LOCK] SL set to {pair.base_be_sl:.2f} (Zero Loss Secured) <<<[/bold green]")
                    self._csv_event(pair, pair.long_leg, "B2_RATCHET_BE", price)

                # TP Hit (B2 Target)
                if price >= pair.long_leg.tp_target:
                    console.print(f"\n[bold green]>>> [{sym} LONG B2 TP HIT ({pair.cfg.b2_tp_mult}D)] @ {price:.2f} <<<[/bold green]")
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
        if pair.status not in ("ACTIVE", "INCUBATION", "RUNNER_B1", "RUNNER_B2"):
            return
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
        pair.b1_trailed_stage2 = False
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

            with self._lock:
                for sym, pair in self.pairs.items():
                    if pair.status != "ACTIVE":
                        continue

                    active_indices = active_by_sym.get(sym, set())
                    px = self._get_fresh_price(pair)
                    if px > Decimal("0"):
                        self._process_pair_tick(pair, px)

                    if pair.status != "ACTIVE":
                        continue

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
                            if not pair.cfg.asymmetric:
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
                            if not pair.cfg.asymmetric:
                                self._apply_be_lock_long(pair, px)

                    # Check if cycle ended
                    l_done = pair.long_leg is None or pair.long_leg.status != "ACTIVE"
                    s_done = pair.short_leg is None or pair.short_leg.status != "ACTIVE"
                    if l_done and s_done and pair.status == "ACTIVE":
                        self._close_pair_cycle(pair)

                self._dump_state_json()
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
                    "price", "size", "extreme_price", "trailing_sl", "tp_target",
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
                f"{leg.size}",
                f"{leg.extreme_price:.2f}",
                f"{leg.trailing_sl:.2f}",
                f"{leg.tp_target:.2f}",
                f"{pnl_usd:+.2f}",
                f"{pair.cumulative_pnl:+.2f}",
            ])
