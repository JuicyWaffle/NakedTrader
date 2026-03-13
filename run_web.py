#!/usr/bin/env python3
"""NakedTrader — Web dashboard entry point.

Gebruik: python run_web.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from nakedtrader.web.app import run_server


if __name__ == "__main__":
    run_server()
