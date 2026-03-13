#!/usr/bin/env python3
"""NakedTrader — Trading bot entry point.

Gebruik: python run_bot.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from nakedtrader.config import load_config
from nakedtrader.orchestrator import TradingBot
from nakedtrader.performance.store import PerformanceStore

log = logging.getLogger(__name__)


def main():
    config = load_config()

    log.info(f"Modus: {'PAPER' if config.paper_mode else 'LIVE'}")
    log.info(f"Kapitaal: EUR {config.total_capital:,.2f}")
    log.info(f"Interval: {config.interval_seconds}s")

    # Performance store + backfill
    store = PerformanceStore(
        data_dir=config.data_dir,
        starting_capital=config.total_capital,
    )
    store.backfill_history(months=36)

    bot = TradingBot(config)
    bot.store = store

    try:
        bot.connect_brokers()
        bot.run_loop(interval_seconds=config.interval_seconds)
    finally:
        bot.disconnect_brokers()


if __name__ == "__main__":
    main()
