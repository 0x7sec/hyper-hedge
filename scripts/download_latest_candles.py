"""
download_latest_candles.py - Institutional-Grade Bybit Kline Downloader

Downloads and caches continuous historical Kline/Candle data from Bybit Unified Trading V5 API
using backward timestamp pagination. Caches results into scratch/*.pkl for instant replay.
"""

import os
import sys
import time
import pickle
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("KlineDownloader")


def download_candles_for_symbol(
    symbol: str,
    bars: int = 8000,
    interval: str = "60",
    out_dir: str = "scratch",
    testnet: bool = False,
) -> Dict[str, Any]:
    """
    Downloads historical candles for a single symbol using backward pagination.
    Saves to {out_dir}/{symbol.lower()}_{interval}m_cache.pkl.
    """
    try:
        from pybit.unified_trading import HTTP
    except ImportError:
        logger.error("pybit is required. Install via `pip install pybit`.")
        return {"error": "pybit not installed"}

    session = HTTP(testnet=testnet)
    all_raw: List[List[str]] = []
    end_time: Optional[int] = None
    remaining = bars
    logger.info(f"Downloading up to {bars} {interval}m bars for {symbol}...")

    while remaining > 0:
        batch_limit = min(remaining, 1000)
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": batch_limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        try:
            res = session.get_kline(**params)
        except Exception as ex:
            logger.error(f"Error calling Bybit get_kline for {symbol}: {ex}")
            break

        if res.get("retCode") != 0:
            logger.warning(f"Bybit API retCode {res.get('retCode')}: {res.get('retMsg')}")
            break

        batch = res.get("result", {}).get("list", [])
        if not batch:
            break

        all_raw.extend(batch)
        remaining -= len(batch)

        oldest_ts = int(batch[-1][0])
        end_time = oldest_ts - 1

        if len(batch) < batch_limit:
            break  # Reached the beginning of available history

        time.sleep(0.04)

    if not all_raw:
        logger.error(f"No candles retrieved for {symbol}.")
        return {"symbol": symbol, "count": 0, "error": "No data returned"}

    # Bybit returns newest first -> reverse to chronological order (oldest -> newest)
    all_raw.reverse()

    candles: List[Dict[str, Any]] = []
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

    os.makedirs(out_dir, exist_ok=True)
    sym_clean = symbol.lower().replace("-", "").replace("/", "")
    cache_path = os.path.join(out_dir, f"{sym_clean}_{interval}m_cache.pkl")

    with open(cache_path, "wb") as f:
        pickle.dump(candles, f)

    file_size_kb = round(os.path.getsize(cache_path) / 1024.0, 1)
    start_dt = candles[0]["datetime"]
    end_dt = candles[-1]["datetime"]
    start_px = candles[0]["close"]
    end_px = candles[-1]["close"]
    price_change_pct = round(((end_px - start_px) / start_px) * 100.0, 2)

    logger.info(
        f"✓ {symbol}: Saved {len(candles)} candles to {cache_path} ({file_size_kb} KB) "
        f"[{start_dt} -> {end_dt}] (${start_px:.2f} -> ${end_px:.2f}, {price_change_pct:+}%)"
    )

    return {
        "symbol": symbol,
        "count": len(candles),
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "start_price": start_px,
        "end_price": end_px,
        "price_change_pct": price_change_pct,
        "cache_path": cache_path,
        "file_size_kb": file_size_kb,
    }


def download_candles_for_symbols(
    symbols: List[str],
    bars: int = 8000,
    interval: str = "60",
    out_dir: str = "scratch",
    testnet: bool = False,
) -> Dict[str, Any]:
    """Downloads candles for multiple symbols and returns aggregated summary."""
    results = {}
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym:
            continue
        res = download_candles_for_symbol(
            symbol=sym,
            bars=bars,
            interval=interval,
            out_dir=out_dir,
            testnet=testnet,
        )
        results[sym] = res
    return results


def main():
    parser = argparse.ArgumentParser(description="Download latest Bybit klines.")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,PAXGUSDT", help="Comma-separated symbols")
    parser.add_argument("--bars", type=int, default=8000, help="Number of bars to fetch backwards (default: 8000)")
    parser.add_argument("--interval", type=str, default="60", help="Candle interval in minutes (default: 60)")
    parser.add_argument("--out-dir", type=str, default="scratch", help="Output directory for caches (default: scratch)")
    parser.add_argument("--testnet", action="store_true", help="Fetch from Bybit testnet instead of mainnet")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"================================================================")
    print(f"Bybit Kline Historical Downloader")
    print(f"Symbols: {symbols} | Target Bars: {args.bars} | Interval: {args.interval}m")
    print(f"================================================================")

    res = download_candles_for_symbols(
        symbols=symbols,
        bars=args.bars,
        interval=args.interval,
        out_dir=args.out_dir,
        testnet=args.testnet,
    )

    print("\nSummary Results:")
    for sym, d in res.items():
        if "error" in d:
            print(f"  {sym}: ERROR - {d['error']}")
        else:
            print(f"  {sym}: {d['count']} bars [{d['start_datetime']} -> {d['end_datetime']}] ({d['file_size_kb']} KB)")


if __name__ == "__main__":
    main()
