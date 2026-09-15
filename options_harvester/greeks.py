"""
greeks.py — Black-Scholes-Merton mathematical engine and Bybit native Greek aggregator.
Calculates individual leg and portfolio-level Delta, Gamma, Theta, Vega, and Strangle Breakevens.
"""

import math
from typing import Dict, Any, Tuple, Optional


def norm_cdf(x: float) -> float:
    """Cumulative distribution function for standard normal distribution."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def norm_pdf(x: float) -> float:
    """Probability density function for standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_bsm_greeks(
    spot: float,
    strike: float,
    dte_years: float,
    iv: float,
    risk_free_rate: float = 0.03,
    is_call: bool = True,
) -> Dict[str, float]:
    """
    Calculate Black-Scholes-Merton theoretical price and Greeks.
    Used as an analytical fallback and sanity check against Bybit ticker values.
    """
    if dte_years <= 0.00001 or iv <= 0.001 or spot <= 0.0 or strike <= 0.0:
        intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
        delta = 1.0 if (is_call and spot >= strike) else (-1.0 if (not is_call and spot <= strike) else 0.0)
        return {
            "price": intrinsic,
            "delta": delta,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
        }

    sigma_sqrt_t = iv * math.sqrt(dte_years)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * iv * iv) * dte_years) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t

    discount = math.exp(-risk_free_rate * dte_years)

    if is_call:
        price = spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
        delta = norm_cdf(d1)
        theta = (
            -(spot * norm_pdf(d1) * iv) / (2.0 * math.sqrt(dte_years))
            - risk_free_rate * strike * discount * norm_cdf(d2)
        ) / 365.0
    else:
        price = strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1.0
        theta = (
            -(spot * norm_pdf(d1) * iv) / (2.0 * math.sqrt(dte_years))
            + risk_free_rate * strike * discount * norm_cdf(-d2)
        ) / 365.0

    gamma = norm_pdf(d1) / (spot * sigma_sqrt_t)
    vega = (spot * math.sqrt(dte_years) * norm_pdf(d1)) / 100.0  # Per 1% IV change

    return {
        "price": max(0.0, price),
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
    }


class GreeksAggregator:
    """
    Aggregates Greeks across active short strangle legs and linear perpetual hedges.
    Tracks net portfolio directional exposure and breakevens.
    """

    @staticmethod
    def calculate_portfolio_greeks(
        put_leg: Optional[Dict[str, Any]],
        call_leg: Optional[Dict[str, Any]],
        perp_qty: float = 0.0,
    ) -> Dict[str, float]:
        """
        Calculate total portfolio Greeks.
        Note on short strangle signs:
          - Short Call: position delta is - (qty * call_delta) [since call_delta > 0, short call delta is negative]
          - Short Put: position delta is - (qty * put_delta) [since put_delta < 0, short put delta is positive]
          - Long Perp: + perp_qty delta
          - Short Perp: - perp_qty delta
        """
        net_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0
        net_vega = 0.0

        if put_leg and put_leg.get("active", False):
            qty = float(put_leg.get("qty", 0.0))
            raw_delta = float(put_leg.get("delta", -0.15))
            raw_gamma = float(put_leg.get("gamma", 0.0))
            raw_theta = float(put_leg.get("theta", 0.0))
            raw_vega = float(put_leg.get("vega", 0.0))

            # Short position inverts delta, gamma, theta, vega
            # Short Put has positive delta contribution: - (qty * raw_delta) where raw_delta < 0
            net_delta += -1.0 * qty * raw_delta
            net_gamma += -1.0 * qty * raw_gamma
            # Theta is income for short option: raw_theta is negative decay from buyer's perspective
            net_theta += qty * abs(raw_theta)
            net_vega += -1.0 * qty * raw_vega

        if call_leg and call_leg.get("active", False):
            qty = float(call_leg.get("qty", 0.0))
            raw_delta = float(call_leg.get("delta", 0.15))
            raw_gamma = float(call_leg.get("gamma", 0.0))
            raw_theta = float(call_leg.get("theta", 0.0))
            raw_vega = float(call_leg.get("vega", 0.0))

            # Short Call has negative delta contribution: - (qty * raw_delta) where raw_delta > 0
            net_delta += -1.0 * qty * raw_delta
            net_gamma += -1.0 * qty * raw_gamma
            net_theta += qty * abs(raw_theta)
            net_vega += -1.0 * qty * raw_vega

        # Add Linear Perpetual hedge delta (1 perp = 1.0 delta)
        net_delta += perp_qty

        # Normalize delta per base contract (e.g. per 0.01 BTC base)
        base_qty = max(
            float(put_leg.get("qty", 0.01)) if put_leg else 0.01,
            float(call_leg.get("qty", 0.01)) if call_leg else 0.01,
        )
        normalized_delta = net_delta / base_qty if base_qty > 0 else net_delta

        return {
            "net_delta_btc": net_delta,
            "normalized_net_delta": normalized_delta,
            "net_gamma": net_gamma,
            "net_theta_usd_per_day": net_theta,
            "net_vega": net_vega,
            "perp_qty": perp_qty,
        }

    @staticmethod
    def calculate_breakevens(
        put_strike: float,
        call_strike: float,
        put_premium: float,
        call_premium: float,
        spot_price: float,
    ) -> Dict[str, float]:
        """
        Calculate lower and upper breakeven thresholds for the Short Strangle.
        Total collected premium expands the cushion beyond the strikes.
        """
        total_premium = put_premium + call_premium
        lower_breakeven = put_strike - total_premium
        upper_breakeven = call_strike + total_premium

        pct_to_lower_be = ((spot_price - lower_breakeven) / spot_price) * 100.0 if spot_price > 0 else 0.0
        pct_to_upper_be = ((upper_breakeven - spot_price) / spot_price) * 100.0 if spot_price > 0 else 0.0

        return {
            "total_premium": total_premium,
            "put_strike": put_strike,
            "call_strike": call_strike,
            "lower_breakeven": lower_breakeven,
            "upper_breakeven": upper_breakeven,
            "pct_to_lower_be": pct_to_lower_be,
            "pct_to_upper_be": pct_to_upper_be,
            "range_width_usd": upper_breakeven - lower_breakeven,
            "range_width_pct": ((upper_breakeven - lower_breakeven) / spot_price) * 100.0 if spot_price > 0 else 0.0,
        }

    @staticmethod
    def calculate_expected_move(
        spot: float,
        atm_iv: float,
        hours_to_expiry: float,
    ) -> Dict[str, float]:
        """
        Calculate expected move cone based on ATM Implied Volatility.
        sigma_1 = S * IV * sqrt(T/365)
        """
        t_years = max(0.0001, hours_to_expiry / (24.0 * 365.0))
        one_sigma = spot * atm_iv * math.sqrt(t_years)
        one_half_sigma = 1.5 * one_sigma

        return {
            "one_sigma_move": one_sigma,
            "one_sigma_lower": spot - one_sigma,
            "one_sigma_upper": spot + one_sigma,
            "one_half_sigma_lower": spot - one_half_sigma,
            "one_half_sigma_upper": spot + one_half_sigma,
            "one_sigma_pct": (one_sigma / spot) * 100.0 if spot > 0 else 0.0,
        }
