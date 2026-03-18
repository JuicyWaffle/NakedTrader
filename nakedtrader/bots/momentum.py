"""
momentum.py — Momentum Rider strategie.

EMA(9/21) crossover + RSI(14) filter.
Zelfaanpassing: bij hoge volatiliteit (ATR) worden EMA-periodes verlengd.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import ema, rsi, atr
from .base import BaseStrategy
from .data_feeds import _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    EMA(9/21) crossover + RSI(14) filter.
    Zelfaanpassing: bij hoge volatiliteit (ATR) worden EMA-periodes verlengd.
    """

    meta = StrategyMeta(
        id="momentum",
        name="Momentum Rider",
        color="#00ff88",
        description="EMA(9/21) crossover with RSI filter. Adapts EMA periods based on ATR volatility.",
        description_nl="EMA(9/21) crossover met RSI filter. Past EMA-periodes aan op basis van ATR-volatiliteit.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=15.0,
        expected_return_max=45.0,
        markets=["BTC/EUR", "ETH/EUR", "SOL/EUR", "ADA/EUR", "DOT/EUR", "AVAX/EUR", "LINK/EUR"],
        indicators=["EMA(9/21)", "RSI(14)", "ATR(14)"],
        timeframe="1h",
        broker="kraken",
    )

    PAIRS = {
        "BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR", "SOL/EUR": "SOLEUR",
        "ADA/EUR": "ADAEUR", "DOT/EUR": "DOTEUR", "AVAX/EUR": "AVAXEUR",
        "LINK/EUR": "LINKEUR",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        for market, pair in self.PAIRS.items():
            try:
                data = _kraken_ohlc(pair, interval=60, count=200)
                if not data:
                    continue
                closes = data["close"]
                highs = data["high"]
                lows = data["low"]

                # Zelfaanpassing: ATR-based EMA periode aanpassing
                atr_vals = atr(highs, lows, closes, 14)
                latest_atr = atr_vals[-1] if not np.isnan(atr_vals[-1]) else 0
                avg_atr = np.nanmean(atr_vals[-50:]) if len(atr_vals) >= 50 else latest_atr

                # Hoge volatiliteit -> langere EMA periodes (minder gevoelig)
                if avg_atr > 0 and latest_atr > avg_atr * 1.5:
                    ema_fast, ema_slow = 13, 30
                elif avg_atr > 0 and latest_atr > avg_atr * 1.2:
                    ema_fast, ema_slow = 11, 25
                else:
                    ema_fast, ema_slow = 9, 21

                ema_f = ema(closes, ema_fast)
                ema_s = ema(closes, ema_slow)
                rsi_vals = rsi(closes, 14)

                # Signaal: EMA crossover + RSI filter
                if (not np.isnan(ema_f[-1]) and not np.isnan(ema_s[-1])
                        and not np.isnan(ema_f[-2]) and not np.isnan(ema_s[-2])
                        and not np.isnan(rsi_vals[-1])):

                    crossover = ema_f[-1] > ema_s[-1] and ema_f[-2] <= ema_s[-2]
                    rsi_ok = 40 < rsi_vals[-1] < 75  # niet overbought, niet oversold

                    if aggressive:
                        # Aggressief: ook signaal bij sterk momentum (gap > 0.3%) of gunstige RSI
                        ema_gap_pct = (ema_f[-1] - ema_s[-1]) / ema_s[-1]
                        strong_momentum = ema_f[-1] > ema_s[-1] and ema_gap_pct > 0.003
                        rsi_ok = 35 < rsi_vals[-1] < 80
                        trigger = (crossover or strong_momentum) and rsi_ok
                    else:
                        trigger = crossover and rsi_ok

                    if trigger:
                        price = _kraken_ticker(pair) or float(closes[-1])
                        win_rate = self._get_rolling_win_rate()

                        # Multi-factor bevestiging: funding rate + Fear & Greed
                        confirmation_boost = 0.0
                        extra_notes = []
                        try:
                            from nakedtrader.bots.data_feeds import _binance_funding_rate
                            sym_map = {"XXBTZEUR": "BTCUSDT", "XETHZEUR": "ETHUSDT"}
                            bn_sym = sym_map.get(pair)
                            if bn_sym:
                                fr = _binance_funding_rate(bn_sym)
                                rate = fr.get("funding_rate", 0)
                                # Negatieve funding = contraire bevestiging voor long momentum
                                if rate < -0.0002:
                                    confirmation_boost += 0.02
                                    extra_notes.append(f"FR={rate*100:.3f}%")
                                # Sterk positieve funding = verzwakking (crowded long)
                                elif rate > 0.0005:
                                    confirmation_boost -= 0.02
                                    extra_notes.append(f"FR_warn={rate*100:.3f}%")
                        except Exception:
                            pass

                        note = f"Momentum Rider {market}: EMA({ema_fast}/{ema_slow}) cross RSI={rsi_vals[-1]:.1f}"
                        if extra_notes:
                            note += " " + " ".join(extra_notes)

                        signals.append(TradeSignal(
                            symbol=pair,
                            broker="kraken",
                            direction="long",
                            win_probability=max(0.45, min(0.70, win_rate + confirmation_boost)),
                            expected_win_pct=0.10,
                            expected_loss_pct=0.05,
                            current_price=price,
                            strategy_id="momentum",
                            notes=note,
                        ))
            except Exception as e:
                log.warning(f"Momentum Rider fout {pair}: {e}")
        return self._llm_enhance_signals(signals)
