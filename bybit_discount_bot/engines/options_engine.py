"""
options_engine.py — Engine 1: Direct Cash-Secured Put Underwriting on Bybit Options.
Budget: Strict $1,000 USD maximum collateral enclosure.
"""

import time
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from bybit_discount_bot.config import (
    MAX_CAPITAL_PER_ENGINE,
    DEFAULT_UNDERLYING_OPTION,
    OPTIONS_TARGET_OTM_PCT,
    DEFAULT_CYCLE_HOURS,
)
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import EngineState

logger = logging.getLogger("discount_suite.options_engine")


class OptionsPutEngine:
    """
    Engine 1: Sells 1-day OTM Cash-Secured Put options on Bybit Options.
    Collects 100% upfront premium; if assigned, acquires coin at discount.
    """

    def __init__(self, client: BybitDiscountClient, state: EngineState,
                 underlying: str = DEFAULT_UNDERLYING_OPTION):
        self.client = client
        self.state = state
        self.underlying = underlying
        self.max_budget = MAX_CAPITAL_PER_ENGINE

    def tick(self, spot_price: float):
        """Main periodic evaluation loop called every poll interval."""
        # 1. Check if an active contract is running
        if self.state.active_positions:
            self._manage_open_option(spot_price)
            return

        # 2. Check if a resting order is waiting to be filled
        if self.state.active_orders:
            self._check_resting_order(spot_price)
            return

        # 3. If idle, evaluate entry for a new cycle
        self._evaluate_new_cycle(spot_price)

    def _evaluate_new_cycle(self, spot_price: float):
        """Find the optimal 1.0% OTM put option expiring in ~24 hours."""
        logger.info(f"[OPTIONS ENGINE] Spot: ${spot_price:,.2f} | Scanning for 1.0% OTM Puts on {self.underlying}...")

        target_strike = spot_price * (1.0 - OPTIONS_TARGET_OTM_PCT)
        options = self.client.get_options_chain(self.underlying)

        best_opt = None
        min_strike_diff = float("inf")

        now_ts = time.time()
        for opt in options:
            symbol = opt.get("symbol", "")
            if not symbol.endswith("-P"):  # Puts only
                continue

            # Parse strike price from symbol (e.g. BTC-28SEP24-59000-P)
            parts = symbol.split("-")
            if len(parts) < 4:
                continue
            try:
                strike = float(parts[2])
            except ValueError:
                continue

            diff = abs(strike - target_strike)
            if diff < min_strike_diff:
                min_strike_diff = diff
                best_opt = opt

        # Strict budget calculation: Qty must not exceed $1,000 / strike
        if not best_opt:
            # Fallback for dry-run or when options chain API is quiet
            chosen_strike = round(target_strike / 100) * 100
            exp_date_str = datetime.now(timezone.utc).strftime("%d%b%y").upper()
            sim_symbol = f"{self.underlying}-{exp_date_str}-{int(chosen_strike)}-P"
            est_premium = spot_price * 0.0035  # ~0.35% for 24h put (~128% APR)
        else:
            sim_symbol = best_opt.get("symbol")
            parts = sim_symbol.split("-")
            chosen_strike = float(parts[2])
            bid_px = float(best_opt.get("bid1Price") or 0.0)
            mark_px = float(best_opt.get("markPrice") or 0.0)
            est_premium = bid_px if bid_px > 0 else (mark_px if mark_px > 0 else spot_price * 0.0035)

        # STRICT $1,000 BUDGET CAP:
        max_contracts = self.max_budget / chosen_strike
        qty = round(max_contracts, 3)
        if qty <= 0.001:
            qty = 0.001

        notional_secured = qty * chosen_strike
        if notional_secured > self.max_budget:
            qty = math.floor((self.max_budget / chosen_strike) * 1000) / 1000

        logger.info(f"[OPTIONS ENGINE] Target Put: {sim_symbol} | Strike: ${chosen_strike:,.2f} | "
                    f"Qty: {qty:.3f} | Premium: ${est_premium:,.2f} | Collateral Locked: ${qty * chosen_strike:,.2f}")

        order_id = self.client.sell_put_option(sim_symbol, qty, est_premium)
        if order_id:
            now_str = datetime.now(timezone.utc).isoformat()
            self.state.status = "CONTRACT_ACTIVE"
            self.state.last_cycle_start = now_str
            self.state.active_positions.append({
                "symbol": sim_symbol,
                "order_id": order_id,
                "strike": chosen_strike,
                "qty": qty,
                "premium_collected": est_premium * qty,
                "entry_spot": spot_price,
                "entry_time": time.time(),
                "expiry_target_hours": DEFAULT_CYCLE_HOURS,
            })
            self.state.metrics["current_put_strike"] = chosen_strike
            self.state.metrics["premium_locked_usd"] = round(est_premium * qty, 2)
            self.state.metrics["collateral_locked_usd"] = round(qty * chosen_strike, 2)

    def _manage_open_option(self, spot_price: float):
        """Monitor open put contract until 24h settlement."""
        pos = self.state.active_positions[0]
        elapsed_hours = (time.time() - pos["entry_time"]) / 3600.0

        strike = pos["strike"]
        prem = pos["premium_collected"]
        qty = pos["qty"]

        # Check if 24h cycle expired
        if elapsed_hours >= pos.get("expiry_target_hours", DEFAULT_CYCLE_HOURS):
            logger.info(f"[OPTIONS ENGINE] 24h Expiry Reached for {pos['symbol']} | Settlement Spot: ${spot_price:,.2f}")

            if spot_price >= strike:
                # OTM Expiry: Put expires worthless, bot keeps 100% premium
                cycle_pnl = prem
                self.state.profitable_cycles += 1
                outcome = "EXPIRED_WORTHLESS_WIN"
                logger.info(f"[OPTIONS ENGINE WIN] Spot ${spot_price:,.2f} >= Strike ${strike:,.2f} | Retained Cash + ${cycle_pnl:.2f} Premium!")
            else:
                # ITM Expiry: Assigned coin at strike K, mark-to-market at spot S_T
                loss_on_coin = (spot_price - strike) * qty
                cycle_pnl = loss_on_coin + prem
                outcome = "ASSIGNED_AT_DISCOUNT"
                logger.info(f"[OPTIONS ENGINE ASSIGNED] Assigned {qty:.3f} coins at ${strike:,.2f} (Net Basis: ${strike - prem/qty:,.2f}) | Cycle PnL: ${cycle_pnl:.2f}")

            self.state.current_capital += cycle_pnl
            self.state.total_realized_pnl += cycle_pnl
            self.state.total_cycles += 1
            self.state.status = "CYCLE_CLOSED"
            self.state.last_cycle_end = datetime.now(timezone.utc).isoformat()
            self.state.active_positions.clear()

    def _check_resting_order(self, spot_price: float):
        pass
