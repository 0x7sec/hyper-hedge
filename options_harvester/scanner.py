"""
scanner.py — Real-time Bybit option chain surface scanner and strangle strike selector.
Finds the optimal ~15-delta Put and Call pair within 18h-72h DTE.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from options_harvester.config import (
    MIN_DTE_HOURS,
    MAX_DTE_HOURS,
    TARGET_PUT_DELTA_MIN,
    TARGET_PUT_DELTA_MAX,
    IDEAL_PUT_DELTA,
    TARGET_CALL_DELTA_MIN,
    TARGET_CALL_DELTA_MAX,
    IDEAL_CALL_DELTA,
    MIN_IV_RANK,
)
from options_harvester.greeks import GreeksAggregator

logger = logging.getLogger("options_harvester.scanner")

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_expiry_from_symbol(symbol: str) -> Optional[datetime]:
    """
    Parse UTC expiration datetime from Bybit option symbol.
    Example: BTC-17SEP26-77750-C-USDT -> 2026-09-17 08:00:00 UTC.
    """
    try:
        parts = symbol.split("-")
        if len(parts) < 4:
            return None
        date_str = parts[1]  # e.g., '17SEP26' or '2OCT26'

        # Extract day, month, year
        day_str = ""
        month_str = ""
        year_str = ""

        idx = 0
        while idx < len(date_str) and date_str[idx].isdigit():
            day_str += date_str[idx]
            idx += 1

        while idx < len(date_str) and date_str[idx].isalpha():
            month_str += date_str[idx]
            idx += 1

        year_str = date_str[idx:]

        day = int(day_str)
        month = MONTH_MAP.get(month_str.upper(), 1)
        # Year e.g. '26' -> 2026
        year = 2000 + int(year_str) if len(year_str) <= 2 else int(year_str)

        # Bybit options expire at 08:00:00 UTC
        return datetime(year, month, day, 8, 0, 0, tzinfo=timezone.utc)
    except Exception as e:
        logger.debug(f"Error parsing expiry from symbol {symbol}: {e}")
        return None


def get_hours_to_expiry(expiry_dt: datetime) -> float:
    """Compute hours remaining until expiration datetime."""
    now = datetime.now(timezone.utc)
    delta = expiry_dt - now
    return max(0.0, delta.total_seconds() / 3600.0)


class StrangleScanner:
    """Scans Bybit option chains to select optimal delta-neutral strangle candidate."""

    def __init__(self, client):
        self.client = client

    def scan_optimal_strangle(
        self,
        base_coin: str = "BTC",
        spot_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Scan full options chain and identify the best Short Strangle candidate.
        Returns candidate dictionary or None if no acceptable pair is found.
        """
        if spot_price is None or spot_price <= 0:
            spot_price = self.client.get_perp_price("BTCUSDT")

        chain = self.client.get_options_chain(base_coin=base_coin)
        if not chain:
            logger.warning(f"Options chain for {base_coin} returned empty.")
            return None

        # Group tickers by parsed expiry date
        now = datetime.now(timezone.utc)
        expiries: Dict[str, List[Dict[str, Any]]] = {}

        for item in chain:
            sym = item.get("symbol", "")
            exp_dt = parse_expiry_from_symbol(sym)
            if not exp_dt:
                continue

            dte_hours = get_hours_to_expiry(exp_dt)
            if MIN_DTE_HOURS <= dte_hours <= MAX_DTE_HOURS:
                exp_key = exp_dt.strftime("%Y-%m-%d %H:%M")
                expiries.setdefault(exp_key, []).append({**item, "_dte_hours": dte_hours, "_exp_dt": exp_dt})

        if not expiries:
            logger.info(f"No active options within {MIN_DTE_HOURS}h - {MAX_DTE_HOURS}h DTE window.")
            return None

        # Evaluate candidates per expiry
        best_candidate: Optional[Dict[str, Any]] = None
        min_net_delta_diff = 999.0

        for exp_key, tickers in sorted(expiries.items()):
            puts: List[Dict[str, Any]] = []
            calls: List[Dict[str, Any]] = []

            for t in tickers:
                sym = t.get("symbol", "")
                is_put = "-P-" in sym or sym.endswith("-P")
                is_call = "-C-" in sym or sym.endswith("-C")

                try:
                    delta = float(t.get("delta") or 0.0)
                    mark_px = float(t.get("markPrice") or 0.0)
                    bid_px = float(t.get("bid1Price") or 0.0)
                    ask_px = float(t.get("ask1Price") or 0.0)
                    iv = float(t.get("markIv") or 0.0)

                    # Option must have positive mark price
                    if mark_px <= 0:
                        continue

                    # Filter Put within delta window
                    if is_put and (TARGET_PUT_DELTA_MIN <= delta <= TARGET_PUT_DELTA_MAX):
                        puts.append({**t, "_delta": delta, "_mark": mark_px, "_bid": bid_px, "_ask": ask_px, "_iv": iv})

                    # Filter Call within delta window
                    if is_call and (TARGET_CALL_DELTA_MIN <= delta <= TARGET_CALL_DELTA_MAX):
                        calls.append({**t, "_delta": delta, "_mark": mark_px, "_bid": bid_px, "_ask": ask_px, "_iv": iv})
                except Exception:
                    continue

            if not puts or not calls:
                continue

            # Find best Put and Call pair that minimizes net delta drift
            for p in puts:
                for c in calls:
                    # Strangle initial net delta = Call Delta + Put Delta (e.g. +0.15 + (-0.15) = 0.00)
                    net_delta = c["_delta"] + p["_delta"]
                    abs_drift = abs(net_delta)

                    # Extract strikes
                    try:
                        p_strike = float(p["symbol"].split("-")[2])
                        c_strike = float(c["symbol"].split("-")[2])
                    except Exception:
                        continue

                    if p_strike >= c_strike:
                        continue  # Must be an OTM Strangle (Put strike < Call strike)

                    # Score candidate: prioritize delta balance and close-to-ideal delta
                    put_dev = abs(p["_delta"] - IDEAL_PUT_DELTA)
                    call_dev = abs(c["_delta"] - IDEAL_CALL_DELTA)
                    total_score = abs_drift * 2.0 + put_dev + call_dev

                    if total_score < min_net_delta_diff:
                        min_net_delta_diff = total_score
                        p_bid = p["_bid"] if p["_bid"] > 0 else p["_mark"]
                        c_bid = c["_bid"] if c["_bid"] > 0 else c["_mark"]

                        # Estimate entry premium
                        p_entry_px = round(p_bid / 5.0) * 5.0
                        c_entry_px = round(c_bid / 5.0) * 5.0

                        dte_h = p["_dte_hours"]
                        avg_iv = (p["_iv"] + c["_iv"]) / 2.0

                        be = GreeksAggregator.calculate_breakevens(
                            put_strike=p_strike,
                            call_strike=c_strike,
                            put_premium=p_entry_px,
                            call_premium=c_entry_px,
                            spot_price=spot_price,
                        )

                        cone = GreeksAggregator.calculate_expected_move(
                            spot=spot_price,
                            atm_iv=avg_iv,
                            hours_to_expiry=dte_h,
                        )

                        pop = GreeksAggregator.calculate_probability_of_profit(
                            put_delta=p["_delta"],
                            call_delta=c["_delta"],
                            has_premium_buffer=True,
                        )

                        best_candidate = {
                            "expiry_str": exp_key,
                            "dte_hours": dte_h,
                            "expiry_dt": p["_exp_dt"],
                            "spot_price": spot_price,
                            "atm_iv": avg_iv,
                            "net_delta_initial": net_delta,
                            "probability_of_profit": pop,
                            "breakevens": be,
                            "cone": cone,
                            "put": {
                                "symbol": p["symbol"],
                                "strike": p_strike,
                                "delta": p["_delta"],
                                "gamma": float(p.get("gamma") or 0.0),
                                "theta": float(p.get("theta") or 0.0),
                                "vega": float(p.get("vega") or 0.0),
                                "mark_price": p["_mark"],
                                "entry_price": p_entry_px,
                                "bid": p["_bid"],
                                "ask": p["_ask"],
                                "iv": p["_iv"],
                            },
                            "call": {
                                "symbol": c["symbol"],
                                "strike": c_strike,
                                "delta": c["_delta"],
                                "gamma": float(c.get("gamma") or 0.0),
                                "theta": float(c.get("theta") or 0.0),
                                "vega": float(c.get("vega") or 0.0),
                                "mark_price": c["_mark"],
                                "entry_price": c_entry_px,
                                "bid": c["_bid"],
                                "ask": c["_ask"],
                                "iv": c["_iv"],
                            },
                        }

        return best_candidate
