"""
equity_mean_reversion.py — Equity Mean Reversion strategie via IBKR.

Bollinger Band + Z-score oversold bounces in liquide large-cap aandelen.
Alleen mean-revert binnen langetermijn-uptrends (SMA200 filter).
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, rsi, bollinger_bands, zscore
from .base import BaseStrategy
from .data_feeds import _equity_daily_bars, _equity_get_price

log = logging.getLogger(__name__)


class EquityMeanReversionStrategy(BaseStrategy):
    """Equity Mean Reversion — koopt oversold large-caps in uptrends."""

    meta = StrategyMeta(
        id="equity-mean-reversion",
        name="Equity Mean Reversion",
        color="#cc44ff",
        description="Bollinger Band + Z-score oversold bounces in liquid large-cap stocks. Only mean-reverts within long-term uptrends (SMA200 filter).",
        description_nl="Bollinger Band + Z-score oversold bounces in liquide large-cap aandelen. Mean-revert alleen binnen langetermijn-uptrends (SMA200 filter).",
        risk_level="Conservatief",
        risk_score=2,
        expected_return_min=6.0,
        expected_return_max=22.0,
        markets=[
            "AAPL", "MSFT", "AMZN", "GOOGL",
            "CRM", "ADBE", "ORCL", "INTC", "AMD", "AVGO", "QCOM",
            "C", "WFC", "MS", "BLK", "SCHW",
            "MRK", "LLY", "TMO", "BMY", "AMGN",
            "CAT", "DE", "HON", "UPS", "RTX",
            "DIS", "SBUX", "NKE", "HD",
        ],
        indicators=["BB(20,2.0)", "Z-score(20)", "RSI(14)", "SMA(200)", "Volume SMA(20)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # Tech
        "AAPL": "tech", "MSFT": "tech", "AMZN": "tech", "GOOGL": "tech",
        "CRM": "tech", "ADBE": "tech", "ORCL": "tech", "INTC": "tech",
        "AMD": "tech", "AVGO": "tech", "QCOM": "tech",
        # Financials
        "C": "financials", "WFC": "financials", "MS": "financials",
        "BLK": "financials", "SCHW": "financials",
        # Healthcare
        "MRK": "healthcare", "LLY": "healthcare", "TMO": "healthcare",
        "BMY": "healthcare", "AMGN": "healthcare",
        # Industrials
        "CAT": "industrials", "DE": "industrials", "HON": "industrials",
        "UPS": "industrials", "RTX": "defense",
        # Consumer
        "DIS": "consumer", "SBUX": "consumer", "NKE": "consumer", "HD": "consumer",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        ibkr_ok = ibkr and getattr(ibkr, "is_connected", False)

        for symbol, sector in self.UNIVERSE.items():
            try:
                data = _equity_daily_bars(symbol, ibkr, ibkr_ok)
                if not data or len(data.get("close", [])) < 200:
                    continue

                closes = data["close"]
                volumes = data["volume"]

                # Indicatoren
                bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2.0)
                z_vals = zscore(closes, 20)
                rsi_vals = rsi(closes, 14)
                trend_sma = sma(closes, 100 if aggressive else 200)
                vol_sma = sma(volumes, 20)

                if any(np.isnan(x[-1]) for x in [bb_lower, z_vals, rsi_vals, trend_sma]):
                    continue
                if np.isnan(vol_sma[-1]) or vol_sma[-1] == 0:
                    continue

                # ── Filters ──
                # 1. Prijs < lower Bollinger Band
                if closes[-1] >= bb_lower[-1]:
                    continue

                # 2. Z-score oversold
                z_threshold = -1.2 if aggressive else -1.8
                if z_vals[-1] >= z_threshold:
                    continue

                # 3. RSI oversold
                rsi_threshold = 40 if aggressive else 35
                if rsi_vals[-1] >= rsi_threshold:
                    continue

                # 4. Langetermijn-uptrend
                if closes[-1] <= trend_sma[-1]:
                    continue

                # 5. Volume spike (capitulatie)
                vol_mult = 0.9 if aggressive else 1.2
                if volumes[-1] < vol_sma[-1] * vol_mult:
                    continue

                # Quality filter: golden cross (normaal mode)
                if not aggressive:
                    sma50 = sma(closes, 50)
                    sma200 = sma(closes, 200)
                    if np.isnan(sma50[-1]) or np.isnan(sma200[-1]):
                        continue
                    if sma50[-1] <= sma200[-1]:
                        continue

                price = _equity_get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                win_rate = self._get_rolling_win_rate()

                # Diepere z-score = hogere kans op reversion
                z_boost = min(0.05, abs(z_vals[-1] + 1.8) * 0.02)
                wp = max(0.50, min(0.72, win_rate + z_boost))

                signals.append(TradeSignal(
                    symbol=symbol,
                    broker="ibkr",
                    direction="long",
                    win_probability=wp,
                    expected_win_pct=0.05,
                    expected_loss_pct=0.025,
                    current_price=price,
                    strategy_id="equity-mean-reversion",
                    notes=(
                        f"Equity MeanRev LONG {symbol} ({sector}): "
                        f"Z={z_vals[-1]:.2f} RSI={rsi_vals[-1]:.0f} "
                        f"BB%={(closes[-1]-bb_lower[-1])/(bb_upper[-1]-bb_lower[-1])*100:.0f} "
                        f"Vol={volumes[-1]/vol_sma[-1]:.1f}x"
                    ),
                ))

            except Exception as e:
                log.warning("Equity MeanRev fout %s: %s", symbol, e)

        return self._llm_enhance_signals(signals)
