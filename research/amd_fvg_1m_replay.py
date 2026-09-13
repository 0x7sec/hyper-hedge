#!/usr/bin/env python3
"""
Accumulation, Manipulation, FVG & Distribution (AMD) Quantitative Replay Engine
Tests ICT Power of 3 (AMD) + Fair Value Gap (FVG) Strategy on 2 Full Years of Authentic Bybit 1-Minute Data.

Mechanics:
1. Accumulation (A): K-bar range consolidation (BSL = Range High, SSL = Range Low).
2. Manipulation (M): Liquidity Sweep of BSL/SSL (wick pierces boundary, closes back inside).
3. Displacement & FVG: Strong reversal candle leaves a 3-bar Fair Value Gap.
4. Distribution Entry (D): Post-Only Maker Limit Order at FVG Retest / Consequent Encroachment (50%).
5. Stop-Loss: Structural stop beyond the manipulation sweep wick.
6. Take-Profit: Target opposite liquidity pool or 2.5x - 3.5x Risk-to-Reward.

Evaluates on:
- 15-Minute Resolution AMD + FVG
- 60-Minute Resolution AMD + FVG
- Across all 7 pairs (BTC, ETH, SOL, AVAX, XMR, LINK, DOGE) over 2.0 full years (7.35M bars).
- Fee Tiers: Bybit VIP0, Hyperliquid HYPE (-20%), and MEXC Futures MX (-20%).
"""

import os
import sys
import gc
import time
import pickle
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

NOTIONAL = 1000.0

ASSETS = ["btcusdt", "ethusdt", "solusdt", "avaxusdt", "xmrusdt", "linkusdt", "dogeusdt"]

FEE_TIERS = {
    "Bybit VIP0": (0.00020, 0.00055),
    "Hyperliquid HYPE": (0.00015, 0.00036),
    "MEXC Futures MX": (0.00000, 0.00032),
}


# ==============================================================================
# FAST NUMPY UTILITIES
# ==============================================================================

def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    atr = np.full(n, np.nan)
    if n < period + 1:
        return atr
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def resample(ts_1m: np.ndarray, o_1m: np.ndarray, h_1m: np.ndarray, l_1m: np.ndarray, c_1m: np.ndarray, minutes: int):
    sec = minutes * 60
    keys = (ts_1m // sec).astype(np.int64)
    unique_keys, split_indices = np.unique(keys, return_index=True)
    n = len(unique_keys)
    o_m = np.zeros(n)
    h_m = np.zeros(n)
    l_m = np.zeros(n)
    c_m = np.zeros(n)
    ts_m = unique_keys * sec * 1000
    split_indices = np.append(split_indices, len(ts_1m))
    slices = []
    for i in range(n):
        s0, s1 = split_indices[i], split_indices[i + 1]
        slices.append((s0, s1))
        o_m[i] = o_1m[s0]
        h_m[i] = np.max(h_1m[s0:s1])
        l_m[i] = np.min(l_1m[s0:s1])
        c_m[i] = c_1m[s1 - 1]
    return ts_m, o_m, h_m, l_m, c_m, slices


class AMDTrade:
    def __init__(self, sym: str, tf: str, direction: str, ep: float, et: float, sl: float, tp: float, sweep_px: float):
        self.sym = sym
        self.tf = tf
        self.direction = direction
        self.ep = ep
        self.et = et
        self.sl = sl
        self.tp = tp
        self.sweep_px = sweep_px

        self.xp = 0.0
        self.xt = 0.0
        self.reason = ""
        self.gross_pnl = 0.0
        self.duration_min = 0.0


# ==============================================================================
# AMD + FVG DETECTOR & 1-MINUTE STREAMING SIMULATOR
# ==============================================================================

def simulate_amd_fvg(
    ts_1m: np.ndarray, o_1m: np.ndarray, h_1m: np.ndarray, l_1m: np.ndarray, c_1m: np.ndarray,
    sym: str, timeframe_minutes: int = 15, range_lookback: int = 16,
    max_range_pct: float = 0.035, rr_ratio: float = 2.5, max_wait_bars: int = 60
) -> List[AMDTrade]:
    """
    Simulates AMD + FVG strategy on macro timeframe with authentic 1m micro-candle replay.
    """
    ts_macro, o_macro, h_macro, l_macro, c_macro, slices_macro = resample(
        ts_1m, o_1m, h_1m, l_1m, c_1m, timeframe_minutes
    )
    n_macro = len(c_macro)
    if n_macro < range_lookback + 20:
        return []

    atr_macro = calc_atr(h_macro, l_macro, c_macro, 14)

    trades: List[AMDTrade] = []
    active_trade: Optional[AMDTrade] = None

    # Pending Limit Order at FVG Retest
    pending_side: Optional[str] = None
    pending_ep: float = 0.0
    pending_sl: float = 0.0
    pending_tp: float = 0.0
    pending_sweep: float = 0.0
    pending_expire_idx: int = 0

    # Sweep Tracker State
    # (sweep_dir, sweep_bar_idx, sweep_extreme_px, bsl_px, ssl_px)
    last_sweep: Optional[Tuple[str, int, float, float, float]] = None

    for i in range(range_lookback + 5, n_macro - 1):
        s0, s1 = slices_macro[i]

        # 1. Step through 1-minute bars within macro bar i
        for idx in range(s0, s1):
            ml, mh, mt = l_1m[idx], h_1m[idx], ts_1m[idx]

            # A. Check Pending FVG Limit Order Fill (Maker Entry)
            if active_trade is None and pending_side is not None:
                if idx > pending_expire_idx:
                    pending_side = None  # Order expired unfilled
                else:
                    if pending_side == "LONG" and ml <= pending_ep:
                        active_trade = AMDTrade(
                            sym, f"{timeframe_minutes}m", "LONG", pending_ep, mt,
                            pending_sl, pending_tp, pending_sweep
                        )
                        pending_side = None
                    elif pending_side == "SHORT" and mh >= pending_ep:
                        active_trade = AMDTrade(
                            sym, f"{timeframe_minutes}m", "SHORT", pending_ep, mt,
                            pending_sl, pending_tp, pending_sweep
                        )
                        pending_side = None

            # B. Manage Active Position (Pessimistic Stop-First)
            if active_trade:
                tr = active_trade
                ep = tr.ep

                if tr.direction == "LONG":
                    # Stop-loss check
                    if ml <= tr.sl:
                        tr.xp = tr.sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active_trade = None
                        continue

                    # Take-profit check (Maker limit exit)
                    if mh >= tr.tp:
                        tr.xp = tr.tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active_trade = None
                        continue

                elif tr.direction == "SHORT":
                    # Stop-loss check
                    if mh >= tr.sl:
                        tr.xp = tr.sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active_trade = None
                        continue

                    # Take-profit check (Maker limit exit)
                    if ml <= tr.tp:
                        tr.xp = tr.tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active_trade = None
                        continue

        # 2. Macro Bar Evaluation at Close of Bar i (AMD Detection)
        a_curr = atr_macro[i]
        if np.isnan(a_curr) or a_curr <= 0:
            continue

        c_now = c_macro[i]
        o_now = o_macro[i]
        h_now = h_macro[i]
        l_now = l_macro[i]

        # A. Accumulation Range Definition (Previous K bars: i - range_lookback to i - 1)
        w_h = h_macro[i - range_lookback:i]
        w_l = l_macro[i - range_lookback:i]
        bsl = np.max(w_h)  # Buy-Side Liquidity
        ssl = np.min(w_l)  # Sell-Side Liquidity
        rng_span = bsl - ssl

        # Check if accumulation was tight
        if rng_span / c_now <= max_range_pct:
            # B. Manipulation / Liquidity Sweep Detection on bar i
            # Bullish Sweep: Low penetrates SSL, but bar closes back inside range
            if l_now < ssl and c_now > ssl:
                last_sweep = ("BULLISH", i, l_now, bsl, ssl)
            # Bearish Sweep: High penetrates BSL, but bar closes back inside range
            elif h_now > bsl and c_now < bsl:
                last_sweep = ("BEARISH", i, h_now, bsl, ssl)

        # C. Displacement & Fair Value Gap (FVG) Check
        # Check if we have a recent sweep within the last 3 bars
        if last_sweep is not None and active_trade is None and pending_side is None:
            sw_dir, sw_idx, sw_ext, sw_bsl, sw_ssl = last_sweep
            bars_since_sweep = i - sw_idx

            if 1 <= bars_since_sweep <= 3:
                # Check 3-candle pattern: [i-2, i-1, i]
                c_prev2_h = h_macro[i - 2]
                c_prev2_l = l_macro[i - 2]
                c_disp_o = o_macro[i - 1]
                c_disp_c = c_macro[i - 1]
                c_disp_h = h_macro[i - 1]
                c_disp_l = l_macro[i - 1]

                # Displacement Body Quality Check: candle body >= 55% of candle range
                disp_range = c_disp_h - c_disp_l
                disp_body = abs(c_disp_c - c_disp_o)
                strong_disp = (disp_body / disp_range >= 0.55) if disp_range > 0 else False

                if sw_dir == "BULLISH" and strong_disp and c_disp_c > c_disp_o:
                    # Bullish FVG: High of bar i-2 is strictly less than Low of bar i
                    # The gap is between c_prev2_h and l_now
                    if c_prev2_h < l_now:
                        fvg_top = l_now
                        fvg_bottom = c_prev2_h
                        fvg_ce = 0.5 * (fvg_top + fvg_bottom)  # Consequent Encroachment (50% midpoint)

                        # Place Limit Order at FVG Top or CE
                        pending_side = "LONG"
                        pending_ep = fvg_top
                        pending_sl = sw_ext - 0.15 * a_curr  # Structural SL below sweep wick
                        risk = pending_ep - pending_sl
                        if risk > 0:
                            # Target: Opposite BSL or 2.5x RR, whichever is greater
                            target_rr = pending_ep + rr_ratio * risk
                            pending_tp = max(sw_bsl, target_rr)
                            pending_sweep = sw_ext
                            pending_expire_idx = s1 + (max_wait_bars * 60)  # Timeout
                            last_sweep = None

                elif sw_dir == "BEARISH" and strong_disp and c_disp_c < c_disp_o:
                    # Bearish FVG: Low of bar i-2 is strictly greater than High of bar i
                    # The gap is between c_prev2_l and h_now
                    if c_prev2_l > h_now:
                        fvg_bottom = h_now
                        fvg_top = c_prev2_l
                        fvg_ce = 0.5 * (fvg_top + fvg_bottom)

                        pending_side = "SHORT"
                        pending_ep = fvg_bottom
                        pending_sl = sw_ext + 0.15 * a_curr  # Structural SL above sweep wick
                        risk = pending_sl - pending_ep
                        if risk > 0:
                            target_rr = pending_ep - rr_ratio * risk
                            pending_tp = min(sw_ssl, target_rr)
                            pending_sweep = sw_ext
                            pending_expire_idx = s1 + (max_wait_bars * 60)
                            last_sweep = None

            elif bars_since_sweep > 3:
                last_sweep = None  # Sweep timed out without displacement

    return trades


# ==============================================================================
# PERFORMANCE EVALUATOR WITH MULTI-FEE TIERS
# ==============================================================================

def compute_fee_metrics(trades: List[AMDTrade], maker_fee: float, taker_fee: float) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0, "net": 0.0, "gross": 0.0, "fees": 0.0, "pf": 0.0, "wr": 0.0, "dd": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "avg_dur": 0.0}

    pnls = []
    fees = []
    gross = []
    durs = []

    for t in trades:
        gp = t.gross_pnl
        e_fee = maker_fee * NOTIONAL
        if t.reason == "TP":
            x_fee = maker_fee * (t.xp / t.ep * NOTIONAL) if t.ep > 0 else 0.0
        else:
            x_fee = taker_fee * (t.xp / t.ep * NOTIONAL) if t.ep > 0 else 0.0

        tot_fee = e_fee + x_fee
        pnls.append(gp - tot_fee)
        fees.append(tot_fee)
        gross.append(gp)
        durs.append(t.duration_min)

    pnls = np.array(pnls)
    gross = np.array(gross)
    fees = np.array(fees)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    w_sum = np.sum(wins) if len(wins) > 0 else 0.0
    l_sum = abs(np.sum(losses)) if len(losses) > 0 else 0.0
    pf = w_sum / l_sum if l_sum > 0 else (99.0 if w_sum > 0 else 0.0)
    wr = len(wins) / len(pnls) * 100.0

    eq = 1000.0 + np.cumsum(pnls)
    peak = np.maximum.accumulate(np.insert(eq, 0, 1000.0))
    dd = float(np.max((peak - np.insert(eq, 0, 1000.0)) / peak)) * 100.0

    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "net": float(np.sum(pnls)),
        "gross": float(np.sum(gross)),
        "fees": float(np.sum(fees)),
        "pf": float(pf),
        "wr": float(wr),
        "dd": float(dd),
        "avg_win": float(np.mean(wins)) if len(wins) > 0 else 0.0,
        "avg_loss": float(np.mean(losses)) if len(losses) > 0 else 0.0,
        "avg_dur": float(np.mean(durs)) if len(durs) > 0 else 0.0,
    }


# ==============================================================================
# MAIN MULTI-YEAR RESEARCH EXECUTION
# ==============================================================================

def run_amd_fvg_study():
    end_dt = datetime(2026, 9, 13, 23, 59, 59)
    ts_end = end_dt.timestamp()
    ts_start = ts_end - (2.0 * 365.25 * 86400.0)

    print("=" * 115)
    print("ACCUMULATION, MANIPULATION (SWEEP), FVG & DISTRIBUTION (AMD) RESEARCH ENGINE")
    print("2.0 Full Years (2024-09-13 -> 2026-09-13) Across All 7 Assets | 7,353,605 Authentic 1-Minute Bars")
    print("=" * 115)

    resolutions = [15, 60]  # Test 15-Minute and 60-Minute AMD
    all_res_trades = {15: [], 60: []}
    asset_res_breakdown = {15: {}, 60: {}}

    for sym in ASSETS:
        cache_path = os.path.join(BASE_DIR, f"scratch/{sym}_1m_full.pkl")
        if not os.path.exists(cache_path):
            continue

        print(f"\nProcessing {sym.upper()} 1-Minute Bars...")
        t0 = time.time()
        with open(cache_path, "rb") as f:
            raw = pickle.load(f)

        ts_arr = np.array([c["timestamp"] / 1000.0 if c["timestamp"] > 1e11 else float(c["timestamp"]) for c in raw], dtype=np.float64)
        mask = (ts_arr >= ts_start) & (ts_arr <= ts_end)

        ts_f = ts_arr[mask]
        o_f = np.array([raw[idx]["open"] for idx in np.where(mask)[0]], dtype=np.float64)
        h_f = np.array([raw[idx]["high"] for idx in np.where(mask)[0]], dtype=np.float64)
        l_f = np.array([raw[idx]["low"] for idx in np.where(mask)[0]], dtype=np.float64)
        c_f = np.array([raw[idx]["close"] for idx in np.where(mask)[0]], dtype=np.float64)

        del raw, ts_arr, mask
        gc.collect()

        print(f"  * {sym.upper()}: Extracted {len(ts_f):,} 1m bars in {time.time()-t0:.2f}s")

        for tf in resolutions:
            trades = simulate_amd_fvg(
                ts_f, o_f, h_f, l_f, c_f, sym.upper(),
                timeframe_minutes=tf, range_lookback=16,
                max_range_pct=0.030 if tf == 15 else 0.060,
                rr_ratio=2.5, max_wait_bars=60
            )
            all_res_trades[tf].extend(trades)
            asset_res_breakdown[tf][sym.upper()] = trades
            print(f"    - {tf}m AMD+FVG: {len(trades)} trades detected")

        del ts_f, o_f, h_f, l_f, c_f
        gc.collect()

    # Summarize across fee tiers
    print("\n" + "=" * 115)
    print("MASTER PERFORMANCE BENCHMARK: AMD + FVG STRATEGY ACROSS FEE TIERS (2 FULL YEARS)")
    print("=" * 115)

    results_report = {}

    for tf in resolutions:
        trades_list = sorted(all_res_trades[tf], key=lambda x: x.xt)
        print(f"\n" + "#" * 115)
        print(f"RESOLUTION: {tf}-MINUTE AMD + FVG ({len(trades_list):,} Trades across 7 Assets)")
        print("#" * 115)

        print(f"{'Exchange Fee Tier':<30} | {'Maker/Taker':<15} | {'Net PnL ($)':<14} | {'Fees Paid ($)':<14} | {'Win Rate':<9} | {'Profit Factor':<13} | {'Max DD':<9}")
        print("-" * 115)

        results_report[tf] = {}

        for tier_name, (m_fee, t_fee) in FEE_TIERS.items():
            metrics = compute_fee_metrics(trades_list, m_fee, t_fee)
            results_report[tf][tier_name] = metrics

            rate_str = f"{m_fee*100:.3f}% / {t_fee*100:.3f}%"
            net_str = f"${metrics['net']:+,.2f}"
            fees_str = f"${metrics['fees']:,.2f}"
            wr_str = f"{metrics['wr']:.1f}%"
            pf_str = f"{metrics['pf']:.2f}"
            dd_str = f"{metrics['dd']:.1f}%"

            print(f"{tier_name:<30} | {rate_str:<15} | {net_str:<14} | {fees_str:<14} | {wr_str:<9} | {pf_str:<13} | {dd_str:<9}")

        # Asset Breakdown under MEXC MX Token (0% Maker / 0.032% Taker)
        print(f"\n  >>> Per-Asset Breakdown under MEXC Futures MX (0.00% Maker / 0.032% Taker):")
        for sym_name, s_trades in asset_res_breakdown[tf].items():
            am = compute_fee_metrics(s_trades, 0.00000, 0.00032)
            print(f"      * {sym_name:<9}: Net ${am['net']:+9.2f} | Gross ${am['gross']:+9.2f} | Fees ${am['fees']:6.1f} | PF {am['pf']:4.2f} | WR {am['wr']:5.1f}% | DD {am['dd']:5.1f}% | Trades {am['trades']}")

    # Save complete study results
    out_file = os.path.join(BASE_DIR, "scratch/amd_fvg_study_results.pkl")
    with open(out_file, "wb") as f:
        pickle.dump({"report": results_report, "trades": all_res_trades, "breakdown": asset_res_breakdown}, f)
    print(f"\n[INFO] Saved complete AMD + FVG research study to {out_file}")

    return all_res_trades, results_report


if __name__ == "__main__":
    run_amd_fvg_study()
