"""
breakout.py — Breakout Hunter strategie.

Donchian Channel breakout.
Zelfaanpassing: kapitaalverschuiving tussen brokers o.b.v. per-broker win-rate.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import donchian_channels, atr
from .base import BaseStrategy
from .data_feeds import _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


class BreakoutStrategy(BaseStrategy):
    """
    Donchian Channel breakout.
    Zelfaanpassing: kapitaalverschuiving tussen brokers o.b.v. per-broker win-rate.
    """

    meta = StrategyMeta(
        id="breakout",
        name="Breakout Hunter",
        color="#ff4466",
        description="Donchian Channel breakout with ATR expansion filter. Shifts capital between brokers based on per-broker win rate.",
        description_nl="Donchian Channel breakout met ATR-expansie filter. Verschuift kapitaal tussen brokers op basis van per-broker win-rate.",
        risk_level="Risicovol",
        risk_score=5,
        expected_return_min=-10.0,
        expected_return_max=60.0,
        markets=["BTC/EUR", "ETH/EUR", "SOL/USD"],
        indicators=["Donchian(20)", "ATR(14)"],
        timeframe="4h",
        broker="ibkr+kraken",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR", "SOL/USD": "SOLUSD"}

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        # Per-broker win-rate uit custom state
        custom = self._get_custom_state()
        ibkr_wins = custom.get("ibkr_win_rate", 0.5)
        kraken_wins = custom.get("kraken_win_rate", 0.5)

        # Kies voorkeurs-broker o.b.v. win-rate
        preferred_broker = "ibkr" if ibkr_wins >= kraken_wins else "kraken"

        for market, pair in self.PAIRS.items():
            try:
                data = _kraken_ohlc(pair, interval=240, count=200)
                if not data:
                    continue
                highs = data["high"]
                lows = data["low"]
                closes = data["close"]

                if len(closes) < 21:
                    continue

                # Donchian channels
                dc_upper, dc_lower, dc_middle = donchian_channels(highs, lows, 20)
                atr_vals = atr(highs, lows, closes, 14)

                latest_close = closes[-1]
                latest_dc_upper = dc_upper[-2]  # vorige bar (breakout boven vorige upper)
                latest_atr = atr_vals[-1]
                prev_atr = atr_vals[-2] if len(atr_vals) > 1 else np.nan

                if np.isnan(latest_dc_upper) or np.isnan(latest_atr) or np.isnan(prev_atr):
                    continue

                atr_expanding = latest_atr > prev_atr

                # Signaal: close boven vorige Donchian upper + ATR expanding
                if aggressive:
                    # Aggressief: ook near-breakout (binnen 0.5%) zonder ATR vereiste
                    near_breakout = latest_close > latest_dc_upper * 0.995
                    trigger = near_breakout
                else:
                    trigger = latest_close > latest_dc_upper and atr_expanding
                if trigger:
                    price = _kraken_ticker(pair) or float(latest_close)
                    win_rate = self._get_rolling_win_rate()

                    # Open Interest bevestiging: stijgende OI = echte breakout
                    oi_note = ""
                    oi_boost = 0.0
                    try:
                        from nakedtrader.bots.data_feeds import _binance_funding_rate
                        sym_map = {"XXBTZEUR": "BTCUSDT", "XETHZEUR": "ETHUSDT", "SOLUSD": "SOLUSDT"}
                        bn_sym = sym_map.get(pair)
                        if bn_sym:
                            fr = _binance_funding_rate(bn_sym)
                            oi = fr.get("open_interest", 0)
                            if oi > 0:
                                oi_note = f" OI={oi:.0f}"
                                # Hoge OI = meer convictie in breakout
                                oi_boost = 0.02
                    except Exception:
                        pass

                    signals.append(TradeSignal(
                        symbol=pair,
                        broker=preferred_broker,
                        direction="long",
                        win_probability=max(0.40, min(0.65, win_rate + oi_boost)),
                        expected_win_pct=0.15,
                        expected_loss_pct=0.07,
                        current_price=price,
                        strategy_id="breakout",
                        notes=f"Breakout Hunter {market}: boven Donchian({latest_dc_upper:.2f}) ATR expanding via {preferred_broker}{oi_note}",
                    ))
            except Exception as e:
                log.warning(f"Breakout Hunter fout {pair}: {e}")
        return self._llm_enhance_signals(signals)
