"""
ddh_engine.py — Dynamic Delta Hedging (DDH) quantitative rebalancing engine.
Monitors net delta drift against the ±0.10 tolerance band and executes micro-rebalancing.
"""

import time
import logging
from typing import Dict, Any, Optional, Tuple

from options_harvester.config import (
    DDH_TOLERANCE_BAND,
    DDH_MIN_REBALANCE_QTY,
    DDH_COOLDOWN_SECONDS,
    ENABLE_PERP_DDH,
    USE_DEFENSIVE_ROLL,
    BASE_PERP_SYMBOL,
)
from options_harvester.greeks import GreeksAggregator

logger = logging.getLogger("options_harvester.ddh")


class DDHEngine:
    """Manages continuous delta monitoring and executes micro-hedging / defensive rolling."""

    def __init__(self, client):
        self.client = client
        self.last_rebalance_time = 0.0
        self.total_rebalance_count = 0
        self.ddh_realized_pnl = 0.0
        self.current_perp_position = 0.0  # + for Long perp, - for Short perp
        self.perp_avg_entry_price = 0.0

    def evaluate_and_rebalance(
        self,
        put_leg: Dict[str, Any],
        call_leg: Dict[str, Any],
        current_spot: float,
    ) -> Dict[str, Any]:
        """
        Evaluate portfolio delta and execute rebalance if drift exceeds tolerance.
        Returns rebalance decision and updated Greeks.
        """
        now = time.time()
        greeks = GreeksAggregator.calculate_portfolio_greeks(
            put_leg=put_leg,
            call_leg=call_leg,
            perp_qty=self.current_perp_position,
        )

        norm_delta = greeks["normalized_net_delta"]
        abs_drift = abs(norm_delta)

        action_taken = "NONE"
        order_details: Optional[Dict[str, Any]] = None

        # Check if tolerance band is breached
        if abs_drift > DDH_TOLERANCE_BAND:
            time_since_last = now - self.last_rebalance_time
            if time_since_last >= DDH_COOLDOWN_SECONDS:
                logger.info(
                    f"[DDH DRIFT BREACH] Normalized Net Delta: {norm_delta:+.4f} (Threshold: ±{DDH_TOLERANCE_BAND:.2f})"
                )

                if ENABLE_PERP_DDH:
                    # Calculate required perp adjustment to restore Delta to 0.00
                    base_qty = max(float(put_leg.get("qty", 0.01)), float(call_leg.get("qty", 0.01)))
                    # Target delta in BTC = - (normalized delta * base_qty)
                    needed_perp_adj = -1.0 * greeks["net_delta_btc"]

                    # Round to lot step 0.001
                    rounded_adj = round(needed_perp_adj, 3)

                    if abs(rounded_adj) >= DDH_MIN_REBALANCE_QTY:
                        side = "Buy" if rounded_adj > 0 else "Sell"
                        qty = abs(rounded_adj)

                        logger.info(
                            f"[DDH REBALANCE] Executing {side.upper()} {qty:.3f} {BASE_PERP_SYMBOL} to reset Delta to 0.00"
                        )
                        order_id = self.client.place_ddh_perp(
                            symbol=BASE_PERP_SYMBOL,
                            side=side,
                            qty=qty,
                        )

                        if order_id:
                            action_taken = f"PERP_HEDGE_{side.upper()}"
                            self.last_rebalance_time = now
                            self.total_rebalance_count += 1

                            # Update perp position tracking
                            prev_pos = self.current_perp_position
                            if side == "Buy":
                                self.current_perp_position += qty
                            else:
                                self.current_perp_position -= qty

                            self.perp_avg_entry_price = current_spot
                            order_details = {
                                "order_id": order_id,
                                "side": side,
                                "qty": qty,
                                "spot_price": current_spot,
                                "prev_perp_pos": prev_pos,
                                "new_perp_pos": self.current_perp_position,
                                "timestamp": now,
                            }
            else:
                action_taken = "COOLDOWN"

        # Calculate floating PnL on perp hedge
        perp_pnl = 0.0
        if abs(self.current_perp_position) > 0 and self.perp_avg_entry_price > 0:
            perp_pnl = self.current_perp_position * (current_spot - self.perp_avg_entry_price)

        return {
            "action": action_taken,
            "net_delta_btc": greeks["net_delta_btc"],
            "normalized_net_delta": norm_delta,
            "within_tolerance": abs_drift <= DDH_TOLERANCE_BAND,
            "tolerance_threshold": DDH_TOLERANCE_BAND,
            "drift_magnitude": abs_drift,
            "current_perp_qty": self.current_perp_position,
            "perp_floating_pnl": perp_pnl,
            "total_rebalance_count": self.total_rebalance_count,
            "order_details": order_details,
            "greeks": greeks,
        }

    def close_all_hedges(self, current_spot: float) -> float:
        """Close any open perpetual hedges and return realized PnL."""
        if abs(self.current_perp_position) < 0.0001:
            return 0.0

        side_to_close = "Sell" if self.current_perp_position > 0 else "Buy"
        qty_to_close = abs(self.current_perp_position)

        logger.info(
            f"[DDH FLATTEN] Closing {qty_to_close:.3f} {BASE_PERP_SYMBOL} ({side_to_close})"
        )
        success = self.client.close_ddh_perp(
            symbol=BASE_PERP_SYMBOL,
            side=side_to_close,
            qty=qty_to_close,
        )

        pnl = self.current_perp_position * (current_spot - self.perp_avg_entry_price)
        self.ddh_realized_pnl += pnl
        self.current_perp_position = 0.0
        self.perp_avg_entry_price = 0.0

        return pnl
