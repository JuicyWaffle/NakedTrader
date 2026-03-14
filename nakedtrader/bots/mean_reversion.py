"""
mean_reversion.py — Mean Reversion strategie.

Bollinger Bands + Z-score < -2.0 -> long.
Zelfaanpassing: elke 50 trades herberekening BB-multiplier o.b.v. win-rate.
SL verbreed bij lage VIX proxy.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import bollinger_bands, zscore, vix_proxy
from .base import BaseStrategy
from .data_feeds import _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    Bollinger Bands + Z-score < -2.0 -> long.
    Zelfaanpassing: elke 50 trades herberekening BB-multiplier o.b.v. win-rate.
    SL verbreed bij lage VIX proxy.
    """

    meta = StrategyMeta(
        id="mean-reversion",
        name="Mean Reversion",
        color="#0088ff",
        description="Bollinger Bands + Z-score below -2.0 for mean reversion entries. BB multiplier adjusts based on rolling win rate.",
        description_nl="Bollinger Bands + Z-score onder -2.0 voor mean reversion entries. BB-multiplier past zich aan op basis van rolling win-rate.",
        risk_level="Conservatief",
        risk_score=2,
        expected_return_min=8.0,
        expected_return_max=25.0,
        markets=["BTC/EUR", "ETH/EUR", "SOL/EUR"],
        indicators=["BB(20)", "Z-score(20)", "VIX proxy"],
        timeframe="15m",
        broker="ibkr",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR", "SOL/EUR": "SOLEUR"}

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        # Zelfaanpassing: BB-multiplier o.b.v. win-rate
        custom = self._get_custom_state()
        bb_mult = custom.get("bb_multiplier", 2.0)
        trade_count = custom.get("trade_count_since_recalc", 0)

        # Elke 50 trades: herberekening
        win_rate = self._get_rolling_win_rate()
        if trade_count >= 50:
            if win_rate > 0.60:
                bb_mult = max(1.5, bb_mult - 0.1)  # strenger (meer signalen)
            elif win_rate < 0.45:
                bb_mult = min(2.5, bb_mult + 0.1)  # losser (minder signalen)
            self._set_custom_state("bb_multiplier", round(bb_mult, 2))
            self._set_custom_state("trade_count_since_recalc", 0)

        for market, pair in self.PAIRS.items():
            try:
                data = _kraken_ohlc(pair, interval=15, count=200)
                if not data:
                    continue
                closes = data["close"]

                upper, middle, lower = bollinger_bands(closes, 20, bb_mult)
                z_vals = zscore(closes, 20)
                vix = vix_proxy(closes, 20)

                latest_z = z_vals[-1]
                latest_close = closes[-1]
                latest_lower = lower[-1]
                latest_vix = vix[-1] if not np.isnan(vix[-1]) else 20.0

                if np.isnan(latest_z) or np.isnan(latest_lower):
                    continue

                # Signaal: prijs onder BB + Z-score sterk negatief
                z_threshold = -1.2 if aggressive else -2.0
                if latest_close < latest_lower and latest_z < z_threshold:
                    price = _kraken_ticker(pair) or float(latest_close)

                    # Lage VIX -> markt is rustig -> verbreed SL
                    sl_adj = 0.04 if latest_vix < 15 else 0.03

                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="ibkr",
                        direction="long",
                        win_probability=max(0.50, min(0.70, win_rate)),
                        expected_win_pct=0.08,
                        expected_loss_pct=sl_adj,
                        current_price=price,
                        strategy_id="mean-reversion",
                        notes=f"Mean Reversion {market}: Z={latest_z:.2f} BB({bb_mult:.1f}) VIX={latest_vix:.1f}",
                    ))
            except Exception as e:
                log.warning(f"Mean Reversion fout {pair}: {e}")
        return signals
