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
        best_score = float("inf")

        now_ts = time.time()
        for opt in options:
            symbol = opt.get("symbol", "")
            # Bybit USDT/USDC options: e.g. BTC-16SEP26-75000-P-USDT or BTC-28SEP24-60000-P
            if not ("-P-" in symbol or symbol.endswith("-P")):
                continue

            parts = symbol.split("-")
            if len(parts) < 4:
                continue
            try:
                strike = float(parts[2])
            except ValueError:
                continue

            # Check days to expiration
            exp_date_str = parts[1]
            try:
                exp_dt = datetime.strptime(exp_date_str, "%d%b%y").replace(tzinfo=timezone.utc)
                dte_days = (exp_dt.timestamp() - now_ts) / 86400.0
            except Exception:
                dte_days = 1.0

            # Prefer options expiring within 1 to 7 days, strike below spot (OTM Put)
            if dte_days < 0:
                continue
            if strike > spot_price:  # Avoid ITM puts for discount buy
                continue

            strike_diff = abs(strike - target_strike)
            # Score balances closest strike to target discount and nearest expiry
            score = strike_diff + (max(0, dte_days - 1.0) * 200)
            if score < best_score:
                best_score = score
                best_opt = opt

        if best_opt:
            sim_symbol = best_opt.get("symbol")
            parts = sim_symbol.split("-")
            chosen_strike = float(parts[2])
            bid_px = float(best_opt.get("bid1Price") or 0.0)
            mark_px = float(best_opt.get("markPrice") or 0.0)
            est_premium = bid_px if bid_px > 0 else (mark_px if mark_px > 0 else spot_price * 0.0035)

            # Budget calculation: Qty must not exceed $1,000 / strike (lot step 0.01)
            max_contracts = self.max_budget / chosen_strike
            qty = max(0.01, math.floor(max_contracts * 100) / 100)
            if qty * chosen_strike > self.max_budget:
                qty = max(0.01, math.floor((self.max_budget / chosen_strike) * 100) / 100)

            logger.info(f"[OPTIONS ENGINE] Selected Listed Put: {sim_symbol} | Strike: ${chosen_strike:,.2f} | "
                        f"Qty: {qty:.2f} | Premium: ${est_premium:,.2f} | Collateral Locked: ${qty * chosen_strike:,.2f}")
            order_id = self.client.sell_put_option(sim_symbol, qty, est_premium)
        else:
            # Fallback when options chain has no active matching listed contracts on testnet
            logger.info(f"[OPTIONS ENGINE] No active listed puts found on Bybit matching criteria. Running simulated underwriting mode for this cycle.")
            chosen_strike = round(target_strike / 100) * 100
            exp_date_str = datetime.now(timezone.utc).strftime("%d%b%y").upper()
            sim_symbol = f"{self.underlying}-{exp_date_str}-{int(chosen_strike)}-P-USDT"
            est_premium = spot_price * 0.0035
            qty = max(0.01, round(self.max_budget / chosen_strike, 2))
            order_id = f"sim_opt_synth_{int(time.time()*1000)}"

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
