"""
funding_rate.py — Funding Rate Contrarian strategie.

Extreme perpetual funding rates voorspellen correcties:
- Hoge positieve funding (>0.05%) = crowded long → short signaal
- Sterk negatieve funding (<-0.03%) = short squeeze risico → long signaal
Bevestiging via RSI divergentie of orderbook flip.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import rsi
from .base import BaseStrategy
from .data_feeds import _binance_funding_rate, _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


class FundingRateStrategy(BaseStrategy):
    """Funding Rate Contrarian — handelt tegen extreme futures positioning."""

    meta = StrategyMeta(
        id="funding-contrarian",
        name="Funding Contrarian",
        color="#ff8800",
        description="Trades against extreme perpetual funding rates. High positive funding = short, extreme negative = long.",
        description_nl="Handelt tegen extreme perpetual funding rates. Hoge positieve funding = short, extreem negatief = long.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=10.0,
        expected_return_max=35.0,
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["Funding Rate", "RSI(14)", "Open Interest"],
        timeframe="8h",
        broker="kraken",
    )

    PAIRS = {
        "BTC/EUR": {"kraken": "XXBTZEUR", "binance": "BTCUSDT"},
        "ETH/EUR": {"kraken": "XETHZEUR", "binance": "ETHUSDT"},
    }

    HIGH_FUNDING_THRESHOLD = 0.0005    # 0.05% per 8h
    LOW_FUNDING_THRESHOLD = -0.0003    # -0.03% per 8h

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        for market, pair_map in self.PAIRS.items():
            try:
                # Haal funding rate op
                fr_data = _binance_funding_rate(pair_map["binance"])
                rate = fr_data.get("funding_rate", 0)
                oi = fr_data.get("open_interest", 0)

                # Check of funding rate extreem genoeg is
                if aggressive:
                    high_threshold = self.HIGH_FUNDING_THRESHOLD * 0.7
                    low_threshold = self.LOW_FUNDING_THRESHOLD * 0.7
                else:
                    high_threshold = self.HIGH_FUNDING_THRESHOLD
                    low_threshold = self.LOW_FUNDING_THRESHOLD

                if low_threshold <= rate <= high_threshold:
                    continue  # Neutraal, geen signaal

                # RSI bevestiging
                data = _kraken_ohlc(pair_map["kraken"], interval=60, count=200)
                if not data:
                    continue
                closes = data["close"]
                rsi_vals = rsi(closes, 14)
                latest_rsi = rsi_vals[-1] if not np.isnan(rsi_vals[-1]) else 50

                # High funding + overbought RSI = sterker short signaal
                if rate > high_threshold:
                    if latest_rsi < 40 and not aggressive:
                        continue  # RSI zegt nog geen correctie
                    direction = "short"
                    strength = min(abs(rate) / 0.001, 1.0)
                    notes = f"Funding Contrarian SHORT {market}: funding={rate*100:.4f}% RSI={latest_rsi:.1f} OI={oi:.0f}"

                # Low funding + oversold RSI = sterker long signaal
                elif rate < low_threshold:
                    if latest_rsi > 60 and not aggressive:
                        continue  # RSI zegt nog geen bounce
                    direction = "long"
                    strength = min(abs(rate) / 0.001, 1.0)
                    notes = f"Funding Contrarian LONG {market}: funding={rate*100:.4f}% RSI={latest_rsi:.1f} OI={oi:.0f}"
                else:
                    continue

                price = _kraken_ticker(pair_map["kraken"]) or float(closes[-1])
                win_rate = self._get_rolling_win_rate()

                signals.append(TradeSignal(
                    symbol=pair_map["kraken"],
                    broker="kraken",
                    direction=direction,
                    win_probability=max(0.50, min(0.70, win_rate + strength * 0.05)),
                    expected_win_pct=0.08,
                    expected_loss_pct=0.04,
                    current_price=price,
                    strategy_id="funding-contrarian",
                    notes=notes,
                ))
            except Exception as e:
                log.warning("Funding Contrarian fout %s: %s", market, e)
        return signals
