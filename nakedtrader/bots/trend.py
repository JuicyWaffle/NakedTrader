"""
trend.py — Macro Trend (TrendFollowingStrategy).

SMA(200) richting + regime filter (SPY vs TLT).
Risk-on = SPY/TLT stijgend -> agressiever.
Risk-off = SPY/TLT dalend -> defensief of skip.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma
from .base import BaseStrategy

log = logging.getLogger(__name__)


class TrendFollowingStrategy(BaseStrategy):
    """
    SMA(200) richting + regime filter (SPY vs TLT).
    Risk-on = SPY/TLT stijgend -> agressiever.
    Risk-off = SPY/TLT dalend -> defensief of skip.
    """

    meta = StrategyMeta(
        id="trend-follow",
        name="Macro Trend",
        color="#ffaa44",
        description="SMA(200) trend with SPY/TLT regime filter. Risk-on/risk-off detection adjusts position sizing.",
        description_nl="SMA(200) trend met SPY/TLT regime filter. Risk-on/risk-off detectie past positiegrootte aan.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=5.0,
        expected_return_max=20.0,
        markets=["EUR/USD", "SPY", "TLT"],
        indicators=["SMA(200)", "SPY/TLT ratio"],
        timeframe="1d",
        broker="ibkr",
    )

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        try:
            if not (ibkr and ibkr.ib and ibkr.ib.isConnected()):
                log.info("Macro Trend: geen IBKR verbinding, skip")
                return signals

            # Haal EURUSD bars op
            bars = ibkr.get_historical_bars(
                symbol="EUR",
                duration="1 Y",
                bar_size="1 day",
                what_to_show="MIDPOINT",
                currency="USD",
            )
            if len(bars) < 201:
                return signals
            closes = np.array([b.close for b in bars])

            sma200 = sma(closes, 200)
            if np.isnan(sma200[-1]):
                return signals

            # Regime detectie via SPY/TLT ratio
            regime = "neutral"
            try:
                spy_bars = ibkr.get_stock_bars("SPY", duration="60 D", bar_size="1 day")
                tlt_bars = ibkr.get_stock_bars("TLT", duration="60 D", bar_size="1 day")

                if spy_bars and tlt_bars and len(spy_bars) >= 20 and len(tlt_bars) >= 20:
                    spy_closes = np.array([b.close for b in spy_bars])
                    tlt_closes = np.array([b.close for b in tlt_bars])

                    # SPY/TLT ratio: stijgend = risk-on, dalend = risk-off
                    ratio = spy_closes / tlt_closes
                    ratio_sma = sma(ratio, 20)

                    if not np.isnan(ratio_sma[-1]) and not np.isnan(ratio_sma[-5]):
                        if ratio[-1] > ratio_sma[-1] and ratio_sma[-1] > ratio_sma[-5]:
                            regime = "risk-on"
                        elif ratio[-1] < ratio_sma[-1] and ratio_sma[-1] < ratio_sma[-5]:
                            regime = "risk-off"
            except Exception as e:
                log.debug(f"Regime detectie fout: {e}")

            # Bewaar regime in custom state
            self._set_custom_state("regime", regime)

            # Signaal: prijs boven SMA200 + niet risk-off
            if closes[-1] > sma200[-1] and regime != "risk-off":
                win_rate = self._get_rolling_win_rate()

                # Risk-on -> hogere allocatie
                win_adj = 0.02 if regime == "risk-on" else 0.0

                signals.append(TradeSignal(
                    symbol="EURUSD",
                    broker="ibkr",
                    direction="long",
                    win_probability=max(0.45, min(0.65, win_rate + win_adj)),
                    expected_win_pct=0.05,
                    expected_loss_pct=0.03,
                    current_price=float(closes[-1]),
                    strategy_id="trend-follow",
                    notes=f"Macro Trend EURUSD: boven SMA200 regime={regime}",
                ))
        except Exception as e:
            log.warning(f"Macro Trend fout: {e}")
        return signals
