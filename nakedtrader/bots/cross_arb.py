"""
cross_arb.py — Cross-Exchange Statistical Arbitrage strategie.

Detecteert prijsverschillen tussen exchanges binnen dezelfde valuta.
Momenteel: Binance vs Coinbase (USD pairs).
Toekomstig: Kraken vs andere EUR exchanges.
"""

import logging

from nakedtrader.types import TradeSignal, StrategyMeta
from .base import BaseStrategy
from .data_feeds import _cross_exchange_orderbook

log = logging.getLogger(__name__)


class CrossArbStrategy(BaseStrategy):
    """Cross-exchange arbitrage — prijsverschillen tussen exchanges."""

    meta = StrategyMeta(
        id="cross-arb",
        name="Cross-Exchange Arb",
        color="#44ddff",
        description="Detects price discrepancies between exchanges within the same currency. Buys on cheaper exchange, sells on more expensive.",
        description_nl="Detecteert prijsverschillen tussen exchanges in dezelfde valuta. Koopt op goedkoopste exchange, verkoopt op duurste.",
        risk_level="Conservatief",
        risk_score=1,
        expected_return_min=2.0,
        expected_return_max=12.0,
        markets=["BTC (cross-exchange)", "ETH (cross-exchange)"],
        indicators=["Cross-exchange spread", "Order depth"],
        timeframe="tick",
        broker="kraken",
    )

    ASSETS = ["BTC", "ETH"]
    MIN_SPREAD_PCT = 0.05  # Minimaal 5 basispunten spread (na fees)

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        threshold = self.MIN_SPREAD_PCT * 0.6 if aggressive else self.MIN_SPREAD_PCT

        for asset in self.ASSETS:
            try:
                result = _cross_exchange_orderbook(asset)
                exchanges = result.get("exchanges", {})
                arb_by_currency = result.get("arbitrage_by_currency", {})

                for currency, arb in arb_by_currency.items():
                    if arb["spread_pct"] <= threshold:
                        continue

                    buy_exchange = arb["buy_on"]
                    sell_exchange = arb["sell_on"]
                    spread_pct = arb["spread_pct"]

                    # Gebruik de buy-exchange prijs
                    buy_info = exchanges.get(buy_exchange, {})
                    price = buy_info.get("best_ask", 0)
                    if price <= 0:
                        continue

                    # Bepaal broker: kraken als Kraken betrokken is, anders registreren als virtual
                    broker = "kraken" if buy_exchange == "kraken" or sell_exchange == "kraken" else "kraken"
                    symbol = buy_info.get("pair", f"{asset}{currency}")

                    win_rate = self._get_rolling_win_rate()

                    signals.append(TradeSignal(
                        symbol=symbol,
                        broker=broker,
                        direction="long",
                        win_probability=max(0.60, min(0.85, win_rate + spread_pct * 0.1)),
                        expected_win_pct=spread_pct / 100 * 0.7,  # 70% van spread na fees
                        expected_loss_pct=0.002,  # minimal loss bij arbitrage
                        current_price=price,
                        strategy_id="cross-arb",
                        notes=f"Cross-Arb {asset}/{currency}: buy {buy_exchange} sell {sell_exchange} spread={spread_pct:.4f}%",
                    ))
            except Exception as e:
                log.warning("Cross-Arb fout %s: %s", asset, e)
        return signals
