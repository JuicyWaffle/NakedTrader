"""
scalper.py — Crypto Scalper (ArbitrageStrategy).

Order book imbalance scalper.
Zelfaanpassing: pauzeert bij hoge spread of dunne liquiditeit.
"""

import logging

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import order_book_imbalance
from .base import BaseStrategy
from .data_feeds import _kraken_orderbook, _binance_funding_rate

log = logging.getLogger(__name__)


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

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
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

                imb_threshold = 0.15 if aggressive else self.MIN_IMBALANCE
                if abs(imb) > imb_threshold:
                    price = (best_bid + best_ask) / 2
                    direction = "long" if imb > 0 else "short"
                    win_rate = self._get_rolling_win_rate()

                    # Funding rate bevestiging: hoge funding + short-imbalance = sterker signaal
                    funding_boost = 0.0
                    try:
                        symbol_map = {"XXBTZEUR": "BTCUSDT", "XETHZEUR": "ETHUSDT"}
                        bn_sym = symbol_map.get(pair)
                        if bn_sym:
                            fr = _binance_funding_rate(bn_sym)
                            rate = fr.get("funding_rate", 0)
                            # Funding rate bevestigt richting: positief = long-heavy, negatief = short-heavy
                            if (direction == "short" and rate > 0.0003) or (direction == "long" and rate < -0.0003):
                                funding_boost = 0.03  # bonus op win_prob
                    except Exception:
                        pass

                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction=direction,
                        win_probability=max(0.55, min(0.75, win_rate + funding_boost)),
                        expected_win_pct=abs(imb) * 0.02,
                        expected_loss_pct=0.005,
                        current_price=price,
                        strategy_id="arbitrage",
                        notes=f"Scalper {market}: imbalance={imb:.3f} spread={spread_pct:.4%} funding_boost={funding_boost:.2f}",
                    ))
            except Exception as e:
                log.warning(f"Crypto Scalper fout {pair}: {e}")
        return signals
