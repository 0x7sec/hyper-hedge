#!/usr/bin/env python3
"""
1-Minute Sub-Candle Replay Engine for Path B Asymmetric Hedging Strategy.
Eliminates intra-bar retracement and flash-crash overshoot blind spots by
sequentially replaying 1-minute sub-candles within each macro trading cycle.
"""

import os
import sys
import time
import pickle
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger("ReplayEngine")

TAKER_FEE_RATE = 0.00055  # Bybit VIP0 Taker fee: 0.055%

# Default champion profiles
CHAMPION_PROFILES: Dict[str, Dict[str, Any]] = {
    "BTCUSDT": {
        "d_pct": 0.70,
        "confirm_mult": 0.80,
        "b1_tp_mult": 2.80,
        "b1_r1_trig": 1.40,
        "b1_r1_sl": 1.00,
        "b1_r2_trig": 2.20,
        "b1_r2_sl": 1.70,
        "b2_tp_mult": 3.50,
        "b2_be_cushion": 0.10,
        "b2_r2_trig": 2.50,
        "b2_r2_sl": 2.10,
        "adx_min": 15.0,
        "timeout_bars": 50,
        "hedge_ratio": 0.30,
        "leverage": 2.5,
    },
    "ETHUSDT": {
        "d_pct": 0.80,
        "confirm_mult": 0.80,
        "b1_tp_mult": 2.80,
        "b1_r1_trig": 1.40,
        "b1_r1_sl": 1.00,
        "b1_r2_trig": 2.20,
        "b1_r2_sl": 1.70,
        "b2_tp_mult": 3.00,
        "b2_be_cushion": 0.10,
        "b2_r2_trig": 2.50,
        "b2_r2_sl": 2.10,
        "adx_min": 0.0,
        "timeout_bars": 50,
        "hedge_ratio": 0.30,
        "leverage": 2.5,
    },
    "SOLUSDT": {
        "d_pct": 0.80,
        "confirm_mult": 0.80,
        "b1_tp_mult": 2.80,
        "b1_r1_trig": 1.40,
        "b1_r1_sl": 1.00,
        "b1_r2_trig": 2.20,
        "b1_r2_sl": 1.70,
        "b2_tp_mult": 3.50,
        "b2_be_cushion": 0.10,
        "b2_r2_trig": 2.50,
        "b2_r2_sl": 2.10,
        "adx_min": 15.0,
        "timeout_bars": 50,
        "hedge_ratio": 0.30,
        "leverage": 2.5,
    },
    "PAXGUSDT": {
        "d_pct": 0.50,
        "confirm_mult": 0.80,
        "b1_tp_mult": 2.50,
        "b1_r1_trig": 1.40,
        "b1_r1_sl": 1.00,
        "b1_r2_trig": 2.00,
        "b1_r2_sl": 1.50,
        "b2_tp_mult": 3.00,
        "b2_be_cushion": 0.10,
        "b2_r2_trig": 2.50,
        "b2_r2_sl": 2.10,
        "adx_min": 15.0,
        "timeout_bars": 50,
        "hedge_ratio": 0.30,
        "leverage": 2.5,
    },
}


@dataclass
class TradeRecord:
    cycle_id: int
    symbol: str
    direction: str  # "bullish" | "bearish"
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    scenario: str  # e.g., "SCENARIO_1_B1_TP", "SCENARIO_4_B2_TP", "SCENARIO_EXHAUSTION_GUARD", "SCENARIO_5_WHIPSAW"
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars_held: int
    minutes_held: int
    exhaustion_guard_triggered: bool = False
    entry_bar_idx: int = 0
    exit_bar_idx: int = 0
    primary_exit: float = 0.0
    counter_exit: float = 0.0
    primary_pnl: float = 0.0
    counter_pnl: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    total_trades: int
    winning_trades: int
    breakeven_trades: int
    losing_trades: int
    win_rate: float
    shield_rate: float
    gross_profit: float
    gross_loss: float
    total_fees: float
    net_profit: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    scenario_counts: Dict[str, int]
    trades: List[TradeRecord]
    equity_curve: List[Dict[str, Any]]
    drawdown_curve: List[Dict[str, Any]]
    # Jesse-Grade Extended Metrics
    long_trades: int = 0
    long_win_rate: float = 0.0
    long_profit: float = 0.0
    short_trades: int = 0
    short_win_rate: float = 0.0
    short_profit: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_loss_ratio: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    expectancy_usd: float = 0.0
    cagr_pct: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_bars_held: float = 0.0
    max_bars_held: int = 0
    min_bars_held: int = 0
    candles_count: int = 0


def calc_ema(arr: np.ndarray, span: int) -> np.ndarray:
    """Fast vectorised exponential moving average."""
    alpha = 2.0 / (span + 1.0)
    ema = np.empty_like(arr, dtype=np.float64)
    ema[0] = arr[0]
    for i in range(1, len(arr)):
        ema[i] = alpha * arr[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Fast vectorised Wilder Average Directional Index."""
    n = len(closes)
    if n < 2 * period:
        return np.zeros(n, dtype=np.float64)

    tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
    tr = np.insert(tr, 0, highs[0] - lows[0])

    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]
    up_move = np.insert(up_move, 0, 0.0)
    down_move = np.insert(down_move, 0, 0.0)

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = calc_ema(tr, period)
    plus_di = 100.0 * calc_ema(plus_dm, period) / np.maximum(atr, 1e-9)
    minus_di = 100.0 * calc_ema(minus_dm, period) / np.maximum(atr, 1e-9)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-9)
    return calc_ema(dx, period)


class ReplayEngine:
    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital

    @staticmethod
    def load_candles(symbol: str, limit: int = 4000) -> List[Dict[str, Any]]:
        """
        Attempts to load historical candles in priority order:
        1. Local scratch/ cache or artifact scratch directory.
        2. Bybit Unified Trading V5 REST API.
        3. Deterministic synthetic sequence fallback.
        """
        sym_lower = symbol.lower()
        possible_paths = [
            os.path.join("scratch", f"{sym_lower}_60m_cache.pkl"),
            rf"C:\Users\x000sec\.gemini\antigravity-ide\brain\4b5628a6-7459-4e07-9deb-7e93dc31da90\scratch\{sym_lower}_60m_cache.pkl",
        ]

        for p in possible_paths:
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        data = pickle.load(f)
                    if data and len(data) > 50:
                        logger.info(f"Loaded {len(data)} candles for {symbol} from {p}")
                        return data[-limit:] if len(data) > limit else data
                except Exception as e:
                    logger.warning(f"Failed to load cache from {p}: {e}")

        # Try fetching from Bybit REST API with backward pagination
        try:
            from pybit.unified_trading import HTTP
            import time as _time
            session = HTTP(testnet=False)
            all_raw = []
            end_time = None
            remaining = limit
            while remaining > 0:
                batch_limit = min(remaining, 1000)
                params = {"category": "linear", "symbol": symbol, "interval": "60", "limit": batch_limit}
                if end_time is not None:
                    params["endTime"] = end_time
                res = session.get_kline(**params)
                if res.get("retCode") != 0 or not res.get("result", {}).get("list"):
                    break
                batch = res["result"]["list"]
                all_raw.extend(batch)
                remaining -= len(batch)
                if len(batch) < batch_limit:
                    break
                oldest_ts = int(batch[-1][0])
                end_time = oldest_ts - 1
                _time.sleep(0.04)

            if all_raw:
                all_raw.reverse()
                candles = []
                for item in all_raw:
                    ts_ms = int(item[0])
                    candles.append({
                        "timestamp": ts_ms,
                        "datetime": datetime.fromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d %H:%M"),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    })
                logger.info(f"Fetched {len(candles)} live Bybit 60m candles for {symbol}.")
                # Cache locally for fast subsequent queries
                os.makedirs("scratch", exist_ok=True)
                cache_file = os.path.join("scratch", f"{sym_lower}_60m_cache.pkl")
                try:
                    with open(cache_file, "wb") as cf:
                        pickle.dump(candles, cf)
                except Exception:
                    pass
                return candles[-limit:] if len(candles) > limit else candles
        except Exception as e:
            logger.warning(f"Bybit API fetch failed for {symbol}: {e}")

        # Fallback: Synthetic realistic series
        logger.info(f"Generating realistic synthetic candles for {symbol} ({limit} bars)...")
        np.random.seed(42)
        base_px = 50000.0 if "BTC" in symbol else (2500.0 if "ETH" in symbol else 140.0)
        curr_p = base_px
        candles = []
        now_ts = int(time.time()) - (limit * 3600)
        for i in range(limit):
            ret = np.random.normal(0.0001, 0.006)
            c_close = curr_p * (1.0 + ret)
            c_high = max(curr_p, c_close) * (1.0 + abs(np.random.normal(0, 0.003)))
            c_low = min(curr_p, c_close) * (1.0 - abs(np.random.normal(0, 0.003)))
            candles.append({
                "timestamp": (now_ts + i * 3600) * 1000,
                "datetime": datetime.fromtimestamp(now_ts + i * 3600).strftime("%Y-%m-%d %H:%M"),
                "open": curr_p,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": 1000.0,
            })
            curr_p = c_close
        return candles

    def _generate_synthetic_1m_subcandles(self, o: float, h: float, l: float, c: float, num_sub: int = 60) -> List[Tuple[float, float, float, float]]:
        """
        Creates a realistic 60-step 1-minute price progression matching OHLC bounds.
        If bullish (C >= O), trajectory visits Open -> Low -> High -> Close.
        If bearish (C < O), trajectory visits Open -> High -> Low -> Close.
        Returns list of (sub_open, sub_high, sub_low, sub_close).
        """
        sub_candles = []
        if h == l:
            for _ in range(num_sub):
                sub_candles.append((o, o, o, o))
            return sub_candles

        t = np.linspace(0, 1, num_sub)
        if c >= o:
            # Low first, then High, then Close
            w1, w2 = int(num_sub * 0.25), int(num_sub * 0.75)
            pts = np.concatenate([
                np.linspace(o, l, w1, endpoint=False),
                np.linspace(l, h, w2 - w1, endpoint=False),
                np.linspace(h, c, num_sub - w2)
            ])
        else:
            # High first, then Low, then Close
            w1, w2 = int(num_sub * 0.25), int(num_sub * 0.75)
            pts = np.concatenate([
                np.linspace(o, h, w1, endpoint=False),
                np.linspace(h, l, w2 - w1, endpoint=False),
                np.linspace(l, c, num_sub - w2)
            ])

        # Add subtle micro-variations while preserving envelope
        for i in range(num_sub):
            base = pts[i]
            delta = (h - l) * 0.015
            s_open = base
            s_high = min(h, base + delta)
            s_low = max(l, base - delta)
            s_close = pts[min(i + 1, num_sub - 1)]
            sub_candles.append((float(s_open), float(s_high), float(s_low), float(s_close)))

        return sub_candles

    def run_backtest(
        self,
        symbol: str,
        macro_candles: List[Dict[str, Any]],
        sub_candles_map: Optional[Dict[int, List[Dict[str, Any]]]] = None,
        custom_params: Optional[Dict[str, Any]] = None,
    ) -> BacktestResult:
        """
        Executes chronological 1-minute sub-candle replay backtest across macro 60m candles.
        """
        prof = CHAMPION_PROFILES.get(symbol, CHAMPION_PROFILES["BTCUSDT"]).copy()
        if custom_params:
            prof.update(custom_params)

        d_pct = float(prof["d_pct"])
        d_val = d_pct / 100.0
        confirm_mult = float(prof["confirm_mult"])
        b1_tp_mult = float(prof["b1_tp_mult"])
        b1_r1_trig = float(prof["b1_r1_trig"])
        b1_r1_sl = float(prof["b1_r1_sl"])
        b1_r2_trig = float(prof["b1_r2_trig"])
        b1_r2_sl = float(prof["b1_r2_sl"])
        b2_tp_mult = float(prof["b2_tp_mult"])
        b2_be_cushion = float(prof["b2_be_cushion"])
        b2_r2_trig = float(prof["b2_r2_trig"])
        b2_r2_sl = float(prof["b2_r2_sl"])
        adx_min = float(prof["adx_min"])
        timeout_bars = int(prof["timeout_bars"])
        hedge_ratio = float(prof["hedge_ratio"])
        leverage = float(prof["leverage"])

        notional_base = self.initial_capital * leverage  # e.g., $2,500 on $1,000 equity

        closes = np.array([float(c["close"]) for c in macro_candles], dtype=np.float64)
        highs = np.array([float(c["high"]) for c in macro_candles], dtype=np.float64)
        lows = np.array([float(c["low"]) for c in macro_candles], dtype=np.float64)
        n = len(closes)

        if n < 60:
            raise ValueError(f"Insufficient macro candles ({n} < 60) for indicator warmup.")

        ema_fast = calc_ema(closes, 9)
        ema_slow = calc_ema(closes, 21)
        adx = calc_adx(highs, lows, closes, 14)

        # Detect crossovers
        cross_up = (ema_fast[:-1] <= ema_slow[:-1]) & (ema_fast[1:] > ema_slow[1:])
        cross_down = (ema_fast[:-1] >= ema_slow[:-1]) & (ema_fast[1:] < ema_slow[1:])

        sig_indices = []
        sig_directions = []
        for i in range(25, n - 1):
            if cross_up[i - 1] and (adx_min <= 0 or adx[i] >= adx_min):
                sig_indices.append(i)
                sig_directions.append("bullish")
            elif cross_down[i - 1] and (adx_min <= 0 or adx[i] >= adx_min):
                sig_indices.append(i)
                sig_directions.append("bearish")

        trades: List[TradeRecord] = []
        equity = self.initial_capital
        equity_curve = [{"time": macro_candles[0].get("datetime", "0"), "equity": equity, "drawdown": 0.0}]
        drawdown_curve = [{"time": macro_candles[0].get("datetime", "0"), "drawdown_pct": 0.0}]
        peak_equity = equity

        scenario_counts: Dict[str, int] = {
            "SCENARIO_1_B1_TP": 0,
            "SCENARIO_2_B1_RATCHET": 0,
            "SCENARIO_3_B1_ZERO_LOSS": 0,
            "SCENARIO_4_B2_TP": 0,
            "SCENARIO_EXHAUSTION_GUARD": 0,
            "SCENARIO_5_B2_WHIPSAW": 0,
            "SCENARIO_6_TIMEOUT": 0,
        }

        i_ptr = 0
        curr_macro_bar = 0

        while i_ptr < len(sig_indices):
            bar_idx = sig_indices[i_ptr]
            if bar_idx < curr_macro_bar:
                i_ptr += 1
                continue

            direction = sig_directions[i_ptr]
            p0 = closes[bar_idx]
            entry_time_str = str(macro_candles[bar_idx].get("datetime", f"bar_{bar_idx}"))

            # Sizes in base currency units
            primary_notional = notional_base
            counter_notional = notional_base * hedge_ratio
            primary_qty = primary_notional / p0
            counter_qty = counter_notional / p0

            # State machine state
            phase = "INCUBATION"
            long_active = True
            short_active = True
            b1_armed = False
            b2_armed = False
            b1_ratchet_stage = 0
            b2_fast_be_locked = False
            b2_milestone2_locked = False

            primary_sl = 0.0
            primary_tp = 0.0
            blended_b2_entry = 0.0
            true_be_sl = 0.0
            b2_tp_level = 0.0

            trapped_loss = 0.0
            trapped_fees = 0.0
            counter_profit = 0.0
            total_fees = (primary_notional + counter_notional) * TAKER_FEE_RATE  # Entry fees

            cycle_done = False
            exit_px = p0
            exit_time_str = entry_time_str
            cycle_scenario = "SCENARIO_6_TIMEOUT"
            minutes_held = 0
            bars_held = 0
            exhaustion_triggered = False

            # Iterate macro bars until cycle finishes
            for k in range(bar_idx + 1, min(n, bar_idx + 1 + timeout_bars)):
                bars_held += 1
                mb = macro_candles[k]

                # Check if authentic 1-minute subcandles exist for this macro bar
                m_ts = mb.get("timestamp")
                if sub_candles_map and m_ts in sub_candles_map:
                    raw_sub = sub_candles_map[m_ts]
                    sub_steps = [(float(s["open"]), float(s["high"]), float(s["low"]), float(s["close"])) for s in raw_sub]
                else:
                    sub_steps = self._generate_synthetic_1m_subcandles(
                        float(mb["open"]), float(mb["high"]), float(mb["low"]), float(mb["close"]), num_sub=60
                    )

                # Chronological 1-minute subcandle stepping
                for s_open, s_high, s_low, s_close in sub_steps:
                    minutes_held += 1

                    # Determine intra-minute price checkpoints
                    if s_close >= s_open:
                        price_ticks = [s_open, s_low, s_high, s_close]
                    else:
                        price_ticks = [s_open, s_high, s_low, s_close]

                    for p in price_ticks:
                        # -------------------------------------------------------------
                        # 1. INCUBATION PHASE
                        # -------------------------------------------------------------
                        if phase == "INCUBATION":
                            if direction == "bullish":
                                # Check Branch 1 (+0.80D)
                                if p >= p0 * (1.0 + confirm_mult * d_val):
                                    phase = "RUNNER_B1"
                                    b1_armed = True
                                    confirm_px = p
                                    # Collapse 30% Short counter leg
                                    c_loss = (p0 - confirm_px) * counter_qty
                                    c_fees = (counter_qty * confirm_px) * TAKER_FEE_RATE
                                    trapped_loss = c_loss
                                    trapped_fees += c_fees
                                    total_fees += c_fees

                                    # Arm 100% Long Runner Zero-Loss Stop Loss
                                    # Gain required: |c_loss| + all roundtrip fees
                                    fee_buf = TAKER_FEE_RATE * 4.0
                                    primary_sl = p0 * (1.0 + (hedge_ratio * confirm_mult * d_val) + fee_buf)
                                    primary_tp = p0 * (1.0 + b1_tp_mult * d_val)

                                # Check Branch 2 (-0.80D Trap)
                                elif p <= p0 * (1.0 - confirm_mult * d_val):
                                    phase = "RUNNER_B2"
                                    confirm_px = p
                                    b2_tp_level = p0 * (1.0 - b2_tp_mult * d_val)

                                    # Collapse Trapped 100% Long
                                    t_loss = (confirm_px - p0) * primary_qty
                                    t_fees = (primary_qty * confirm_px) * TAKER_FEE_RATE
                                    trapped_loss = t_loss
                                    trapped_fees += t_fees
                                    total_fees += t_fees

                                    # EXHAUSTION GUARD CHECK
                                    if p <= b2_tp_level:
                                        # Flash dump overshot Apex TP! Abort upsize, close 30% short for profit
                                        exhaustion_triggered = True
                                        c_gain = (p0 - p) * counter_qty
                                        c_fee = (counter_qty * p) * TAKER_FEE_RATE
                                        counter_profit = c_gain
                                        total_fees += c_fee
                                        exit_px = p
                                        cycle_scenario = "SCENARIO_EXHAUSTION_GUARD"
                                        cycle_done = True
                                        break
                                    else:
                                        # Normal Scenario 5 Size-Flip (+70% notional added)
                                        b2_armed = True
                                        upsize_qty = primary_qty - counter_qty
                                        upsize_fee = (upsize_qty * confirm_px) * TAKER_FEE_RATE
                                        total_fees += upsize_fee
                                        # Blended Short Entry Price
                                        blended_b2_entry = (counter_qty * p0 + upsize_qty * confirm_px) / primary_qty
                                        # True Breakeven SL
                                        total_drain = abs(trapped_loss) + trapped_fees + upsize_fee + (primary_qty * blended_b2_entry * TAKER_FEE_RATE * 2.0)
                                        true_be_sl = blended_b2_entry - (total_drain / primary_qty)
                                        primary_sl = p0  # Initial stop at P0

                            else:  # Bearish entry
                                # Check Branch 1 (-0.80D Expansion down)
                                if p <= p0 * (1.0 - confirm_mult * d_val):
                                    phase = "RUNNER_B1"
                                    b1_armed = True
                                    confirm_px = p
                                    # Collapse 30% Long counter leg
                                    c_loss = (confirm_px - p0) * counter_qty
                                    c_fees = (counter_qty * confirm_px) * TAKER_FEE_RATE
                                    trapped_loss = c_loss
                                    trapped_fees += c_fees
                                    total_fees += c_fees

                                    fee_buf = TAKER_FEE_RATE * 4.0
                                    primary_sl = p0 * (1.0 - (hedge_ratio * confirm_mult * d_val) - fee_buf)
                                    primary_tp = p0 * (1.0 - b1_tp_mult * d_val)

                                # Check Branch 2 (+0.80D Trap up)
                                elif p >= p0 * (1.0 + confirm_mult * d_val):
                                    phase = "RUNNER_B2"
                                    confirm_px = p
                                    b2_tp_level = p0 * (1.0 + b2_tp_mult * d_val)

                                    # Collapse Trapped 100% Short
                                    t_loss = (p0 - confirm_px) * primary_qty
                                    t_fees = (primary_qty * confirm_px) * TAKER_FEE_RATE
                                    trapped_loss = t_loss
                                    trapped_fees += t_fees
                                    total_fees += t_fees

                                    # EXHAUSTION GUARD CHECK
                                    if p >= b2_tp_level:
                                        # Flash pump overshot Apex TP!
                                        exhaustion_triggered = True
                                        c_gain = (p - p0) * counter_qty
                                        c_fee = (counter_qty * p) * TAKER_FEE_RATE
                                        counter_profit = c_gain
                                        total_fees += c_fee
                                        exit_px = p
                                        cycle_scenario = "SCENARIO_EXHAUSTION_GUARD"
                                        cycle_done = True
                                        break
                                    else:
                                        # Normal Scenario 5 Size-Flip
                                        b2_armed = True
                                        upsize_qty = primary_qty - counter_qty
                                        upsize_fee = (upsize_qty * confirm_px) * TAKER_FEE_RATE
                                        total_fees += upsize_fee
                                        blended_b2_entry = (counter_qty * p0 + upsize_qty * confirm_px) / primary_qty
                                        total_drain = abs(trapped_loss) + trapped_fees + upsize_fee + (primary_qty * blended_b2_entry * TAKER_FEE_RATE * 2.0)
                                        true_be_sl = blended_b2_entry + (total_drain / primary_qty)
                                        primary_sl = p0

                        # -------------------------------------------------------------
                        # 2. RUNNER BRANCH 1 (Signal was right)
                        # -------------------------------------------------------------
                        elif phase == "RUNNER_B1":
                            if direction == "bullish":
                                # Apex TP hit (+2.80D)
                                if p >= primary_tp:
                                    exit_px = primary_tp
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (exit_px - p0) * primary_qty
                                    cycle_scenario = "SCENARIO_1_B1_TP"
                                    cycle_done = True
                                    break
                                # Stage 2 Ratchet (+2.20D)
                                elif p >= p0 * (1.0 + b1_r2_trig * d_val) and b1_ratchet_stage < 2:
                                    b1_ratchet_stage = 2
                                    primary_sl = max(primary_sl, p0 * (1.0 + b1_r2_sl * d_val))
                                # Stage 1 Ratchet (+1.40D)
                                elif p >= p0 * (1.0 + b1_r1_trig * d_val) and b1_ratchet_stage < 1:
                                    b1_ratchet_stage = 1
                                    primary_sl = max(primary_sl, p0 * (1.0 + b1_r1_sl * d_val))
                                # Stop Loss hit
                                elif p <= primary_sl:
                                    exit_px = primary_sl
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (exit_px - p0) * primary_qty
                                    if b1_ratchet_stage >= 1:
                                        cycle_scenario = "SCENARIO_2_B1_RATCHET"
                                    else:
                                        cycle_scenario = "SCENARIO_3_B1_ZERO_LOSS"
                                    cycle_done = True
                                    break

                            else:  # Bearish
                                if p <= primary_tp:
                                    exit_px = primary_tp
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (p0 - exit_px) * primary_qty
                                    cycle_scenario = "SCENARIO_1_B1_TP"
                                    cycle_done = True
                                    break
                                elif p <= p0 * (1.0 - b1_r2_trig * d_val) and b1_ratchet_stage < 2:
                                    b1_ratchet_stage = 2
                                    primary_sl = min(primary_sl, p0 * (1.0 - b1_r2_sl * d_val))
                                elif p <= p0 * (1.0 - b1_r1_trig * d_val) and b1_ratchet_stage < 1:
                                    b1_ratchet_stage = 1
                                    primary_sl = min(primary_sl, p0 * (1.0 - b1_r1_sl * d_val))
                                elif p >= primary_sl:
                                    exit_px = primary_sl
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (p0 - exit_px) * primary_qty
                                    if b1_ratchet_stage >= 1:
                                        cycle_scenario = "SCENARIO_2_B1_RATCHET"
                                    else:
                                        cycle_scenario = "SCENARIO_3_B1_ZERO_LOSS"
                                    cycle_done = True
                                    break

                        # -------------------------------------------------------------
                        # 3. RUNNER BRANCH 2 (Trap Size-Flip)
                        # -------------------------------------------------------------
                        elif phase == "RUNNER_B2":
                            if direction == "bullish":  # Flipped to 100% Short Runner
                                # Apex TP hit
                                if p <= b2_tp_level:
                                    exit_px = b2_tp_level
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (blended_b2_entry - exit_px) * primary_qty
                                    cycle_scenario = "SCENARIO_4_B2_TP"
                                    cycle_done = True
                                    break
                                # Milestone 2 Ratchet (-2.50D -> lock -2.10D)
                                elif p <= p0 * (1.0 - b2_r2_trig * d_val) and not b2_milestone2_locked:
                                    b2_milestone2_locked = True
                                    primary_sl = min(primary_sl, p0 * (1.0 - b2_r2_sl * d_val))
                                # Fast True BE Lock
                                elif p <= true_be_sl - (b2_be_cushion * d_val * p0) and not b2_fast_be_locked:
                                    b2_fast_be_locked = True
                                    primary_sl = min(primary_sl, true_be_sl)
                                # Stopped out
                                elif p >= primary_sl:
                                    exit_px = primary_sl
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (blended_b2_entry - exit_px) * primary_qty
                                    if b2_fast_be_locked or b2_milestone2_locked:
                                        cycle_scenario = "SCENARIO_4_B2_TP" if counter_profit + trapped_loss > 0 else "SCENARIO_3_B1_ZERO_LOSS"
                                    else:
                                        cycle_scenario = "SCENARIO_5_B2_WHIPSAW"
                                    cycle_done = True
                                    break

                            else:  # Bearish entry flipped to 100% Long Runner
                                if p >= b2_tp_level:
                                    exit_px = b2_tp_level
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (exit_px - blended_b2_entry) * primary_qty
                                    cycle_scenario = "SCENARIO_4_B2_TP"
                                    cycle_done = True
                                    break
                                elif p >= p0 * (1.0 + b2_r2_trig * d_val) and not b2_milestone2_locked:
                                    b2_milestone2_locked = True
                                    primary_sl = max(primary_sl, p0 * (1.0 + b2_r2_sl * d_val))
                                elif p >= true_be_sl + (b2_be_cushion * d_val * p0) and not b2_fast_be_locked:
                                    b2_fast_be_locked = True
                                    primary_sl = max(primary_sl, true_be_sl)
                                elif p <= primary_sl:
                                    exit_px = primary_sl
                                    total_fees += (primary_qty * exit_px) * TAKER_FEE_RATE
                                    counter_profit = (exit_px - blended_b2_entry) * primary_qty
                                    if b2_fast_be_locked or b2_milestone2_locked:
                                        cycle_scenario = "SCENARIO_4_B2_TP" if counter_profit + trapped_loss > 0 else "SCENARIO_3_B1_ZERO_LOSS"
                                    else:
                                        cycle_scenario = "SCENARIO_5_B2_WHIPSAW"
                                    cycle_done = True
                                    break

                    if cycle_done:
                        break
                if cycle_done:
                    break

            # Handle timeout if neither B1 nor B2 closed out within 50 bars
            if not cycle_done:
                exit_px = closes[min(n - 1, bar_idx + timeout_bars)]
                cycle_scenario = "SCENARIO_6_TIMEOUT"
                total_fees += (primary_notional + counter_notional) * TAKER_FEE_RATE
                if direction == "bullish":
                    counter_profit = (exit_px - p0) * primary_qty + (p0 - exit_px) * counter_qty
                else:
                    counter_profit = (p0 - exit_px) * primary_qty + (exit_px - p0) * counter_qty

            gross_pnl = trapped_loss + counter_profit
            net_pnl = gross_pnl - total_fees
            ret_pct = (net_pnl / self.initial_capital) * 100.0

            equity += net_pnl
            peak_equity = max(peak_equity, equity)
            dd_val = peak_equity - equity
            dd_pct = (dd_val / peak_equity) * 100.0 if peak_equity > 0 else 0.0

            exit_time_str = str(macro_candles[min(n - 1, bar_idx + bars_held)].get("datetime", f"bar_{bar_idx + bars_held}"))

            rec = TradeRecord(
                cycle_id=len(trades) + 1,
                symbol=symbol,
                direction=direction,
                entry_time=entry_time_str,
                exit_time=exit_time_str,
                entry_price=round(p0, 4),
                exit_price=round(exit_px, 4),
                scenario=cycle_scenario,
                gross_pnl=round(gross_pnl, 2),
                fees=round(total_fees, 2),
                net_pnl=round(net_pnl, 2),
                return_pct=round(ret_pct, 2),
                bars_held=bars_held,
                minutes_held=minutes_held,
                exhaustion_guard_triggered=exhaustion_triggered,
                entry_bar_idx=bar_idx,
                exit_bar_idx=min(n - 1, bar_idx + bars_held),
                primary_exit=round(exit_px, 4),
                counter_exit=round(exit_px, 4),
                primary_pnl=round(trapped_loss, 2),
                counter_pnl=round(counter_profit, 2),
            )
            trades.append(rec)
            scenario_counts[cycle_scenario] = scenario_counts.get(cycle_scenario, 0) + 1

            equity_curve.append({"time": exit_time_str, "equity": round(equity, 2), "drawdown": round(dd_val, 2)})
            drawdown_curve.append({"time": exit_time_str, "drawdown_pct": round(dd_pct, 2)})

            curr_macro_bar = bar_idx + max(1, bars_held)
            i_ptr += 1

        # Summary statistics
        total_trades = len(trades)
        if total_trades == 0:
            return BacktestResult(
                symbol=symbol,
                total_trades=0,
                winning_trades=0,
                breakeven_trades=0,
                losing_trades=0,
                win_rate=0.0,
                shield_rate=0.0,
                gross_profit=0.0,
                gross_loss=0.0,
                total_fees=0.0,
                net_profit=0.0,
                profit_factor=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                scenario_counts=scenario_counts,
                trades=[],
                equity_curve=equity_curve,
                drawdown_curve=drawdown_curve,
                candles_count=len(macro_candles),
            )

        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0.05]
        bes = [p for p in pnls if -0.05 <= p <= 0.05]
        losses = [p for p in pnls if p < -0.05]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        net_profit = sum(pnls)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0
        win_rate = round((len(wins) / total_trades) * 100.0, 1)
        shield_rate = round(((len(wins) + len(bes)) / total_trades) * 100.0, 1)

        dd_pcts = [d["drawdown_pct"] for d in drawdown_curve]
        max_dd_pct = round(max(dd_pcts) if dd_pcts else 0.0, 2)
        dds = [d["drawdown"] for d in equity_curve]
        max_dd = round(max(dds) if dds else 0.0, 2)

        # Annualized Sharpe approximation (hourly steps)
        pnl_arr = np.array(pnls, dtype=np.float64)
        mean_ret = np.mean(pnl_arr)
        std_ret = np.std(pnl_arr)
        sharpe = round((mean_ret / std_ret) * np.sqrt(365 * 4), 2) if std_ret > 0 else 0.0

        # Long vs Short breakdown
        long_trades_list = [t for t in trades if t.direction == "bullish"]
        short_trades_list = [t for t in trades if t.direction == "bearish"]

        long_wins = [t.net_pnl for t in long_trades_list if t.net_pnl > 0.05]
        long_wr = round((len(long_wins) / len(long_trades_list)) * 100.0, 1) if long_trades_list else 0.0
        long_profit = round(sum(t.net_pnl for t in long_trades_list), 2)

        short_wins = [t.net_pnl for t in short_trades_list if t.net_pnl > 0.05]
        short_wr = round((len(short_wins) / len(short_trades_list)) * 100.0, 1) if short_trades_list else 0.0
        short_profit = round(sum(t.net_pnl for t in short_trades_list), 2)

        avg_win = round(float(np.mean(wins)), 2) if wins else 0.0
        avg_loss = round(float(np.mean(losses)), 2) if losses else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 999.0
        largest_win = round(max(wins), 2) if wins else 0.0
        largest_loss = round(max(losses), 2) if losses else 0.0

        win_prob = len(wins) / total_trades
        loss_prob = len(losses) / total_trades
        expectancy_usd = round((win_prob * avg_win) - (loss_prob * avg_loss), 2)

        # Holding duration
        bars_held_list = [t.bars_held for t in trades]
        avg_bars = round(float(np.mean(bars_held_list)), 1) if bars_held_list else 0.0
        max_bars = max(bars_held_list) if bars_held_list else 0
        min_bars = min(bars_held_list) if bars_held_list else 0

        # Streaks
        cur_win_streak = max_win_streak = 0
        cur_loss_streak = max_loss_streak = 0
        for p in pnls:
            if p > 0.05:
                cur_win_streak += 1
                cur_loss_streak = 0
                max_win_streak = max(max_win_streak, cur_win_streak)
            elif p < -0.05:
                cur_loss_streak += 1
                cur_win_streak = 0
                max_loss_streak = max(max_loss_streak, cur_loss_streak)
            else:
                cur_win_streak = 0
                cur_loss_streak = 0

        # Sortino
        downside_returns = [p for p in pnls if p < 0]
        downside_std = np.std(downside_returns) if downside_returns else 0.0
        sortino = round((mean_ret / downside_std) * np.sqrt(365 * 4), 2) if downside_std > 0 else 0.0

        # CAGR & Calmar
        total_hours = len(macro_candles)
        years = max(total_hours / (24 * 365.25), 0.01)
        final_equity = equity
        cagr = round(((max(0.01, final_equity) / self.initial_capital) ** (1.0 / years) - 1.0) * 100.0, 2)
        calmar = round(cagr / max_dd_pct, 2) if max_dd_pct > 0 else 0.0

        return BacktestResult(
            symbol=symbol,
            total_trades=total_trades,
            winning_trades=len(wins),
            breakeven_trades=len(bes),
            losing_trades=len(losses),
            win_rate=win_rate,
            shield_rate=shield_rate,
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            total_fees=round(sum(t.fees for t in trades), 2),
            net_profit=round(net_profit, 2),
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            scenario_counts=scenario_counts,
            trades=trades,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            long_trades=len(long_trades_list),
            long_win_rate=long_wr,
            long_profit=long_profit,
            short_trades=len(short_trades_list),
            short_win_rate=short_wr,
            short_profit=short_profit,
            avg_win=avg_win,
            avg_loss=avg_loss,
            win_loss_ratio=win_loss_ratio,
            largest_win=largest_win,
            largest_loss=largest_loss,
            expectancy_usd=expectancy_usd,
            cagr_pct=cagr,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_consecutive_wins=max_win_streak,
            max_consecutive_losses=max_loss_streak,
            avg_bars_held=avg_bars,
            max_bars_held=max_bars,
            min_bars_held=min_bars,
            candles_count=len(macro_candles),
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="1-Minute Sub-Candle Replay Engine")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair symbol")
    parser.add_argument("--bars", type=int, default=1000, help="Number of 60m bars to evaluate")
    args = parser.parse_args()

    print(f"Initializing 1-Minute Sub-Candle Replay Engine for {args.symbol}...")
    engine = ReplayEngine(initial_capital=1000.0)

    # Generate or load candles
    t0 = time.time()
    # Synthetic dataset for quick self-test
    dates = [datetime.fromtimestamp(1700000000 + i * 3600).strftime("%Y-%m-%d %H:%M") for i in range(args.bars)]
    np.random.seed(42)
    p = 50000.0
    candles = []
    for i in range(args.bars):
        ret = np.random.normal(0.0002, 0.006)
        p_close = p * (1.0 + ret)
        p_high = max(p, p_close) * (1.0 + abs(np.random.normal(0, 0.003)))
        p_low = min(p, p_close) * (1.0 - abs(np.random.normal(0, 0.003)))
        candles.append({
            "timestamp": 1700000000 + i * 3600,
            "datetime": dates[i],
            "open": p,
            "high": p_high,
            "low": p_low,
            "close": p_close,
        })
        p = p_close

    res = engine.run_backtest(args.symbol, candles)
    print(f"Replay completed in {time.time() - t0:.3f}s:")
    print(f"  Total Trades : {res.total_trades} (Win Rate: {res.win_rate}%, Shield: {res.shield_rate}%)")
    print(f"  Net Realized : ${res.net_profit:+.2f} (PF: {res.profit_factor})")
    print(f"  Max Drawdown : ${res.max_drawdown:.2f} ({res.max_drawdown_pct}%)")
    print(f"  Scenarios    : {res.scenario_counts}")
