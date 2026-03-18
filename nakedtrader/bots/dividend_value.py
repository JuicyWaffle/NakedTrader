"""
dividend_value.py — Dividend & Value strategie via IBKR.

Koopt oversold dividend-aristocraten en hoog-rendement aandelen.
RSI + z-score entry timing op kwaliteitsnamen met SMA(200) trendfilter.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, rsi, bollinger_bands, zscore
from .base import BaseStrategy
from .data_feeds import _equity_daily_bars, _equity_get_price

log = logging.getLogger(__name__)


class DividendValueStrategy(BaseStrategy):
    """Dividend & Value — koopt oversold dividend-aristocraten."""

    meta = StrategyMeta(
        id="dividend-value",
        name="Dividend & Value",
        color="#4488cc",
        description="Buy oversold dividend aristocrats and high-yield stocks. RSI + z-score entry timing on quality names with SMA(200) trend filter.",
        description_nl="Koop oversold dividend-aristocraten en hoog-rendement aandelen. RSI + z-score entry timing op kwaliteitsnamen met SMA(200) trendfilter.",
        risk_level="Conservatief",
        risk_score=2,
        expected_return_min=5.0,
        expected_return_max=18.0,
        markets=[
            "KO", "PG", "MMM", "T", "VZ", "IBM",
            "MO", "PM", "EMR", "ADP", "CL", "SYY",
            "WBA", "BEN", "TROW",
            "O", "MAIN", "STAG",
            "EPD", "MPC", "PSX", "VLO",
            "VIG", "SCHD", "DVY", "HDV", "SPYD",
            "NEE", "DUK", "SO",
        ],
        indicators=["RSI(14)", "SMA(50)", "SMA(200)", "BB(20,2.0)", "Z-score(20)", "Volume SMA(20)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # Dividend Aristocrats
        "KO": "staples", "PG": "staples", "MMM": "industrials",
        "T": "telecom", "VZ": "telecom", "IBM": "tech",
        "MO": "staples", "PM": "staples", "EMR": "industrials",
        "ADP": "tech", "CL": "staples", "SYY": "staples",
        "WBA": "healthcare", "BEN": "financials", "TROW": "financials",
        # High-Yield / REITs
        "O": "reit", "MAIN": "reit", "STAG": "reit",
        "EPD": "energy-mlp", "MPC": "energy", "PSX": "energy", "VLO": "energy",
        # Dividend ETFs
        "VIG": "div-growth-etf", "SCHD": "div-quality-etf", "DVY": "div-yield-etf",
        "HDV": "div-hd-etf", "SPYD": "div-sp-etf",
        # Utilities
        "NEE": "utilities", "DUK": "utilities", "SO": "utilities",
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
                rsi_vals = rsi(closes, 14)
                sma200 = sma(closes, 200)
                bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2.0)
                z_vals = zscore(closes, 20)
                vol_sma = sma(volumes, 20)

                if any(np.isnan(x[-1]) for x in [rsi_vals, sma200, bb_lower, z_vals]):
                    continue
                if np.isnan(vol_sma[-1]) or vol_sma[-1] == 0:
                    continue

                # ── Filters (alleen long) ──
                # 1. RSI oversold
                rsi_threshold = 50 if aggressive else 40
                if rsi_vals[-1] >= rsi_threshold:
                    continue

                # 2. Langetermijn-uptrend
                if closes[-1] <= sma200[-1]:
                    continue

                # 3. Z-score pullback
                z_threshold = -0.5 if aggressive else -1.0
                if z_vals[-1] >= z_threshold:
                    continue

                # 4. Prijs dicht bij lower Bollinger Band (normaal mode)
                if not aggressive:
                    bb_range = bb_upper[-1] - bb_lower[-1]
                    if bb_range > 0:
                        bb_pct = (closes[-1] - bb_lower[-1]) / bb_range
                        if bb_pct > 0.25:
                            continue

                # 5. Volume check
                if volumes[-1] < vol_sma[-1] * 0.8:
                    continue

                # 6. Quality filter: golden cross (normaal mode)
                if not aggressive:
                    sma50 = sma(closes, 50)
                    if np.isnan(sma50[-1]):
                        continue
                    if sma50[-1] <= sma200[-1]:
                        continue

                price = _equity_get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                win_rate = self._get_rolling_win_rate()

                # RSI depth bonus
                rsi_boost = min(0.04, (40 - rsi_vals[-1]) * 0.002) if rsi_vals[-1] < 40 else 0
                wp = max(0.50, min(0.72, win_rate + rsi_boost))

                signals.append(TradeSignal(
                    symbol=symbol,
                    broker="ibkr",
                    direction="long",
                    win_probability=wp,
                    expected_win_pct=0.04,
                    expected_loss_pct=0.02,
                    current_price=price,
                    strategy_id="dividend-value",
                    notes=(
                        f"Dividend Value LONG {symbol} ({sector}): "
                        f"RSI={rsi_vals[-1]:.0f} Z={z_vals[-1]:.2f} "
                        f"Vol={volumes[-1]/vol_sma[-1]:.1f}x"
                    ),
                ))

            except Exception as e:
                log.warning("Dividend Value fout %s: %s", symbol, e)

        return self._llm_enhance_signals(signals)
