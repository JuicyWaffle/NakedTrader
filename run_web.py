"""
run_web.py — Start de NakedTrader web dashboard.
"""

import logging
import argparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    parser = argparse.ArgumentParser(description="NakedTrader Dashboard Server")
    parser.add_argument("--port", type=int, default=8081, help="Server poort")
    args = parser.parse_args()

    from nakedtrader.web.app import run_server
    run_server(port=args.port)

if __name__ == "__main__":
    main()
