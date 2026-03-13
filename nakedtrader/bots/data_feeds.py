"""
data_feeds.py — Kraken public API helpers (geen auth nodig).

Functies:
  - _kraken_ohlc(pair, interval, count) -> dict met numpy arrays
  - _kraken_ticker(pair) -> Optional[float]
  - _kraken_orderbook(pair, count) -> dict met bids/asks
"""

import logging
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


def _kraken_ohlc(pair: str, interval: int = 60, count: int = 200) -> dict:
    """
    Haal OHLC data op via Kraken public API.
    Retourneert dict met numpy arrays: open, high, low, close, volume, time.
    """
    import krakenex
    api = krakenex.API()
    since = int(time.time()) - count * interval * 60
    response = api.query_public("OHLC", {"pair": pair, "interval": interval, "since": since})
    errors = response.get("error", [])
    if errors:
        log.warning(f"Kraken OHLC fout {pair}: {errors}")
        return {}
    result = response["result"]
    ohlc_key = [k for k in result if k != "last"][0]
    rows = result[ohlc_key]
    if not rows:
        return {}
    return {
        "time":   np.array([r[0] for r in rows], dtype=float),
        "open":   np.array([float(r[1]) for r in rows]),
        "high":   np.array([float(r[2]) for r in rows]),
        "low":    np.array([float(r[3]) for r in rows]),
        "close":  np.array([float(r[4]) for r in rows]),
        "volume": np.array([float(r[6]) for r in rows]),
    }


def _kraken_ticker(pair: str) -> Optional[float]:
    """Haal laatste prijs op via Kraken public ticker."""
    import krakenex
    api = krakenex.API()
    response = api.query_public("Ticker", {"pair": pair})
    errors = response.get("error", [])
    if errors:
        return None
    result = response["result"]
    key = list(result.keys())[0]
    return float(result[key]["c"][0])  # laatste trade prijs


def _kraken_orderbook(pair: str, count: int = 25) -> dict:
    """Haal orderboek op via Kraken public Depth API."""
    import krakenex
    api = krakenex.API()
    response = api.query_public("Depth", {"pair": pair, "count": count})
    errors = response.get("error", [])
    if errors:
        log.warning(f"Kraken orderbook fout {pair}: {errors}")
        return {"bids": [], "asks": []}
    result = response["result"]
    book_key = [k for k in result][0]
    return {
        "bids": result[book_key].get("bids", []),
        "asks": result[book_key].get("asks", []),
    }
