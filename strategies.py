"""
strategies.py — 5 trading strategieen voor NakedTrader.

Elke strategie:
  - Haalt OHLC data op (Kraken public API / IBKR)
  - Berekent indicatoren via indicators.py
  - Retourneert list[TradeSignal] (compatible met TradingBot)

Registry: STRATEGIES dict — IDs matchen bot_performance.json
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from indicators import sma, ema, rsi, roc, bollinger_bands, atr
from bot import TradeSignal, KrakenBroker, IBKRBroker

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────

@dataclass
class StrategyMeta:
    id: str
    name: str
    color: str
    description: str
    description_nl: str
    risk_level: str
    risk_score: int                 # 1-5
    expected_return_min: float      # % per jaar
    expected_return_max: float
    markets: list[str]
    indicators: list[str]
    timeframe: str
    broker: str                     # "kraken" | "ibkr"
    active: bool = True


# ─────────────────────────────────────────────
# Helper: Kraken OHLC ophalen (geen auth nodig)
# ─────────────────────────────────────────────

def _kraken_ohlc(pair: str, interval: int = 60, count: int = 200) -> dict:
    """
    Haal OHLC data op via Kraken public API.
    Retourneert dict met numpy arrays: open, high, low, close, volume, time.
    """
    import krakenex
    api = krakenex.API()
    since = int(time.time()) - count * interval * 60
    response = api.query_public("OHLC", {"pair": pair, "interval": interval, "since": since})
    errors = response.get("error", [])
    if errors:
        log.warning(f"Kraken OHLC fout {pair}: {errors}")
        return {}
    result = response["result"]
    ohlc_key = [k for k in result if k != "last"][0]
    rows = result[ohlc_key]
    if not rows:
        return {}
    return {
        "time":   np.array([r[0] for r in rows], dtype=float),
        "open":   np.array([float(r[1]) for r in rows]),
        "high":   np.array([float(r[2]) for r in rows]),
        "low":    np.array([float(r[3]) for r in rows]),
        "close":  np.array([float(r[4]) for r in rows]),
        "volume": np.array([float(r[6]) for r in rows]),
    }


def _kraken_ticker(pair: str) -> Optional[float]:
    """Haal laatste prijs op via Kraken public ticker."""
    import krakenex
    api = krakenex.API()
    response = api.query_public("Ticker", {"pair": pair})
    errors = response.get("error", [])
    if errors:
        return None
    result = response["result"]
    key = list(result.keys())[0]
    return float(result[key]["c"][0])  # laatste trade prijs


# ─────────────────────────────────────────────
# Base Strategy
# ─────────────────────────────────────────────

class BaseStrategy:
    meta: StrategyMeta

    def generate_signals(
        self,
        kraken: Optional[KrakenBroker] = None,
        ibkr: Optional[IBKRBroker] = None,
    ) -> list[TradeSignal]:
        raise NotImplementedError


# ─────────────────────────────────────────────
# 1. Momentum Strategy
# ─────────────────────────────────────────────

class MomentumStrategy(BaseStrategy):
    """RSI(14) > 50 + ROC(12) > 0 → long signaal."""

    meta = StrategyMeta(
        id="momentum",
        name="Momentum",
        color="#00ff88",
        description="Enters long when RSI(14) > 50 and Rate of Change(12) > 0, indicating upward momentum.",
        description_nl="Gaat long wanneer RSI(14) > 50 en Rate of Change(12) > 0, wat opwaarts momentum aangeeft.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=15.0,
        expected_return_max=45.0,
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["RSI(14)", "ROC(12)"],
        timeframe="1h",
        broker="kraken",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR"}

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
        for market, pair in self.PAIRS.items():
            try:
                data = _kraken_ohlc(pair, interval=60, count=200)
                if not data:
                    continue
                closes = data["close"]
                rsi_vals = rsi(closes, 14)
                roc_vals = roc(closes, 12)

                latest_rsi = rsi_vals[-1]
                latest_roc = roc_vals[-1]

                if np.isnan(latest_rsi) or np.isnan(latest_roc):
                    continue

                if latest_rsi > 50 and latest_roc > 0:
                    price = _kraken_ticker(pair) or float(closes[-1])
                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction="long",
                        win_probability=0.58,
                        expected_win_pct=0.15,
                        expected_loss_pct=0.06,
                        current_price=price,
                        notes=f"Momentum {market}: RSI={latest_rsi:.1f} ROC={latest_roc:.1f}%",
                    ))
            except Exception as e:
                log.warning(f"Momentum fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 2. Mean Reversion Strategy
# ─────────────────────────────────────────────

class MeanReversionStrategy(BaseStrategy):
    """Prijs < onderste Bollinger Band + RSI < 30 → long (mean reversion)."""

    meta = StrategyMeta(
        id="mean-reversion",
        name="Mean Reversion",
        color="#0088ff",
        description="Buys when price drops below the lower Bollinger Band and RSI < 30, expecting a bounce back to the mean.",
        description_nl="Koopt wanneer de prijs onder de onderste Bollinger Band zakt en RSI < 30, in afwachting van een terugkeer naar het gemiddelde.",
        risk_level="Conservatief",
        risk_score=2,
        expected_return_min=8.0,
        expected_return_max=25.0,
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["Bollinger Bands(20,2)", "RSI(14)"],
        timeframe="15m",
        broker="kraken",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR"}

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
        for market, pair in self.PAIRS.items():
            try:
                data = _kraken_ohlc(pair, interval=15, count=200)
                if not data:
                    continue
                closes = data["close"]
                upper, middle, lower = bollinger_bands(closes, 20, 2.0)
                rsi_vals = rsi(closes, 14)

                latest_rsi = rsi_vals[-1]
                latest_close = closes[-1]
                latest_lower = lower[-1]

                if np.isnan(latest_rsi) or np.isnan(latest_lower):
                    continue

                if latest_close < latest_lower and latest_rsi < 30:
                    price = _kraken_ticker(pair) or float(latest_close)
                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction="long",
                        win_probability=0.62,
                        expected_win_pct=0.08,
                        expected_loss_pct=0.04,
                        current_price=price,
                        notes=f"Mean Reversion {market}: RSI={latest_rsi:.1f} prijs onder BB",
                    ))
            except Exception as e:
                log.warning(f"Mean Reversion fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 3. Breakout Strategy
# ─────────────────────────────────────────────

class BreakoutStrategy(BaseStrategy):
    """Prijs > 20-period high + ATR expanding → long breakout."""

    meta = StrategyMeta(
        id="breakout",
        name="Breakout",
        color="#ff4466",
        description="Goes long when price breaks above the 20-period high with expanding ATR, catching momentum breakouts.",
        description_nl="Gaat long wanneer de prijs boven het 20-periode hoogtepunt breekt met stijgende ATR, om momentum-uitbraken te vangen.",
        risk_level="Risicovol",
        risk_score=5,
        expected_return_min=-10.0,
        expected_return_max=60.0,
        markets=["BTC/EUR", "ETH/EUR", "SOL/USD"],
        indicators=["20-period High", "ATR(14)"],
        timeframe="4h",
        broker="kraken",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR", "SOL/USD": "SOLUSD"}

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
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

                # 20-period high (rolling)
                period_high = np.max(highs[-21:-1])
                atr_vals = atr(highs, lows, closes, 14)

                latest_close = closes[-1]
                latest_atr = atr_vals[-1]
                prev_atr = atr_vals[-2] if len(atr_vals) > 1 else np.nan

                if np.isnan(latest_atr) or np.isnan(prev_atr):
                    continue

                atr_expanding = latest_atr > prev_atr

                if latest_close > period_high and atr_expanding:
                    price = _kraken_ticker(pair) or float(latest_close)
                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction="long",
                        win_probability=0.52,
                        expected_win_pct=0.20,
                        expected_loss_pct=0.10,
                        current_price=price,
                        notes=f"Breakout {market}: boven 20p high ({period_high:.2f}) ATR expanding",
                    ))
            except Exception as e:
                log.warning(f"Breakout fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 4. Trend Following Strategy
# ─────────────────────────────────────────────

class TrendFollowingStrategy(BaseStrategy):
    """EMA(50) kruist boven EMA(200) → golden cross long signaal."""

    meta = StrategyMeta(
        id="trend-follow",
        name="Trend Follow",
        color="#ffaa44",
        description="Enters long on a golden cross: EMA(50) crosses above EMA(200), indicating a long-term uptrend.",
        description_nl="Gaat long bij een golden cross: EMA(50) kruist boven EMA(200), wat een langetermijn opwaartse trend aangeeft.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=5.0,
        expected_return_max=20.0,
        markets=["EUR/USD"],
        indicators=["EMA(50)", "EMA(200)"],
        timeframe="1d",
        broker="ibkr",
    )

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
        try:
            if ibkr and ibkr.ib and ibkr.ib.isConnected():
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
            else:
                # Fallback: gebruik Kraken EURUSD proxy (minder ideaal maar werkt)
                log.info("Trend Follow: geen IBKR verbinding, skip")
                return signals

            ema50 = ema(closes, 50)
            ema200 = ema(closes, 200)

            # Golden cross: EMA50 net boven EMA200 gekomen
            if (not np.isnan(ema50[-1]) and not np.isnan(ema200[-1])
                    and not np.isnan(ema50[-2]) and not np.isnan(ema200[-2])):
                if ema50[-1] > ema200[-1] and ema50[-2] <= ema200[-2]:
                    signals.append(TradeSignal(
                        symbol="EURUSD",
                        broker="ibkr",
                        direction="long",
                        win_probability=0.56,
                        expected_win_pct=0.05,
                        expected_loss_pct=0.03,
                        current_price=float(closes[-1]),
                        notes=f"Trend Follow EURUSD: Golden Cross EMA50={ema50[-1]:.5f} > EMA200={ema200[-1]:.5f}",
                    ))
        except Exception as e:
            log.warning(f"Trend Follow fout: {e}")
        return signals


# ─────────────────────────────────────────────
# 5. Arbitrage Strategy
# ─────────────────────────────────────────────

class ArbitrageStrategy(BaseStrategy):
    """Driehoeksarbitrage: BTC/EUR × ETH/BTC vs ETH/EUR, spread > 0.3%."""

    meta = StrategyMeta(
        id="arbitrage",
        name="Arbitrage",
        color="#aa44ff",
        description="Triangular arbitrage across BTC/EUR, ETH/EUR, and ETH/BTC when the price discrepancy exceeds 0.3%.",
        description_nl="Driehoeksarbitrage over BTC/EUR, ETH/EUR en ETH/BTC wanneer het prijsverschil groter is dan 0.3%.",
        risk_level="Conservatief",
        risk_score=1,
        expected_return_min=2.0,
        expected_return_max=8.0,
        markets=["BTC/EUR", "ETH/EUR", "ETH/BTC"],
        indicators=["Driehoeks-spread"],
        timeframe="tick",
        broker="kraken",
    )

    THRESHOLD = 0.003  # 0.3%

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
        try:
            btc_eur = _kraken_ticker("XXBTZEUR")
            eth_eur = _kraken_ticker("XETHZEUR")
            eth_btc = _kraken_ticker("XETHXXBT")

            if not all([btc_eur, eth_eur, eth_btc]):
                return signals

            # Driehoek: koop ETH met EUR, verkoop ETH voor BTC, verkoop BTC voor EUR
            # Implied ETH/EUR via BTC = eth_btc * btc_eur
            implied_eth_eur = eth_btc * btc_eur
            spread = (implied_eth_eur - eth_eur) / eth_eur

            if abs(spread) > self.THRESHOLD:
                if spread > 0:
                    # Koop ETH/EUR direct, verkoop ETH/BTC + verkoop BTC/EUR
                    direction = "long"
                    notes = f"Arb: koop ETH/EUR ({eth_eur:.2f}), implied={implied_eth_eur:.2f}, spread={spread*100:.3f}%"
                else:
                    # Omgekeerd
                    direction = "short"
                    notes = f"Arb: verkoop ETH/EUR ({eth_eur:.2f}), implied={implied_eth_eur:.2f}, spread={spread*100:.3f}%"

                signals.append(TradeSignal(
                    symbol="XETHZEUR",
                    broker="kraken",
                    direction=direction,
                    win_probability=0.75,
                    expected_win_pct=abs(spread) * 0.8,
                    expected_loss_pct=0.001,
                    current_price=eth_eur,
                    notes=notes,
                ))
        except Exception as e:
            log.warning(f"Arbitrage fout: {e}")
        return signals


# ─────────────────────────────────────────────
# Registry — IDs matchen bot_performance.json
# ─────────────────────────────────────────────

STRATEGIES: dict[str, BaseStrategy] = {
    "momentum":       MomentumStrategy(),
    "mean-reversion": MeanReversionStrategy(),
    "breakout":       BreakoutStrategy(),
    "trend-follow":   TrendFollowingStrategy(),
    "arbitrage":      ArbitrageStrategy(),
}
