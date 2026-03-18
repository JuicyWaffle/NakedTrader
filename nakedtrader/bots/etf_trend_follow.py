"""
etf_trend_follow.py — ETF Trend Follow strategie via IBKR.

Donchian Channel breakout over 35 wereldwijde ETFs in aandelen, obligaties,
grondstoffen en thematische sectoren. ATR-expansie bevestigt echte breakouts.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import ema, rsi, atr, donchian_channels
from .base import BaseStrategy
from .data_feeds import _equity_daily_bars, _equity_get_price

log = logging.getLogger(__name__)

# Leveraged/inverse ETFs — alleen in aggressive mode
_LEVERAGED = {"SQQQ", "TQQQ", "UVXY", "VIXY"}


class EtfTrendFollowStrategy(BaseStrategy):
    """ETF Trend Follow — Donchian breakout over wereldwijde ETFs."""

    meta = StrategyMeta(
        id="etf-trend-follow",
        name="ETF Trend Follow",
        color="#44cc44",
        description="Donchian Channel breakout across 35 global ETFs spanning equities, bonds, commodities, and thematic sectors. ATR expansion confirms real breakouts.",
        description_nl="Donchian Channel breakout over 35 wereldwijde ETFs in aandelen, obligaties, grondstoffen en thematische sectoren. ATR-expansie bevestigt echte breakouts.",
        risk_level="Gematigd",
        risk_score=4,
        expected_return_min=5.0,
        expected_return_max=35.0,
        markets=[
            "IWM", "MDY", "DIA", "RSP", "VTI",
            "INDA", "EWT", "EWY", "EWA", "EWC", "EWU", "EUFN", "VWO",
            "ICLN", "LIT", "HACK", "ROBO", "BLOK", "TAN",
            "JETS", "PAVE",
            "SHY", "AGG", "BNDX", "EMB", "TIP",
            "DBA", "DBC", "COPX", "URA", "WEAT",
            "VIXY", "SQQQ", "TQQQ", "UVXY",
        ],
        indicators=["Donchian(20)", "EMA(9/21)", "ATR(14)", "RSI(14)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # Broad Market
        "IWM": "small-cap", "MDY": "mid-cap", "DIA": "large-cap",
        "RSP": "equal-weight", "VTI": "total-market",
        # International
        "INDA": "india", "EWT": "taiwan", "EWY": "south-korea",
        "EWA": "australia", "EWC": "canada", "EWU": "uk",
        "EUFN": "europe-fin", "VWO": "emerging",
        # Thematic
        "ICLN": "clean-energy", "LIT": "lithium", "HACK": "cybersecurity",
        "ROBO": "robotics", "BLOK": "blockchain", "TAN": "solar",
        "JETS": "airlines", "PAVE": "infrastructure",
        # Fixed Income
        "SHY": "short-bonds", "AGG": "agg-bonds", "BNDX": "intl-bonds",
        "EMB": "em-bonds", "TIP": "tips",
        # Commodity
        "DBA": "agriculture", "DBC": "commodities", "COPX": "copper",
        "URA": "uranium", "WEAT": "wheat",
        # Leveraged / Inverse
        "VIXY": "vix-short", "SQQQ": "inverse-nasdaq",
        "TQQQ": "3x-nasdaq", "UVXY": "vix-long",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        ibkr_ok = ibkr and getattr(ibkr, "is_connected", False)

        for symbol, sector in self.UNIVERSE.items():
            # Leveraged ETFs alleen in aggressive mode
            if symbol in _LEVERAGED and not aggressive:
                continue

            try:
                data = _equity_daily_bars(symbol, ibkr, ibkr_ok)
                if not data or len(data.get("close", [])) < 50:
                    continue

                closes = data["close"]
                highs = data["high"]
                lows = data["low"]

                # Indicatoren
                dc_upper, dc_lower, dc_mid = donchian_channels(highs, lows, 20)
                ema9 = ema(closes, 9)
                ema21 = ema(closes, 21)
                atr_vals = atr(highs, lows, closes, 14)
                rsi_vals = rsi(closes, 14)

                if any(np.isnan(x[-1]) for x in [dc_upper, dc_lower, ema9, ema21, atr_vals, rsi_vals]):
                    continue

                # ATR expansie: huidig > vorig
                atr_expanding = (
                    not np.isnan(atr_vals[-2])
                    and atr_vals[-1] > atr_vals[-2]
                )

                # ── Long breakout ──
                breakout_up = closes[-1] >= dc_upper[-1]
                near_breakout_up = aggressive and closes[-1] >= dc_upper[-1] * 0.995
                ema_bullish = ema9[-1] > ema21[-1]

                rsi_low = 40 if aggressive else 45
                rsi_high = 85 if aggressive else 80

                if (breakout_up or near_breakout_up) and ema_bullish and atr_expanding:
                    if rsi_low < rsi_vals[-1] < rsi_high:
                        price = _equity_get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                        win_rate = self._get_rolling_win_rate()
                        atr_boost = 0.02 if atr_expanding else 0.0
                        wp = max(0.42, min(0.65, win_rate + atr_boost))

                        is_leveraged = symbol in _LEVERAGED
                        signals.append(TradeSignal(
                            symbol=symbol,
                            broker="ibkr",
                            direction="long",
                            win_probability=wp,
                            expected_win_pct=0.20 if is_leveraged else 0.10,
                            expected_loss_pct=0.10 if is_leveraged else 0.05,
                            current_price=price,
                            strategy_id="etf-trend-follow",
                            notes=(
                                f"ETF Trend LONG {symbol} ({sector}): "
                                f"Donchian breakout RSI={rsi_vals[-1]:.0f} "
                                f"ATR={'expanding' if atr_expanding else 'flat'}"
                            ),
                        ))

                # ── Short breakout (aggressive only) ──
                if aggressive:
                    breakout_down = closes[-1] <= dc_lower[-1]
                    ema_bearish = ema9[-1] < ema21[-1]

                    if breakout_down and ema_bearish:
                        if 20 < rsi_vals[-1] < 55:
                            price = _equity_get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                            win_rate = self._get_rolling_win_rate()
                            wp = max(0.40, min(0.60, win_rate))

                            signals.append(TradeSignal(
                                symbol=symbol,
                                broker="ibkr",
                                direction="short",
                                win_probability=wp,
                                expected_win_pct=0.10,
                                expected_loss_pct=0.05,
                                current_price=price,
                                strategy_id="etf-trend-follow",
                                notes=(
                                    f"ETF Trend SHORT {symbol} ({sector}): "
                                    f"Donchian breakdown RSI={rsi_vals[-1]:.0f}"
                                ),
                            ))

            except Exception as e:
                log.warning("ETF Trend fout %s: %s", symbol, e)

        return self._llm_enhance_signals(signals)
