#!/usr/bin/env python3
"""
Test Run Storage Manager for Hyper Hedge Quantitative Research Suite.
Provides persistent serialization, unique test ID generation, deep-linking URL formatting,
and in-memory indexing for Backtest, Monte Carlo, RST, and Walk-Forward Optimization runs.
"""

import os
import sys
import json
import time
import secrets
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("TestStore")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(BASE_DIR, "research_runs")

# Ensure research_runs directory exists
os.makedirs(RUNS_DIR, exist_ok=True)

_LOCK = threading.Lock()
_RUNS_CACHE: Dict[str, Dict[str, Any]] = {}
_INDEX_LOADED = False


def _generate_test_id(test_type: str, symbol: str) -> str:
    """
    Generate a human-readable, unique, sortable test ID.
    Format: <prefix>_<symbol>_<YYYYMMDD_HHMMSS>_<hex4>
    Example: bt_btcusdt_20260911_032500_a1b2
    """
    prefix_map = {
        "backtest": "bt",
        "monte_carlo": "mc",
        "rst": "rst",
        "optimizer": "opt",
    }
    pfx = prefix_map.get(test_type.lower(), "test")
    clean_sym = symbol.lower().replace("/", "").replace("-", "") if symbol else "multi"
    now_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    rand_suffix = secrets.token_hex(2)
    return f"{pfx}_{clean_sym}_{now_str}_{rand_suffix}"


def _ensure_index_loaded():
    """Load lightweight run summaries from disk on first call."""
    global _INDEX_LOADED
    if _INDEX_LOADED:
        return

    with _LOCK:
        if _INDEX_LOADED:
            return
        try:
            for fname in os.listdir(RUNS_DIR):
                if fname.endswith(".json"):
                    test_id = fname[:-5]
                    fpath = os.path.join(RUNS_DIR, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            run_data = json.load(f)
                            _RUNS_CACHE[test_id] = run_data
                    except Exception as e:
                        logger.warning(f"Could not load run file {fname}: {e}")
            _INDEX_LOADED = True
            logger.info(f"Loaded {len(_RUNS_CACHE)} research runs into index.")
        except Exception as e:
            logger.error(f"Error initializing test store index: {e}")
            _INDEX_LOADED = True


def save_test_run(
    test_type: str,
    symbol: str,
    title: str,
    summary: Dict[str, Any],
    data: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    test_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist a research test run to disk and memory cache.
    Returns the metadata dictionary including test_id and test_url.
    """
    _ensure_index_loaded()

    if not test_id:
        test_id = _generate_test_id(test_type, symbol)

    created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    epoch_ts = int(time.time())

    run_obj = {
        "test_id": test_id,
        "test_type": test_type,
        "symbol": symbol.upper() if symbol else "MULTI",
        "title": title or f"{symbol} {test_type.replace('_', ' ').title()}",
        "created_at": created_at,
        "timestamp": epoch_ts,
        "summary": summary or {},
        "params": params or {},
        "data": data or {},
        "test_url": f"/dashboard?test_id={test_id}",
    }

    def _json_serialize_fallback(obj):
        if isinstance(obj, (datetime,)):
            return obj.isoformat()
        try:
            from decimal import Decimal
            if isinstance(obj, Decimal):
                return float(obj)
        except ImportError:
            pass
        try:
            import numpy as np
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.ndarray,)):
                return obj.tolist()
        except ImportError:
            pass
        return str(obj)

    fpath = os.path.join(RUNS_DIR, f"{test_id}.json")
    with _LOCK:
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(run_obj, f, indent=2, default=_json_serialize_fallback)
            _RUNS_CACHE[test_id] = run_obj
            logger.info(f"Saved research test {test_id} to {fpath}")
        except Exception as e:
            logger.error(f"Failed to save test run {test_id}: {e}", exc_info=True)
            raise e


    return {
        "test_id": test_id,
        "test_url": f"/dashboard?test_id={test_id}",
        "title": run_obj["title"],
        "symbol": run_obj["symbol"],
        "test_type": run_obj["test_type"],
        "created_at": created_at,
        "summary": run_obj["summary"],
    }


def get_test_run(test_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full test run object by test_id."""
    _ensure_index_loaded()

    with _LOCK:
        if test_id in _RUNS_CACHE:
            return _RUNS_CACHE[test_id]

    # Fallback to direct file read if not in cache
    fpath = os.path.join(RUNS_DIR, f"{test_id}.json")
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                run_obj = json.load(f)
            with _LOCK:
                _RUNS_CACHE[test_id] = run_obj
            return run_obj
        except Exception as e:
            logger.error(f"Failed to read test run file {fpath}: {e}")
    return None


def list_test_runs(limit: int = 50, test_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns sorted list of lightweight test summaries (newest first).
    """
    _ensure_index_loaded()

    with _LOCK:
        runs = list(_RUNS_CACHE.values())

    if test_type:
        runs = [r for r in runs if r.get("test_type") == test_type]

    # Sort descending by timestamp
    runs.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    runs_slice = runs[:limit]

    # Return lightweight summaries (exclude massive candlestick lists or raw bootstrap paths)
    summaries = []
    for r in runs_slice:
        summaries.append({
            "test_id": r.get("test_id"),
            "test_type": r.get("test_type"),
            "symbol": r.get("symbol"),
            "title": r.get("title"),
            "created_at": r.get("created_at"),
            "timestamp": r.get("timestamp"),
            "summary": r.get("summary", {}),
            "test_url": r.get("test_url", f"/dashboard?test_id={r.get('test_id')}"),
        })
    return summaries


def delete_test_run(test_id: str) -> bool:
    """Delete a test run from disk and in-memory cache."""
    _ensure_index_loaded()

    deleted = False
    fpath = os.path.join(RUNS_DIR, f"{test_id}.json")
    with _LOCK:
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                deleted = True
            except Exception as e:
                logger.error(f"Failed removing test run file {fpath}: {e}")
        if test_id in _RUNS_CACHE:
            del _RUNS_CACHE[test_id]
            deleted = True
    return deleted


__all__ = [
    "save_test_run",
    "get_test_run",
    "list_test_runs",
    "delete_test_run",
    "RUNS_DIR",
]
