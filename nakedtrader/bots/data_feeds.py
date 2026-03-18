"""
data_feeds.py — Market data helpers (Kraken, Binance, Coinbase — geen auth nodig).

Functies:
  - _kraken_ohlc(pair, interval, count) -> dict met numpy arrays
  - _kraken_ticker(pair) -> Optional[float]  (cached, TTL 5s)
  - _kraken_orderbook(pair, count) -> dict met bids/asks
  - _binance_orderbook(symbol, limit) -> dict met bids/asks
  - _coinbase_orderbook(product_id, level) -> dict met bids/asks
  - _cross_exchange_orderbook(asset) -> dict met per-exchange books + spread info
  - _binance_funding_rate(symbol) -> dict met funding rate + OI
  - _kraken_futures_funding(symbol) -> dict met funding rate
"""

import logging
import time
from typing import Optional

import numpy as np
import requests

from nakedtrader.exceptions import KrakenError

log = logging.getLogger(__name__)

# ── Ticker cache (pair → (timestamp, price)) ────────────
_ticker_cache: dict[str, tuple[float, float]] = {}
_TICKER_TTL = 5.0  # seconden

# ── yfinance caches ─────────────────────────────────────
_yf_bars_cache: dict[str, tuple[float, dict]] = {}
_YF_BARS_TTL = 300.0  # 5 minuten
_yf_ticker_cache: dict[str, tuple[float, float]] = {}
_YF_TICKER_TTL = 60.0  # 1 minuut


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


# ── Binance public API ──────────────────────────────────────

def _binance_orderbook(symbol: str = "BTCUSDT", limit: int = 25) -> dict:
    """Haal orderboek op via Binance public Depth API (geen auth)."""
    resp = requests.get(
        "https://api.binance.com/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "bids": data.get("bids", []),
        "asks": data.get("asks", []),
    }


def _binance_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Haal funding rate + open interest op via Binance Futures (public, geen auth)."""
    result = {}
    # Funding rate
    resp = requests.get(
        "https://fapi.binance.com/fapi/v1/fundingRate",
        params={"symbol": symbol, "limit": 1},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data:
        result["funding_rate"] = float(data[0]["fundingRate"])
        result["funding_time"] = int(data[0].get("fundingTime", 0))

    # Open interest
    oi_resp = requests.get(
        "https://fapi.binance.com/fapi/v1/openInterest",
        params={"symbol": symbol},
        timeout=10,
    )
    oi_resp.raise_for_status()
    oi_data = oi_resp.json()
    if oi_data:
        result["open_interest"] = float(oi_data.get("openInterest", 0))

    return result


# ── Coinbase public API ──────────────────────────────────────

def _coinbase_orderbook(product_id: str = "BTC-USD", level: int = 2) -> dict:
    """Haal orderboek op via Coinbase Exchange API (public, geen auth)."""
    resp = requests.get(
        f"https://api.exchange.coinbase.com/products/{product_id}/book",
        params={"level": level},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "bids": data.get("bids", []),
        "asks": data.get("asks", []),
    }


# ── Kraken Futures API ───────────────────────────────────────

def _kraken_futures_funding(symbol: str = "PI_XBTUSD") -> dict:
    """Haal funding rate op via Kraken Futures (public, geen auth)."""
    resp = requests.get(
        "https://futures.kraken.com/derivatives/api/v3/tickers",
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    result = {}
    for ticker in data.get("tickers", []):
        if ticker.get("symbol", "").upper() == symbol.upper():
            result["funding_rate"] = ticker.get("fundingRate", 0)
            result["mark_price"] = ticker.get("markPrice", 0)
            result["open_interest"] = ticker.get("openInterest", 0)
            break
    return result


# ── Cross-exchange samenvoeging ──────────────────────────────

# Asset → per-exchange pair mapping (currency + pair)
_CROSS_PAIRS = {
    "BTC": {
        "kraken":   {"pair": "XXBTZEUR",  "currency": "EUR"},
        "binance":  {"pair": "BTCUSDT",   "currency": "USD"},
        "coinbase": {"pair": "BTC-USD",   "currency": "USD"},
    },
    "ETH": {
        "kraken":   {"pair": "XETHZEUR",  "currency": "EUR"},
        "binance":  {"pair": "ETHUSDT",   "currency": "USD"},
        "coinbase": {"pair": "ETH-USD",   "currency": "USD"},
    },
}


def _cross_exchange_orderbook(asset: str = "BTC") -> dict:
    """
    Haal orderbooks op van meerdere exchanges en bereken cross-exchange spread.
    Vergelijkt alleen exchanges met dezelfde currency (EUR-groep en USD-groep apart).
    """
    pairs = _CROSS_PAIRS.get(asset.upper(), {})
    books = {}

    for exchange, info in pairs.items():
        pair = info["pair"]
        currency = info["currency"]
        try:
            if exchange == "kraken":
                book = _kraken_orderbook(pair)
            elif exchange == "binance":
                book = _binance_orderbook(pair)
            elif exchange == "coinbase":
                book = _coinbase_orderbook(pair)
            else:
                continue

            best_bid = float(book["bids"][0][0]) if book["bids"] else 0
            best_ask = float(book["asks"][0][0]) if book["asks"] else 0
            books[exchange] = {
                "pair": pair,
                "currency": currency,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_pct": round((best_ask - best_bid) / best_bid * 100, 4) if best_bid else 0,
            }
        except Exception as e:
            log.warning("Orderbook %s/%s mislukt: %s", exchange, pair, e)

    result = {"exchanges": books, "arbitrage_by_currency": {}}

    # Bereken arbitrage alleen binnen dezelfde currency-groep
    for currency in ("EUR", "USD"):
        group = {ex: b for ex, b in books.items() if b["currency"] == currency and b["best_bid"] > 0}
        if len(group) < 2:
            continue
        all_bids = [(ex, b["best_bid"]) for ex, b in group.items()]
        all_asks = [(ex, b["best_ask"]) for ex, b in group.items()]
        best_bid_ex, best_bid = max(all_bids, key=lambda x: x[1])
        best_ask_ex, best_ask = min(all_asks, key=lambda x: x[1])
        arb_spread = best_bid - best_ask
        arb_pct = round(arb_spread / best_ask * 100, 4) if best_ask else 0
        result["arbitrage_by_currency"][currency] = {
            "buy_on": best_ask_ex,
            "sell_on": best_bid_ex,
            "spread": round(arb_spread, 2),
            "spread_pct": arb_pct,
            "profitable": arb_pct > 0.05,
        }

    return result


# ── yfinance data feeds (aandelen/ETFs fallback) ─────────────

def _yfinance_daily_bars(symbol: str, period: str = "1y") -> dict:
    """Haal dagelijkse bars op via yfinance (gratis, geen auth).

    Returns:
        dict met numpy arrays: open, high, low, close, volume, time.
        Leeg dict bij fout.
    """
    now = time.monotonic()
    cache_key = f"{symbol}_{period}"
    cached = _yf_bars_cache.get(cache_key)
    if cached and (now - cached[0]) < _YF_BARS_TTL:
        return cached[1]

    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        # Flatten multi-level columns (yfinance retourneert soms MultiIndex)
        if hasattr(df.columns, 'levels'):
            df.columns = df.columns.get_level_values(0)
        result = {
            "time": np.array([t.timestamp() for t in df.index], dtype=float),
            "open": df["Open"].to_numpy(dtype=float),
            "high": df["High"].to_numpy(dtype=float),
            "low": df["Low"].to_numpy(dtype=float),
            "close": df["Close"].to_numpy(dtype=float),
            "volume": df["Volume"].to_numpy(dtype=float),
        }
        _yf_bars_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        log.warning("yfinance bars %s mislukt: %s", symbol, e)
        return {}


def _yfinance_ticker(symbol: str) -> Optional[float]:
    """Haal laatste prijs op via yfinance (cached, TTL 60s)."""
    now = time.monotonic()
    cached = _yf_ticker_cache.get(symbol)
    if cached and (now - cached[0]) < _YF_TICKER_TTL:
        return cached[1]

    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        info = tk.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        if price and price > 0:
            _yf_ticker_cache[symbol] = (now, float(price))
            return float(price)
        return None
    except Exception as e:
        log.warning("yfinance ticker %s mislukt: %s", symbol, e)
        return None


# ── Gedeelde equity data helpers ──────────────────────────

def _equity_daily_bars(symbol: str, ibkr, ibkr_ok: bool, period: str = "1y") -> dict:
    """Haal dagelijkse bars op — IBKR als beschikbaar, yfinance als fallback.

    Returns:
        dict met numpy arrays: time, open, high, low, close, volume.
        Leeg dict als geen data beschikbaar.
    """
    if ibkr_ok:
        try:
            bars = ibkr.get_stock_bars(symbol, duration="1 Y", bar_size="1 day")
            if bars and len(bars) >= 50:
                return {
                    "time": np.array([
                        b.date.timestamp() if hasattr(b.date, "timestamp") else 0
                        for b in bars
                    ]),
                    "open": np.array([b.open for b in bars]),
                    "high": np.array([b.high for b in bars]),
                    "low": np.array([b.low for b in bars]),
                    "close": np.array([b.close for b in bars]),
                    "volume": np.array([float(b.volume) for b in bars]),
                }
        except Exception:
            pass
    return _yfinance_daily_bars(symbol, period=period)


def _equity_get_price(symbol: str, ibkr, ibkr_ok: bool) -> float:
    """Haal huidige prijs op — IBKR of yfinance. Returns 0.0 bij fout."""
    if ibkr_ok:
        try:
            p = ibkr.get_stock_price(symbol)
            if p > 0:
                return p
        except Exception:
            pass
    return _yfinance_ticker(symbol) or 0.0
