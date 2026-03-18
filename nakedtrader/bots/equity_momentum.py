"""
equity_momentum.py — Equity Momentum strategie via IBKR.

Multi-factor momentum: SMA(50) trend + ROC(20) + EMA(9/21) crossover
+ relatieve sterkte vs SPY + volume bevestiging.
Dagelijks timeframe. yfinance als fallback wanneer IBKR niet draait.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, ema, rsi, roc
from .base import BaseStrategy

log = logging.getLogger(__name__)


class EquityMomentumStrategy(BaseStrategy):
    """Multi-factor equity momentum met relatieve sterkte filter."""

    meta = StrategyMeta(
        id="equity-momentum",
        name="Equity Momentum",
        color="#22ccff",
        description="Multi-factor equity momentum: SMA(50) trend + ROC(20) + EMA crossover + relative strength vs SPY. Daily timeframe.",
        description_nl="Multi-factor equity momentum: SMA(50) trend + ROC(20) + EMA crossover + relatieve sterkte vs SPY. Dagelijks timeframe.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=8.0,
        expected_return_max=30.0,
        markets=[
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "JPM", "GS", "BAC", "V",
            "JNJ", "UNH", "PFE", "ABBV",
            "XOM", "CVX", "COP",
            "SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "VGK", "EFA",
        ],
        indicators=["SMA(50)", "EMA(9/21)", "ROC(20)", "RSI(14)", "Volume SMA(20)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # Tech
        "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "tech",
        "META": "tech", "NVDA": "tech", "TSLA": "tech",
        # Financials
        "JPM": "financials", "GS": "financials", "BAC": "financials", "V": "financials",
        # Healthcare
        "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
        # Energy
        "XOM": "energy", "CVX": "energy", "COP": "energy",
        # ETFs
        "SPY": "broad", "QQQ": "tech-etf", "XLK": "tech-etf", "XLF": "fin-etf",
        "XLE": "energy-etf", "XLV": "health-etf", "VGK": "europe", "EFA": "intl",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        ibkr_ok = ibkr and getattr(ibkr, "is_connected", False)

        # SPY als benchmark voor relatieve sterkte
        spy_data = self._get_bars("SPY", ibkr, ibkr_ok)
        if not spy_data or len(spy_data.get("close", [])) < 50:
            log.info("Equity Momentum: geen SPY data, skip")
            return signals

        spy_roc_vals = roc(spy_data["close"], 20)
        spy_roc_latest = spy_roc_vals[-1] if not np.isnan(spy_roc_vals[-1]) else 0

        for symbol, sector in self.UNIVERSE.items():
            if symbol == "SPY":
                continue  # benchmark, niet handelen
            try:
                data = self._get_bars(symbol, ibkr, ibkr_ok)
                if not data or len(data.get("close", [])) < 50:
                    continue

                closes = data["close"]
                volumes = data["volume"]

                # Indicatoren
                sma50 = sma(closes, 50)
                ema9 = ema(closes, 9)
                ema21 = ema(closes, 21)
                rsi_vals = rsi(closes, 14)
                roc_vals = roc(closes, 20)
                vol_sma = sma(volumes, 20)

                # Check voor NaN
                if any(np.isnan(x[-1]) for x in [sma50, ema9, ema21, rsi_vals, roc_vals]):
                    continue
                if np.isnan(vol_sma[-1]) or vol_sma[-1] == 0:
                    continue

                # ── Filters ──
                # 1. Uptrend: prijs > SMA(50)
                if closes[-1] <= sma50[-1]:
                    continue

                # 2. Momentum: ROC(20) > 0 + EMA(9) > EMA(21)
                crossover = ema9[-1] > ema21[-1] and ema9[-2] <= ema21[-2]
                ema_bullish = ema9[-1] > ema21[-1]
                roc_positive = roc_vals[-1] > 0

                if aggressive:
                    trigger = (crossover or ema_bullish) and roc_vals[-1] > -1
                else:
                    trigger = (crossover or (ema_bullish and roc_positive))

                if not trigger:
                    continue

                # 3. RSI filter
                rsi_low = 35 if aggressive else 40
                rsi_high = 80 if aggressive else 75
                if not (rsi_low < rsi_vals[-1] < rsi_high):
                    continue

                # 4. Relatieve sterkte vs SPY
                rel_strength = roc_vals[-1] - spy_roc_latest
                if aggressive:
                    if rel_strength < spy_roc_latest * -0.5:
                        continue
                else:
                    if rel_strength < 0:
                        continue

                # 5. Volume bevestiging
                if volumes[-1] < vol_sma[-1] * 0.8:
                    continue

                # Prijs
                price = self._get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                win_rate = self._get_rolling_win_rate()

                # Win probability: base + relatieve sterkte boost
                rel_boost = min(0.05, max(0, rel_strength / 100))
                wp = max(0.45, min(0.70, win_rate + rel_boost))

                signals.append(TradeSignal(
                    symbol=symbol,
                    broker="ibkr",
                    direction="long",
                    win_probability=wp,
                    expected_win_pct=0.08,
                    expected_loss_pct=0.04,
                    current_price=price,
                    strategy_id="equity-momentum",
                    notes=(
                        f"Equity Momentum {symbol} ({sector}): "
                        f"ROC={roc_vals[-1]:.1f}% RSI={rsi_vals[-1]:.0f} "
                        f"RelStr={rel_strength:+.1f}% Vol={volumes[-1]/vol_sma[-1]:.1f}x"
                    ),
                ))

            except Exception as e:
                log.warning("Equity Momentum fout %s: %s", symbol, e)

        return self._llm_enhance_signals(signals)

    def _get_bars(self, symbol: str, ibkr, ibkr_ok: bool) -> dict:
        """Haal dagelijkse bars op — IBKR als beschikbaar, yfinance als fallback."""
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
        # yfinance fallback
        from nakedtrader.bots.data_feeds import _yfinance_daily_bars
        return _yfinance_daily_bars(symbol, period="1y")

    def _get_price(self, symbol: str, ibkr, ibkr_ok: bool) -> float:
        """Haal huidige prijs op — IBKR of yfinance."""
        if ibkr_ok:
            try:
                p = ibkr.get_stock_price(symbol)
                if p > 0:
                    return p
            except Exception:
                pass
        from nakedtrader.bots.data_feeds import _yfinance_ticker
        return _yfinance_ticker(symbol) or 0.0
