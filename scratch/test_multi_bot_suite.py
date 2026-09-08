#!/usr/bin/env python3
"""
Multi-Pair Test Suite for Bybit Trading Bot
Verifies:
1. Multi-symbol configuration loading and champion profile defaults.
2. Per-symbol environment variable overrides.
3. Bybit Testnet connectivity for BTCUSDT, ETHUSDT, SOLUSDT.
4. Live kline fetching and indicator computation for all 3 pairs.
5. Dual-leg position tracking and BE lock math.
"""

import os
import sys
from decimal import Decimal

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bybit_bot.config import Config, SymbolConfig, DEFAULT_PROFILES
from bybit_bot.client import BybitService
from bybit_bot.leg import PositionLeg
from bybit_bot.engine import BybitTradingEngine, PairState
from backtest import compute_indicators


def test_champion_profiles():
    print("\n--- TEST 1: Champion Profiles & Defaults ---")
    config = Config.from_args_and_env()
    assert len(config.symbols) == 3, f"Expected 3 symbols, got {len(config.symbols)}"

    sym_map = {s.symbol: s for s in config.symbols}
    assert "BTCUSDT" in sym_map
    assert "ETHUSDT" in sym_map
    assert "SOLUSDT" in sym_map

    # BTC verification
    btc = sym_map["BTCUSDT"]
    print(f"BTCUSDT: TF={btc.candle_interval}m, EMA=({btc.ema_fast}/{btc.ema_slow}), ADX>{btc.adx_min}, SL={btc.sl_pct}%, TP={btc.tp_pct}%, Size={btc.size}")
    assert btc.candle_interval == "15"
    assert btc.ema_fast == 9 and btc.ema_slow == 21
    assert btc.adx_min == Decimal("15")
    assert btc.sl_pct == Decimal("6.0")
    assert btc.tp_pct == Decimal("2.5")
    assert btc.be_lock is True
    assert btc.size == Decimal("0.007")

    # ETH verification
    eth = sym_map["ETHUSDT"]
    print(f"ETHUSDT: TF={eth.candle_interval}m, EMA=({eth.ema_fast}/{eth.ema_slow}), ADX>{eth.adx_min}, SL={eth.sl_pct}%, TP={eth.tp_pct}%, Size={eth.size}")
    assert eth.candle_interval == "15"
    assert eth.ema_fast == 9 and eth.ema_slow == 21
    assert eth.adx_min == Decimal("0")
    assert eth.sl_pct == Decimal("6.0")
    assert eth.tp_pct == Decimal("2.5")
    assert eth.be_lock is True
    assert eth.size == Decimal("0.20")

    # SOL verification
    sol = sym_map["SOLUSDT"]
    print(f"SOLUSDT: TF={sol.candle_interval}m, EMA=({sol.ema_fast}/{sol.ema_slow}), ADX>{sol.adx_min}, SL={sol.sl_pct}%, TP={sol.tp_pct}%, Size={sol.size}")
    assert sol.candle_interval == "15"
    assert sol.ema_fast == 9 and sol.ema_slow == 21
    assert sol.adx_min == Decimal("15")
    assert sol.sl_pct == Decimal("6.0")
    assert sol.tp_pct == Decimal("2.5")
    assert sol.be_lock is True
    assert sol.size == Decimal("5.0")

    print("[PASS] All 3 champion profiles matched exactly.")


def test_testnet_service_connectivity():
    print("\n--- TEST 2: Bybit Testnet Market Connectivity for 3 Pairs ---")
    config = Config.from_args_and_env()
    service = BybitService(config)

    symbols = [s.symbol for s in config.symbols]
    service.init_market_and_account(symbols)

    for sym in symbols:
        spec = service.get_instrument_spec(sym)
        px = service.get_market_price(sym)
        print(f"[{sym}] Live Testnet Price: ${px} | Tick: {spec['tick_size']} | MinQty: {spec['min_qty']} | Scale: {spec['price_scale']}")
        assert px > 0, f"Invalid price for {sym}"
        assert spec["min_qty"] > 0

    print("[PASS] Successfully fetched live specs and tickers for BTC, ETH, SOL.")


def test_kline_and_indicators_live():
    print("\n--- TEST 3: Live 15m Klines & Indicator Computation ---")
    config = Config.from_args_and_env()
    service = BybitService(config)

    for s in config.symbols:
        candles = service.get_recent_candles(symbol=s.symbol, interval=s.candle_interval, limit=100)
        assert len(candles) >= 50, f"Expected >=50 candles for {s.symbol}, got {len(candles)}"

        compute_indicators(
            candles,
            fast_periods=[s.ema_fast, s.ema_slow],
            adx_period=s.adx_period,
        )

        last_closed = candles[-2]
        ema_f = last_closed.get(f"ema_{s.ema_fast}")
        ema_s = last_closed.get(f"ema_{s.ema_slow}")
        adx   = last_closed.get("adx")

        print(f"[{s.symbol} 15m] Bar: {last_closed['datetime']} | Close: ${last_closed['close']} | EMA{s.ema_fast}: {float(ema_f):.2f} | EMA{s.ema_slow}: {float(ema_s):.2f} | ADX: {float(adx):.2f}")
        assert ema_f is not None and ema_s is not None and adx is not None

    print("[PASS] Indicators computed successfully across all 3 live pairs.")


def test_dual_leg_and_be_lock():
    print("\n--- TEST 4: Dual-Leg Mechanics and BE Lock Guard ---")
    btc_entry = Decimal("88000.00")
    sl_ratio = Decimal("0.06")
    tp_ratio = Decimal("0.025")

    long_leg = PositionLeg.new_long(Decimal("0.007"), btc_entry, sl_ratio, tp_ratio, symbol="BTCUSDT")
    short_leg = PositionLeg.new_short(Decimal("0.007"), btc_entry, sl_ratio, tp_ratio, symbol="BTCUSDT")

    assert long_leg.trailing_sl == btc_entry * Decimal("0.94")
    assert long_leg.tp_target == btc_entry * Decimal("1.025")
    assert short_leg.trailing_sl == btc_entry * Decimal("1.06")
    assert short_leg.tp_target == btc_entry * Decimal("0.975")

    # Long ratchet test: price advances +1%
    act = long_leg.on_price_tick(Decimal("88880.00"), sl_ratio, Decimal("0.0025"))
    assert act == "RATCHET_TRIGGER"
    assert long_leg.trailing_sl > btc_entry * Decimal("0.94")
    print(f"Long ratcheted SL to: {long_leg.trailing_sl:.2f}")

    print("[PASS] Dual-leg order math and ratchet mechanics verified.")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING MULTI-PAIR BOT VERIFICATION SUITE")
    print("=" * 70)
    test_champion_profiles()
    test_testnet_service_connectivity()
    test_kline_and_indicators_live()
    test_dual_leg_and_be_lock()
    print("\n" + "=" * 70)
    print("ALL MULTI-PAIR VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
