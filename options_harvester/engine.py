"""
engine.py — Core Autonomous Delta-Neutral Options Harvester & Strangle Lifecycle State Machine.
Strict $1,000 USD capital enclosure with automated 2.0x SL, 70% profit harvest, and DDH hedging.
"""

import os
import csv
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from options_harvester.config import (
    ALLOCATED_CAPITAL,
    CIRCUIT_BREAKER_MAX_DD_PCT,
    PREMIUM_STOP_LOSS_MULT,
    BASE_ORDER_QTY,
    PROFIT_TARGET_DECAY_PCT,
    DEFENSIVE_ROLL_DECAY_PCT,
    GAMMA_PIN_CUTOFF_MINUTES,
    STATE_FILE,
    TRADES_FILE,
    LOG_FILE,
)
from options_harvester.scanner import StrangleScanner, get_hours_to_expiry, parse_expiry_from_symbol
from options_harvester.ddh_engine import DDHEngine
from options_harvester.greeks import GreeksAggregator

logger = logging.getLogger("options_harvester.engine")


class OptionsHarvesterEngine:
    """State machine coordinator for the Delta-Neutral Options Harvester."""

    def __init__(self, client, state_file: str = STATE_FILE, trades_file: str = TRADES_FILE):
        self.client = client
        self.state_file = state_file
        self.trades_file = trades_file

        self.scanner = StrangleScanner(self.client)
        self.ddh = DDHEngine(self.client)

        # Capital & PnL Metrics
        self.allocated_capital = ALLOCATED_CAPITAL
        self.current_capital = ALLOCATED_CAPITAL
        self.peak_capital = ALLOCATED_CAPITAL
        self.total_realized_pnl = 0.0
        self.total_theta_harvested = 0.0
        self.total_cycles_completed = 0
        self.profitable_cycles = 0
        self.loss_cycles = 0

        # State Machine
        self.state = "STATE_0_SCANNING"
        self.cycle_id = 1
        self.cycle_start_time = 0.0
        self.last_status_message = "Engine initialized in STATE_0_SCANNING."
        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0.0

        # Active Strangle Legs
        self.active_put: Optional[Dict[str, Any]] = None
        self.active_call: Optional[Dict[str, Any]] = None
        self.breakevens: Dict[str, float] = {}
        self.cone: Dict[str, float] = {}
        self.initial_net_premium = 0.0

        # Load existing state if available
        self._load_state()
        self._ensure_trades_csv_header()

    def _ensure_trades_csv_header(self) -> None:
        """Create trades audit CSV with header if not exists."""
        if not os.path.exists(self.trades_file):
            try:
                with open(self.trades_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "timestamp", "cycle_id", "outcome", "exit_reason",
                        "put_symbol", "put_strike", "put_entry_px", "put_exit_px",
                        "call_symbol", "call_strike", "call_entry_px", "call_exit_px",
                        "gross_pnl_usd", "ddh_hedge_pnl_usd", "net_pnl_usd",
                        "return_on_1k_pct", "capital_after_usd", "hold_time_hours"
                    ])
            except Exception as e:
                logger.error(f"Error creating trades CSV header: {e}")

    def _save_state(self) -> None:
        """Atomically persist engine state to JSON."""
        state_dict = {
            "version": "1.0.0",
            "timestamp": time.time(),
            "state": self.state,
            "cycle_id": self.cycle_id,
            "cycle_start_time": self.cycle_start_time,
            "allocated_capital": self.allocated_capital,
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "total_realized_pnl": self.total_realized_pnl,
            "total_theta_harvested": self.total_theta_harvested,
            "total_cycles_completed": self.total_cycles_completed,
            "profitable_cycles": self.profitable_cycles,
            "loss_cycles": self.loss_cycles,
            "win_rate_pct": (self.profitable_cycles / self.total_cycles_completed * 100.0) if self.total_cycles_completed > 0 else 0.0,
            "circuit_breaker_active": self.circuit_breaker_active,
            "circuit_breaker_until": self.circuit_breaker_until,
            "dry_run": getattr(self.client, "dry_run", True),
            "last_status_message": self.last_status_message,
            "active_put": self.active_put,
            "active_call": self.active_call,
            "breakevens": self.breakevens,
            "cone": self.cone,
            "initial_net_premium": self.initial_net_premium,
            "ddh_perp_position": self.ddh.current_perp_position,
            "ddh_rebalance_count": self.ddh.total_rebalance_count,
            "ddh_realized_pnl": self.ddh.ddh_realized_pnl,
        }

        tmp_path = f"{self.state_file}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_dict, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except Exception as e:
            logger.error(f"Error persisting state to {self.state_file}: {e}")

    def _load_state(self) -> None:
        """Load state from file on boot."""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.state = data.get("state", "STATE_0_SCANNING")
            self.cycle_id = data.get("cycle_id", 1)
            self.cycle_start_time = data.get("cycle_start_time", 0.0)
            self.current_capital = data.get("current_capital", ALLOCATED_CAPITAL)
            self.peak_capital = data.get("peak_capital", ALLOCATED_CAPITAL)
            self.total_realized_pnl = data.get("total_realized_pnl", 0.0)
            self.total_theta_harvested = data.get("total_theta_harvested", 0.0)
            self.total_cycles_completed = data.get("total_cycles_completed", 0)
            self.profitable_cycles = data.get("profitable_cycles", 0)
            self.loss_cycles = data.get("loss_cycles", 0)
            self.circuit_breaker_active = data.get("circuit_breaker_active", False)
            self.circuit_breaker_until = data.get("circuit_breaker_until", 0.0)
            self.last_status_message = data.get("last_status_message", "State loaded from disk.")
            self.active_put = data.get("active_put")
            self.active_call = data.get("active_call")
            self.breakevens = data.get("breakevens", {})
            self.cone = data.get("cone", {})
            self.initial_net_premium = data.get("initial_net_premium", 0.0)
            self.ddh.current_perp_position = data.get("ddh_perp_position", 0.0)
            self.ddh.total_rebalance_count = data.get("ddh_rebalance_count", 0)
            self.ddh.ddh_realized_pnl = data.get("ddh_realized_pnl", 0.0)

            # If switching from simulation to live exchange, discard simulated legs to place authentic Bybit orders
            if not self.client.dry_run and data.get("dry_run", True) is True:
                logger.info("[LIVE TRANSITION] Switching from simulation to LIVE Bybit UTA. Clearing simulated state to deploy authentic exchange orders.")
                self.state = "STATE_0_SCANNING"
                self.active_put = None
                self.active_call = None
                self.initial_net_premium = 0.0
                self.last_status_message = "Switched to LIVE mode. Scanning Bybit options chain for authentic execution..."

            logger.info(f"Loaded options harvester state: {self.state} | Cycle {self.cycle_id} | Live: {not self.client.dry_run}")
        except Exception as e:
            logger.error(f"Error loading state from {self.state_file}: {e}")

    def _record_trade_audit(
        self,
        outcome: str,
        exit_reason: str,
        gross_pnl: float,
        ddh_pnl: float,
        put_exit_px: float,
        call_exit_px: float,
    ) -> None:
        """Append closed cycle trade audit to CSV ledger."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        net_pnl = gross_pnl + ddh_pnl
        ret_pct = (net_pnl / self.allocated_capital) * 100.0
        hold_time_h = (time.time() - self.cycle_start_time) / 3600.0 if self.cycle_start_time > 0 else 0.0

        p_sym = self.active_put.get("symbol", "") if self.active_put else ""
        p_strike = self.active_put.get("strike", 0.0) if self.active_put else 0.0
        p_entry = self.active_put.get("entry_price", 0.0) if self.active_put else 0.0

        c_sym = self.active_call.get("symbol", "") if self.active_call else ""
        c_strike = self.active_call.get("strike", 0.0) if self.active_call else 0.0
        c_entry = self.active_call.get("entry_price", 0.0) if self.active_call else 0.0

        try:
            with open(self.trades_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    now_str, self.cycle_id, outcome, exit_reason,
                    p_sym, p_strike, p_entry, put_exit_px,
                    c_sym, c_strike, c_entry, call_exit_px,
                    f"{gross_pnl:.2f}", f"{ddh_pnl:.2f}", f"{net_pnl:.2f}",
                    f"{ret_pct:+.2f}%", f"{self.current_capital:.2f}", f"{hold_time_h:.2f}"
                ])
        except Exception as e:
            logger.error(f"Error writing to trades CSV: {e}")

    def _reconcile_open_positions_with_exchange(self) -> None:
        """Verify that active strangle legs actually exist on Bybit exchange."""
        if self.client.dry_run or not self.client.session:
            return

        try:
            res = self.client.session.get_positions(category="option", baseCoin="BTC")
            if res.get("retCode") == 0:
                open_positions = {
                    p.get("symbol"): float(p.get("size", 0.0))
                    for p in res.get("result", {}).get("list", [])
                    if float(p.get("size", 0.0)) > 0
                }

                # If in HARVESTING or DEPLOYING, verify legs exist on Bybit
                if self.state in ("STATE_1_DEPLOYING", "STATE_2_HARVESTING"):
                    p_sym = self.active_put.get("symbol") if self.active_put else None
                    c_sym = self.active_call.get("symbol") if self.active_call else None

                    # If missing from Bybit, reset to SCANNING to place live orders
                    if not p_sym or not c_sym or (p_sym not in open_positions and c_sym not in open_positions):
                        logger.warning(
                            f"[RECONCILE] Active strangle legs (Put: {p_sym}, Call: {c_sym}) NOT found in Bybit positions: {list(open_positions.keys())}. "
                            f"Resetting to STATE_0_SCANNING to deploy authentic exchange orders."
                        )
                        self.state = "STATE_0_SCANNING"
                        self.active_put = None
                        self.active_call = None
                        self.initial_net_premium = 0.0
                        self._save_state()
        except Exception as e:
            logger.error(f"Error reconciling options positions with Bybit: {e}")

    # ── Main Tick Execution Loop ─────────────────────────────────────────────

    def tick(self, spot_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Execute one complete tick cycle of the options harvester state machine.
        Returns full telemetry dictionary.
        """
        now = time.time()
        if spot_price is None or spot_price <= 0:
            spot_price = self.client.get_perp_price("BTCUSDT")

        # 0. Exchange position reconciliation guard
        self._reconcile_open_positions_with_exchange()

        # 1. Circuit breaker cooldown check
        if self.circuit_breaker_active:
            if now < self.circuit_breaker_until:
                remaining_m = (self.circuit_breaker_until - now) / 60.0
                self.last_status_message = f"CIRCUIT BREAKER ACTIVE. Cooldown remaining: {remaining_m:.1f}m"
                return self.get_telemetry(spot_price)
            else:
                self.circuit_breaker_active = False
                self.state = "STATE_0_SCANNING"
                self.last_status_message = "Circuit breaker cooldown expired. Resuming STATE_0_SCANNING."
                self._save_state()

        # 2. Portfolio Max Drawdown Circuit Breaker Guard (5.0% / $50 USD)
        current_dd = (self.peak_capital - self.current_capital) / self.peak_capital if self.peak_capital > 0 else 0.0
        if current_dd >= CIRCUIT_BREAKER_MAX_DD_PCT:
            self._trigger_circuit_breaker(spot_price, f"Portfolio drawdown {current_dd*100:.1f}% exceeded 5.0% cap")
            return self.get_telemetry(spot_price)

        # 3. State Machine Router
        if self.state == "STATE_0_SCANNING":
            self._handle_state_scanning(spot_price)

        elif self.state == "STATE_1_DEPLOYING":
            self._handle_state_deploying(spot_price)

        elif self.state in ("STATE_2_HARVESTING", "STATE_3_DDH_REBALANCING"):
            self._handle_state_harvesting(spot_price)

        self._save_state()
        return self.get_telemetry(spot_price)

    # ── State Handlers ───────────────────────────────────────────────────────

    def _handle_state_scanning(self, spot_price: float) -> None:
        """Scan Bybit options surface for candidate Short Strangle."""
        candidate = self.scanner.scan_optimal_strangle(base_coin="BTC", spot_price=spot_price)

        if not candidate:
            self.last_status_message = "Scanning Bybit surface: No optimal ~15-delta pairs in 18h-72h DTE window."
            return

        # Found candidate!
        p = candidate["put"]
        c = candidate["call"]
        self.active_put = {**p, "qty": BASE_ORDER_QTY, "active": False, "entry_time": 0.0}
        self.active_call = {**c, "qty": BASE_ORDER_QTY, "active": False, "entry_time": 0.0}
        self.breakevens = candidate["breakevens"]
        self.cone = candidate["cone"]
        self.initial_net_premium = (p["entry_price"] + c["entry_price"]) * BASE_ORDER_QTY

        self.state = "STATE_1_DEPLOYING"
        self.last_status_message = (
            f"Selected Strangle: Put {p['symbol']} (${p['strike']:,.0f}) & "
            f"Call {c['symbol']} (${c['strike']:,.0f}) | Net Delta: {candidate['net_delta_initial']:+.4f} | "
            f"Gross Premium: ${self.initial_net_premium:.2f}"
        )
        logger.info(self.last_status_message)

    def _handle_state_deploying(self, spot_price: float) -> None:
        """Deploy Short Put and Short Call orders via Bybit V5 Options."""
        if not self.active_put or not self.active_call:
            self.state = "STATE_0_SCANNING"
            return

        p_sym = self.active_put["symbol"]
        p_px = self.active_put["entry_price"]
        c_sym = self.active_call["symbol"]
        c_px = self.active_call["entry_price"]

        logger.info(f"[DEPLOYING STRANGLE] Selling {BASE_ORDER_QTY} {p_sym} @ ${p_px} and {BASE_ORDER_QTY} {c_sym} @ ${c_px}")

        p_order = self.client.sell_option_leg(symbol=p_sym, qty=BASE_ORDER_QTY, price=p_px, tag="put")
        c_order = self.client.sell_option_leg(symbol=c_sym, qty=BASE_ORDER_QTY, price=c_px, tag="call")

        if p_order and c_order:
            now = time.time()
            self.cycle_start_time = now
            self.active_put["active"] = True
            self.active_put["order_id"] = p_order
            self.active_put["entry_time"] = now
            self.active_call["active"] = True
            self.active_call["order_id"] = c_order
            self.active_call["entry_time"] = now

            self.state = "STATE_2_HARVESTING"
            self.last_status_message = (
                f"Strangle deployed and active! Harvest target: 70% decay | "
                f"Stop Loss: 2.0x premium (${p_px*PREMIUM_STOP_LOSS_MULT:,.0f} / ${c_px*PREMIUM_STOP_LOSS_MULT:,.0f})"
            )
            logger.info(self.last_status_message)
        else:
            self.last_status_message = "Failed placing one or both strangle legs. Returning to scan."
            logger.warning(self.last_status_message)
            self.state = "STATE_0_SCANNING"

    def _handle_state_harvesting(self, spot_price: float) -> None:
        """Monitor active strangle legs for Theta decay, 2.0x SL, Gamma pin, and DDH drift."""
        if not self.active_put or not self.active_call:
            self.state = "STATE_0_SCANNING"
            return

        # Fetch fresh mark prices and Greeks from Bybit
        put_ticker = self.client.get_option_ticker(self.active_put["symbol"])
        call_ticker = self.client.get_option_ticker(self.active_call["symbol"])

        put_mark = float(put_ticker.get("markPrice") or self.active_put["mark_price"]) if put_ticker else self.active_put["mark_price"]
        call_mark = float(call_ticker.get("markPrice") or self.active_call["mark_price"]) if call_ticker else self.active_call["mark_price"]

        if put_ticker:
            self.active_put["mark_price"] = put_mark
            self.active_put["delta"] = float(put_ticker.get("delta") or self.active_put["delta"])
            self.active_put["gamma"] = float(put_ticker.get("gamma") or self.active_put["gamma"])
            self.active_put["theta"] = float(put_ticker.get("theta") or self.active_put["theta"])

        if call_ticker:
            self.active_call["mark_price"] = call_mark
            self.active_call["delta"] = float(call_ticker.get("delta") or self.active_call["delta"])
            self.active_call["gamma"] = float(call_ticker.get("gamma") or self.active_call["gamma"])
            self.active_call["theta"] = float(call_ticker.get("theta") or self.active_call["theta"])

        # ── Check 1: 2.0x Premium Hard Stop-Loss Guard ─────────────────────────
        put_entry = self.active_put["entry_price"]
        call_entry = self.active_call["entry_price"]

        if put_mark >= put_entry * PREMIUM_STOP_LOSS_MULT:
            self._close_cycle_stop_loss(spot_price, put_mark, call_mark, "PUT_2X_SL_HIT")
            return

        if call_mark >= call_entry * PREMIUM_STOP_LOSS_MULT:
            self._close_cycle_stop_loss(spot_price, put_mark, call_mark, "CALL_2X_SL_HIT")
            return

        # ── Check 2: Expiration Gamma Pin Avoidance (T-120m Cutoff) ─────────────
        exp_dt = parse_expiry_from_symbol(self.active_put["symbol"])
        if exp_dt:
            hours_left = get_hours_to_expiry(exp_dt)
            minutes_left = hours_left * 60.0
            if minutes_left <= GAMMA_PIN_CUTOFF_MINUTES:
                self._close_cycle_profit(spot_price, put_mark, call_mark, "GAMMA_PIN_T120M_CUTOFF")
                return

        # ── Check 3: 70% Profit Target Decay Exit ──────────────────────────────
        current_stew = (put_mark + call_mark) * BASE_ORDER_QTY
        decay_pct = (self.initial_net_premium - current_stew) / self.initial_net_premium if self.initial_net_premium > 0 else 0.0

        if decay_pct >= PROFIT_TARGET_DECAY_PCT:
            self._close_cycle_profit(spot_price, put_mark, call_mark, "70PCT_THETA_HARVESTED")
            return

        # ── Check 4: Dynamic Delta Hedging (DDH) Rebalancing ───────────────────
        ddh_res = self.ddh.evaluate_and_rebalance(
            put_leg=self.active_put,
            call_leg=self.active_call,
            current_spot=spot_price,
        )

        if not ddh_res["within_tolerance"]:
            self.state = "STATE_3_DDH_REBALANCING"
            self.last_status_message = (
                f"DDH Active: Normalized Delta {ddh_res['normalized_net_delta']:+.4f} | "
                f"Action: {ddh_res['action']} | Perp Qty: {ddh_res['current_perp_qty']:.3f}"
            )
        else:
            self.state = "STATE_2_HARVESTING"
            decay_str = f"{decay_pct*100:.1f}%"
            self.last_status_message = (
                f"Harvesting: Premium Decayed {decay_str} / 70% Target | "
                f"Net Delta: {ddh_res['normalized_net_delta']:+.4f} (Safe ±0.10) | Spot: ${spot_price:,.1f}"
            )

    # ── Cycle Termination Methods ────────────────────────────────────────────

    def _close_cycle_profit(
        self,
        spot_price: float,
        put_mark: float,
        call_mark: float,
        reason: str,
    ) -> None:
        """Close strangle for profit take or gamma pin cutoff."""
        logger.info(f"[CYCLE PROFIT EXIT] Reason: {reason} | Closing strangle at market...")

        self.client.close_option_leg(self.active_put["symbol"], BASE_ORDER_QTY, price=put_mark, reason=reason)
        self.client.close_option_leg(self.active_call["symbol"], BASE_ORDER_QTY, price=call_mark, reason=reason)
        ddh_pnl = self.ddh.close_all_hedges(spot_price)

        put_gain = (self.active_put["entry_price"] - put_mark) * BASE_ORDER_QTY
        call_gain = (self.active_call["entry_price"] - call_mark) * BASE_ORDER_QTY
        gross_pnl = put_gain + call_gain
        net_pnl = gross_pnl + ddh_pnl

        self.current_capital += net_pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        self.total_realized_pnl += net_pnl
        self.total_theta_harvested += gross_pnl
        self.total_cycles_completed += 1
        self.profitable_cycles += 1

        self._record_trade_audit("WIN", reason, gross_pnl, ddh_pnl, put_mark, call_mark)

        self.last_status_message = (
            f"Cycle {self.cycle_id} WON! Realized: +${net_pnl:.2f} ({reason}) | "
            f"Capital: ${self.current_capital:.2f}"
        )
        logger.info(self.last_status_message)

        self.cycle_id += 1
        self.active_put = None
        self.active_call = None
        self.state = "STATE_0_SCANNING"

    def _close_cycle_stop_loss(
        self,
        spot_price: float,
        put_mark: float,
        call_mark: float,
        reason: str,
    ) -> None:
        """Close strangle immediately when 2.0x premium hard stop is hit."""
        logger.warning(f"[CYCLE STOP LOSS] Reason: {reason} | 2.0x premium exceeded! Flattening...")

        self.client.close_option_leg(self.active_put["symbol"], BASE_ORDER_QTY, price=put_mark, reason=reason)
        self.client.close_option_leg(self.active_call["symbol"], BASE_ORDER_QTY, price=call_mark, reason=reason)
        ddh_pnl = self.ddh.close_all_hedges(spot_price)

        put_gain = (self.active_put["entry_price"] - put_mark) * BASE_ORDER_QTY
        call_gain = (self.active_call["entry_price"] - call_mark) * BASE_ORDER_QTY
        gross_pnl = put_gain + call_gain
        net_pnl = gross_pnl + ddh_pnl

        self.current_capital += net_pnl
        self.total_realized_pnl += net_pnl
        self.total_cycles_completed += 1
        self.loss_cycles += 1

        self._record_trade_audit("LOSS", reason, gross_pnl, ddh_pnl, put_mark, call_mark)

        self.last_status_message = (
            f"Cycle {self.cycle_id} SL HIT: Net PnL: ${net_pnl:.2f} ({reason}) | "
            f"Capital: ${self.current_capital:.2f}"
        )
        logger.warning(self.last_status_message)

        self.cycle_id += 1
        self.active_put = None
        self.active_call = None
        self.state = "STATE_0_SCANNING"

    def _trigger_circuit_breaker(self, spot_price: float, reason: str) -> None:
        """Trigger emergency circuit breaker and halt bot."""
        logger.error(f"[CIRCUIT BREAKER TRIGGERED] {reason}")
        if self.active_put:
            self.client.close_option_leg(self.active_put["symbol"], BASE_ORDER_QTY, reason="CIRCUIT_BREAKER")
        if self.active_call:
            self.client.close_option_leg(self.active_call["symbol"], BASE_ORDER_QTY, reason="CIRCUIT_BREAKER")
        self.ddh.close_all_hedges(spot_price)

        self.circuit_breaker_active = True
        self.circuit_breaker_until = time.time() + (3600.0 * 4.0)  # 4-hour pause
        self.state = "STATE_6_CIRCUIT_BREAKER"
        self.last_status_message = f"CIRCUIT BREAKER: {reason}. Paused until 4 hours."
        self._save_state()

    # ── Telemetry Payload Generator ──────────────────────────────────────────

    def get_telemetry(self, spot_price: float) -> Dict[str, Any]:
        """Generate comprehensive real-time telemetry dictionary for Port 8083."""
        greeks = GreeksAggregator.calculate_portfolio_greeks(
            put_leg=self.active_put,
            call_leg=self.active_call,
            perp_qty=self.ddh.current_perp_position,
        )

        floating_pnl = 0.0
        decay_pct = 0.0
        if self.active_put and self.active_call and self.initial_net_premium > 0:
            put_val = self.active_put.get("mark_price", 0.0) * BASE_ORDER_QTY
            call_val = self.active_call.get("mark_price", 0.0) * BASE_ORDER_QTY
            current_stew = put_val + call_val
            floating_pnl = (self.initial_net_premium - current_stew) + (
                self.ddh.current_perp_position * (spot_price - self.ddh.perp_avg_entry_price)
                if abs(self.ddh.current_perp_position) > 0 and self.ddh.perp_avg_entry_price > 0 else 0.0
            )
            decay_pct = ((self.initial_net_premium - current_stew) / self.initial_net_premium) * 100.0

        win_rate = (self.profitable_cycles / self.total_cycles_completed * 100.0) if self.total_cycles_completed > 0 else 0.0
        drawdown_pct = ((self.peak_capital - self.current_capital) / self.peak_capital) * 100.0 if self.peak_capital > 0 else 0.0

        return {
            "status": "RUNNING",
            "state": self.state,
            "timestamp": time.time(),
            "timestamp_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "cycle_id": self.cycle_id,
            "allocated_capital": self.allocated_capital,
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "total_realized_pnl": self.total_realized_pnl,
            "total_theta_harvested": self.total_theta_harvested,
            "floating_pnl": floating_pnl,
            "decay_pct": decay_pct,
            "win_rate_pct": win_rate,
            "total_cycles_completed": self.total_cycles_completed,
            "profitable_cycles": self.profitable_cycles,
            "loss_cycles": self.loss_cycles,
            "drawdown_pct": drawdown_pct,
            "circuit_breaker_active": self.circuit_breaker_active,
            "dry_run": getattr(self.client, "dry_run", True),
            "last_status_message": self.last_status_message,
            "spot_price": spot_price,
            "greeks": greeks,
            "active_put": self.active_put,
            "active_call": self.active_call,
            "breakevens": self.breakevens,
            "cone": self.cone,
            "ddh": {
                "perp_position": self.ddh.current_perp_position,
                "rebalance_count": self.ddh.total_rebalance_count,
                "realized_pnl": self.ddh.ddh_realized_pnl,
            },
        }
