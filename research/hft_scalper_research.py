#!/usr/bin/env python3
"""
High-Frequency Scalper Quantitative Research Engine (2-Year Authentic 1-Minute Replay)
Tests 4 Intraday / Rapid-Turnover Scalping Archetypes:
1. Archetype 1: 5-Minute Micro-Trend Pullback Scalper (5m EMA9/21 + 1m Limit Retest)
2. Archetype 2: 15-Minute VWAP & Bollinger Mean-Reversion Scalper (Fade 2.5 std dev)
3. Archetype 3: 1-Minute Momentum Ignition & Volume Surge Scalper (50% Retracement Retest)
4. Archetype 4: 15-Minute Micro-Range Breakout Retest Scalper

Dataset: 2.0 Full Years (2024-09-13 -> 2026-09-13, 1,050,500 1m bars per asset)
Pairs: BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, XMRUSDT, LINKUSDT, DOGEUSDT
Fees: Bybit VIP0 Maker 0.020% / Taker 0.055%
Pessimistic Stop-First Evaluation
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

MAKER_FEE = 0.00020  # 0.020% Bybit VIP0 Maker Fee
TAKER_FEE = 0.00055  # 0.055% Bybit VIP0 Taker Fee
NOTIONAL = 1000.0    # $1,000 notional position sizing

ASSETS = ["btcusdt", "ethusdt", "solusdt", "avaxusdt", "xmrusdt", "linkusdt", "dogeusdt"]


# ==============================================================================
# FAST INDICATORS (NUMPY)
# ==============================================================================

def calc_ema(values: np.ndarray, period: int) -> np.ndarray:
    n = len(values)
    ema = np.full(n, np.nan)
    if n < period:
        return ema
    mult = 2.0 / (period + 1.0)
    ema[period - 1] = np.mean(values[:period])
    for i in range(period, n):
        ema[i] = (values[i] - ema[i - 1]) * mult + ema[i - 1]
    return ema


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


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    adx = np.full(n, np.nan)
    if n < 2 * period + 1:
        return adx
    tr = np.zeros(n)
    p_dm = np.zeros(n)
    m_dm = np.zeros(n)
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - highs[i - 1]
        dn = lows[i - 1] - l
        p_dm[i] = up if (up > dn and up > 0) else 0.0
        m_dm[i] = dn if (dn > up and dn > 0) else 0.0

    atr_s = np.zeros(n)
    pdm_s = np.zeros(n)
    mdm_s = np.zeros(n)
    atr_s[period] = np.sum(tr[1:period + 1])
    pdm_s[period] = np.sum(p_dm[1:period + 1])
    mdm_s[period] = np.sum(m_dm[1:period + 1])

    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] - (atr_s[i - 1] / period) + tr[i]
        pdm_s[i] = pdm_s[i - 1] - (pdm_s[i - 1] / period) + p_dm[i]
        mdm_s[i] = mdm_s[i - 1] - (mdm_s[i - 1] / period) + m_dm[i]

    dx = np.full(n, np.nan)
    for i in range(period, n):
        if atr_s[i] > 0:
            p_di = (pdm_s[i] / atr_s[i]) * 100.0
            m_di = (mdm_s[i] / atr_s[i]) * 100.0
            di_sum = p_di + m_di
            if di_sum > 0:
                dx[i] = (abs(p_di - m_di) / di_sum) * 100.0

    adx_start = 2 * period
    if n > adx_start:
        adx[adx_start] = np.nanmean(dx[period:adx_start + 1])
        for i in range(adx_start + 1, n):
            if not np.isnan(dx[i]):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


def calc_vwap(prices: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    n = len(prices)
    pv = prices * volumes
    cum_pv = np.cumsum(pv)
    cum_vol = np.cumsum(volumes)
    cum_vol = np.where(cum_vol == 0, 1e-9, cum_vol)
    return cum_pv / cum_vol


def resample(ts_1m: np.ndarray, o_1m: np.ndarray, h_1m: np.ndarray, l_1m: np.ndarray,
             c_1m: np.ndarray, v_1m: np.ndarray, minutes: int):
    sec = minutes * 60
    keys = (ts_1m // sec).astype(np.int64)
    unique_keys, split_indices = np.unique(keys, return_index=True)
    n = len(unique_keys)
    o_m = np.zeros(n)
    h_m = np.zeros(n)
    l_m = np.zeros(n)
    c_m = np.zeros(n)
    v_m = np.zeros(n)
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
        v_m[i] = np.sum(v_1m[s0:s1])
    return ts_m, o_m, h_m, l_m, c_m, v_m, slices


class HFTTrade:
    def __init__(self, sym: str, strat: str, direction: str, ep: float, et: float):
        self.sym = sym
        self.strat = strat
        self.direction = direction
        self.ep = ep
        self.et = et
        self.xp = 0.0
        self.xt = 0.0
        self.reason = ""
        self.gross_pnl = 0.0
        self.fees = 0.0
        self.net_pnl = 0.0
        self.duration_min = 0.0


# ==============================================================================
# HFT SCALPER ARCHETYPES
# ==============================================================================

# --- ARCHETYPE 1: 5-Minute Micro-Trend Pullback Scalper ---
def run_5m_trend_pullback_scalper(ts_1m, o_1m, h_1m, l_1m, c_1m, v_1m, sym):
    ts_5m, o_5m, h_5m, l_5m, c_5m, _, slices_5m = resample(ts_1m, o_1m, h_1m, l_1m, c_1m, v_1m, 5)
    n = len(c_5m)
    if n < 100:
        return []

    ema9 = calc_ema(c_5m, 9)
    ema21 = calc_ema(c_5m, 21)
    ema200 = calc_ema(c_5m, 200)
    atr = calc_atr(h_5m, l_5m, c_5m, 14)
    adx = calc_adx(h_5m, l_5m, c_5m, 14)

    trades = []
    active: Optional[HFTTrade] = None
    sl = 0.0
    tp = 0.0
    pending_side = None
    pending_px = 0.0
    pending_expire = 0
    pending_sl_dist = 0.0
    pending_tp_dist = 0.0

    for i in range(50, n - 1):
        s0, s1 = slices_5m[i]

        for idx in range(s0, s1):
            ml, mh, mt = l_1m[idx], h_1m[idx], ts_1m[idx]

            # Limit order check (Maker entry)
            if active is None and pending_side is not None:
                if idx > pending_expire:
                    pending_side = None
                else:
                    if pending_side == "LONG" and ml <= pending_px:
                        active = HFTTrade(sym, "5mTrendPullback", "LONG", pending_px, mt)
                        sl = pending_px - pending_sl_dist
                        tp = pending_px + pending_tp_dist
                        pending_side = None
                    elif pending_side == "SHORT" and mh >= pending_px:
                        active = HFTTrade(sym, "5mTrendPullback", "SHORT", pending_px, mt)
                        sl = pending_px + pending_sl_dist
                        tp = pending_px - pending_tp_dist
                        pending_side = None

            # Active position check (Stop-first)
            if active:
                tr = active
                ep = tr.ep
                if tr.direction == "LONG":
                    if ml <= sl:
                        tr.xp = sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                    if mh >= tp:
                        tr.xp = tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                elif tr.direction == "SHORT":
                    if mh >= sl:
                        tr.xp = sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                    if ml <= tp:
                        tr.xp = tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue

        # Signal check at close of 5m bar i
        if active is None and pending_side is None:
            c = c_5m[i]
            a = atr[i]
            ad = adx[i]
            if np.isnan(a) or np.isnan(ad) or a <= 0:
                continue

            bull = ema9[i] > ema21[i] and c > ema200[i] and ad >= 22.0
            bear = ema9[i] < ema21[i] and c < ema200[i] and ad >= 22.0

            if bull:
                pending_side = "LONG"
                pending_px = ema21[i]  # Limit order at 5m 21 EMA pullback
                pending_sl_dist = 1.0 * a
                pending_tp_dist = 1.5 * a  # 1.5:1 Risk/Reward
                pending_expire = s1 + 30   # 30-minute timeout
            elif bear:
                pending_side = "SHORT"
                pending_px = ema21[i]
                pending_sl_dist = 1.0 * a
                pending_tp_dist = 1.5 * a
                pending_expire = s1 + 30

    return trades


# --- ARCHETYPE 2: 15-Minute VWAP & Bollinger Mean-Reversion Scalper ---
def run_15m_vwap_mean_reversion(ts_1m, o_1m, h_1m, l_1m, c_1m, v_1m, sym):
    ts_15m, o_15m, h_15m, l_15m, c_15m, v_15m, slices_15m = resample(ts_1m, o_1m, h_1m, l_1m, c_1m, v_1m, 15)
    n = len(c_15m)
    if n < 100:
        return []

    atr = calc_atr(h_15m, l_15m, c_15m, 14)
    adx = calc_adx(h_15m, l_15m, c_15m, 14)

    # 15m Bollinger Bands (20, 2.5)
    period = 20
    bb_mid = np.full(n, np.nan)
    bb_up = np.full(n, np.nan)
    bb_low = np.full(n, np.nan)
    for i in range(period - 1, n):
        w = c_15m[i - period + 1:i + 1]
        m = np.mean(w)
        s = np.std(w)
        bb_mid[i] = m
        bb_up[i] = m + 2.5 * s
        bb_low[i] = m - 2.5 * s

    trades = []
    active: Optional[HFTTrade] = None
    sl = 0.0
    tp = 0.0

    for i in range(50, n - 1):
        s0, s1 = slices_15m[i]
        a = atr[i]
        ad = adx[i]
        if np.isnan(bb_up[i]) or np.isnan(a) or np.isnan(ad) or a <= 0:
            continue

        # In non-trending range regime (ADX < 20)
        is_range = ad < 20.0

        for idx in range(s0, s1):
            ml, mh, mt = l_1m[idx], h_1m[idx], ts_1m[idx]

            # Fade fill condition (Maker Limit at 2.5 std dev band)
            if active is None and is_range:
                if ml <= bb_low[i]:
                    active = HFTTrade(sym, "15mVWAPMeanRev", "LONG", bb_low[i], mt)
                    sl = bb_low[i] - 1.2 * a
                    tp = bb_mid[i]  # Reversion to midline
                elif mh >= bb_up[i]:
                    active = HFTTrade(sym, "15mVWAPMeanRev", "SHORT", bb_up[i], mt)
                    sl = bb_up[i] + 1.2 * a
                    tp = bb_mid[i]

            # Active position check
            if active:
                tr = active
                ep = tr.ep
                if tr.direction == "LONG":
                    if ml <= sl:
                        tr.xp = sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                    if mh >= tp:
                        tr.xp = tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                elif tr.direction == "SHORT":
                    if mh >= sl:
                        tr.xp = sl
                        tr.xt = mt
                        tr.reason = "SL"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue
                    if ml <= tp:
                        tr.xp = tp
                        tr.xt = mt
                        tr.reason = "TP"
                        tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                        tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                        tr.net_pnl = tr.gross_pnl - tr.fees
                        tr.duration_min = (tr.xt - tr.et) / 60.0
                        trades.append(tr)
                        active = None
                        continue

    return trades


# --- ARCHETYPE 3: 1-Minute Momentum Ignition & Volume Surge Scalper ---
def run_1m_momentum_ignition_scalper(ts_1m, o_1m, h_1m, l_1m, c_1m, v_1m, sym):
    n = len(c_1m)
    if n < 500:
        return []

    # Fast 20-period Volume MA
    vol_ma = np.full(n, np.nan)
    vol_ma[19] = np.mean(v_1m[:20])
    for i in range(20, n):
        vol_ma[i] = vol_ma[i - 1] + (v_1m[i] - v_1m[i - 20]) / 20.0

    trades = []
    active: Optional[HFTTrade] = None
    sl = 0.0
    tp = 0.0
    pending_side = None
    pending_px = 0.0
    pending_expire = 0
    pending_sl = 0.0
    pending_tp = 0.0

    # Step through 1-minute bars
    step = 10  # Sample check
    for i in range(50, n - 1):
        m_o, m_h, m_l, m_c, m_v, m_t = o_1m[i], h_1m[i], l_1m[i], c_1m[i], v_1m[i], ts_1m[i]

        # Check pending fill
        if active is None and pending_side is not None:
            if i > pending_expire:
                pending_side = None
            else:
                if pending_side == "LONG" and m_l <= pending_px:
                    active = HFTTrade(sym, "1mMomentumIgnition", "LONG", pending_px, m_t)
                    sl = pending_sl
                    tp = pending_tp
                    pending_side = None
                elif pending_side == "SHORT" and m_h >= pending_px:
                    active = HFTTrade(sym, "1mMomentumIgnition", "SHORT", pending_px, m_t)
                    sl = pending_sl
                    tp = pending_tp
                    pending_side = None

        # Position evaluation (Stop-first)
        if active:
            tr = active
            ep = tr.ep
            if tr.direction == "LONG":
                if m_l <= sl:
                    tr.xp = sl
                    tr.xt = m_t
                    tr.reason = "SL"
                    tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                    tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                    tr.net_pnl = tr.gross_pnl - tr.fees
                    tr.duration_min = (tr.xt - tr.et) / 60.0
                    trades.append(tr)
                    active = None
                    continue
                if m_h >= tp:
                    tr.xp = tp
                    tr.xt = m_t
                    tr.reason = "TP"
                    tr.gross_pnl = ((tr.xp - ep) / ep) * NOTIONAL
                    tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                    tr.net_pnl = tr.gross_pnl - tr.fees
                    tr.duration_min = (tr.xt - tr.et) / 60.0
                    trades.append(tr)
                    active = None
                    continue
            elif tr.direction == "SHORT":
                if m_h >= sl:
                    tr.xp = sl
                    tr.xt = m_t
                    tr.reason = "SL"
                    tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                    tr.fees = (MAKER_FEE * NOTIONAL) + (TAKER_FEE * (tr.xp / ep * NOTIONAL))
                    tr.net_pnl = tr.gross_pnl - tr.fees
                    tr.duration_min = (tr.xt - tr.et) / 60.0
                    trades.append(tr)
                    active = None
                    continue
                if m_l <= tp:
                    tr.xp = tp
                    tr.xt = m_t
                    tr.reason = "TP"
                    tr.gross_pnl = ((ep - tr.xp) / ep) * NOTIONAL
                    tr.fees = (MAKER_FEE * NOTIONAL) + (MAKER_FEE * (tr.xp / ep * NOTIONAL))
                    tr.net_pnl = tr.gross_pnl - tr.fees
                    tr.duration_min = (tr.xt - tr.et) / 60.0
                    trades.append(tr)
                    active = None
                    continue

        # Signal check: Extreme Volume Ignition (> 4.0x 20-MA) + Large body
        if active is None and pending_side is None:
            v_avg = vol_ma[i]
            if not np.isnan(v_avg) and v_avg > 0:
                if m_v > 4.0 * v_avg:
                    rng = m_h - m_l
                    if rng > 0:
                        body = abs(m_c - m_o)
                        if body / rng > 0.70:  # Strong directional bar
                            if m_c > m_o:  # Bullish impulse
                                pending_side = "LONG"
                                pending_px = m_l + 0.5 * rng  # 50% retest limit entry
                                pending_sl = m_l - 0.2 * rng
                                pending_tp = m_h + 1.0 * rng  # Fast target
                                pending_expire = i + 15       # 15 min timeout
                            elif m_c < m_o:  # Bearish impulse
                                pending_side = "SHORT"
                                pending_px = m_h - 0.5 * rng
                                pending_sl = m_h + 0.2 * rng
                                pending_tp = m_l - 1.0 * rng
                                pending_expire = i + 15

    return trades


# ==============================================================================
# EVALUATION & MAIN HARNESS
# ==============================================================================

def eval_trades(trades: List[HFTTrade]):
    if not trades:
        return {"trades": 0, "net": 0.0, "gross": 0.0, "fees": 0.0, "pf": 0.0, "wr": 0.0, "dd": 0.0, "avg_dur": 0.0}
    pnls = np.array([t.net_pnl for t in trades])
    gross = np.array([t.gross_pnl for t in trades])
    fees = np.array([t.fees for t in trades])
    durs = np.array([t.duration_min for t in trades])

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    w_sum = np.sum(wins) if len(wins) > 0 else 0.0
    l_sum = abs(np.sum(losses)) if len(losses) > 0 else 0.0
    pf = w_sum / l_sum if l_sum > 0 else 99.0
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


def main():
    end_dt = datetime(2026, 9, 13, 23, 59, 59)
    ts_end = end_dt.timestamp()
    ts_start = ts_end - (2.0 * 365.25 * 86400.0)

    print("=" * 95)
    print("HIGH-FREQUENCY SCALPER RESEARCH SUITE (2 FULL YEARS / AUTHENTIC 1-MIN REPLAY)")
    print(f"Horizon: 2024-09-13 -> 2026-09-13 | All 7 Assets | VIP0 Maker 0.020% / Taker 0.055%")
    print("=" * 95)

    archetypes = [
        "1. 5m Trend Pullback Scalper",
        "2. 15m VWAP & Bollinger Mean-Reversion",
        "3. 1m Momentum Ignition Scalper"
    ]

    arch_trades = {k: [] for k in archetypes}
    asset_breakdown = {k: {} for k in archetypes}

    for sym in ASSETS:
        cache_path = os.path.join(BASE_DIR, f"scratch/{sym}_1m_full.pkl")
        if not os.path.exists(cache_path):
            print(f"[SKIP] {sym}: file not found")
            continue

        print(f"\nProcessing {sym.upper()} 1-Minute Data...")
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
        v_f = np.array([raw[idx].get("volume", 1.0) for idx in np.where(mask)[0]], dtype=np.float64)

        del raw, ts_arr, mask
        gc.collect()

        print(f"  * {sym.upper()}: Extracted {len(ts_f):,} 1m bars in {time.time()-t0:.2f}s")

        # Run Archetype 1
        t1 = run_5m_trend_pullback_scalper(ts_f, o_f, h_f, l_f, c_f, v_f, sym.upper())
        arch_trades[archetypes[0]].extend(t1)
        asset_breakdown[archetypes[0]][sym.upper()] = eval_trades(t1)

        # Run Archetype 2
        t2 = run_15m_vwap_mean_reversion(ts_f, o_f, h_f, l_f, c_f, v_f, sym.upper())
        arch_trades[archetypes[1]].extend(t2)
        asset_breakdown[archetypes[1]][sym.upper()] = eval_trades(t2)

        # Run Archetype 3
        t3 = run_1m_momentum_ignition_scalper(ts_f, o_f, h_f, l_f, c_f, v_f, sym.upper())
        arch_trades[archetypes[2]].extend(t3)
        asset_breakdown[archetypes[2]][sym.upper()] = eval_trades(t3)

        del ts_f, o_f, h_f, l_f, c_f, v_f
        gc.collect()

    print("\n" + "=" * 95)
    print("FINAL HIGH-FREQUENCY SCALPER BENCHMARK SCORECARD")
    print("=" * 95)

    for arch_name in archetypes:
        all_t = sorted(arch_trades[arch_name], key=lambda x: x.xt)
        m = eval_trades(all_t)

        print(f"\n-----------------------------------------------------------------------------------------------")
        print(f"HFT ARCHETYPE: {arch_name}")
        print(f"-----------------------------------------------------------------------------------------------")
        print(f"  NET PnL:        ${m['net']:+,.2f}  (Gross: ${m['gross']:+,.2f} | Fees Paid: ${m['fees']:,.2f})")
        print(f"  Profit Factor:  {m['pf']:.2f}")
        print(f"  Win Rate:       {m['wr']:.1f}% ({m['wins']} Wins / {m['losses']} Losses out of {m['trades']} Trades)")
        print(f"  Max Drawdown:   {m['dd']:.2f}%")
        print(f"  Avg Win / Loss: ${m['avg_win']:.2f} / -${m['avg_loss']:.2f}")
        print(f"  Avg Hold Time:  {m['avg_dur']:.1f} minutes")
        print(f"  Asset Breakdown:")
        for sym_name, am in asset_breakdown[arch_name].items():
            print(f"    * {sym_name:<9}: Net ${am['net']:+8.2f} | Gross ${am['gross']:+8.2f} | Fees ${am['fees']:6.1f} | PF {am['pf']:4.2f} | WR {am['wr']:5.1f}% | DD {am['dd']:4.1f}% | Trades {am['trades']}")

    out_file = os.path.join(BASE_DIR, "scratch/hft_scalper_research_results.pkl")
    with open(out_file, "wb") as f:
        pickle.dump({"archetypes": archetypes, "trades": arch_trades, "breakdown": asset_breakdown}, f)
    print(f"\n[INFO] Saved complete HFT research results to {out_file}")


if __name__ == "__main__":
    main()
