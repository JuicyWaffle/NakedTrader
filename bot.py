"""Backward compatibility shim — gebruik nakedtrader package.

Nieuwe entry point: python run_bot.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from nakedtrader.config import Config, load_config  # noqa: E402,F401
from nakedtrader.types import TradeSignal  # noqa: E402,F401
from nakedtrader.money.kelly import KellyPositionSizer  # noqa: E402,F401
from nakedtrader.brokers.ibkr import IBKRBroker  # noqa: E402,F401
from nakedtrader.brokers.kraken import KrakenBroker  # noqa: E402,F401
from nakedtrader.brokers.binance import BinanceBroker  # noqa: E402,F401
from nakedtrader.orchestrator import TradingBot, demo_signals  # noqa: E402,F401
from nakedtrader.performance.tracker import PerformanceTracker  # noqa: E402,F401
from nakedtrader.performance.paper_engine import PaperEngine  # noqa: E402,F401
from nakedtrader.money.adaptive import AdaptiveEngine  # noqa: E402,F401
from nakedtrader.money.risk import RiskManager, RiskLimits  # noqa: E402,F401

log = logging.getLogger(__name__)

if __name__ == "__main__":
    config = load_config()

    log.info(f"Modus: {'PAPER' if config.paper_mode else 'LIVE'}")
    log.info(f"Kapitaal: €{config.total_capital:,.2f}")
    log.info(f"Interval: {config.interval_seconds}s")

    bot = TradingBot(config)

    try:
        bot.connect_brokers()
        bot.run_loop(interval_seconds=config.interval_seconds)
    finally:
        bot.disconnect_brokers()
