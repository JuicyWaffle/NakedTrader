"""
trend.py — Macro Trend (TrendFollowingStrategy).

SMA(200) richting + regime filter.
IBKR mode: SPY/TLT ratio als regime indicator.
Kraken fallback: BTC als macro proxy met VIX proxy + Fear & Greed.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, ema, vix_proxy
from .base import BaseStrategy
from .data_feeds import _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


class TrendFollowingStrategy(BaseStrategy):
    """
    SMA(200) richting + regime filter.
    Dual mode: IBKR (SPY/TLT) of Kraken (BTC als macro proxy).
    """

    meta = StrategyMeta(
        id="trend-follow",
        name="Macro Trend",
        color="#ffaa44",
        description="SMA(200) trend with regime filter. Uses SPY/TLT (IBKR) or BTC macro proxy (Kraken) for risk-on/risk-off detection.",
        description_nl="SMA(200) trend met regime filter. Gebruikt SPY/TLT (IBKR) of BTC macro proxy (Kraken) voor risk-on/risk-off detectie.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=5.0,
        expected_return_max=20.0,
        markets=["BTC/EUR", "EUR/USD"],
        indicators=["SMA(200)", "EMA(50)", "VIX proxy", "Fear & Greed"],
        timeframe="1d",
        broker="kraken",
    )

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        # Probeer IBKR mode eerst
        ibkr_connected = ibkr and hasattr(ibkr, "ib") and ibkr.ib and ibkr.ib.isConnected()
        if ibkr_connected:
            signals = self._ibkr_signals(ibkr, aggressive)

        # Kraken fallback: BTC als macro proxy
        if not signals:
            signals = self._kraken_signals(aggressive)

        return self._llm_enhance_signals(signals)

    def _kraken_signals(self, aggressive: bool) -> list[TradeSignal]:
        """Kraken-gebaseerde trend signalen met BTC als macro proxy."""
        signals = []
        try:
            # Dagelijkse BTC data (4h candles, 200 bars = ~33 dagen)
            data = _kraken_ohlc("XXBTZEUR", interval=240, count=200)
            if not data or len(data["close"]) < 50:
                return signals
            closes = data["close"]

            sma50 = sma(closes, 50)
            ema20 = ema(closes, 20)

            if np.isnan(sma50[-1]) or np.isnan(ema20[-1]):
                return signals

            # Regime via VIX proxy + trend richting
            vix_vals = vix_proxy(closes, 20)
            latest_vix = float(vix_vals[-1]) if not np.isnan(vix_vals[-1]) else 20.0

            # Fear & Greed als macro-regime indicator
            regime = "neutral"
            fg_note = ""
            try:
                import requests
                resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
                fg_val = int(resp.json().get("data", [{}])[0].get("value", 50))
                fg_note = f" F&G={fg_val}"
                if fg_val >= 60 and closes[-1] > sma50[-1]:
                    regime = "risk-on"
                elif fg_val <= 30 or closes[-1] < sma50[-1]:
                    regime = "risk-off"
            except Exception:
                # Fallback: puur technisch
                if closes[-1] > sma50[-1] and ema20[-1] > sma50[-1]:
                    regime = "risk-on"
                elif closes[-1] < sma50[-1]:
                    regime = "risk-off"

            self._set_custom_state("regime", regime)
            self._set_custom_state("vix_proxy", round(latest_vix, 1))

            # Signaal: prijs boven SMA50 + EMA20 stijgend + niet risk-off
            if closes[-1] > sma50[-1] and ema20[-1] > ema20[-3] and regime != "risk-off":
                price = _kraken_ticker("XXBTZEUR") or float(closes[-1])
                win_rate = self._get_rolling_win_rate()
                win_adj = 0.03 if regime == "risk-on" else 0.0

                # Lage VIX = rustige markt = betere trend-volging
                if latest_vix < 15:
                    win_adj += 0.02

                signals.append(TradeSignal(
                    symbol="XXBTZEUR",
                    broker="kraken",
                    direction="long",
                    win_probability=max(0.45, min(0.65, win_rate + win_adj)),
                    expected_win_pct=0.08,
                    expected_loss_pct=0.04,
                    current_price=price,
                    strategy_id="trend-follow",
                    notes=f"Macro Trend BTC: boven SMA50 regime={regime} VIX={latest_vix:.1f}{fg_note}",
                ))

            # ETH volgt BTC trend
            if regime == "risk-on":
                eth_data = _kraken_ohlc("XETHZEUR", interval=240, count=50)
                if eth_data and len(eth_data["close"]) >= 20:
                    eth_closes = eth_data["close"]
                    eth_sma = sma(eth_closes, 20)
                    if not np.isnan(eth_sma[-1]) and eth_closes[-1] > eth_sma[-1]:
                        eth_price = _kraken_ticker("XETHZEUR") or float(eth_closes[-1])
                        signals.append(TradeSignal(
                            symbol="XETHZEUR",
                            broker="kraken",
                            direction="long",
                            win_probability=max(0.45, min(0.60, self._get_rolling_win_rate())),
                            expected_win_pct=0.06,
                            expected_loss_pct=0.04,
                            current_price=eth_price,
                            strategy_id="trend-follow",
                            notes=f"Macro Trend ETH: risk-on regime, boven SMA20{fg_note}",
                        ))

        except Exception as e:
            log.warning("Macro Trend (Kraken) fout: %s", e)
        return signals

    def _ibkr_signals(self, ibkr, aggressive: bool) -> list[TradeSignal]:
        """IBKR-gebaseerde trend signalen met SPY/TLT regime filter."""
        signals = []
        try:
            bars = ibkr.get_historical_bars(
                symbol="EUR", duration="1 Y", bar_size="1 day",
                what_to_show="MIDPOINT", currency="USD",
            )
            if len(bars) < 201:
                return signals
            closes = np.array([b.close for b in bars])

            sma200 = sma(closes, 200)
            if np.isnan(sma200[-1]):
                return signals

            regime = "neutral"
            try:
                spy_bars = ibkr.get_stock_bars("SPY", duration="60 D", bar_size="1 day")
                tlt_bars = ibkr.get_stock_bars("TLT", duration="60 D", bar_size="1 day")
                if spy_bars and tlt_bars and len(spy_bars) >= 20 and len(tlt_bars) >= 20:
                    spy_closes = np.array([b.close for b in spy_bars])
                    tlt_closes = np.array([b.close for b in tlt_bars])
                    ratio = spy_closes / tlt_closes
                    ratio_sma = sma(ratio, 20)
                    if not np.isnan(ratio_sma[-1]) and not np.isnan(ratio_sma[-5]):
                        if ratio[-1] > ratio_sma[-1] and ratio_sma[-1] > ratio_sma[-5]:
                            regime = "risk-on"
                        elif ratio[-1] < ratio_sma[-1] and ratio_sma[-1] < ratio_sma[-5]:
                            regime = "risk-off"
            except Exception as e:
                log.debug("Regime detectie fout: %s", e)

            self._set_custom_state("regime", regime)

            if closes[-1] > sma200[-1] and regime != "risk-off":
                win_rate = self._get_rolling_win_rate()
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
            log.warning("Macro Trend (IBKR) fout: %s", e)
        return signals
