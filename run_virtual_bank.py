#!/usr/bin/env python3
"""NakedTrader — Virtual Bank trading loop.

Start de virtuele trading bots die handelen op echte Kraken-marktdata.
Elke bot krijgt €100.000 startkapitaal.

Gebruik: python run_virtual_bank.py
"""

import logging
import sys
from pathlib import Path

# Voeg project root toe
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)


def main():
    from nakedtrader.config import load_config
    from nakedtrader.virtual_bank import VirtualBankBot

    config = load_config()
    data_dir = str(PROJECT_ROOT / config.data_dir)

    log.info("=== NakedTrader Virtual Bank ===")
    log.info(f"Data directory: {data_dir}")

    bot = VirtualBankBot(data_dir=data_dir)
    bot.run_loop(interval=120)


if __name__ == "__main__":
    main()
