"""
strategies.py — 5 geavanceerde trading strategieen voor NakedTrader.

Elke strategie:
  - Haalt OHLC/orderbook data op (Kraken public API / IBKR)
  - Berekent indicatoren via indicators.py
  - Leest rolling metrics uit AdaptiveEngine voor zelfaanpassing
  - Retourneert list[TradeSignal] (compatible met TradingBot)

Strategieen:
  1. Momentum Rider   — EMA(9/21) crossover + RSI filter, ATR-based vol aanpassing
  2. Mean Reversion   — BB + Z-score < -2.0, BB-multiplier herberekening
  3. Breakout Hunter  — Donchian channels, kapitaalverschuiving tussen brokers
  4. Crypto Scalper   — Order book imbalance, pauzeert bij dunne liquiditeit
  5. Macro Trend      — SMA(200) + regime filter (SPY/TLT)

Registry: STRATEGIES dict — IDs matchen bot_performance.json
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from indicators import (
    sma, ema, rsi, roc, bollinger_bands, atr,
    donchian_channels, zscore, vix_proxy, order_book_imbalance,
)
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
    broker: str                     # "kraken" | "ibkr" | "ibkr+kraken"
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


def _kraken_orderbook(pair: str, count: int = 25) -> dict:
    """Haal orderboek op via Kraken public Depth API."""
    import krakenex
    api = krakenex.API()
    response = api.query_public("Depth", {"pair": pair, "count": count})
    errors = response.get("error", [])
    if errors:
        log.warning(f"Kraken orderbook fout {pair}: {errors}")
        return {"bids": [], "asks": []}
    result = response["result"]
    book_key = [k for k in result][0]
    return {
        "bids": result[book_key].get("bids", []),
        "asks": result[book_key].get("asks", []),
    }


# ─────────────────────────────────────────────
# Base Strategy (stateful)
# ─────────────────────────────────────────────

class BaseStrategy:
    meta: StrategyMeta

    # Optionele referentie naar AdaptiveEngine (inject via TradingBot)
    _adaptive = None

    def set_adaptive(self, adaptive):
        """Koppel AdaptiveEngine voor zelfaanpassing."""
        self._adaptive = adaptive

    def _get_custom_state(self) -> dict:
        """Haal custom_state op uit AdaptiveEngine."""
        if self._adaptive:
            return self._adaptive.get_state(self.meta.id).custom_state
        return {}

    def _set_custom_state(self, key: str, value):
        """Sla custom_state op in AdaptiveEngine."""
        if self._adaptive:
            state = self._adaptive.get_state(self.meta.id)
            state.custom_state[key] = value
            self._adaptive._save()

    def _get_rolling_win_rate(self) -> float:
        """Haal rolling win-rate uit AdaptiveEngine."""
        if self._adaptive:
            return self._adaptive.get_rolling_win_rate(self.meta.id)
        return 0.5

    def generate_signals(
        self,
        kraken: Optional[KrakenBroker] = None,
        ibkr: Optional[IBKRBroker] = None,
    ) -> list[TradeSignal]:
        raise NotImplementedError


# ─────────────────────────────────────────────
# 1. Momentum Rider — EMA(9/21) crossover + RSI
# ─────────────────────────────────────────────

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
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["EMA(9/21)", "RSI(14)", "ATR(14)"],
        timeframe="1h",
        broker="ibkr",
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
                highs = data["high"]
                lows = data["low"]

                # Zelfaanpassing: ATR-based EMA periode aanpassing
                atr_vals = atr(highs, lows, closes, 14)
                latest_atr = atr_vals[-1] if not np.isnan(atr_vals[-1]) else 0
                avg_atr = np.nanmean(atr_vals[-50:]) if len(atr_vals) >= 50 else latest_atr

                # Hoge volatiliteit → langere EMA periodes (minder gevoelig)
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

                    if crossover and rsi_ok:
                        price = _kraken_ticker(pair) or float(closes[-1])
                        win_rate = self._get_rolling_win_rate()
                        signals.append(TradeSignal(
                            symbol=pair,
                            broker="ibkr",
                            direction="long",
                            win_probability=max(0.45, min(0.70, win_rate)),
                            expected_win_pct=0.10,
                            expected_loss_pct=0.05,
                            current_price=price,
                            strategy_id="momentum",
                            notes=f"Momentum Rider {market}: EMA({ema_fast}/{ema_slow}) cross RSI={rsi_vals[-1]:.1f}",
                        ))
            except Exception as e:
                log.warning(f"Momentum Rider fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 2. Mean Reversion — BB + Z-score
# ─────────────────────────────────────────────

class MeanReversionStrategy(BaseStrategy):
    """
    Bollinger Bands + Z-score < -2.0 → long.
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
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["BB(20)", "Z-score(20)", "VIX proxy"],
        timeframe="15m",
        broker="ibkr",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR"}

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
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
                if latest_close < latest_lower and latest_z < -2.0:
                    price = _kraken_ticker(pair) or float(latest_close)

                    # Lage VIX → markt is rustig → verbreed SL
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


# ─────────────────────────────────────────────
# 3. Breakout Hunter — Donchian Channels
# ─────────────────────────────────────────────

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

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
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
                if latest_close > latest_dc_upper and atr_expanding:
                    price = _kraken_ticker(pair) or float(latest_close)
                    win_rate = self._get_rolling_win_rate()

                    signals.append(TradeSignal(
                        symbol=pair,
                        broker=preferred_broker,
                        direction="long",
                        win_probability=max(0.40, min(0.65, win_rate)),
                        expected_win_pct=0.15,
                        expected_loss_pct=0.07,
                        current_price=price,
                        strategy_id="breakout",
                        notes=f"Breakout Hunter {market}: boven Donchian({latest_dc_upper:.2f}) ATR expanding via {preferred_broker}",
                    ))
            except Exception as e:
                log.warning(f"Breakout Hunter fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 4. Crypto Scalper — Order Book Imbalance
# ─────────────────────────────────────────────

class ArbitrageStrategy(BaseStrategy):
    """
    Order book imbalance scalper.
    Zelfaanpassing: pauzeert bij hoge spread of dunne liquiditeit.
    """

    meta = StrategyMeta(
        id="arbitrage",
        name="Crypto Scalper",
        color="#aa44ff",
        description="Order book imbalance scalper. Pauses during high spread or thin liquidity.",
        description_nl="Order book imbalance scalper. Pauzeert bij hoge spread of dunne liquiditeit.",
        risk_level="Conservatief",
        risk_score=1,
        expected_return_min=2.0,
        expected_return_max=8.0,
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["Order Book Imbalance", "Spread"],
        timeframe="tick",
        broker="kraken",
    )

    PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR"}
    MIN_IMBALANCE = 0.3      # minimaal 30% imbalance
    MAX_SPREAD_PCT = 0.002   # max 0.2% spread
    MIN_BOOK_DEPTH = 10      # minimaal 10 levels

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
        signals = []
        for market, pair in self.PAIRS.items():
            try:
                book = _kraken_orderbook(pair, count=25)
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                if len(bids) < self.MIN_BOOK_DEPTH or len(asks) < self.MIN_BOOK_DEPTH:
                    log.debug(f"Scalper {pair}: te weinig orderbook diepte")
                    continue

                # Spread check
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                spread_pct = (best_ask - best_bid) / best_bid

                if spread_pct > self.MAX_SPREAD_PCT:
                    log.debug(f"Scalper {pair}: spread te hoog ({spread_pct:.4%})")
                    continue

                # Order book imbalance
                imb = order_book_imbalance(bids, asks)

                if abs(imb) > self.MIN_IMBALANCE:
                    price = (best_bid + best_ask) / 2
                    direction = "long" if imb > 0 else "short"
                    win_rate = self._get_rolling_win_rate()

                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction=direction,
                        win_probability=max(0.55, min(0.75, win_rate)),
                        expected_win_pct=abs(imb) * 0.02,
                        expected_loss_pct=0.005,
                        current_price=price,
                        strategy_id="arbitrage",
                        notes=f"Scalper {market}: imbalance={imb:.3f} spread={spread_pct:.4%}",
                    ))
            except Exception as e:
                log.warning(f"Crypto Scalper fout {pair}: {e}")
        return signals


# ─────────────────────────────────────────────
# 5. Macro Trend — SMA(200) + Regime Filter
# ─────────────────────────────────────────────

class TrendFollowingStrategy(BaseStrategy):
    """
    SMA(200) richting + regime filter (SPY vs TLT).
    Risk-on = SPY/TLT stijgend → agressiever.
    Risk-off = SPY/TLT dalend → defensief of skip.
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

    def generate_signals(self, kraken=None, ibkr=None) -> list[TradeSignal]:
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

                # Risk-on → hogere allocatie
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
