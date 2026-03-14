"""
data_feeds.py — Kraken public API helpers (geen auth nodig).

Functies:
  - _kraken_ohlc(pair, interval, count) -> dict met numpy arrays
  - _kraken_ticker(pair) -> Optional[float]  (cached, TTL 5s)
  - _kraken_orderbook(pair, count) -> dict met bids/asks
"""

import logging
import time
from typing import Optional

import numpy as np

from nakedtrader.exceptions import KrakenError

log = logging.getLogger(__name__)

# ── Ticker cache (pair → (timestamp, price)) ────────────
_ticker_cache: dict[str, tuple[float, float]] = {}
_TICKER_TTL = 5.0  # seconden


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
        raise KrakenError(f"OHLC {pair}: {errors}")
    result = response["result"]
    ohlc_keys = [k for k in result if k != "last"]
    if not ohlc_keys:
        log.warning("OHLC %s: geen data in response", pair)
        return {}
    ohlc_key = ohlc_keys[0]
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
    """Haal laatste prijs op via Kraken public ticker (cached, TTL 5s)."""
    now = time.monotonic()
    cached = _ticker_cache.get(pair)
    if cached and (now - cached[0]) < _TICKER_TTL:
        return cached[1]

    import krakenex
    api = krakenex.API()
    response = api.query_public("Ticker", {"pair": pair})
    errors = response.get("error", [])
    if errors:
        return None
    result = response["result"]
    if not result:
        log.warning("Ticker %s: lege response", pair)
        return None
    key = list(result.keys())[0]
    price = float(result[key]["c"][0])  # laatste trade prijs
    _ticker_cache[pair] = (now, price)
    return price


def _kraken_orderbook(pair: str, count: int = 25) -> dict:
    """Haal orderboek op via Kraken public Depth API."""
    import krakenex
    api = krakenex.API()
    response = api.query_public("Depth", {"pair": pair, "count": count})
    errors = response.get("error", [])
    if errors:
        raise KrakenError(f"Orderbook {pair}: {errors}")
    result = response["result"]
    if not result:
        raise KrakenError(f"Orderbook {pair}: lege response")
    book_key = list(result.keys())[0]
    return {
        "bids": result[book_key].get("bids", []),
        "asks": result[book_key].get("asks", []),
    }
