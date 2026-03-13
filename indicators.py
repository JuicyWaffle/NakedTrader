"""
indicators.py — Technische indicatoren (pure numpy, geen extra dependencies).

Functies:
  sma(closes, period) -> np.ndarray
  ema(closes, period) -> np.ndarray
  rsi(closes, period=14) -> np.ndarray
  roc(closes, period=12) -> np.ndarray
  bollinger_bands(closes, period=20, std_dev=2.0) -> (upper, middle, lower)
  atr(highs, lows, closes, period=14) -> np.ndarray
"""

import numpy as np


def sma(closes: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average. Eerste period-1 waarden zijn NaN."""
    out = np.full_like(closes, np.nan, dtype=float)
    if len(closes) < period:
        return out
    cumsum = np.cumsum(closes, dtype=float)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    out[period - 1:] = cumsum[period - 1:] / period
    return out


def ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average. Seed = SMA van eerste period waarden."""
    out = np.full_like(closes, np.nan, dtype=float)
    if len(closes) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = np.mean(closes[:period])
    for i in range(period, len(closes)):
        out[i] = alpha * closes[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index (Wilder's smoothing)."""
    out = np.full_like(closes, np.nan, dtype=float)
    if len(closes) < period + 1:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    # Eerste RSI-waarde
    if avg_loss == 0:
        out[period] = 100.0
    else:
        first_avg_gain = np.mean(gains[:period])
        first_avg_loss = np.mean(losses[:period])
        if first_avg_loss == 0:
            out[period] = 100.0
        else:
            out[period] = 100.0 - (100.0 / (1.0 + first_avg_gain / first_avg_loss))

    return out


def roc(closes: np.ndarray, period: int = 12) -> np.ndarray:
    """Rate of Change (%). ROC = (close - close[n periods ago]) / close[n periods ago] * 100."""
    out = np.full_like(closes, np.nan, dtype=float)
    if len(closes) <= period:
        return out
    out[period:] = (closes[period:] - closes[:-period]) / closes[:-period] * 100.0
    return out


def bollinger_bands(
    closes: np.ndarray, period: int = 20, std_dev: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands. Retourneert (upper, middle, lower)."""
    middle = sma(closes, period)
    std = np.full_like(closes, np.nan, dtype=float)
    for i in range(period - 1, len(closes)):
        std[i] = np.std(closes[i - period + 1 : i + 1], ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> np.ndarray:
    """Average True Range (Wilder's smoothing)."""
    out = np.full(len(closes), np.nan, dtype=float)
    if len(closes) < 2:
        return out
    # True Range
    tr = np.empty(len(closes), dtype=float)
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    if len(closes) < period + 1:
        return out
    out[period] = np.mean(tr[1 : period + 1])
    for i in range(period + 1, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out
