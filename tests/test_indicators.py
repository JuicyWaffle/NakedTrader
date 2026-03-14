"""Unit tests for nakedtrader.indicators."""

import numpy as np
import pytest

from nakedtrader.indicators import (
    sma, ema, rsi, roc, bollinger_bands, atr,
    donchian_channels, zscore, vix_proxy, order_book_imbalance,
)


# ── Test data ──────────────────────────────────────

CLOSES = np.array([
    44.0, 44.3, 44.1, 43.6, 44.3, 44.8, 45.1, 45.4, 45.0, 44.5,
    44.2, 44.6, 45.0, 45.3, 45.5, 45.8, 46.0, 45.7, 45.4, 45.1,
    45.5, 45.8, 46.2, 46.5, 46.3, 46.0, 46.4, 46.8, 47.0, 46.7,
])

HIGHS = CLOSES + 0.3
LOWS = CLOSES - 0.3


class TestSMA:
    def test_basic(self):
        result = sma(CLOSES, 5)
        assert np.isnan(result[0])
        assert np.isnan(result[3])
        assert not np.isnan(result[4])

    def test_first_value(self):
        result = sma(CLOSES, 5)
        expected = np.mean(CLOSES[:5])
        assert abs(result[4] - expected) < 1e-10

    def test_period_equals_length(self):
        result = sma(CLOSES, len(CLOSES))
        assert np.isnan(result[-2])
        assert abs(result[-1] - np.mean(CLOSES)) < 1e-10

    def test_too_short(self):
        result = sma(np.array([1.0, 2.0]), 5)
        assert all(np.isnan(result))

    def test_period_1(self):
        result = sma(CLOSES, 1)
        np.testing.assert_array_almost_equal(result, CLOSES)


class TestEMA:
    def test_basic(self):
        result = ema(CLOSES, 10)
        assert np.isnan(result[0])
        assert np.isnan(result[8])
        assert not np.isnan(result[9])

    def test_seed_is_sma(self):
        result = ema(CLOSES, 10)
        expected_seed = np.mean(CLOSES[:10])
        assert abs(result[9] - expected_seed) < 1e-10

    def test_too_short(self):
        result = ema(np.array([1.0, 2.0]), 5)
        assert all(np.isnan(result))

    def test_monotonic_input(self):
        rising = np.arange(1.0, 31.0)
        result = ema(rising, 5)
        # EMA should be increasing for monotonically rising input
        valid = result[~np.isnan(result)]
        assert all(np.diff(valid) > 0)


class TestRSI:
    def test_basic(self):
        result = rsi(CLOSES, 14)
        assert np.isnan(result[0])
        valid = result[~np.isnan(result)]
        assert len(valid) > 0

    def test_bounds(self):
        result = rsi(CLOSES, 14)
        valid = result[~np.isnan(result)]
        assert all(0 <= v <= 100 for v in valid)

    def test_constant_rising(self):
        rising = np.arange(1.0, 30.0)
        result = rsi(rising, 14)
        valid = result[~np.isnan(result)]
        # All gains, no losses → RSI should be 100
        assert all(v == 100.0 for v in valid)

    def test_too_short(self):
        result = rsi(np.array([1.0, 2.0, 3.0]), 14)
        assert all(np.isnan(result))


class TestROC:
    def test_basic(self):
        result = roc(CLOSES, 5)
        assert np.isnan(result[0])
        assert not np.isnan(result[5])

    def test_calculation(self):
        result = roc(CLOSES, 1)
        # ROC[1] = (closes[1] - closes[0]) / closes[0] * 100
        expected = (CLOSES[1] - CLOSES[0]) / CLOSES[0] * 100
        assert abs(result[1] - expected) < 1e-10

    def test_flat(self):
        flat = np.ones(20) * 50.0
        result = roc(flat, 5)
        valid = result[~np.isnan(result)]
        assert all(abs(v) < 1e-10 for v in valid)


class TestBollingerBands:
    def test_shape(self):
        upper, middle, lower = bollinger_bands(CLOSES, 20, 2.0)
        assert len(upper) == len(CLOSES)
        assert len(middle) == len(CLOSES)
        assert len(lower) == len(CLOSES)

    def test_order(self):
        upper, middle, lower = bollinger_bands(CLOSES, 20, 2.0)
        for i in range(19, len(CLOSES)):
            assert upper[i] >= middle[i] >= lower[i]

    def test_middle_is_sma(self):
        upper, middle, lower = bollinger_bands(CLOSES, 20, 2.0)
        sma_result = sma(CLOSES, 20)
        np.testing.assert_array_almost_equal(middle, sma_result)


class TestATR:
    def test_basic(self):
        result = atr(HIGHS, LOWS, CLOSES, 14)
        assert np.isnan(result[0])
        assert not np.isnan(result[14])

    def test_positive(self):
        result = atr(HIGHS, LOWS, CLOSES, 14)
        valid = result[~np.isnan(result)]
        assert all(v > 0 for v in valid)

    def test_constant_range(self):
        h = np.ones(30) * 51.0
        l = np.ones(30) * 49.0
        c = np.ones(30) * 50.0
        result = atr(h, l, c, 14)
        valid = result[~np.isnan(result)]
        # ATR should be ~2.0 for constant range
        assert all(abs(v - 2.0) < 0.5 for v in valid)


class TestDonchianChannels:
    def test_shape(self):
        upper, lower, middle = donchian_channels(HIGHS, LOWS, 20)
        assert len(upper) == len(HIGHS)

    def test_order(self):
        upper, lower, middle = donchian_channels(HIGHS, LOWS, 10)
        for i in range(9, len(HIGHS)):
            assert upper[i] >= middle[i] >= lower[i]


class TestZScore:
    def test_basic(self):
        result = zscore(CLOSES, 20)
        assert np.isnan(result[0])
        assert not np.isnan(result[19])

    def test_constant_is_zero(self):
        flat = np.ones(30) * 100.0
        result = zscore(flat, 20)
        valid = result[~np.isnan(result)]
        assert all(abs(v) < 1e-10 for v in valid)


class TestVixProxy:
    def test_basic(self):
        result = vix_proxy(CLOSES, 20)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert all(v >= 0 for v in valid)


class TestOrderBookImbalance:
    def test_balanced(self):
        bids = [[100, 10], [99, 10]]
        asks = [[101, 10], [102, 10]]
        assert order_book_imbalance(bids, asks) == 0.0

    def test_all_bids(self):
        result = order_book_imbalance([[100, 10]], [])
        assert result == 1.0

    def test_all_asks(self):
        result = order_book_imbalance([], [[100, 10]])
        assert result == -1.0

    def test_empty(self):
        assert order_book_imbalance([], []) == 0.0

    def test_imbalanced(self):
        bids = [[100, 30]]
        asks = [[101, 10]]
        result = order_book_imbalance(bids, asks)
        assert abs(result - 0.5) < 1e-10  # (30-10)/(30+10)
